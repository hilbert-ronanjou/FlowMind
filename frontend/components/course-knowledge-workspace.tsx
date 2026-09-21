"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import {
  AlertCircle,
  ExternalLink,
  FileText,
  Loader2,
  RefreshCw,
  Send,
  Trash2,
  UploadCloud,
} from "lucide-react";

import { useToast } from "@/components/toast-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { CourseDocument, KnowledgeAnswer } from "@/lib/types";
import { cn } from "@/lib/utils";

const MAX_PDF_BYTES = 20 * 1024 * 1024;

type HistoryEntry = {
  id: number;
  question: string;
  result: KnowledgeAnswer;
};

const statusPresentation = {
  PROCESSING: { label: "正在处理", className: "bg-amber-50 text-amber-700" },
  READY: { label: "已就绪", className: "bg-emerald-50 text-emerald-700" },
  FAILED: { label: "处理失败", className: "bg-red-50 text-red-700" },
} as const;

function formatBytes(value: number): string {
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function friendlyError(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) {
    return error instanceof Error ? error.message : fallback;
  }
  const messages: Record<string, string> = {
    duplicate_content: "该资料已经上传过。",
    same_filename_different_content:
      "当前课程已有同名但内容不同的资料。请删除旧资料或修改文件名后重新上传。",
    document_limit_reached: "当前课程已达到 20 份资料限制，请先删除一份资料。",
    file_too_large: "PDF 不能超过 20 MB。",
    invalid_file: "请选择可提取文字的有效 PDF 文件。",
    document_processing: "资料仍在处理中，请等待处理完成后再操作。",
    retry_not_allowed: "只有处理失败的资料可以重试。",
    source_file_missing: "原始 PDF 已不可用，请删除后重新上传。",
  };
  if (error.code && messages[error.code]) return messages[error.code];
  if (error.status === 401) return "登录已过期，请重新登录后再试。";
  if (error.status === 404) return "课程或资料不存在，或者你没有访问权限。";
  if (error.status === 413) return "PDF 不能超过 20 MB。";
  if (error.status === 422) return "提交内容无效，请检查后重试。";
  if (error.status === 502) return "资料问答结果暂时无法验证，请稍后重试。";
  if (error.status === 503) return "AI 服务暂时不可用，请稍后重试。";
  return fallback;
}

