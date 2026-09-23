import Link from "next/link";
import { readFromApi } from "@/lib/api";
import { networkInfo } from "@/lib/network";
import type { Claim } from "@/lib/types";

export const metadata = {
  title: "How ImpactGraph works",
  description:
    "What the system records, what it commits to a blockchain, and what a reader can check without trusting us.",
};

/** The chain the product exists to make inspectable, in the order money travels it. */
const CHAIN = [
  { stage: "Funding", detail: "A contribution enters the system, with its source recorded." },
  { stage: "Allocation", detail: "The money is earmarked for a specific project." },
  { stage: "Transaction", detail: "A payment is observed leaving for a named payee." },
  { stage: "Delivery", detail: "Goods or work arrive, with quantity and date." },
  { stage: "Evidence", detail: "A document is filed and hashed, byte for byte." },
  { stage: "Verification", detail: "An independent party signs what they checked." },
  { stage: "Outcome", detail: "The result the money was meant to produce." },
];

/**
 * Each is answered on a page, not in prose.
 *
 * The page is linked to a real record rather than a named one, so this reads correctly on
 * a deployment that never ran the showcase seed. Where no example exists yet, the question
 * still stands and there is simply nothing to open.
 */
function questions(claimId: string | null, evidenceId: string | null) {
  return [
    { question: "What happened?", answer: "The claim, in one sentence, with its status.", href: claimId && `/claims/${claimId}` },
    { question: "Where did the funding originate?", answer: "The first record in the chain names the funder and the amount.", href: claimId && `/claims/${claimId}` },
    { question: "Where did the money go?", answer: "Every observed payment, reconciled against the evidence filed for it.", href: "/financial" },
    { question: "What evidence supports it?", answer: "The documents, what was extracted from them, and what they cross-check against.", href: evidenceId && `/evidence/${evidenceId}` },
    { question: "Who verified the result?", answer: "Named attestations, each tied to the wallet that signed it.", href: claimId && `/claims/${claimId}` },
    { question: "How reliable is the evidence?", answer: "A score broken into its components, with the largest gap named.", href: claimId && `/claims/${claimId}` },
  ];
}

/** A claim to point the explanation at, with the evidence it rests on. */
async function example() {
  const claims = (await readFromApi<{ id: string }[]>("/claims")) ?? [];
  const claimId = claims[0]?.id ?? null;
  if (!claimId) return { claimId: null, evidenceId: null };
  const claim = await readFromApi<Claim>(`/claims/${claimId}`);
  return { claimId, evidenceId: claim?.evidenceIds[0] ?? null };
}

/** The policy in verification.py. A claim is verified only when every one passes. */
const POLICY = [
  ["Provenance is complete", "There is an unbroken path from funding to outcome."],
  ["Evidence is committed onchain", "Its commitment was registered and confirmed."],
  ["Evidence still matches", "Re-hashing the stored bytes reproduces that commitment."],
  ["Reconciliation passed", "The document agrees with the payment and the delivery."],
  ["The operator has attested", "The delivering organisation has put its name to the claim."],
  ["An independent verifier has confirmed", "A second organisation signed it onchain."],
  ["The attestation covers current evidence", "The signature is bound to this bundle, not an earlier one."],
  ["The verifier is not the operator", "Resolved from the program's operator, not from a request header."],
];

