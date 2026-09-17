import * as React from "react";
import { cn } from "@/lib/utils";

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "min-h-24 w-full resize-y rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-sm text-ink outline-none placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

