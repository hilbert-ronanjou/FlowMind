"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckSquare2, Plus, Search } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { TaskForm, type TaskPayload } from "@/components/task-form";
import { TaskRow } from "@/components/task-row";
import { useToast } from "@/components/toast-provider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Course, Task, TaskStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const filters: { label: string; value: "ALL" | TaskStatus }[] = [
  { label: "All", value: "ALL" },
  { label: "To do", value: "TODO" },
  { label: "In progress", value: "IN_PROGRESS" },
  { label: "Completed", value: "COMPLETED" },
];

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [courses, setCourses] = useState<Course[]>([]);
  const [filter, setFilter] = useState<"ALL" | TaskStatus>("ALL");
  const [query, setQuery] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Task | undefined>();
  const [error, setError] = useState("");
  const { toast } = useToast();

  const load = useCallback(async () => {
    try {
      const [taskList, courseList] = await Promise.all([api.tasks(), api.courses()]);
      setError("");
      setTasks(taskList);
      setCourses(courseList);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Unable to load tasks."); }
  }, []);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.tasks(), api.courses()]).then(
      ([taskList, courseList]) => {
        if (cancelled) return;
        setError("");
        setTasks(taskList);
        setCourses(courseList);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Unable to load tasks.");
      },
    );
    return () => { cancelled = true; };
  }, []);

  const visible = useMemo(() => (tasks ?? []).filter((task) => {
    const matchesFilter = filter === "ALL" || task.status === filter;
    const matchesQuery = task.title.toLowerCase().includes(query.toLowerCase());
    return matchesFilter && matchesQuery;
  }), [tasks, filter, query]);
  const courseMap = new Map(courses.map((course) => [course.id, course]));

  async function save(payload: TaskPayload) {
    if (editing) {
      const updated = await api.updateTask(editing.id, payload);
      setTasks((current) => (current ?? []).map((task) => task.id === updated.id ? updated : task));
      toast("Task updated.");
    } else {
      const created = await api.createTask(payload);
      setTasks((current) => [created, ...(current ?? [])]);
      toast("Task created.");
    }
    setShowForm(false);
    setEditing(undefined);
  }

  async function updateStatus(task: Task, status: TaskStatus) {
    try {
      const updated = await api.updateTaskStatus(task.id, status);
      setTasks((current) => (current ?? []).map((item) => item.id === updated.id ? updated : item));
      const messages: Record<TaskStatus, string> = {
        TODO: "Task moved to to do.",
        IN_PROGRESS: "Task in progress.",
        COMPLETED: "Task completed.",
      };
      toast(messages[status]);
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Unable to update task.", "error"); }
  }

  async function remove(task: Task) {
    if (!window.confirm(`Delete “${task.title}”?`)) return;
    try {
      await api.deleteTask(task.id);
      setTasks((current) => (current ?? []).filter((item) => item.id !== task.id));
      toast("Task deleted.");
    } catch (caught) { toast(caught instanceof Error ? caught.message : "Unable to delete task.", "error"); }
  }

  function openCreate() { setEditing(undefined); setShowForm(true); }
  function openEdit(task: Task) { setEditing(task); setShowForm(true); }

  return (
    <>
      <PageHeader eyebrow="Your work" title="Tasks" description="Capture the next step, then keep moving." action={<Button variant="brand" onClick={openCreate}><Plus className="h-4 w-4" />Add task</Button>} />
      <div className="mb-5 flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-1 overflow-x-auto">{filters.map((item) => <button key={item.value} className={cn("whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium", filter === item.value ? "bg-ink text-white" : "text-slate-500 hover:bg-slate-100")} onClick={() => setFilter(item.value)}>{item.label}</button>)}</div>
        <div className="relative sm:w-64"><Search className="absolute left-3 top-3.5 h-4 w-4 text-slate-400" /><Input className="pl-9" placeholder="Search tasks" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
      </div>
      {error ? <ErrorState message={error} retry={() => void load()} /> : tasks === null ? <div className="space-y-2">{[1, 2, 3, 4].map((item) => <Skeleton key={item} className="h-20" />)}</div> : visible.length ? <div className="space-y-2">{visible.map((task) => <TaskRow key={task.id} task={task} course={task.course_id ? courseMap.get(task.course_id) : undefined} onStatus={(status) => void updateStatus(task, status)} onEdit={() => openEdit(task)} onDelete={() => void remove(task)} />)}</div> : <EmptyState icon={<CheckSquare2 className="h-6 w-6" />} title={tasks.length ? "No matching tasks" : "No tasks yet"} description={tasks.length ? "Try another status or search phrase." : "Add your first task and make the next action obvious."} action={!tasks.length ? <Button variant="brand" onClick={openCreate}><Plus className="h-4 w-4" />Add task</Button> : undefined} />}
      {showForm && <TaskForm courses={courses} initial={editing} onCancel={() => { setShowForm(false); setEditing(undefined); }} onSave={save} />}
    </>
  );
}
