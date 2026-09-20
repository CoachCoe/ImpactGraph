import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ProvenanceGraph } from "./ProvenanceGraph";
import type { Provenance } from "@/lib/types";

const graph: Provenance = {
  nodes: [
    { id: "funding-1", type: "FUNDING", title: "Jane Smith", detail: "$10,000 contributed" },
    { id: "ftx-1", type: "FINANCIAL_TRANSACTION", title: "Aqua Systems Ltd.", detail: "$4,200" },
    { id: "delivery-1", type: "DELIVERY", title: "2 × AquaPure X200", detail: "Delivered" },
    { id: "claim-1", type: "CLAIM", title: "Clean drinking water", detail: "VERIFIED" },
    { id: "ev-1", type: "EVIDENCE", title: "Invoice INV-8291", detail: "Integrity confirmed" },
  ],
  edges: [
    { source: "funding-1", relationship: "PAYS", target: "ftx-1", confirmedOnchain: true },
    { source: "ftx-1", relationship: "SUPPORTS", target: "delivery-1", confirmedOnchain: true },
    { source: "delivery-1", relationship: "PRODUCES", target: "claim-1", confirmedOnchain: true },
    { source: "ev-1", relationship: "EVIDENCES", target: "delivery-1", confirmedOnchain: true },
    { source: "ev-1", relationship: "SUPPORTS", target: "claim-1", confirmedOnchain: true },
  ],
};

const pending = (): Provenance => ({
  nodes: graph.nodes.map((node) =>
    node.id === "claim-1" ? { ...node, detail: "VERIFICATION_PENDING" } : node,
  ),
  edges: graph.edges,
});

describe("ProvenanceGraph", () => {
  it("renders the graph it is given, not a fixed diagram", () => {
    render(<ProvenanceGraph graph={graph} />);
    expect(screen.getByLabelText("Claim provenance graph")).toBeInTheDocument();
    expect(screen.getByText("Jane Smith")).toBeInTheDocument();
    expect(screen.getByText("pays")).toBeInTheDocument();
    expect(screen.getAllByText("supports").length).toBeGreaterThan(0);
  });

  it("reflects a different graph rather than the seeded showcase", () => {
    render(
      <ProvenanceGraph
        graph={{
          nodes: [{ id: "a", type: "FUNDING", title: "Another funder", detail: "€500" }],
          edges: [],
        }}
      />,
    );
    expect(screen.getByText("Another funder")).toBeInTheDocument();
    expect(screen.queryByText("Jane Smith")).not.toBeInTheDocument();
  });

  it("links evidence for inspection and leaves other records inert", () => {
    render(<ProvenanceGraph graph={graph} />);
    // Previously every node was a button labelled "Inspect" with no handler.
    expect(screen.getByRole("link", { name: /Invoice INV-8291/ })).toHaveAttribute(
      "href",
      "/evidence/ev-1",
    );
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("attaches each evidence object once, to what it evidences", () => {
    // ev-1 both EVIDENCES the delivery and SUPPORTS the claim. Drawing it against both
    // would show one invoice as two records.
    render(<ProvenanceGraph graph={graph} />);
    expect(screen.getAllByRole("link", { name: /Invoice INV-8291/ })).toHaveLength(1);
    const delivery = screen.getByText("2 × AquaPure X200").closest("li");
    expect(delivery).not.toBeNull();
    expect(within(delivery!).getByText("Invoice INV-8291")).toBeInTheDocument();
  });

  it("ends the chain in words rather than on a status constant", () => {
    render(<ProvenanceGraph graph={pending()} />);
    expect(screen.getByText("claimed, not yet attested")).toBeInTheDocument();
    expect(screen.queryByText("VERIFICATION_PENDING")).not.toBeInTheDocument();
  });

  it("breaks the line into a claim nobody has independently attested", () => {
    // Every edge here is confirmedOnchain. The onchain flag covers the relationship, not
    // the claim, so a solid line would make an unverified claim look like a fact.
    const { container } = render(<ProvenanceGraph graph={pending()} />);
    const steps = container.querySelectorAll(".chainStep");
    const delivery = Array.from(steps).find((step) =>
      step.textContent?.includes("AquaPure X200"),
    );
    expect(delivery).toHaveAttribute("data-attested", "false");
    expect(screen.getByText(/awaiting independent verification/)).toBeInTheDocument();
  });

  it("keeps the line solid once the claim is verified", () => {
    const { container } = render(<ProvenanceGraph graph={graph} />);
    const unattested = container.querySelectorAll('.chainStep[data-attested="false"]');
    expect(unattested).toHaveLength(0);
    expect(screen.queryByText(/awaiting independent verification/)).not.toBeInTheDocument();
  });

  it("does not claim an onchain attestation for relationships", () => {
    // confirmedOnchain was a seed literal set true on every edge while linkProvenance had
    // no caller anywhere, so the graph drew an attestation that had never happened. The
    // drawing must not vary on that flag in either direction until edges are genuinely
    // committed -- reading it would either overstate or break every segment.
    const shape = (confirmedOnchain: boolean) => {
      const { container } = render(
        <ProvenanceGraph
          graph={{ nodes: graph.nodes, edges: graph.edges.map((e) => ({ ...e, confirmedOnchain })) }}
        />,
      );
      return Array.from(container.querySelectorAll(".chainStep")).map((step) =>
        step.getAttribute("data-attested"),
      );
    };
    expect(shape(false)).toEqual(shape(true));
  });

  it("shows records it cannot place rather than dropping them", () => {
    const stranded: Provenance = {
      nodes: [...graph.nodes, { id: "x", type: "OUTCOME", title: "Orphan", detail: "—" }],
      edges: graph.edges,
    };
    render(<ProvenanceGraph graph={stranded} />);
    expect(screen.getByText("Orphan")).toBeInTheDocument();
  });
});
