"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BookOpen, CheckCircle2, CheckSquare2, Clock3, Plus } from "lucide-react";

import { useAuth } from "@/components/auth-provider";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { TaskRow } from "@/components/task-row";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Course, DashboardData } from "@/lib/types";

export default function DashboardPage() {
  const { user } = useAuth();
  const [data, setData] = useState<DashboardData | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [dashboard, courseList] = await Promise.all([api.dashboard(), api.courses()]);
      setError("");
      setData(dashboard);
      setCourses(courseList);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load dashboard.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.dashboard(), api.courses()]).then(
      ([dashboard, courseList]) => {
        if (cancelled) return;
        setError("");
        setData(dashboard);
        setCourses(courseList);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Unable to load dashboard.");
      },
    );
    return () => { cancelled = true; };
  }, []);

  if (error) return <ErrorState message={error} retry={() => void load()} />;
  if (!data) return <DashboardLoading />;

  const courseMap = new Map(courses.map((course) => [course.id, course]));
  const statCards = [
    { label: "Today", value: data.today_tasks.length, icon: Clock3, tone: "bg-brand-50 text-brand-700" },
    { label: "Courses", value: data.course_count, icon: BookOpen, tone: "bg-sky-50 text-sky-700" },
    { label: "Completed", value: data.completed_task_count, icon: CheckCircle2, tone: "bg-emerald-50 text-emerald-700" },
  ];

  return (
    <>
      <PageHeader eyebrow="Your workspace" title={`Good ${greeting()}, ${user?.username?.split(" ")[0] ?? "there"}`} description="Here is what deserves your attention today." action={<Button asChild variant="brand"><Link href="/tasks"><Plus className="h-4 w-4" />Add task</Link></Button>} />
      <div className="grid gap-4 sm:grid-cols-3">
        {statCards.map((stat) => <Card key={stat.label}><CardContent className="flex items-center gap-4"><div className={`rounded-xl p-3 ${stat.tone}`}><stat.icon className="h-5 w-5" /></div><div><p className="text-2xl font-bold text-ink">{stat.value}</p><p className="text-sm text-slate-500">{stat.label}</p></div></CardContent></Card>)}
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-[1.35fr_.65fr]">
        <section>
          <div className="mb-3 flex items-center justify-between"><h2 className="font-bold text-ink">Today&apos;s tasks</h2><Link className="text-sm font-semibold text-brand-700" href="/tasks">View all</Link></div>
          {data.today_tasks.length ? <div className="space-y-2">{data.today_tasks.map((task) => <TaskRow key={task.id} task={task} course={task.course_id ? courseMap.get(task.course_id) : undefined} compact />)}</div> : <EmptyState icon={<CheckSquare2 className="h-6 w-6" />} title="A clear day" description="Nothing is due today. Add a task when you know your next step." action={<Button asChild size="sm" variant="outline"><Link href="/tasks">Open tasks</Link></Button>} />}
        </section>
        <section>
          <h2 className="mb-3 font-bold text-ink">Upcoming</h2>
          <Card><CardContent className="space-y-1 p-2">{data.upcoming_tasks.length ? data.upcoming_tasks.map((task) => <div key={task.id} className="rounded-xl p-3 hover:bg-slate-50"><p className="truncate text-sm font-semibold">{task.title}</p><p className="mt-1 text-xs text-slate-500">{task.deadline ? new Date(task.deadline).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) : "No deadline"}</p></div>) : <p className="p-5 text-center text-sm text-slate-500">No upcoming deadlines.</p>}</CardContent></Card>
          <h2 className="mb-3 mt-6 font-bold text-ink">Recent tasks</h2>
          <Card><CardContent className="space-y-1 p-2">{data.recent_tasks.length ? data.recent_tasks.map((task) => <div key={task.id} className="flex items-center gap-2 rounded-xl p-3"><span className={`h-2 w-2 rounded-full ${task.status === "COMPLETED" ? "bg-emerald-500" : "bg-brand-500"}`} /><p className="truncate text-sm font-medium">{task.title}</p></div>) : <p className="p-5 text-center text-sm text-slate-500">Your recent work will appear here.</p>}</CardContent></Card>
        </section>
      </div>
    </>
  );
}

function greeting() {
  const hour = new Date().getHours();
  return hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
}

function DashboardLoading() {
  return <div className="space-y-6"><div><Skeleton className="h-8 w-72" /><Skeleton className="mt-3 h-4 w-96 max-w-full" /></div><div className="grid gap-4 sm:grid-cols-3">{[1, 2, 3].map((item) => <Skeleton key={item} className="h-28" />)}</div><div className="grid gap-6 lg:grid-cols-2"><Skeleton className="h-80" /><Skeleton className="h-80" /></div></div>;
}