export function CourseKnowledgeWorkspace({ courseId }: { courseId: number }) {
  const [documents, setDocuments] = useState<CourseDocument[] | null>(null);
  const [documentError, setDocumentError] = useState("");
  const [documentLoadFailed, setDocumentLoadFailed] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [busyDocumentId, setBusyDocumentId] = useState<number | null>(null);
  const [viewingDocumentId, setViewingDocumentId] = useState<number | null>(null);
  const [highlightedDocumentId, setHighlightedDocumentId] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const [querying, setQuerying] = useState(false);
  const [queryError, setQueryError] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const historyId = useRef(0);
  const { toast } = useToast();

  const loadDocuments = useCallback(async () => {
    try {
      const result = await api.documents(courseId);
      setDocuments(result);
      setDocumentError("");
      setDocumentLoadFailed(false);
    } catch (error) {
      setDocumentError(friendlyError(error, "无法加载课程资料，请重试。"));
      setDocumentLoadFailed(true);
    }
  }, [courseId]);

  useEffect(() => {
    let cancelled = false;
    api.documents(courseId).then(
      (result) => {
        if (cancelled) return;
        setDocuments(result);
        setDocumentError("");
        setDocumentLoadFailed(false);
      },
      (error: unknown) => {
        if (!cancelled) {
          setDocuments([]);
          setDocumentError(friendlyError(error, "无法加载课程资料，请重试。"));
          setDocumentLoadFailed(true);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [courseId]);

  const hasProcessing = documents?.some((document) => document.status === "PROCESSING") ?? false;

  useEffect(() => {
    if (!hasProcessing) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const result = await api.documents(courseId);
        if (cancelled) return;
        setDocuments(result);
        setDocumentError("");
        if (result.some((document) => document.status === "PROCESSING")) {
          timer = window.setTimeout(poll, 2000);
        }
      } catch (error) {
        if (cancelled) return;
        setDocumentError(friendlyError(error, "资料状态刷新失败，正在重试。"));
        timer = window.setTimeout(poll, 2000);
      }
    };

    timer = window.setTimeout(poll, 2000);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [courseId, hasProcessing]);

  async function upload(file: File | undefined) {
    if (!file) return;
    setHighlightedDocumentId(null);
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setDocumentError("只支持 PDF 文件。请重新选择。");
      return;
    }
    if (file.size > MAX_PDF_BYTES) {
      setDocumentError("PDF 不能超过 20 MB。");
      return;
    }

    setUploading(true);
    setDocumentError("");
    try {
      const created = await api.uploadDocument(courseId, file);
      setDocuments((current) => [created, ...(current ?? [])]);
      toast("资料已上传，正在处理。", "success");
    } catch (error) {
      if (error instanceof ApiError && error.code === "duplicate_content") {
        const existingId = error.details?.existing_document_id;
        if (typeof existingId === "number") {
          setHighlightedDocumentId(existingId);
          window.requestAnimationFrame(() => {
            document.getElementById(`course-document-${existingId}`)?.scrollIntoView({
              behavior: "smooth",
              block: "center",
            });
          });
        }
      }
      setDocumentError(friendlyError(error, "资料上传失败，请稍后重试。"));
    } finally {
      setUploading(false);
    }
  }

  async function retryDocument(documentId: number) {
    setBusyDocumentId(documentId);
    setDocumentError("");
    try {
      const updated = await api.retryDocument(documentId);
      setDocuments((current) =>
        current?.map((document) => (document.id === documentId ? updated : document)) ?? [],
      );
      toast("已重新开始处理资料。", "success");
    } catch (error) {
      setDocumentError(friendlyError(error, "无法重试该资料。"));
    } finally {
      setBusyDocumentId(null);
    }
  }

  async function deleteDocument(document: CourseDocument) {
    const confirmed = window.confirm("删除后，该资料将不再参与课程知识问答。");
    if (!confirmed) return;
    setBusyDocumentId(document.id);
    setDocumentError("");
    try {
      await api.deleteDocument(document.id);
      setDocuments((current) => current?.filter((item) => item.id !== document.id) ?? []);
      toast("资料已删除。", "success");
    } catch (error) {
      setDocumentError(friendlyError(error, "无法删除该资料。"));
    } finally {
      setBusyDocumentId(null);
    }
  }

  async function openDocument(documentId: number) {
    const viewer = window.open("about:blank", "_blank");
    if (viewer) viewer.opener = null;
    setViewingDocumentId(documentId);
    try {
      const blob = await api.documentFile(documentId);
      const url = URL.createObjectURL(blob);
      if (!viewer) {
        URL.revokeObjectURL(url);
        throw new Error("浏览器阻止了新窗口，请允许弹出窗口后重试。");
      }
      viewer.location.href = url;
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (error) {
      viewer?.close();
      const message = friendlyError(error, "无法打开来源 PDF。请稍后重试。");
      setDocumentError(message);
      toast(message, "error");
    } finally {
      setViewingDocumentId(null);
    }
  }

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = question.trim();
    if (!normalized) {
      setQueryError("请输入问题后再发送。");
      return;
    }
    if (normalized.length > 2000) {
      setQueryError("问题不能超过 2000 个字符。");
      return;
    }

    setQuerying(true);
    setQueryError("");
    try {
      const result = await api.queryKnowledge(courseId, normalized);
      historyId.current += 1;
      setHistory((current) => [
        { id: historyId.current, question: normalized, result },
        ...current,
      ]);
      setQuestion("");
    } catch (error) {
      setQueryError(friendlyError(error, "无法完成资料问答，请稍后重试。"));
    } finally {
      setQuerying(false);
    }
  }

  const readyCount = documents?.filter((document) => document.status === "READY").length ?? 0;

  return (
    <div className="mt-8 grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
      <section aria-labelledby="course-documents-heading">
        <Card className="h-full">
          <CardHeader className="border-b border-slate-100 pb-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <CardTitle id="course-documents-heading" className="text-lg">课程资料</CardTitle>
                <p className="mt-1 text-sm leading-6 text-slate-500">
                  只支持可提取文字的 PDF，最大 20 MB，单课程最多 20 份。
                </p>
              </div>
              <UploadCloud className="h-5 w-5 shrink-0 text-brand-600" />
            </div>
            <div className="mt-4">
              <Label htmlFor="course-pdf-upload">上传 PDF</Label>
              <Input
                id="course-pdf-upload"
                className="mt-2 cursor-pointer file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-semibold file:text-brand-700"
                type="file"
                accept=".pdf,application/pdf"
                disabled={uploading}
                aria-describedby="course-pdf-help"
                onChange={(event) => {
                  const input = event.currentTarget;
                  void upload(input.files?.[0]).finally(() => {
                    input.value = "";
                  });
                }}
              />
              <p id="course-pdf-help" className="mt-2 text-xs text-slate-500">
                {uploading ? "正在上传，请勿重复操作…" : "上传后会立即显示处理状态。"}
              </p>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {documentError && (
              <div role="alert" className="flex gap-2 rounded-xl bg-red-50 p-3 text-sm text-red-700">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{documentError}</span>
              </div>
            )}
            {documents === null ? (
              <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
            ) : documents.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-300 px-5 py-9 text-center">
                <FileText className="mx-auto h-7 w-7 text-slate-400" />
                <p className="mt-3 font-semibold text-ink">还没有课程资料</p>
                <p className="mt-1 text-sm text-slate-500">上传 PDF 后即可建立当前课程的资料库。</p>
              </div>
            ) : (
              documents.map((document) => {
                const presentation = statusPresentation[document.status];
                const busy = busyDocumentId === document.id;
                return (
                  <article
                    id={`course-document-${document.id}`}
                    key={document.id}
                    className={cn(
                      "rounded-xl border border-slate-200 p-4 transition",
                      highlightedDocumentId === document.id && "border-brand-500 ring-2 ring-brand-100",
                    )}
                  >
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="break-all text-sm font-semibold text-ink">{document.filename}</p>
                          <Badge className={presentation.className}>{presentation.label}</Badge>
                        </div>
                        <p className="mt-1 text-xs text-slate-500">
                          {formatBytes(document.file_size)} · {new Date(document.created_at).toLocaleString()}
                        </p>
                        {document.status === "FAILED" && document.failure_reason && (
                          <p className="mt-2 text-sm leading-5 text-red-700">{document.failure_reason}</p>
                        )}
                        {document.status === "PROCESSING" && (
                          <p className="mt-2 flex items-center gap-2 text-sm text-amber-700">
                            <Loader2 className="h-4 w-4 animate-spin" />正在提取文字并建立索引
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 flex-wrap gap-2">
                        {document.status === "READY" && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={viewingDocumentId === document.id}
                            onClick={() => void openDocument(document.id)}
                          >
                            {viewingDocumentId === document.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <ExternalLink className="h-4 w-4" />}
                            查看来源
                          </Button>
                        )}
                        {document.status === "FAILED" && (
                          <Button size="sm" variant="outline" disabled={busy} onClick={() => void retryDocument(document.id)}>
                            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                            Retry
                          </Button>
                        )}
                        {document.status !== "PROCESSING" && (
                          <Button size="sm" variant="danger" disabled={busy} onClick={() => void deleteDocument(document)}>
                            <Trash2 className="h-4 w-4" />删除
                          </Button>
                        )}
                      </div>
                    </div>
                  </article>
                );
              })
            )}
            {documents !== null && documentLoadFailed && (
              <Button size="sm" variant="ghost" onClick={() => void loadDocuments()}>
                <RefreshCw className="h-4 w-4" />重新加载资料
              </Button>
            )}
          </CardContent>
        </Card>
      </section>

      <section aria-labelledby="course-knowledge-heading">
        <Card className="h-full">
          <CardHeader className="border-b border-slate-100 pb-5">
            <CardTitle id="course-knowledge-heading" className="text-lg">课程知识问答</CardTitle>
            <p className="mt-1 text-sm leading-6 text-slate-500">每个问题都是独立请求，回答只使用当前已就绪资料。</p>
          </CardHeader>
          <CardContent>
            {readyCount === 0 ? (
              <div className="rounded-xl bg-brand-50 p-4 text-sm leading-6 text-brand-700">
                上传并完成至少一份课程资料处理后，即可进行资料问答。
              </div>
            ) : hasProcessing ? (
              <div className="rounded-xl bg-amber-50 p-4 text-sm leading-6 text-amber-800">
                部分资料仍在处理中，当前回答只会使用已就绪资料。
              </div>
            ) : null}

            <form className="mt-4" onSubmit={submitQuestion}>
              <Label htmlFor="course-knowledge-question">问题</Label>
              <Textarea
                id="course-knowledge-question"
                className="mt-2 min-h-28"
                value={question}
                maxLength={2000}
                disabled={querying || readyCount === 0}
                placeholder="例如：实验什么时候截止？"
                onChange={(event) => setQuestion(event.target.value)}
              />
              <div className="mt-2 flex items-center justify-between gap-3">
                <span className="text-xs text-slate-500">{question.length}/2000</span>
                <Button type="submit" variant="brand" disabled={querying || readyCount === 0 || !question.trim()}>
                  {querying ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                  {querying ? "正在查找资料…" : "发送问题"}
                </Button>
              </div>
              {queryError && <p role="alert" className="mt-3 text-sm text-red-700">{queryError}</p>}
            </form>

            <div className="mt-6 space-y-4" aria-live="polite">
              {history.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-300 px-5 py-8 text-center text-sm text-slate-500">
                  当前页面会话还没有问答记录，刷新后记录会清空。
                </div>
              ) : (
                history.map((entry) => (
                  <article key={entry.id} className="rounded-xl border border-slate-200 p-4">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">问题</p>
                    <p className="mt-1 whitespace-pre-wrap text-sm font-medium text-ink">{entry.question}</p>
                    <div className={cn("mt-4 rounded-xl p-4", entry.result.answerable ? "bg-emerald-50/70" : "bg-slate-50")}>
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        {entry.result.answerable ? "资料回答" : "资料不足"}
                      </p>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{entry.result.answer}</p>
                    </div>
                    {entry.result.answerable && entry.result.citations.length > 0 && (
                      <div className="mt-4">
                        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">来源</p>
                        <div className="mt-2 space-y-2">
                          {entry.result.citations.map((citation) => (
                            <div key={citation.chunk_id} className="flex flex-col gap-2 rounded-lg bg-slate-50 p-3 sm:flex-row sm:items-center sm:justify-between">
                              <p className="min-w-0 break-all text-sm text-slate-700">《{citation.filename}》 · 第 {citation.page_number} 页</p>
                              <Button size="sm" variant="ghost" onClick={() => void openDocument(citation.document_id)}>
                                <ExternalLink className="h-4 w-4" />查看来源
                              </Button>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </article>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
