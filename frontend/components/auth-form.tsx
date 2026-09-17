"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { Logo } from "@/components/logo";
import { useToast } from "@/components/toast-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const { setSession } = useAuth();
  const { toast } = useToast();
  const router = useRouter();
  const register = mode === "register";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    const email = String(data.get("email") ?? "").trim();
    const password = String(data.get("password") ?? "");
    const username = String(data.get("username") ?? "").trim();
    if (!email || !password || (register && !username)) return setError("Please complete every field.");
    if (register && password.length < 8) return setError("Password must be at least 8 characters.");

    setSubmitting(true);
    try {
      const response = register ? await api.register({ email, password, username }) : await api.login({ email, password });
      setSession(response.access_token, response.user);
      toast(register ? "Your workspace is ready." : "Welcome back.");
      router.push("/dashboard");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to connect to FlowMind.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid min-h-screen bg-white lg:grid-cols-2">
      <div className="flex min-h-screen flex-col px-5 py-6 sm:px-12 lg:px-16">
        <Logo />
        <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-12">
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand-600">{register ? "Start fresh" : "Welcome back"}</p>
          <h1 className="mt-3 text-3xl font-bold tracking-tight text-ink">{register ? "Create your workspace" : "Sign in to FlowMind"}</h1>
          <p className="mt-2 text-sm leading-6 text-slate-500">{register ? "A clear place for your courses and every next step." : "Your courses, tasks, and focus for today are waiting."}</p>
          <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
            {register && <div className="space-y-2"><Label htmlFor="username">Name</Label><Input id="username" name="username" placeholder="Alex Chen" autoComplete="name" /></div>}
            <div className="space-y-2"><Label htmlFor="email">Email</Label><Input id="email" name="email" type="email" placeholder="you@university.edu" autoComplete="email" /></div>
            <div className="space-y-2"><Label htmlFor="password">Password</Label><Input id="password" name="password" type="password" placeholder={register ? "At least 8 characters" : "Your password"} autoComplete={register ? "new-password" : "current-password"} /></div>
            {error && <p role="alert" className="rounded-xl bg-red-50 px-3.5 py-3 text-sm text-red-700">{error}</p>}
            <Button className="w-full" variant="brand" disabled={submitting}>{submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <>{register ? "Create account" : "Sign in"}<ArrowRight className="h-4 w-4" /></>}</Button>
          </form>
          <p className="mt-6 text-center text-sm text-slate-500">{register ? "Already have an account?" : "New to FlowMind?"} <Link className="font-semibold text-brand-700 hover:underline" href={register ? "/login" : "/register"}>{register ? "Sign in" : "Create an account"}</Link></p>
        </div>
      </div>
      <div className="relative hidden overflow-hidden bg-ink p-12 text-white lg:flex lg:flex-col lg:justify-end">
        <div className="absolute left-16 top-20 h-64 w-64 rounded-full bg-brand-500/30 blur-3xl" />
        <blockquote className="relative max-w-lg text-3xl font-semibold leading-tight tracking-tight">“A plan feels useful when it makes the next action obvious.”</blockquote>
        <p className="relative mt-5 text-sm text-slate-300">FlowMind keeps the signal and removes the study-planning noise.</p>
      </div>
    </div>
  );
}

