import Link from "next/link";
import type { Provenance, ProvenanceEdge, ProvenanceNode } from "@/lib/types";

const VERBS: Record<string, string> = {
  FUNDS: "funds",
  PAYS: "pays",
  SUPPORTS: "supports",
  PRODUCES: "produces",
  EVIDENCES: "evidences",
};

/**
 * A claim's standing, in words.
 *
 * The chain begins with a named person and used to end on VERIFICATION_PENDING, which
 * tells the reader which end of it the software cares about.
 */
const CLAIM_STANDING: Record<string, string> = {
  VERIFIED: "independently verified",
  VERIFICATION_PENDING: "claimed, not yet attested",
  EVIDENCE_PENDING: "claimed, evidence still being gathered",
  REJECTED: "rejected by the independent verifier",
};

/**
 * Renders the provenance returned by the API rather than a fixed diagram.
 *
 * The records sit on one continuous line because continuity is the argument the graph is
 * making -- that no link is missing. A segment that is not attested breaks the line
 * rather than quietly inheriting the confident styling of the ones that are, so the
 * claim stays falsifiable by eye.
 */
export function ProvenanceGraph({ graph }: { graph: Provenance }) {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const isEvidence = (id: string) => byId.get(id)?.type === "EVIDENCE";

  // Evidence hangs off the record it evidences; it is not a step in the money's path.
  const spineEdges = graph.edges.filter((edge) => !isEvidence(edge.source));
  const outgoing = new Map(spineEdges.map((edge) => [edge.source, edge]));

  const targets = new Set(spineEdges.map((edge) => edge.target));
  const start = graph.nodes.find((node) => !isEvidence(node.id) && !targets.has(node.id));

  const spine: { node: ProvenanceNode; edge?: ProvenanceEdge }[] = [];
  const seen = new Set<string>();
  let cursor = start;
  while (cursor && !seen.has(cursor.id)) {
    seen.add(cursor.id);
    const edge = outgoing.get(cursor.id);
    spine.push({ node: cursor, edge });
    cursor = edge ? byId.get(edge.target) : undefined;
  }

  // Each evidence object attaches once, to what it evidences rather than to everything
  // it supports -- otherwise the same invoice is drawn against both a delivery and a claim.
  const attachment = new Map<string, ProvenanceNode[]>();
  const attached = new Set<string>();
  for (const node of graph.nodes) {
    if (node.type !== "EVIDENCE") continue;
    const edges = graph.edges.filter((edge) => edge.source === node.id);
    const edge = edges.find((item) => item.relationship === "EVIDENCES") ?? edges[0];
    if (!edge || !seen.has(edge.target)) continue;
    attachment.set(edge.target, [...(attachment.get(edge.target) ?? []), node]);
    attached.add(node.id);
  }
  const orphans = graph.nodes.filter(
    (node) => !seen.has(node.id) && !attached.has(node.id),
  );

  return (
    <div className="provenance" aria-label="Claim provenance graph">
      <ol className="chain">
        {spine.map(({ node, edge }) => {
          const next = edge ? byId.get(edge.target) : undefined;
          const attested = edge ? isAttested(edge, next) : true;
          return (
            <li className="chainStep" key={node.id} data-attested={attested}>
              <span className="chainDot" aria-hidden />
              <Record node={node} />
              {(attachment.get(node.id) ?? []).map((evidence) => (
                <Link
                  className="chainBranch"
                  key={evidence.id}
                  href={`/evidence/${evidence.id}`}
                >
                  <small>EVIDENCE</small>
                  <strong>{evidence.title}</strong>
                  <span>{evidence.detail}</span>
                </Link>
              ))}
              {edge && (
                <p className="chainLink">
                  <i>{VERBS[edge.relationship] ?? edge.relationship.toLowerCase()}</i>
                  {!attested && <span> — awaiting independent verification</span>}
                </p>
              )}
            </li>
          );
        })}
      </ol>
      {orphans.length > 0 && (
        <ul className="chainOrphans">
          {orphans.map((node) => (
            <li key={node.id}>
              <Record node={node} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * A segment is drawn as attested when it does not lead to a claim that no independent
 * verifier has attested.
 *
 * It deliberately does NOT consult `confirmedOnchain`. That flag was a seed literal set
 * to true on every edge while `linkProvenance` had no caller anywhere -- so the graph was
 * drawing an onchain attestation that had never happened. The column is now honest
 * (nothing sets it true), and until edges are genuinely committed, reading it here would
 * only break every segment and overstate in the other direction. What the graph can
 * truthfully distinguish is the claim's own standing, which is what it distinguishes.
 */
function isAttested(_edge: ProvenanceEdge, target?: ProvenanceNode): boolean {
  if (target?.type === "CLAIM" && target.detail !== "VERIFIED") return false;
  return true;
}

function Record({ node }: { node: ProvenanceNode }) {
  const detail =
    node.type === "CLAIM" ? (CLAIM_STANDING[node.detail] ?? node.detail) : node.detail;
  return (
    <div className="chainRecord">
      <small>{node.type.replace(/_/g, " ")}</small>
      <strong>{node.title}</strong>
      <span>{detail}</span>
      {node.type === "OUTCOME" ? <Method node={node} /> : null}
    </div>
  );
}

/**
 * How the figure above was arrived at.
 *
 * Beside the number rather than behind a link, and an outcome with nothing recorded says
 * so. Omitting the line where a method is missing would let the least supported figure on
 * the page look exactly like the best supported one.
 */
function Method({ node }: { node: ProvenanceNode }) {
  const confidence =
    typeof node.confidencePercent === "number" ? ` · ${node.confidencePercent}% confidence` : "";
  return (
    <small className="method">
      {node.method
        ? `Method · ${node.method}${node.source ? ` · ${node.source}` : ""}${confidence}`
        : "No method recorded for this figure"}
    </small>
  );
}