export default async function About() {
  const { claimId, evidenceId } = await example();
  const network = networkInfo();

  return (
    <div className="inspector prose">
      <section className="claimHero">
        <div>
          <span className="eyebrow">HOW THIS WORKS</span>
          <h1>“You say my money produced this outcome. Prove it.”</h1>
          <p>
            Most transparency reporting asks you to trust the organisation doing the
            reporting. This is an attempt at the other thing: a record a donor can check
            without trusting us, and which would visibly break if we altered it.
          </p>
        </div>
      </section>

      <section className="panel">
        <span className="eyebrow">THE CHAIN</span>
        <h2>Every claim is a path, and the path is the argument</h2>
        <p className="subtle">
          A claim is not a number on a dashboard. It is the last link in a chain, and each
          link is a record that points at the one before it. If any link is missing, the
          claim is not verified — the interface says so rather than rounding up.
        </p>
        <ol className="aboutChain">
          {CHAIN.map((item) => (
            <li key={item.stage}>
              <b>{item.stage}</b>
              <span>{item.detail}</span>
            </li>
          ))}
        </ol>
        {claimId ? (
          <p className="note">
            <Link href={`/claims/${claimId}`}>See the chain for a recorded claim →</Link>
          </p>
        ) : null}
      </section>

      <section className="panel">
        <span className="eyebrow">WHAT A DONOR CAN ASK</span>
        <h2>Six questions, each answered by a record</h2>
        <dl className="aboutQuestions">
          {questions(claimId, evidenceId).map((item) => (
            <div key={item.question}>
              <dt>
                {item.href ? <Link href={item.href}>{item.question}</Link> : item.question}
              </dt>
              <dd>{item.answer}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="panel">
        <span className="eyebrow">WHY IT IS CREDIBLE AND NOT MERELY TRANSPARENT</span>
        <h2>Three things we cannot quietly undo</h2>

        <h3>A commitment, not a copy</h3>
        <p className="subtle">
          When evidence is filed, its exact bytes are hashed and that hash is registered on{" "}
          {network.name}. The document itself never leaves our storage — a public chain is
          the wrong place for an invoice. What goes on chain is a fingerprint, and a
          fingerprint cannot be changed after the fact.
        </p>

        <h3>A check you run, not a badge we show</h3>
        <p className="subtle">
          The integrity check re-reads the stored file, hashes it again, and compares the
          result with the registered commitment, character by character in front of you. It
          needs no account. If we altered a stored document, the two hashes would stop
          matching and there is nothing we could do about it.
        </p>

        <h3>Policy that can still say no</h3>
        <p className="subtle">
          A confirmed transaction is necessary, not sufficient. The chain records that an
          identified verifier signed a specific bundle; whether the claim is verified is a
          separate question, and the policy can refuse even when the signature is valid —
          for example when the evidence no longer reconciles.
        </p>
        <ul className="checks">
          {POLICY.map(([requirement, why]) => (
            <li key={requirement} className="pass">
              <b aria-hidden>✓</b>
              <span>
                {requirement}
                <small>{why}</small>
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <span className="eyebrow">WHAT IS REAL HERE</span>
        <h2>And what is deliberately not</h2>
        <p className="subtle">
          This is a proof of concept. Saying so plainly is part of the point: a system whose
          argument is honesty cannot be coy about its own limits.
        </p>
        <div className="aboutSplit">
          <div>
            <h3>Real</h3>
            <ul>
              <li>The registry contract, its roles, and the separation between them</li>
              <li>Byte-exact hashing of uploaded evidence, and the integrity check</li>
              <li>The transactional outbox, idempotency records and audit log</li>
              <li>Receipt, event, bundle-hash and confirmation-depth validation</li>
              <li>The verifier signing with their own wallet</li>
            </ul>
          </div>
          <div>
            <h3>Fictional</h3>
            <ul>
              <li>The NGO, the programme, the payments and every document</li>
              <li>The banking provider — the system observes money, it never moves it</li>
              <li>The document extractor, which returns a fixed result</li>
              <li>Outcome measurement, which is asserted rather than surveyed</li>
            </ul>
          </div>
        </div>
        <p className="note">
          Nothing here is audited, and {network.isPublicEthereum ? "a public testnet" : "a local chain"} is
          not a guarantee of anything beyond what was committed to it.
        </p>
      </section>

      <section className="panel">
        <span className="eyebrow">CHECK IT YOURSELF</span>
        <h2>The shortest path to disproving us</h2>
        <p className="subtle">
          Open the evidence, run the integrity check, and watch the two hashes agree. Then
          have an administrator alter the stored bytes and run it again. The commitment does
          not move, so the mismatch is detectable — by you, not by us telling you.
        </p>
        <div className="uploadActions">
          {evidenceId ? (
            <Link className="button" href={`/evidence/${evidenceId}`}>
              Open the evidence <span aria-hidden>→</span>
            </Link>
          ) : null}
          <Link className="secondary" href="/financial">
            Follow the money
          </Link>
        </div>
      </section>
    </div>
  );
}
