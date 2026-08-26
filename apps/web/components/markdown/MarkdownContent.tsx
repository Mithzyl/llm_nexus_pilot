import type { ComponentPropsWithoutRef, ReactNode } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownContentProps = {
  content: string;
};

/** Allow only navigation protocols that cannot execute browser script. */
function transformSafeMarkdownUrl(url: string): string {
  if (url.startsWith("/") || url.startsWith("#")) return url;
  if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) {
    return defaultUrlTransform(url);
  }
  return "";
}

/** Open remote Markdown links without granting the destination access to this window. */
function MarkdownLink({ href = "", children, ...props }: ComponentPropsWithoutRef<"a">) {
  const isRemoteLink = /^https?:\/\//i.test(href);
  return (
    <a
      {...props}
      href={href}
      {...(isRemoteLink ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}

/** Replace model-generated images with inert text so rendering never triggers remote tracking. */
function MarkdownImage({ alt }: ComponentPropsWithoutRef<"img">) {
  return <span className="markdown-image-placeholder">图片：{alt || "未命名图片"}</span>;
}

/** Keep wide GitHub-Flavored Markdown tables inside the conversation column. */
function MarkdownTable({ children }: { children?: ReactNode }) {
  return (
    <div className="markdown-table-scroll">
      <table>{children}</table>
    </div>
  );
}

/** Render trusted Markdown syntax while keeping model text outside the raw-HTML execution path. */
export function MarkdownContent({ content }: MarkdownContentProps) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        urlTransform={transformSafeMarkdownUrl}
        components={{
          a: MarkdownLink,
          img: MarkdownImage,
          table: MarkdownTable,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
