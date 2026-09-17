"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";

import { api } from "@/lib/api";
import type { User } from "@/lib/types";

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  setSession: (token: string, user: User) => void;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);
const publicPaths = ["/", "/login", "/register"];

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    const token = localStorage.getItem("flowmind_token");
    if (!token) {
      if (!publicPaths.includes(pathname)) router.replace("/login");
      queueMicrotask(() => setLoading(false));
      return;
    }
    api.me()
      .then(setUser)
      .catch(() => {
        localStorage.removeItem("flowmind_token");
        if (!publicPaths.includes(pathname)) router.replace("/login");
      })
      .finally(() => setLoading(false));
  }, [pathname, router]);

  function setSession(token: string, nextUser: User) {
    localStorage.setItem("flowmind_token", token);
    setUser(nextUser);
  }

  async function signOut() {
    try {
      await api.logout();
    } finally {
      localStorage.removeItem("flowmind_token");
      setUser(null);
      router.push("/login");
    }
  }

  return <AuthContext.Provider value={{ user, loading, setSession, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
