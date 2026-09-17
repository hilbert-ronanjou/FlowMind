import * as React from "react";
import { cn } from "@/lib/utils";

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "flex h-11 w-full rounded-xl border border-slate-200 bg-white px-3.5 text-sm text-ink outline-none placeholder:text-slate-400 focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

