"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useSession } from "@/components/SessionProvider";

const ROLE_HOME: Record<string, string> = {
  OPERATOR: "/operator",
  VERIFIER: "/verifier",
  ADMIN: "/admin",
  DONOR: "/",
};

function LoginForm() {
  const { signIn } = useSession();
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await signIn(email, password);
      router.push(next ?? ROLE_HOME[session.role] ?? "/");
      router.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Sign in failed.");
      setBusy(false);
    }
  };

  return (
    <form className="panel authPanel" onSubmit={submit}>
      <span className="eyebrow">SIGN IN</span>
      <h1>ImpactGraph</h1>
      <p className="subtle">
        Operators, verifiers and administrators sign in. Reading a claim, its provenance and
        its evidence needs no account.
      </p>
      <label className="field">
        Email
        <input
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </label>
      <label className="field">
        Password
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
      {error && (
        <div className="errorMessage" role="alert">
          <b>Sign in failed</b>
          <span>{error}</span>
        </div>
      )}
      <button className="button full" type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <p className="note">
        Local demo accounts are created by <code>make seed</code>, which prints their
        addresses and shared password. They are development credentials for a local
        database only.
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="authLayout">
      <Suspense fallback={<div className="panel authPanel">Loading…</div>}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
