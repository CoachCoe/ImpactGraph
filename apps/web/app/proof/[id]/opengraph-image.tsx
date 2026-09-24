import { ImageResponse } from "next/og";
import { claimStanding } from "@/lib/sentences";
import { readProof } from "@/lib/proof";

export const alt = "A verified impact claim on ImpactGraph";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// The proof page declares a large-image card, and a card that declares one and supplies
// nothing renders as a broken preview. This product's whole moment is somebody sharing a
// link to a claim, so the preview carries the verdict rather than a logo.
export default async function OpengraphImage({ params }: { params: Promise<{ id: string }> }) {
  const found = await readProof((await params).id);
  const proof = found.state === "ok" ? found.data : null;
  const standing = proof ? claimStanding(proof.claim.status) : null;
  const verified = standing?.verified ?? false;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "#0e1e1e",
          color: "#f8f9fa",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ width: 14, height: 14, borderRadius: 7, background: "#ff4a22" }} />
          <div style={{ fontSize: 24, letterSpacing: 6, textTransform: "uppercase" }}>
            ImpactGraph
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
          <div
            style={{
              display: "flex",
              alignSelf: "flex-start",
              padding: "10px 22px",
              borderRadius: 999,
              fontSize: 26,
              background: verified ? "#007a7a" : "#3f4f4c",
              color: "#f8f9fa",
            }}
          >
            {standing?.badge ?? "No such claim"}
          </div>
          <div style={{ fontSize: 58, lineHeight: 1.15, letterSpacing: -1 }}>
            {proof?.claim.statement ?? "This claim is not published."}
          </div>
        </div>

        <div style={{ display: "flex", fontSize: 26, color: "#8fa3a3" }}>
          {proof?.operator.name
            ? `${proof.operator.name} · checkable without an account`
            : "Checkable without an account"}
        </div>
      </div>
    ),
    size,
  );
}
