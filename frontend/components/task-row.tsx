"use client";

import { Calendar, MoreHorizontal } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { Course, Task, TaskStatus } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

const statusLabels: Record<TaskStatus, string> = { TODO: "To do", IN_PROGRESS: "In progress", COMPLETED: "Completed" };
const nextStatus: Record<TaskStatus, TaskStatus> = { TODO: "IN_PROGRESS", IN_PROGRESS: "COMPLETED", COMPLETED: "TODO" };

export function TaskRow({ task, course, onStatus, onEdit, onDelete, compact = false }: { task: Task; course?: Course; onStatus?: (status: TaskStatus) => void; onEdit?: () => void; onDelete?: () => void; compact?: boolean }) {
  return (
    <div className={cn("flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center", task.status === "COMPLETED" && "opacity-65")}>
      <button
        aria-label={`Change task status from ${statusLabels[task.status]} to ${statusLabels[nextStatus[task.status]]}`}
        className={cn(
          "h-5 w-5 shrink-0 rounded-full border-2",
          task.status === "COMPLETED" && "border-emerald-500 bg-emerald-500 shadow-[inset_0_0_0_3px_white]",
          task.status === "IN_PROGRESS" && "border-amber-500 bg-amber-400 shadow-[inset_0_0_0_3px_white]",
          task.status === "TODO" && "border-slate-300",
        )}
        onClick={() => onStatus?.(nextStatus[task.status])}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><p className={cn("truncate text-sm font-semibold text-ink", task.status === "COMPLETED" && "line-through")}>{task.title}</p><Badge className={cn(task.priority === "HIGH" ? "bg-red-50 text-red-700" : task.priority === "LOW" ? "bg-slate-100" : "bg-amber-50 text-amber-700")}>{task.priority.toLowerCase()}</Badge></div>
        <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">{course && <span style={{ color: course.color ?? undefined }}>{course.name}</span>}<span className="flex items-center gap-1"><Calendar className="h-3 w-3" />{formatDate(task.deadline)}</span>{!compact && <span>{statusLabels[task.status]}</span>}</div>
      </div>
      {!compact && (onEdit || onDelete) && <div className="flex items-center gap-2 self-end sm:self-auto"><button className="rounded-lg px-2 py-1 text-xs font-medium text-slate-500 hover:bg-slate-100" onClick={onEdit}>Edit</button><button className="rounded-lg px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50" onClick={onDelete}>Delete</button><MoreHorizontal className="hidden h-4 w-4 text-slate-300" /></div>}
    </div>
  );
}
