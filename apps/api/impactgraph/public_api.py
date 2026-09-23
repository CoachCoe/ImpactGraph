"""The read-only API a funder or researcher queries without an account.

Versioned under /v1 because anything anyone automates against is an interface whether it
was meant to be one or not, and this one is meant to be. The internal read routes stay
free to change; these do not.

Rate limited in-process. That is honest about what it is: one worker's view of one
client, which is enough to stop a script hammering the database and is not enough to stop
anyone determined. A deployment behind a proxy should limit there as well, and this does
not pretend otherwise.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, Response

PUBLIC_API_VERSION = "1.0"

#: Per client per window. Generous for a human or a dashboard, and an obstacle to a
#: scraper that would otherwise walk every claim as fast as the database answers.
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass
class RateLimiter:
    """A fixed window per client, kept in memory.

    Deliberately not a distributed counter. A shared one would be a second thing to
    operate and a second thing to be wrong, and the purpose here is to stop accidental
    hammering rather than to be a security control.
    """

    requests: int = RATE_LIMIT_REQUESTS
    window: float = RATE_LIMIT_WINDOW_SECONDS
    _seen: dict[str, deque[float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def check(self, client: str) -> tuple[bool, int]:
        """Whether this request is allowed, and how many remain in the window."""
        now = time.monotonic()
        with self._lock:
            hits = self._seen.setdefault(client, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.requests:
                return False, 0
            hits.append(now)
            return True, self.requests - len(hits)

    def forget(self) -> None:
        with self._lock:
            self._seen.clear()


limiter = RateLimiter()
router = APIRouter(prefix="/v1", tags=["public"])


def _client(request: Request) -> str:
    # The proxy's idea of the caller where there is one, because otherwise every request
    # through it counts as the same client and the first caller exhausts everyone's quota.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def enforce_rate_limit(request: Request, response: Response) -> None:
    allowed, remaining = limiter.check(_client(request))
    response.headers["X-RateLimit-Limit"] = str(limiter.requests)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise HTTPException(
            429,
            detail={
                "code": "RATE_LIMITED",
                "message": (
                    f"More than {limiter.requests} requests in {int(limiter.window)} seconds. "
                    "This limit exists to keep the public API answering for everyone."
                ),
            },
            headers={"Retry-After": str(int(limiter.window))},
        )


def register(app, *, read, claims_list) -> None:
    """Mount the public routes.

    The read models are passed in rather than imported, because this module is the
    contract and `main` owns the wiring; importing the other way round would be a cycle.
    """

    @router.get("/claims")
    def public_claims(
        request: Request,
        response: Response,
        program: str | None = None,
    ) -> dict:
        """Published claims. A claim nobody published is not listed and not readable here."""
        enforce_rate_limit(request, response)
        return {
            "version": PUBLIC_API_VERSION,
            "claims": [item for item in claims_list(program) if item.get("publishedAt")],
        }

    @router.get("/claims/{claim_id}")
    def public_claim(claim_id: str, request: Request, response: Response) -> dict:
        """One published claim, with what was checked and what it does not prove."""
        enforce_rate_limit(request, response)
        # `read` already answers 404 for an unpublished claim, which is the same answer a
        # reader should get for one that does not exist: whether an organisation has an
        # unpublished claim is not a public fact.
        return {"version": PUBLIC_API_VERSION, **read("proof", claim_id)}

    app.include_router(router)
