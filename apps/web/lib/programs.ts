import { readFromApiResult } from "@/lib/api";
import type { Program } from "@/lib/types";

/**
 * Which program a page is about.
 *
 * Every donor-facing route used to name `program-clean-water-kenya-2026` in its source,
 * so a second organisation's program could not be reached at all. A route now says which
 * program it wants, and falls back to the only one when there is only one -- which keeps
 * a single-tenant deployment a single click deep rather than making everyone choose from
 * a list of one.
 */
export type ProgramChoice =
  | { state: "resolved"; program: Program }
  | { state: "choose"; programs: Program[] }
  | { state: "none" }
  | { state: "unavailable" };

export async function resolveProgram(requested?: string): Promise<ProgramChoice> {
  if (requested) {
    const asked = await readFromApiResult<Program>(`/programs/${requested}`);
    if (asked.state === "ok") return { state: "resolved", program: asked.data };
    if (asked.state === "unavailable") return { state: "unavailable" };
    return { state: "none" };
  }
  const all = await readFromApiResult<Program[]>("/programs");
  if (all.state !== "ok") return { state: "unavailable" };
  if (all.data.length === 0) return { state: "none" };
  if (all.data.length === 1) return { state: "resolved", program: all.data[0] };
  return { state: "choose", programs: all.data };
}
