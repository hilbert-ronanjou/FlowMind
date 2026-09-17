import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { AuthProvider } from "@/components/auth-provider";
import { ToastProvider } from "@/components/toast-provider";

export const metadata: Metadata = {
  title: "FlowMind AI Cloud",
  description: "A focused learning workspace for university students.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" data-scroll-behavior="smooth">
      <body>
        <ToastProvider>
          <AuthProvider>{children}</AuthProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
