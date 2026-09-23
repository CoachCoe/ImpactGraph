"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useSession } from "@/components/SessionProvider";
import { networkInfo } from "@/lib/network";

const MENU = "header-menu";

export function Header() {
  const { session, loading, signOut } = useSession();
  const router = useRouter();
  const pathname = usePathname();
  const network = networkInfo();
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);

  // A menu still standing open on the page you asked for reads as a tap that missed.
  useEffect(() => setOpen(false), [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      // Escape that abandons focus somewhere invisible is worse than no Escape.
      toggle.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

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
      <button
        ref={toggle}
        type="button"
        className="burger"
        aria-label="Menu"
        aria-expanded={open}
        aria-controls={MENU}
        onClick={() => setOpen((was) => !was)}
      >
        <span aria-hidden />
        <span aria-hidden />
        <span aria-hidden />
      </button>
      {/*
        One set of destinations serves both layouts. Above 800px `display: contents`
        dissolves this wrapper, so the desktop header lays out exactly as it did;
        below it, the same markup is the panel the button discloses.

        It is a disclosure and not a modal on purpose. A panel that sits in the
        header's own flow leaves tab order running from the button through the
        links and on into the page, which is what a reader expects and what no
        focus trap has to be written to imitate.
      */}
      <div className="headerMenu" id={MENU} data-open={open}>
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
      </div>
    </header>
  );
}
