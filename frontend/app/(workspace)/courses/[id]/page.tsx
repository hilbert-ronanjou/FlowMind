"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, BookOpen, CalendarDays, Pencil, Plus, Trash2, UserRound } from "lucide-react";

import { CourseForm, type CoursePayload } from "@/components/course-form";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { TaskRow } from "@/components/task-row";
import { useToast } from "@/components/toast-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Course, Task } from "@/lib/types";

export default function CourseDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const courseId = Number(params.id);
  const [course, setCourse] = useState<Course | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const { toast } = useToast();

  const load = useCallback(async () => {
    try {
      const [courseData, allTasks] = await Promise.all([api.course(courseId), api.tasks()]);
      setError("");
      setCourse(courseData);
      setTasks(allTasks.filter((task) => task.course_id === courseId));
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to load course."); }
  }, [courseId]);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.course(courseId), api.tasks()]).then(
      ([courseData, allTasks]) => {
        if (cancelled) return;
        setError("");
        setCourse(courseData);
        setTasks(allTasks.filter((task) => task.course_id === courseId));
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Unable to load course.");
      },
    );
    return () => { cancelled = true; };
  }, [courseId]);

  async function update(payload: CoursePayload) {
    const saved = await api.updateCourse(courseId, payload);
    setCourse(saved);
    setEditing(false);
    toast("Course updated.");
  }

  async function remove() {
    if (!window.confirm("Delete this course? Its tasks will remain without a course.")) return;
    await api.deleteCourse(courseId);
    toast("Course deleted.");
    router.push("/courses");
  }

  if (error) return <ErrorState message={error} retry={() => void load()} />;
  if (!course) return <div className="space-y-5"><Skeleton className="h-5 w-28" /><Skeleton className="h-44" /><Skeleton className="h-72" /></div>;

  return (
    <>
      <Link href="/courses" className="mb-5 inline-flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-ink"><ArrowLeft className="h-4 w-4" />Back to courses</Link>
      <Card className="overflow-hidden">
        <div className="h-2" style={{ backgroundColor: course.color ?? "#5b5bd6" }} />
        <CardContent className="p-6 sm:p-8">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
            <div><p className="text-xs font-bold uppercase tracking-[0.16em] text-brand-600">{course.code || "Course"}</p><h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">{course.name}</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-slate-500">{course.description || "No description yet."}</p></div>
            <div className="flex gap-2"><Button variant="outline" onClick={() => setEditing(true)}><Pencil className="h-4 w-4" />Edit</Button><Button variant="danger" size="icon" onClick={() => void remove()} aria-label="Delete course"><Trash2 className="h-4 w-4" /></Button></div>
          </div>
          <div className="mt-7 flex flex-wrap gap-5 border-t border-slate-100 pt-5 text-sm text-slate-500"><span className="flex items-center gap-2"><UserRound className="h-4 w-4" />{course.teacher || "No teacher listed"}</span><span className="flex items-center gap-2"><CalendarDays className="h-4 w-4" />Added {new Date(course.created_at).toLocaleDateString()}</span></div>
        </CardContent>
      </Card>
      <section className="mt-8">
        <div className="mb-4 flex items-center justify-between"><div><h2 className="text-lg font-bold text-ink">Course tasks</h2><p className="mt-1 text-sm text-slate-500">{tasks.length} {tasks.length === 1 ? "task" : "tasks"} linked to this course</p></div><Button asChild size="sm" variant="brand"><Link href="/tasks"><Plus className="h-4 w-4" />Manage tasks</Link></Button></div>
        {tasks.length ? <div className="space-y-2">{tasks.map((task) => <TaskRow key={task.id} task={task} course={course} compact />)}</div> : <EmptyState icon={<BookOpen className="h-6 w-6" />} title="No linked tasks" description="Create a task and choose this course to see it here." action={<Button asChild size="sm" variant="outline"><Link href="/tasks">Open tasks</Link></Button>} />}
      </section>
      {editing && <CourseForm initial={course} onCancel={() => setEditing(false)} onSave={update} />}
    </>
  );
}
