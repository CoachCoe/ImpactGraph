"use client";

import Link from "next/link";
import { useSession } from "@/components/SessionProvider";
import type { Session } from "@/lib/session";

/** Gates a workspace on the signed-in role. The API enforces this too; this is only UX. */
export function RequireRole({
  role,
  children,
}: {
  role: Session["role"];
  children: (session: Session) => React.ReactNode;
}) {
  const { session, loading } = useSession();
  if (loading) return <div className="workspace panel">Checking your session…</div>;
  if (!session) {
    return (
      <div className="workspace panel">
        <h2>Sign in required</h2>
        <p className="subtle">This workspace needs a {role.toLowerCase()} account.</p>
        <Link className="button" href={`/login?next=/${role.toLowerCase()}`}>
          Sign in
        </Link>
      </div>
    );
  }
  if (session.role !== role) {
    return (
      <div className="workspace panel">
        <h2>Not available to your account</h2>
        <p className="subtle">
          You are signed in as {session.displayName} ({session.role}). This workspace is for
          the {role.toLowerCase()} role.
        </p>
      </div>
    );
  }
  return <>{children(session)}</>;
}
