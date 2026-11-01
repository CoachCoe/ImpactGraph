"use client";
import Link from "next/link";
import { observe } from "@/lib/analytics";

// The web's form of the app's first-launch choice (PRD D-1). Donor and partner
// accounts are separate identities, so the choice is which one to start.
export function RoleChooser() {
  return (
    <section className="panel" aria-labelledby="role-chooser">
      <h2 id="role-chooser">How will you take part?</h2>
      <div className="actions">
        <Link className="button" href="/donate" onClick={() => observe("role_selected")}>
          I’m a donor
        </Link>
        <Link className="secondary" href="/partners" onClick={() => observe("role_selected")}>
          I’m a partner
        </Link>
      </div>
    </section>
  );
}
