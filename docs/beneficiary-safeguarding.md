# Asking recipients whether the aid arrived

**Status: draft for review. Not signed off, and the channel is disabled in code until it
is.** Issue #8 requires an ethics and safeguarding review before this collects anything
from anybody, and nothing in this repository can constitute that review. This document is
what the review has to examine, written by the people building it and owed a reading by
people who did not.

## What is being proposed

Contacting a sample of the people a claim describes, on a feature phone, and asking them
to confirm or dispute that a delivery reached them. The answers are aggregated into a
verification signal.

## Why this is not an ordinary survey

The people being asked are receiving aid from the organisation whose work is being
checked. That is an unequal relationship, and three things follow from it.

**Consent is compromised by default.** A request that arrives while someone is receiving
aid, from a system the aid organisation uses, carries an implication whatever the wording
says. UK GDPR treats consent given under that kind of imbalance as not freely given —
which is the same conclusion ADR-011 and the data protection work reached about
photographs. The lawful basis here is legitimate interest with a genuine objection route,
not consent, and the messaging must not pretend otherwise.

**Non-participation must cost nothing, operationally and not just on paper.** If an
operator can see who did not answer, silence becomes a signal about a person, and the next
distribution is where that shows up. This is the reason the design withholds individual
responses from the operator rather than trusting them not to look.

**A list of aid recipients is dangerous in some places.** Displacement, ethnicity,
household composition and receipt of assistance are all inferable from a contact list tied
to deliveries. In a conflict or a hostile administrative environment that list is a
targeting aid. The system therefore holds the minimum that makes dispatch possible and
nothing that makes a directory.

## What the design does about it

- **The operator never sees an individual response.** Only aggregates, and only above a
  minimum sample size, so a response cannot be attributed by elimination.
- **The operator does not choose who is asked.** The sample is drawn by the platform with
  a recorded method and seed, so it is reproducible and auditable, and an operator cannot
  select the households likely to answer favourably.
- **The operator does not hold the channel.** Numbers are stored sealed and are readable
  only by the dispatch process; no API returns them.
- **Phone numbers are stored encrypted and matched by salted hash**, so deduplication and
  rate limiting work without a readable directory.
- **A dispute is not a failure of the person.** It routes to review, and the requirement
  list shows it as an open question rather than an accusation.

## What the design cannot do about it

Stated plainly, because a safeguarding document that lists only mitigations is marketing.

- **An operator who controls enrolment can still choose who is enrolled.** Numbers are
  collected at enrolment rather than at delivery, which separates the two in time, but an
  operator determined to enrol only cooperative households can. The residual mitigation is
  third-party spot audits, which are not built.
- **A platform operator can see everything.** The separation protects against the
  operating organisation, not against whoever runs this system. That is a different trust
  boundary and it is not addressed here.
- **Nobody can verify from inside the system that non-participation carried no cost.**
  That is an operational commitment, checked by people, not a property of software. The
  acceptance criterion asking for it to be "verified operationally" cannot be satisfied by
  this repository.
- **A confirmation is a statement about a delivery, not about whether it helped.** It
  moves the trust floor from *the NGO says so* to *the recipients say so*, which is a
  genuine improvement and not the same as measuring impact.

## Questions the review must answer

1. Is legitimate interest the right basis, and is the objection route real in a setting
   where objecting means replying to a message from an organisation feeding you?
2. Who is the controller for beneficiary contact data — the operating organisation, or
   the platform? ADR-011 records that this differs per programme.
3. What is the minimum sample below which no aggregate may be shown at all?
4. In which countries is holding a hashed recipient list itself a risk, and should the
   feature be unavailable there?
5. Who is accountable if a recipient is harmed by taking part?

Until these have answers, `BENEFICIARY_CONFIRMATION_ENABLED` stays unset and the dispatch
path refuses to run.
