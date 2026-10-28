import { Children, isValidElement, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { decodeString } from "micromark-util-decode-string";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { MarkdownCodeBlock } from "./MarkdownCodeBlock";
import { sourceCodeFromRenderedText } from "./code-copy";
import type { ClaimCitationTarget } from "../../lib/claim-evidence";

type MarkdownContentProps = {
  content: string;
  /** Render raw HTML as inert text instead of dropping it from evidence prose. */
  preserveHtmlText?: boolean;
  /** Persisted candidate citations that may become interactive links. */
  citationTargets?: readonly ClaimCitationTarget[];
  /** Open one persisted citation without changing the conversation route. */
  onCitationClick?: (target: ClaimCitationTarget) => void;
};

type MarkdownAstNode = {
  type?: string;
  value?: string;
  url?: string;
  data?: Record<string, unknown>;
  position?: {
    start?: { line?: number; column?: number; offset?: number };
    end?: { line?: number; column?: number; offset?: number };
  };
  children?: MarkdownAstNode[];
};

/**
 * Convert an original Markdown offset within a text node to its decoded UTF-16 index.
 * Character references and escapes decode through the same parser utility used by Markdown.
 */
function decodedTextIndexForSourceOffset(
  content: string,
  nodeStart: number,
  nodeEnd: number,
  sourceOffset: number,
): number | null {
  if (sourceOffset < nodeStart || sourceOffset > nodeEnd) return null;
  return decodeString(content.slice(nodeStart, sourceOffset)).length;
}

/** Allow only navigation protocols that cannot execute browser script. */
function transformSafeMarkdownUrl(url: string): string {
  if (url.startsWith("/") || url.startsWith("#")) return url;
  if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) {
    return defaultUrlTransform(url);
  }
  return "";
}

/** Open remote Markdown links without granting the destination access to this window. */
export function MarkdownLink({
  href = "",
  children,
  citationTargets,
  onCitationClick,
  ...props
}: ComponentPropsWithoutRef<"a"> & {
  citationTargets?: readonly ClaimCitationTarget[];
  onCitationClick?: (target: ClaimCitationTarget) => void;
}) {
  const isRemoteLink = /^https?:\/\//i.test(href);
  const citationIndex = href.startsWith("#citation:")
    ? Number(href.slice("#citation:".length))
    : null;
  const citationTarget = citationIndex !== null
    ? citationTargets?.[citationIndex] ?? null
    : null;
  if (citationTarget) {
    return (
      <a
        {...props}
        href={href}
        className="citation-reference"
        aria-label={`查看引用 ${citationTarget.alias} 的来源`}
        onClick={(event) => {
          event.preventDefault();
          onCitationClick?.(citationTarget);
        }}
      >
        {children}
      </a>
    );
  }
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

/**
 * Convert citation tokens into durable citation links only when persisted candidate units
 * have already established their source identity.
 */
function remarkCitationLinks(
  content: string,
  citationTargets: readonly ClaimCitationTarget[],
) {
  const sourceMatches = citationTargets.flatMap((target, targetIndex) => {
    const token = content.slice(target.contentStart, target.contentEnd);
    return token === `[${target.alias}]`
      ? [{ ...target, contentStart: target.contentStart, contentEnd: target.contentEnd, targetIndex }]
      : [];
  }).sort((left, right) => left.contentStart - right.contentStart);

  return (tree: MarkdownAstNode) => {
    function transformChildren(node: MarkdownAstNode) {
      if (!node.children?.length) return;
      node.children = node.children.flatMap((child) => {
        if (["code", "inlineCode", "html", "link"].includes(child.type ?? "")) {
          return [child];
        }
        if (child.type === "text" && typeof child.value === "string" && child.value.includes("[")) {
          const sourceStart = child.position?.start?.offset;
          const sourceEnd = child.position?.end?.offset;
          if (sourceStart === undefined || sourceEnd === undefined) return [child];
          const nodeMatches = sourceMatches.filter((target) => (
            target.contentStart >= sourceStart
            && target.contentEnd <= sourceEnd
          ));
          if (nodeMatches.length === 0) return [child];
          const children: MarkdownAstNode[] = [];
          let cursor = 0;
          for (const match of nodeMatches) {
            const decodedStart = decodedTextIndexForSourceOffset(
              content,
              sourceStart,
              sourceEnd,
              match.contentStart,
            );
            const decodedEnd = decodedTextIndexForSourceOffset(
              content,
              sourceStart,
              sourceEnd,
              match.contentEnd,
            );
            if (
              decodedStart === null
              || decodedEnd === null
              || decodedStart < cursor
              || decodedEnd > child.value.length
            ) {
              continue;
            }
            const token = content.slice(match.contentStart, match.contentEnd);
            if (child.value.slice(decodedStart, decodedEnd) !== token) continue;
            if (decodedStart > cursor) {
              children.push({ type: "text", value: child.value.slice(cursor, decodedStart) });
            }
            children.push({
              type: "link",
              url: `#citation:${match.targetIndex}`,
              children: [{ type: "text", value: token }],
            });
            cursor = decodedEnd;
          }
          if (children.length === 0) return [child];
          if (cursor < child.value.length) {
            children.push({ type: "text", value: child.value.slice(cursor) });
          }
          return children;
        }
        transformChildren(child);
        return [child];
      });
    }

    transformChildren(tree);
  };
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

/** Read the parser's code child, never DOM text that can contain toolbar labels. */
function MarkdownPre({ children }: ComponentPropsWithoutRef<"pre">) {
  const codeElement = Children.toArray(children).find((child) =>
    isValidElement<ComponentPropsWithoutRef<"code">>(child) && child.type === "code",
  );
  if (!isValidElement<ComponentPropsWithoutRef<"code">>(codeElement)
      || typeof codeElement.props.children !== "string") {
    return <pre>{children}</pre>;
  }
  const language = /(?:^|\s)language-([^\s]+)/.exec(codeElement.props.className ?? "")?.[1] ?? "代码";
  return (
    <MarkdownCodeBlock code={sourceCodeFromRenderedText(codeElement.props.children)} language={language}>
      {children}
    </MarkdownCodeBlock>
  );
}

/** Render trusted Markdown syntax while keeping model text outside the raw-HTML execution path. */
export function MarkdownContent({
  content,
  preserveHtmlText = false,
  citationTargets = [],
  onCitationClick,
}: MarkdownContentProps) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[
          remarkGfm,
          ...(citationTargets.length > 0
            ? [() => remarkCitationLinks(content, citationTargets)]
            : []),
        ]}
        skipHtml={!preserveHtmlText}
        urlTransform={transformSafeMarkdownUrl}
        components={{
          a: (props) => (
            <MarkdownLink
              {...props}
              citationTargets={citationTargets}
              onCitationClick={onCitationClick}
            />
          ),
          img: MarkdownImage,
          table: MarkdownTable,
          pre: MarkdownPre,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
