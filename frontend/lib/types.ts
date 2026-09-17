export type User = {
  id: number;
  email: string;
  username: string;
  created_at: string;
  updated_at: string;
};

export type Course = {
  id: number;
  user_id: number;
  name: string;
  code: string | null;
  teacher: string | null;
  description: string | null;
  color: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskStatus = "TODO" | "IN_PROGRESS" | "COMPLETED";
export type TaskPriority = "LOW" | "MEDIUM" | "HIGH";

export type Task = {
  id: number;
  user_id: number;
  course_id: number | null;
  title: string;
  description: string | null;
  deadline: string | null;
  priority: TaskPriority;
  status: TaskStatus;
  source: "MANUAL" | "AI";
  created_at: string;
  updated_at: string;
};

export type DashboardData = {
  today_tasks: Task[];
  upcoming_tasks: Task[];
  course_count: number;
  completed_task_count: number;
  recent_tasks: Task[];
};

