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

import os
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
    #: Sweeping on every request would be wasted work at low volume, and never sweeping
    #: is the leak. Swept when the table is larger than this and a client has gone quiet.
    _sweep_above: int = 512
    _lock: Lock = field(default_factory=Lock)

    def check(self, client: str) -> tuple[bool, int]:
        """Whether this request is allowed, and how many remain in the window."""
        now = time.monotonic()
        with self._lock:
            hits = self._seen.setdefault(client, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if not hits and len(self._seen) > self._sweep_above:
                self._sweep(now)
            if len(hits) >= self.requests:
                return False, 0
            hits.append(now)
            return True, self.requests - len(hits)

    def _sweep(self, now: float) -> None:
        """Drop clients with nothing left in the window.

        Without this every distinct address lives for the life of the process. That was an
        unbounded dictionary a caller could grow, and it is only survivable now because a
        client is a peer address rather than a header anyone can invent.
        """
        stale = [
            client
            for client, hits in self._seen.items()
            if not hits or now - hits[-1] > self.window
        ]
        for client in stale:
            del self._seen[client]

    def forget(self, client: str | None = None) -> None:
        with self._lock:
            if client is None:
                self._seen.clear()
            else:
                self._seen.pop(client, None)


limiter = RateLimiter()
router = APIRouter(prefix="/v1", tags=["public"])


def trusted_proxies() -> set[str]:
    """Addresses whose `X-Forwarded-For` may be believed.

    Empty by default. The header is set by the caller, so believing it unconditionally
    means anyone sends a different value per request and has no limit at all -- which is
    what this did, and three lines of shell proved it.
    """
    configured = os.getenv("TRUSTED_PROXY_ADDRESSES", "")
    return {item.strip() for item in configured.split(",") if item.strip()}


def _client(request: Request) -> str:
    """Who to count this request against.

    The peer address unless the peer is a proxy this deployment configured, in which case
    the address that proxy reports. Without that check a caller sets the header and the
    limit evaporates; with it, an unconfigured deployment behind a proxy counts every
    caller as one, which the documentation says and which is the safe way round.
    """
    peer = request.client.host if request.client else "unknown"
    if peer not in trusted_proxies():
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or peer


def client_address(request: Request) -> str:
    """The trusted peer address, for anything that has to decide who is calling."""
    return _client(request)


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
