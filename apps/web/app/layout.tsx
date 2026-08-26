import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "NexusPilot · 对话工作台",
  description: "NexusPilot 阶段4对话式 Web 前端。",
};

/**
 * Provide the shared document shell for the server-rendered NexusPilot app.
 */
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
