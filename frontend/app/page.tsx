import Link from "next/link";
import { ArrowRight, BookOpen, CheckCircle2, LayoutDashboard } from "lucide-react";

import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";

const features = [
  { icon: LayoutDashboard, title: "See the day clearly", description: "Today, upcoming deadlines, and recent work in one calm dashboard." },
  { icon: BookOpen, title: "Keep courses organized", description: "Give every course a clear home with the details you actually need." },
  { icon: CheckCircle2, title: "Move work forward", description: "Capture, prioritize, and complete tasks without project-management overhead." },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-white">
      <header className="mx-auto flex h-20 max-w-6xl items-center justify-between px-5">
        <Logo />
        <div className="flex items-center gap-2">
          <Button asChild variant="ghost"><Link href="/login">Sign in</Link></Button>
          <Button asChild variant="brand"><Link href="/register">Get started</Link></Button>
        </div>
      </header>
      <main>
        <section className="mx-auto grid max-w-6xl items-center gap-12 px-5 py-20 lg:grid-cols-[1.05fr_.95fr] lg:py-28">
          <div>
            <div className="mb-6 inline-flex rounded-full border border-brand-100 bg-brand-50 px-3 py-1.5 text-xs font-bold uppercase tracking-[0.16em] text-brand-700">Built for focused study</div>
            <h1 className="max-w-3xl text-5xl font-bold leading-[1.05] tracking-[-0.045em] text-ink sm:text-6xl">Your coursework, finally in flow.</h1>
            <p className="mt-6 max-w-xl text-lg leading-8 text-slate-600">FlowMind gives university students one clean workspace for courses, tasks, and the work that matters today.</p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Button asChild size="lg" variant="brand"><Link href="/register">Create your workspace <ArrowRight className="h-4 w-4" /></Link></Button>
              <Button asChild size="lg" variant="outline"><Link href="/login">I already have an account</Link></Button>
            </div>
          </div>
          <div className="rounded-[2rem] border border-slate-200 bg-canvas p-4 shadow-soft sm:p-7">
            <div className="rounded-2xl border border-slate-200 bg-white p-5">
              <div className="flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-wider text-brand-600">Monday focus</p><h2 className="mt-1 text-xl font-bold">Good morning, Alex</h2></div><span className="rounded-xl bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-700">3 on track</span></div>
              <div className="mt-6 grid grid-cols-2 gap-3">
                <div className="rounded-xl bg-ink p-4 text-white"><p className="text-xs text-slate-300">Courses</p><p className="mt-2 text-3xl font-bold">4</p></div>
                <div className="rounded-xl bg-brand-50 p-4"><p className="text-xs text-brand-700">Completed</p><p className="mt-2 text-3xl font-bold text-brand-700">12</p></div>
              </div>
              <div className="mt-5 space-y-2">
                {["Review algorithms notes", "Submit design critique", "Read chapter six"].map((task, index) => <div key={task} className="flex items-center gap-3 rounded-xl border border-slate-100 p-3"><span className={`h-2.5 w-2.5 rounded-full ${index === 0 ? "bg-red-500" : index === 1 ? "bg-amber-500" : "bg-slate-300"}`} /><span className="text-sm font-medium">{task}</span></div>)}
              </div>
            </div>
          </div>
        </section>
        <section className="border-y border-slate-200 bg-canvas">
          <div className="mx-auto grid max-w-6xl gap-5 px-5 py-16 md:grid-cols-3">
            {features.map((feature) => <div key={feature.title} className="rounded-2xl border border-slate-200 bg-white p-6"><feature.icon className="h-6 w-6 text-brand-600" /><h2 className="mt-5 font-bold text-ink">{feature.title}</h2><p className="mt-2 text-sm leading-6 text-slate-500">{feature.description}</p></div>)}
          </div>
        </section>
      </main>
      <footer className="mx-auto flex max-w-6xl items-center justify-between px-5 py-8 text-sm text-slate-500"><Logo /><span>FlowMind AI Cloud · Foundation</span></footer>
    </div>
  );
}

