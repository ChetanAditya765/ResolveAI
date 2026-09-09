"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import type { Session, User } from "@/types/api";

interface IdentityContext {
  session?: Session;
  users: User[];
  loading: boolean;
  error?: string;
  retry: () => void;
  switchUser: (id: string) => Promise<void>;
}
const Context = createContext<IdentityContext | null>(null);
const STORAGE_KEY = "resolveai.demo.session";
function saveSession(value?: Session) {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* Memory-only sessions still work when browser storage is disabled. */
  }
}
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session>();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [version, setVersion] = useState(0);
  function retry() {
    setLoading(true);
    setError(undefined);
    setVersion((value) => value + 1);
  }
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function initialize() {
      try {
        const identities = await api<User[]>("/demo/users", {
          signal: controller.signal,
        });
        if (!active) return;
        setUsers(identities);
        let stored: Session | undefined;
        try {
          stored =
            JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "null") ??
            undefined;
        } catch {
          saveSession();
        }
        if (stored?.access_token) {
          const user = await api<User>(
            "/session",
            { signal: controller.signal },
            stored.access_token,
          );
          if (active) setSession({ ...stored, user });
        } else {
          const initial =
            identities.find((user) => user.employee_id === "EMP001") ??
            identities[0];
          if (!initial) return;
          const created = await api<Session>("/demo/session", {
            method: "POST",
            body: JSON.stringify({ user_id: initial.id }),
            signal: controller.signal,
          });
          if (active) {
            saveSession(created);
            setSession(created);
          }
        }
      } catch (failure) {
        if (active) {
          saveSession();
          setError(
            failure instanceof ApiError && failure.status === 401
              ? "Your demo session expired. Select an identity to continue."
              : errorMessage(failure),
          );
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void initialize();
    return () => {
      active = false;
      controller.abort();
    };
  }, [version]);
  async function switchUser(id: string) {
    setLoading(true);
    setSession(undefined);
    setError(undefined);
    saveSession();
    try {
      const created = await api<Session>("/demo/session", {
        method: "POST",
        body: JSON.stringify({ user_id: id }),
      });
      saveSession(created);
      setSession(created);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }
  return (
    <Context.Provider
      value={{ session, users, loading, error, switchUser, retry }}
    >
      {children}
    </Context.Provider>
  );
}
export function useSession() {
  const value = useContext(Context);
  if (!value) throw new Error("SessionProvider is required.");
  return value;
}
