import type {
  ConfirmImportPayload,
  Course,
  CourseDocument,
  DashboardData,
  ExtractionResult,
  ImportResult,
  KnowledgeAnswer,
  Task,
  TaskPriority,
  TaskStatus,
  User,
} from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public details?: Record<string, unknown>,
  ) {
    super(message);
  }
}

function errorDetails(detail: unknown): {
  message: string;
  code?: string;
  details?: Record<string, unknown>;
} {
  if (typeof detail === "string") return { message: detail };
  if (typeof detail === "object" && detail !== null && !Array.isArray(detail)) {
    const record = detail as Record<string, unknown>;
    return {
      message: typeof record.message === "string" ? record.message : "Something went wrong",
      code: typeof record.code === "string" ? record.code : undefined,
      details: record,
    };
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((item) => {
      if (typeof item === "object" && item !== null && "msg" in item && typeof item.msg === "string") {
        return [item.msg];
      }
      return [];
    });
    if (messages.length) return { message: messages.join("; ") };
  }
  return { message: "Something went wrong" };
}

async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const token = typeof window === "undefined" ? null : localStorage.getItem("flowmind_token");
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (options.body && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(`${API_URL}${path}`, {
    ...options,
    headers,
  });
}

async function throwApiError(response: Response): Promise<never> {
  const body = await response.json().catch(() => ({}));
  const parsed = errorDetails(body.detail);
  throw new ApiError(response.status, parsed.message, parsed.code, parsed.details);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, options);
  if (response.status === 204) return undefined as T;
  if (!response.ok) return throwApiError(response);
  return response.json() as Promise<T>;
}

async function requestBlob(path: string): Promise<Blob> {
  const response = await apiFetch(path);
  if (!response.ok) return throwApiError(response);
  return response.blob();
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
  documents: (courseId: number) =>
    request<CourseDocument[]>(`/courses/${courseId}/documents`),
  uploadDocument: (courseId: number, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<CourseDocument>(`/courses/${courseId}/documents`, {
      method: "POST",
      body,
    });
  },
  retryDocument: (documentId: number) =>
    request<CourseDocument>(`/documents/${documentId}/retry`, { method: "POST" }),
  deleteDocument: (documentId: number) =>
    request<void>(`/documents/${documentId}`, { method: "DELETE" }),
  documentFile: (documentId: number) =>
    requestBlob(`/documents/${documentId}/file`),
  queryKnowledge: (courseId: number, question: string) =>
    request<KnowledgeAnswer>(`/courses/${courseId}/knowledge/query`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
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
