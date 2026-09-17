"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BookOpen, MoreHorizontal, Plus } from "lucide-react";

import { CourseForm, type CoursePayload } from "@/components/course-form";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { useToast } from "@/components/toast-provider";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Course } from "@/lib/types";

export default function CoursesPage() {
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState("");
  const { toast } = useToast();

  const load = useCallback(async () => {
    try {
      const courseList = await api.courses();
      setError("");
      setCourses(courseList);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to load courses."); }
  }, []);
  useEffect(() => {
    let cancelled = false;
    api.courses().then(
      (courseList) => {
        if (cancelled) return;
        setError("");
        setCourses(courseList);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Unable to load courses.");
      },
    );
    return () => { cancelled = true; };
  }, []);

  async function create(payload: CoursePayload) {
    const course = await api.createCourse(payload);
    setCourses((current) => [course, ...(current ?? [])]);
    setShowForm(false);
    toast("Course created.");
  }

  return (
    <>
      <PageHeader eyebrow="Course library" title="Courses" description="Keep each subject organized without extra layers." action={<Button variant="brand" onClick={() => setShowForm(true)}><Plus className="h-4 w-4" />Add course</Button>} />
      {error ? <ErrorState message={error} retry={() => void load()} /> : courses === null ? <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[1, 2, 3].map((item) => <Skeleton key={item} className="h-52" />)}</div> : courses.length === 0 ? <EmptyState icon={<BookOpen className="h-6 w-6" />} title="No courses yet" description="Add your first course to give tasks a clear context." action={<Button variant="brand" onClick={() => setShowForm(true)}><Plus className="h-4 w-4" />Add course</Button>} /> : <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{courses.map((course) => <Link key={course.id} href={`/courses/${course.id}`} className="group rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-soft"><div className="flex items-start justify-between"><span className="h-3 w-12 rounded-full" style={{ backgroundColor: course.color ?? "#5b5bd6" }} /><MoreHorizontal className="h-4 w-4 text-slate-300" /></div><h2 className="mt-7 text-lg font-bold text-ink group-hover:text-brand-700">{course.name}</h2><p className="mt-1 text-sm text-slate-500">{course.code || "No course code"}{course.teacher ? ` · ${course.teacher}` : ""}</p><p className="mt-5 line-clamp-2 min-h-10 text-sm leading-5 text-slate-500">{course.description || "Add a description to keep important context close."}</p></Link>)}</div>}
      {showForm && <CourseForm onCancel={() => setShowForm(false)} onSave={create} />}
    </>
  );
}
