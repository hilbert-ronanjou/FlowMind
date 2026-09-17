"use client";

import { useState, type FormEvent } from "react";
import { Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { Course, Task, TaskPriority } from "@/lib/types";

export type TaskPayload = { title: string; description?: string; course_id?: number | null; deadline?: string | null; priority: TaskPriority };

function toLocalInput(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16);
}

export function TaskForm({ courses, initial, onCancel, onSave }: { courses: Course[]; initial?: Task; onCancel: () => void; onSave: (payload: TaskPayload) => Promise<void> }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    const title = String(data.get("title") ?? "").trim();
    if (!title) return setError("Task title is required.");
    const deadline = String(data.get("deadline") ?? "");
    setSaving(true);
    try {
      await onSave({
        title,
        description: String(data.get("description") ?? "").trim() || undefined,
        course_id: data.get("course_id") ? Number(data.get("course_id")) : null,
        deadline: deadline ? new Date(deadline).toISOString() : null,
        priority: String(data.get("priority")) as TaskPriority,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to save task.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/40 p-4" role="dialog" aria-modal="true" aria-labelledby="task-form-title">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-soft">
        <div className="flex items-center justify-between"><div><p className="text-xs font-bold uppercase tracking-wider text-brand-600">Next action</p><h2 id="task-form-title" className="mt-1 text-xl font-bold text-ink">{initial ? "Edit task" : "Add a task"}</h2></div><button onClick={onCancel} aria-label="Close"><X className="h-5 w-5 text-slate-400" /></button></div>
        <form className="mt-6 space-y-4" onSubmit={submit}>
          <div className="space-y-2"><Label htmlFor="task-title">Task title *</Label><Input id="task-title" name="title" defaultValue={initial?.title} placeholder="Finish the problem set" autoFocus /></div>
          <div className="space-y-2"><Label htmlFor="task-description">Description</Label><Textarea id="task-description" name="description" defaultValue={initial?.description ?? ""} placeholder="Helpful details…" /></div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2"><Label htmlFor="task-course">Course</Label><select id="task-course" name="course_id" defaultValue={initial?.course_id ?? ""} className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-brand-500"><option value="">No course</option>{courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}</select></div>
            <div className="space-y-2"><Label htmlFor="task-priority">Priority</Label><select id="task-priority" name="priority" defaultValue={initial?.priority ?? "MEDIUM"} className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-brand-500"><option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option></select></div>
          </div>
          <div className="space-y-2"><Label htmlFor="task-deadline">Deadline</Label><Input id="task-deadline" name="deadline" type="datetime-local" defaultValue={toLocalInput(initial?.deadline)} /></div>
          {error && <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700">{error}</p>}
          <div className="flex justify-end gap-2 pt-2"><Button type="button" variant="ghost" onClick={onCancel}>Cancel</Button><Button variant="brand" disabled={saving}>{saving && <Loader2 className="h-4 w-4 animate-spin" />}{initial ? "Save changes" : "Create task"}</Button></div>
        </form>
      </div>
    </div>
  );
}

