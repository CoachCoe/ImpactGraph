# Rotating the encryption key

`EVIDENCE_ENCRYPTION_KEY` protects two things: the per-object keys for evidence at rest,
and the bank refresh tokens held on organisations' behalf. One key, one procedure, one
place to lose it — which is the reason it is one key rather than two.

## When

- Anyone who held it leaves.
- It appears anywhere it should not: a log, a ticket, a shell history, a screenshot.
- On a schedule, annually, so the procedure is one that has been run rather than one that
  has been written.

## What rotation does not do

It does not make already-erased evidence readable again, and it does not un-erase
anything. It also does not, on its own, protect against a backup of the key store taken
before an erasure — see `data-subject-requests.md`, which says what to re-apply.

## Procedure

Rotation re-seals; it does not swap. Anything sealed under the old key is unreadable under
the new one, so a swap without a re-seal destroys every bank connection and makes every
evidence object unrecoverable. That is the failure this page exists to prevent.

1. **Generate the new key** and keep the old one available.

   ```bash
   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
   ```

2. **Stop the workers.** `retention` and the chain worker both open evidence; re-sealing
   underneath them is a race with no good outcome.

3. **Re-seal**, with both keys in the environment:

   ```bash
   EVIDENCE_ENCRYPTION_KEY=<old> EVIDENCE_ENCRYPTION_KEY_NEXT=<new> \
     python -m impactgraph.cli rotate-encryption-key
   ```

   Every per-object key and every stored refresh token is decrypted with the old key and
   re-encrypted with the new one, in one transaction per object. An object whose key has
   already been destroyed is skipped: there is nothing to re-seal and it stays erased.

4. **Verify before switching over.** The command reports how many objects and credentials
   it re-sealed and how many it skipped. Check an evidence integrity verification and one
   bank connection against the new key before the old one goes anywhere.

5. **Promote** the new key to `EVIDENCE_ENCRYPTION_KEY`, restart the API and the workers,
   and remove `EVIDENCE_ENCRYPTION_KEY_NEXT`.

6. **Destroy the old key** once a verification has passed under the new one — and not
   before. A rotation that failed halfway is recoverable only while the old key exists.

## Bank credentials specifically

A refresh token is sealed bound to the organisation and provider it belongs to, so a
re-sealed token cannot be moved between organisations even by someone with database
access. Rotation preserves that binding.

If a rotation cannot be completed, revoking the bank connections is the safe end state:
`revoke` destroys the sealed token rather than flagging it, and the organisation
reconnects. Losing a connection is an inconvenience; losing evidence is not recoverable.
