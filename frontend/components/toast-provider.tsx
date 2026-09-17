"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { CheckCircle2, X, XCircle } from "lucide-react";

type Toast = { id: number; message: string; kind: "success" | "error" };
type ToastContextValue = { toast: (message: string, kind?: Toast["kind"]) => void };

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toast = useCallback((message: string, kind: Toast["kind"] = "success") => {
    const id = Date.now();
    setToasts((current) => [...current, { id, message, kind }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 3500);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-5 right-5 z-50 flex w-[calc(100%-2.5rem)] max-w-sm flex-col gap-2">
        {toasts.map((item) => (
          <div key={item.id} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 text-sm shadow-soft">
            {item.kind === "success" ? <CheckCircle2 className="h-5 w-5 text-emerald-600" /> : <XCircle className="h-5 w-5 text-red-600" />}
            <span className="flex-1 text-slate-700">{item.message}</span>
            <button aria-label="Dismiss" onClick={() => setToasts((current) => current.filter((toastItem) => toastItem.id !== item.id))}>
              <X className="h-4 w-4 text-slate-400" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}

