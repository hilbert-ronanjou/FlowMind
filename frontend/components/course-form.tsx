"use client";

import { useState, type FormEvent } from "react";
import { Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { Course } from "@/lib/types";

export type CoursePayload = Pick<Course, "name" | "code" | "teacher" | "description" | "color">;

export function CourseForm({ initial, onCancel, onSave }: { initial?: Course; onCancel: () => void; onSave: (payload: CoursePayload) => Promise<void> }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name") ?? "").trim();
    if (!name) return setError("Course name is required.");
    setSaving(true);
    try {
      await onSave({
        name,
        code: String(data.get("code") ?? "").trim() || null,
        teacher: String(data.get("teacher") ?? "").trim() || null,
        description: String(data.get("description") ?? "").trim() || null,
        color: String(data.get("color") ?? "") || "#5b5bd6",
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to save course.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4" role="dialog" aria-modal="true" aria-labelledby="course-form-title">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-soft">
        <div className="flex items-center justify-between">
          <div><p className="text-xs font-bold uppercase tracking-wider text-brand-600">Course details</p><h2 id="course-form-title" className="mt-1 text-xl font-bold text-ink">{initial ? "Edit course" : "Add a course"}</h2></div>
          <button onClick={onCancel} aria-label="Close"><X className="h-5 w-5 text-slate-400" /></button>
        </div>
        <form className="mt-6 space-y-4" onSubmit={submit}>
          <div className="space-y-2"><Label htmlFor="course-name">Course name *</Label><Input id="course-name" name="name" defaultValue={initial?.name} placeholder="Software Engineering" autoFocus /></div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2"><Label htmlFor="course-code">Code</Label><Input id="course-code" name="code" defaultValue={initial?.code ?? ""} placeholder="SE101" /></div>
            <div className="space-y-2"><Label htmlFor="course-teacher">Teacher</Label><Input id="course-teacher" name="teacher" defaultValue={initial?.teacher ?? ""} placeholder="Dr. Lin" /></div>
          </div>
          <div className="space-y-2"><Label htmlFor="course-description">Description</Label><Textarea id="course-description" name="description" defaultValue={initial?.description ?? ""} placeholder="What this course is about…" /></div>
          <div className="space-y-2"><Label htmlFor="course-color">Course color</Label><Input id="course-color" name="color" type="color" defaultValue={initial?.color ?? "#5b5bd6"} className="w-20 p-1.5" /></div>
          {error && <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
          <div className="flex justify-end gap-2 pt-2"><Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button><Button variant="brand" disabled={saving}>{saving && <Loader2 className="h-4 w-4 animate-spin" />}{initial ? "Save changes" : "Create course"}</Button></div>
        </form>
      </div>
    </div>
  );
}

