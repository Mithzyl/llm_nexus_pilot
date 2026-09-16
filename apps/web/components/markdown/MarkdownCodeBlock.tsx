"use client";

import { useState, type ReactNode } from "react";

import { copyCodeText } from "./code-copy";

type MarkdownCodeBlockProps = {
  code: string;
  language: string;
  children: ReactNode;
};

type CopyFeedback = {
  code: string;
  status: "copying" | "copied" | "failed";
};

/** Keep code scrollable while exposing its language, independent copy action, and copy result. */
export function MarkdownCodeBlock({ code, language, children }: MarkdownCodeBlockProps) {
  const [feedback, setFeedback] = useState<CopyFeedback | null>(null);
  // Streaming updates invalidate earlier feedback without overwriting the newly arrived code.
  const status = feedback?.code === code ? feedback.status : null;

  /** Copy this render's code; a late result is not shown for a newer streaming value. */
  async function handleCopy() {
    setFeedback({ code, status: "copying" });
    const result = await copyCodeText(code, navigator.clipboard);
    setFeedback({ code, status: result });
  }

  return (
    <div className="markdown-code-block">
      <div className="markdown-code-toolbar">
        <span className="markdown-code-language" title={language}>{language}</span>
        <span className="markdown-code-feedback" role="status" aria-live="polite" aria-atomic="true">
          {status === "copied" ? "已复制" : status === "failed" ? "复制失败，请手动选择代码" : ""}
        </span>
        <button
          type="button"
          className="markdown-code-copy"
          aria-label="复制代码"
          disabled={status === "copying"}
          onClick={() => void handleCopy()}
        >
          {status === "copying" ? "复制中…" : "复制"}
        </button>
      </div>
      <pre tabIndex={0} role="region" aria-label={`${language} 代码`}>{children}</pre>
    </div>
  );
}
