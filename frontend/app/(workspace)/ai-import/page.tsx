"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, LoaderCircle, RotateCcw, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { useToast } from "@/components/toast-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, api } from "@/lib/api";
import type { ConfirmImportPayload, Course, ExtractionResult, TaskDraft, TaskPriority } from "@/lib/types";

const priorities: TaskPriority[] = ["LOW", "MEDIUM", "HIGH"];
type CourseMode = "EXISTING" | "CREATE_NEW" | null;

function errorCopy(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) return "The AI service is temporarily unavailable. Your text is safe here—please try again in a moment.";
    if (error.status === 401) return "Your session has expired. Sign in again, then retry your analysis.";
    if (error.status === 422) return "Enter a course notification before asking AI to analyze it.";
  }
  return "We couldn’t analyze this notification. Your original text is still here, so you can retry.";
}

function importErrorCopy(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "That course is no longer available. Choose another course and retry.";
    if (error.status === 409) return "A course with this name already exists. Choose the existing course instead.";
    if (error.status === 422) return "Review the title, priority, deadline, task details, and course choice before retrying.";
    if (error.status === 401) return "Your session has expired. Sign in again before importing.";
  }
  return "Import failed and nothing was saved. Your original text and draft are still here, so you can retry.";
}

function PrioritySelect({ id, value, onChange }: { id: string; value: TaskPriority; onChange: (value: TaskPriority) => void }) {
  return (
    <select
      id={id}
      value={value}
      onChange={(event) => onChange(event.target.value as TaskPriority)}
      className="h-11 w-full rounded-xl border border-slate-200 bg-white px-3.5 text-sm text-ink outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
    >
      {priorities.map((priority) => <option key={priority} value={priority}>{priority[0] + priority.slice(1).toLowerCase()}</option>)}
    </select>
  );
}

