"use client";

import { LogOut, ShieldCheck, UserRound } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function SettingsPage() {
  const { user, signOut } = useAuth();

  return (
    <>
      <PageHeader eyebrow="Account" title="Settings" description="Your FlowMind account and session details." />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader><div className="mb-3 grid h-10 w-10 place-items-center rounded-xl bg-brand-50 text-brand-700"><UserRound className="h-5 w-5" /></div><CardTitle>Profile</CardTitle></CardHeader>
          <CardContent className="space-y-4 pt-3"><div><p className="text-xs font-bold uppercase tracking-wider text-slate-400">Name</p><p className="mt-1 text-sm font-medium text-ink">{user?.username}</p></div><div><p className="text-xs font-bold uppercase tracking-wider text-slate-400">Email</p><p className="mt-1 text-sm font-medium text-ink">{user?.email}</p></div></CardContent>
        </Card>
        <Card>
          <CardHeader><div className="mb-3 grid h-10 w-10 place-items-center rounded-xl bg-emerald-50 text-emerald-700"><ShieldCheck className="h-5 w-5" /></div><CardTitle>Session</CardTitle></CardHeader>
          <CardContent className="pt-3"><p className="text-sm leading-6 text-slate-500">You are signed in with a protected JWT session. Sign out when using a shared device.</p><Button className="mt-5" variant="outline" onClick={() => void signOut()}><LogOut className="h-4 w-4" />Sign out</Button></CardContent>
        </Card>
      </div>
    </>
  );
}

