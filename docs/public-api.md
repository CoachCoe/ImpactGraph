# Public API

A read-only interface for funders, researchers and anyone checking a claim without an
account. No key, no signup.

Everything here is versioned under `/v1`. Anything anyone automates against is an
interface whether it was meant to be one or not, and this one is meant to be: the routes
the application uses internally stay free to change, and these do not.

## What is in it

Only claims an organisation chose to publish. A claim nobody published answers `404`,
exactly as a claim that does not exist does — whether an organisation is holding an
unpublished claim is not a public fact, so the two cannot be distinguishable.

Nothing here exposes `RESTRICTED` or `INTERNAL` evidence, and a private individual who
funded a programme is not named unless that person asked to be.

## Routes

### `GET /v1/claims`

Published claims, optionally filtered with `?program=<id>`.

```json
{ "version": "1.0", "claims": [ { "id": "...", "statement": "...", "status": "VERIFIED", "publishedAt": "..." } ] }
```

### `GET /v1/claims/{id}`

One published claim: the statement, the current status, the requirement list with the
reason each passed or failed, the attestations with the wallets that signed them, the
bundle hash, and what the verification does and does not prove.

## Status is current, never as-published

Every response reads the claim's status at the moment you ask. A claim that was verified
and has since been challenged answers `CHALLENGED`, and so does its badge and its proof
page. There is no snapshot endpoint and no as-at parameter, because the most damaging
thing this API could offer is a way to keep quoting a verification that has been
withdrawn.

## Rate limit

60 requests per minute per caller. Every response carries `X-RateLimit-Limit` and
`X-RateLimit-Remaining`; exceeding it answers `429` with `Retry-After`.

The limit is held in the API process, so it is one worker's view of one caller. Run two
workers and the effective limit is twice what this says. It is a brake on accidental
hammering, not a security control, and a deployment that needs one should limit at the
proxy as well.

A caller is identified by the connecting address. `X-Forwarded-For` is **only** believed
when the connecting address is listed in `TRUSTED_PROXY_ADDRESSES`, because the header is
set by whoever is calling: believing it unconditionally means a caller sends a different
value on every request and has no limit at all.

The consequence of leaving that unset while running behind a proxy is that every caller
looks like the proxy and shares one quota. That is the safe way round, and it is the
reason to configure it rather than a reason not to.

## Naming the people behind a record

An organisation that funded a programme is named. A private individual is not, on any
public surface — this API, the proof page, the provenance graph, the money trail or the
CSV exports — unless that person used their own consent link to ask to be. Nobody else
can make that choice for them, including the organisation that received the money.

## The badge

`GET /claims/{id}/badge.svg` returns an SVG for embedding on another site:

```html
<a href="https://example.org/proof/claim-water-12-200">
  <img src="https://example.org/api/claims/claim-water-12-200/badge.svg" height="44" alt="..." />
</a>
```

It is cached for five minutes with `must-revalidate`, and the date it was rendered is
drawn into the image. Both of those are deliberate: a badge on somebody else's site is
asserting something, and when the claim is challenged every copy must stop asserting it.
Five minutes is how long the world may keep believing a verification that has been
withdrawn, which is why the number is small and why a test refuses to let it grow past ten
minutes.

## Stability

Fields will be added. Fields will not be removed or change meaning within `/v1`. A
breaking change gets `/v2`, and `/v1` keeps answering.
