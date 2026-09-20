"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "@/components/SessionProvider";
import { networkInfo } from "@/lib/network";

export function Header() {
  const { session, loading, signOut } = useSession();
  const router = useRouter();
  const network = networkInfo();

  const out = async () => {
    await signOut();
    router.push("/");
    router.refresh();
  };

  return (
    <header className="header">
      <Link className="brand" href="/">
        <span className="brandMark" aria-hidden>
          I
        </span>
        <span>ImpactGraph</span>
      </Link>
      <nav aria-label="Primary navigation">
        <Link href="/">Donor</Link>
        <Link href="/financial">Money trail</Link>
        <Link href="/about">How it works</Link>
        {session?.role === "OPERATOR" && <Link href="/operator">Operator</Link>}
        {session?.role === "VERIFIER" && <Link href="/verifier">Verifier</Link>}
        {session?.role === "ADMIN" && <Link href="/admin">Admin</Link>}
      </nav>
      <div className="headerRight">
        <div className="network" title={network.chainId ? `Chain ${network.chainId}` : undefined}>
          <span aria-hidden /> {network.name}
        </div>
        {!loading &&
          (session ? (
            <div className="account">
              <span>
                {session.displayName}
                <small>{session.organization.name}</small>
              </span>
              <button className="secondary" onClick={out}>
                Sign out
              </button>
            </div>
          ) : (
            <Link className="secondary" href="/login">
              Sign in
            </Link>
          ))}
      </div>
    </header>
  );
}
