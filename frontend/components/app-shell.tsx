"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BookOpen, CheckSquare2, LayoutDashboard, LogOut, Menu, Settings, X } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const navigation = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/courses", label: "Courses", icon: BookOpen },
  { href: "/tasks", label: "Tasks", icon: CheckSquare2 },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, loading, signOut } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  if (loading || !user) {
    return (
      <div className="mx-auto flex min-h-screen max-w-7xl gap-8 p-6">
        <Skeleton className="hidden w-64 sm:block" />
        <div className="flex-1 space-y-4 pt-12">
          <Skeleton className="h-10 w-56" />
          <Skeleton className="h-52 w-full" />
        </div>
      </div>
    );
  }

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-3 py-4">
        <Logo />
        <button className="sm:hidden" onClick={() => setOpen(false)} aria-label="Close navigation"><X className="h-5 w-5" /></button>
      </div>
      <nav className="mt-6 space-y-1">
        {navigation.map((item) => {
          const active = pathname === item.href || (item.href === "/courses" && pathname.startsWith("/courses/"));
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={() => setOpen(false)}
              className={cn(
                "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors",
                active ? "bg-ink text-white" : "text-slate-600 hover:bg-slate-100 hover:text-ink",
              )}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto border-t border-slate-200 pt-4">
        <div className="mb-3 px-3">
          <p className="truncate text-sm font-semibold text-ink">{user.username}</p>
          <p className="truncate text-xs text-slate-500">{user.email}</p>
        </div>
        <Button variant="ghost" className="w-full justify-start" onClick={() => void signOut()}>
          <LogOut className="h-4 w-4" /> Sign out
        </Button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-canvas">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-slate-200 bg-white p-4 sm:block">{sidebar}</aside>
      {open && (
        <>
          <button className="fixed inset-0 z-40 bg-ink/30 sm:hidden" aria-label="Close navigation" onClick={() => setOpen(false)} />
          <aside className="fixed inset-y-0 left-0 z-50 w-72 bg-white p-4 shadow-soft sm:hidden">{sidebar}</aside>
        </>
      )}
      <header className="sticky top-0 z-20 flex h-16 items-center border-b border-slate-200 bg-white/90 px-5 backdrop-blur sm:hidden">
        <button onClick={() => setOpen(true)} aria-label="Open navigation"><Menu className="h-5 w-5" /></button>
        <div className="ml-4"><Logo /></div>
      </header>
      <main className="px-5 py-8 sm:ml-64 sm:px-8 lg:px-12 lg:py-10">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}

