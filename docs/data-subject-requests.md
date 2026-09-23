# Answering a data subject request

What to do when someone asks what is held about them, or asks for it to be deleted. UK
GDPR gives a month to respond, so this is written to be followed rather than worked out.

## What this system holds about a person

Personal data lives in evidence objects — a photograph, a household register, a delivery
receipt naming someone. Each such object has a **data protection record** naming the
lawful basis for holding it, the controller answerable for it, the purpose, the retention
date, and a `subjectReference` identifying who it is about.

Financial records, programmes, claims and outcomes are about organisations and money, not
people, and are out of scope unless an evidence object ties a person to them.

## Find everything held about someone

```
GET /data-subjects/{subjectReference}
```

Returns every object held about that person, with the basis, the controller, when it was
collected, when it is scheduled for erasure, and whether it has already been erased or
objected to. An operator sees the subjects its own organisation is the controller or joint
controller for; an administrator sees all of them.

It reports **what is held, not the contents**. Sending the documents themselves to whoever
asked would make this a way to read every restricted object in the system by guessing a
reference. Release the content through a channel where the requester's identity has been
verified.

The `subjectReference` is the link between a person and their records. If it is not known,
it is on the data protection record of any object involving them, and the operator who
captured the evidence recorded it.

## Erase what is held

```
POST /evidence/{evidenceId}/erase   {"reason": "..."}
```

This destroys the encryption key for the object. The ciphertext may remain on disk and no
longer means anything. Claims that rested on the object are re-evaluated in the same
transaction, so a claim cannot keep a verified badge above evidence that no longer exists.

Erasure is also automatic: `impactgraph.cli retention` erases every object past the
`retainUntil` date on its record, whether or not anyone asked.

## What cannot be erased, and how to say so

The content hash of the object was committed to a public ledger and **cannot be
withdrawn**. This is not a limitation to apologise for in vague terms — say precisely what
remains:

> A cryptographic hash of the document was published to a blockchain when it was
> registered. A hash cannot be reversed into the document, and the document itself has
> been destroyed. What remains is a record that a document of that exact content once
> existed and was registered on a given date. Nobody holding that hash can recover any
> information about you from it.

That is truthful, and it is the reason ADR-011 requires commitments to be over
**publishable, redacted bytes**: what is committed should be what the system would have
been willing to publish anyway.

After erasure, `POST /evidence/{id}/verify-integrity` answers `410 EVIDENCE_ERASED` rather
than reporting a fault. Erased, broken and never-existed are three different answers and
the system gives the right one.

## Objection rather than withdrawal

Where the basis is `LEGITIMATE_INTEREST`, the person is exercising a right to object
rather than withdrawing consent. The effect here is identical — the object is erased — and
the record stores both `withdrawn_at` and `erased_at` so a request that was accepted but
never acted on is visible rather than silent.

## Who is answerable

The controller is recorded per object, because it is not constant: the operating
organisation is controller for programmes it runs, joint controller for ones it co-builds
with a partner, and would be a processor for an organisation running its own programmes on
this platform. A request goes to the controller named on the record, and where a joint
controller is named, both are answerable.

## Limits worth knowing before you promise anything

- **Redaction is not implemented.** Committed bytes are the uploaded bytes, so ADR-011's
  requirement that commitments cover publishable content is a decision the upload path has
  not yet been moved to. Do not invite uploads of photographs of people until it has been.
- **Erasure destroys the key, not the ciphertext.** Backups taken before erasure contain
  the encrypted object; they do not contain the key if the key store is backed up
  separately, which is the point of keeping them apart.
- **Special-category data** — health, vulnerability — is accepted only under explicit
  consent. Whether a substantial-public-interest condition applies instead is a question
  for counsel, not for this file.
