"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Session } from "@/lib/session";

type SessionState = {
  session: Session | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<Session>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setSession(await api<Session>("/auth/me"));
    } catch (cause) {
      // 401 simply means nobody is signed in; anything else leaves us signed out too.
      if (!(cause instanceof ApiError) || cause.status !== 401) setSession(null);
      else setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback(async (email: string, password: string) => {
    const next = await api<Session>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    setSession(next);
    return next;
  }, []);

  const signOut = useCallback(async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    setSession(null);
  }, []);

  const value = useMemo(
    () => ({ session, loading, signIn, signOut, refresh }),
    [session, loading, signIn, signOut, refresh],
  );
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
