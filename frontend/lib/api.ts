import type { ConfirmImportPayload, Course, DashboardData, ExtractionResult, ImportResult, Task, TaskPriority, TaskStatus, User } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function errorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (typeof item === "object" && item !== null && "msg" in item && typeof item.msg === "string") {
        return [item.msg];
      }
      return [];
    });
    if (messages.length) return messages.join("; ");
  }
  return "Something went wrong";
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = typeof window === "undefined" ? null : localStorage.getItem("flowmind_token");
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, errorMessage(body.detail));
  return body as T;
}

export const api = {
  register: (payload: { email: string; username: string; password: string }) =>
    request<{ access_token: string; user: User }>("/auth/register", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  login: (payload: { email: string; password: string }) =>
    request<{ access_token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  me: () => request<User>("/auth/me"),
  logout: () => request<{ message: string }>("/auth/logout", { method: "POST" }),
  extractContent: (text: string) =>
    request<ExtractionResult>("/ai/extract", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  confirmImport: (payload: ConfirmImportPayload) =>
    request<ImportResult>("/ai/import", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  dashboard: () => request<DashboardData>("/dashboard"),
  courses: () => request<Course[]>("/courses"),
  course: (id: number) => request<Course>(`/courses/${id}`),
  createCourse: (payload: Partial<Course>) =>
    request<Course>("/courses", { method: "POST", body: JSON.stringify(payload) }),
  updateCourse: (id: number, payload: Partial<Course>) =>
    request<Course>(`/courses/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteCourse: (id: number) => request<void>(`/courses/${id}`, { method: "DELETE" }),
  tasks: () => request<Task[]>("/tasks"),
  createTask: (payload: {
    title: string;
    description?: string;
    course_id?: number | null;
    deadline?: string | null;
    priority: TaskPriority;
  }) => request<Task>("/tasks", { method: "POST", body: JSON.stringify(payload) }),
  updateTask: (id: number, payload: Partial<Task>) =>
    request<Task>(`/tasks/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  updateTaskStatus: (id: number, status: TaskStatus) =>
    request<Task>(`/tasks/${id}/status`, { method: "PATCH", body: JSON.stringify({ status }) }),
  deleteTask: (id: number) => request<void>(`/tasks/${id}`, { method: "DELETE" }),
};