export default function AiImportPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [sourceText, setSourceText] = useState("");
  const [draft, setDraft] = useState<ExtractionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [inputError, setInputError] = useState("");
  const [importError, setImportError] = useState("");
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseMode, setCourseMode] = useState<CourseMode>(null);
  const [selectedCourseId, setSelectedCourseId] = useState("");
  const [newCourseName, setNewCourseName] = useState("");
  const importInFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api.courses().then(
      (courseList) => { if (!cancelled) setCourses(courseList); },
      () => { if (!cancelled) setImportError("Courses could not be loaded. Refresh the page before importing."); },
    );
    return () => { cancelled = true; };
  }, []);

  async function analyze() {
    if (!sourceText.trim()) {
      setInputError("Paste a course notification to analyze.");
      return;
    }

    setLoading(true);
    setError("");
    setInputError("");
    try {
      const result = await api.extractContent(sourceText);
      setDraft(result);
      setCourseMode(null);
      setSelectedCourseId("");
      setNewCourseName(result.course_name ?? "");
      setImportError("");
    } catch (caught) {
      setError(errorCopy(caught));
    } finally {
      setLoading(false);
    }
  }

  function updateTask(index: number, changes: Partial<TaskDraft>) {
    setDraft((current) => current ? {
      ...current,
      tasks: current.tasks.map((task, taskIndex) => taskIndex === index ? { ...task, ...changes } : task),
    } : current);
  }

  async function confirmImport() {
    if (!draft || importInFlight.current) return;
    if (!draft.title.trim()) {
      setImportError("Add a task title before importing.");
      return;
    }
    if (courseMode === null) {
      setImportError("Choose whether to use an existing course or create a new course.");
      return;
    }
    if (courseMode === "EXISTING" && !selectedCourseId) {
      setImportError("Choose an existing course.");
      return;
    }
    if (courseMode === "CREATE_NEW" && !newCourseName.trim()) {
      setImportError("Enter the new course name.");
      return;
    }

    const course: ConfirmImportPayload["course"] = courseMode === "EXISTING"
      ? { mode: "EXISTING", course_id: Number(selectedCourseId) }
      : { mode: "CREATE_NEW", name: newCourseName.trim() };

    importInFlight.current = true;
    setImporting(true);
    setImportError("");
    try {
      const imported = await api.confirmImport({ course, draft });
      toast(`Imported “${imported.task.title}”.`);
      router.push("/tasks");
    } catch (caught) {
      setImportError(importErrorCopy(caught));
    } finally {
      importInFlight.current = false;
      setImporting(false);
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="AI-assisted drafting"
        title="AI Import"
        description="Paste a course notification, review every extracted field, then explicitly confirm before anything is saved."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Course notification</CardTitle>
            <p className="mt-1 text-sm leading-6 text-slate-500">Your original text stays in this field after success or failure.</p>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="source-text">Original text</Label>
              <Textarea
                id="source-text"
                value={sourceText}
                onChange={(event) => {
                  setSourceText(event.target.value);
                  if (inputError) setInputError("");
                }}
                placeholder="Paste a syllabus update, assignment notice, or course message…"
                className="min-h-64 leading-6"
                aria-describedby={inputError ? "source-error" : "source-help"}
                aria-invalid={Boolean(inputError)}
              />
              {inputError ? <p id="source-error" className="text-sm text-red-600">{inputError}</p> : <p id="source-help" className="text-xs text-slate-500">AI can make mistakes. You’ll be able to edit every extracted field.</p>}
            </div>
            <Button variant="brand" className="w-full" onClick={() => void analyze()} disabled={loading || importing}>
              {loading ? <><LoaderCircle className="h-4 w-4 animate-spin" />Analyzing…</> : <><Sparkles className="h-4 w-4" />Analyze notification</>}
            </Button>
          </CardContent>
        </Card>

        <section aria-live="polite" aria-busy={loading || importing}>
          {loading ? (
            <Card>
              <CardContent className="flex min-h-80 flex-col items-center justify-center text-center">
                <LoaderCircle className="h-8 w-8 animate-spin text-brand-600" />
                <h2 className="mt-4 font-semibold text-ink">Creating your draft</h2>
                <p className="mt-1 max-w-sm text-sm leading-6 text-slate-500">AI is organizing the course, deadline, priority, and task details.</p>
              </CardContent>
            </Card>
          ) : error ? (
            <div className="flex min-h-80 flex-col items-center justify-center rounded-2xl border border-red-100 bg-red-50/60 p-8 text-center">
              <AlertCircle className="h-8 w-8 text-red-600" />
              <h2 className="mt-3 font-semibold text-red-950">Analysis didn’t finish</h2>
              <p className="mt-1 max-w-md text-sm leading-6 text-red-700">{error}</p>
              <Button className="mt-5" variant="outline" onClick={() => void analyze()}>
                <RotateCcw className="h-4 w-4" />Try again
              </Button>
            </div>
          ) : draft ? (
            <Card>
              <CardHeader className="border-b border-slate-100 pb-5">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <CardTitle>Review draft</CardTitle>
                    <p className="mt-1 text-sm text-slate-500">Edit anything the AI misunderstood.</p>
                  </div>
                  <span className="w-fit rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">Draft only · not saved</span>
                </div>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="course-name">Course name</Label>
                    <Input id="course-name" value={draft.course_name ?? ""} onChange={(event) => setDraft({ ...draft, course_name: event.target.value || null })} placeholder="No course identified" />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="draft-title">Title</Label>
                    <Input id="draft-title" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="draft-deadline">Deadline</Label>
                    <Input id="draft-deadline" type="date" value={draft.deadline ?? ""} onChange={(event) => setDraft({ ...draft, deadline: event.target.value || null })} />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="draft-priority">Priority</Label>
                    <PrioritySelect id="draft-priority" value={draft.priority} onChange={(priority) => setDraft({ ...draft, priority })} />
                  </div>
                </div>

                <div className="border-t border-slate-100 pt-5">
                  <div className="mb-4">
                    <h3 className="font-semibold text-ink">Tasks</h3>
                    <p className="mt-1 text-sm text-slate-500">{draft.tasks.length} {draft.tasks.length === 1 ? "task" : "tasks"} extracted</p>
                  </div>
                  {draft.tasks.length ? (
                    <div className="space-y-4">
                      {draft.tasks.map((task, index) => (
                        <div key={index} className="rounded-2xl border border-slate-200 bg-slate-50/60 p-4">
                          <p className="mb-4 text-xs font-bold uppercase tracking-[0.14em] text-slate-400">Task {index + 1}</p>
                          <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2 sm:col-span-2">
                              <Label htmlFor={`task-${index}-title`}>Title</Label>
                              <Input id={`task-${index}-title`} value={task.title} onChange={(event) => updateTask(index, { title: event.target.value })} />
                            </div>
                            <div className="space-y-2 sm:col-span-2">
                              <Label htmlFor={`task-${index}-description`}>Description</Label>
                              <Textarea id={`task-${index}-description`} value={task.description ?? ""} onChange={(event) => updateTask(index, { description: event.target.value || null })} placeholder="No description" />
                            </div>
                            <div className="space-y-2">
                              <Label htmlFor={`task-${index}-deadline`}>Deadline</Label>
                              <Input id={`task-${index}-deadline`} type="date" value={task.deadline ?? ""} onChange={(event) => updateTask(index, { deadline: event.target.value || null })} />
                            </div>
                            <div className="space-y-2">
                              <Label htmlFor={`task-${index}-priority`}>Priority</Label>
                              <PrioritySelect id={`task-${index}-priority`} value={task.priority} onChange={(priority) => updateTask(index, { priority })} />
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="rounded-xl border border-dashed border-slate-200 p-5 text-center text-sm text-slate-500">No task details were extracted. You can edit the main draft above or analyze a clearer notification.</div>
                  )}
                </div>

                <div className="space-y-4 border-t border-slate-100 pt-5">
                  <div>
                    <h3 className="font-semibold text-ink">Import destination</h3>
                    <p className="mt-1 text-sm leading-6 text-slate-500">Choose explicitly where this task belongs. The AI course name never creates a course by itself.</p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className={`rounded-xl border p-4 ${courseMode === "EXISTING" ? "border-brand-500 bg-brand-50/60" : "border-slate-200"}`}>
                      <span className="flex items-center gap-2 text-sm font-semibold text-ink">
                        <input type="radio" name="course-mode" checked={courseMode === "EXISTING"} onChange={() => { setCourseMode("EXISTING"); setImportError(""); }} />
                        Use existing course
                      </span>
                      <select
                        aria-label="Existing course"
                        className="mt-3 h-11 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:bg-slate-100"
                        value={selectedCourseId}
                        onChange={(event) => setSelectedCourseId(event.target.value)}
                        disabled={courseMode !== "EXISTING" || courses.length === 0}
                      >
                        <option value="">{courses.length ? "Choose a course" : "No existing courses"}</option>
                        {courses.map((course) => <option key={course.id} value={course.id}>{course.name}</option>)}
                      </select>
                    </label>
                    <label className={`rounded-xl border p-4 ${courseMode === "CREATE_NEW" ? "border-brand-500 bg-brand-50/60" : "border-slate-200"}`}>
                      <span className="flex items-center gap-2 text-sm font-semibold text-ink">
                        <input type="radio" name="course-mode" checked={courseMode === "CREATE_NEW"} onChange={() => { setCourseMode("CREATE_NEW"); setImportError(""); }} />
                        Create new course
                      </span>
                      <Input
                        className="mt-3"
                        aria-label="New course name"
                        value={newCourseName}
                        onChange={(event) => setNewCourseName(event.target.value)}
                        disabled={courseMode !== "CREATE_NEW"}
                        placeholder="Course name"
                      />
                    </label>
                  </div>
                  <p className="text-xs leading-5 text-slate-500">One confirmed draft creates one task. Extracted steps are saved in that task’s description; no subtask records are created.</p>
                  {importError && <p role="alert" className="rounded-xl border border-red-100 bg-red-50 p-3 text-sm text-red-700">{importError}</p>}
                  <Button variant="brand" className="w-full" onClick={() => void confirmImport()} disabled={importing}>
                    {importing ? <><LoaderCircle className="h-4 w-4 animate-spin" />Importing…</> : <>Confirm import</>}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ) : (
            <div className="flex min-h-80 flex-col items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white/70 p-8 text-center">
              <div className="rounded-2xl bg-brand-50 p-3 text-brand-600"><Sparkles className="h-6 w-6" /></div>
              <h2 className="mt-4 font-semibold text-ink">Your draft will appear here</h2>
              <p className="mt-1 max-w-sm text-sm leading-6 text-slate-500">Paste a notification and analyze it to review the structured result. Nothing is saved until you confirm the import.</p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
