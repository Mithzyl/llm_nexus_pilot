"use client";

import { useState } from "react";

import { reasoningPreview } from "../../lib/reasoning-stream";
import type {
  ReasoningBlockSnapshot,
  ReasoningPresentation,
} from "../../lib/types";

/** Render one server-authorized reasoning presentation without Provider-specific branches. */
export function ReasoningView({
  presentation,
  mode = "chat",
}: {
  presentation: ReasoningPresentation;
  mode?: "chat" | "trajectory";
}) {
  switch (presentation.kind) {
    case "reasoning.raw":
      return (
        <ReasoningDisclosure
          title="思考过程"
          presentation={presentation}
          mode={mode}
        />
      );
    case "reasoning.summary":
      return (
        <ReasoningDisclosure
          title="思考摘要"
          presentation={presentation}
          mode={mode}
        />
      );
    case "reasoning.status":
      if (presentation.status === "running") {
        return <p className="reasoning-status" role="status">正在分析…</p>;
      }
      if (mode === "trajectory") {
        return (
          <section className="reasoning-status-block">
            <p className="reasoning-status">
              {presentation.status === "interrupted" ? "分析已中断，内容不可见" : "推理内容不可见"}
            </p>
            <ReasoningMetrics presentation={presentation} />
          </section>
        );
      }
      return null;
    default:
      return assertNever(presentation);
  }
}

/** Provide an accessible collapsed-by-default disclosure for visible reasoning text. */
function ReasoningDisclosure({
  title,
  presentation,
  mode,
}: {
  title: string;
  presentation: Extract<
    ReasoningPresentation,
    { kind: "reasoning.raw" | "reasoning.summary" }
  >;
  mode: "chat" | "trajectory";
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const contentId = `reasoning-${presentation.block_id.replace(/[^A-Za-z0-9_-]/g, "-")}`;
  const preview = reasoningPreview(presentation);

  return (
    <section className="reasoning-disclosure" data-status={presentation.status}>
      <button
        type="button"
        aria-expanded={isExpanded}
        aria-controls={contentId}
        onClick={() => setIsExpanded((current) => !current)}
      >
        <span>{title}</span>
        <small>{presentation.status === "running" ? "生成中" : presentation.status === "interrupted" ? "已中断" : "已完成"}</small>
        <i aria-hidden="true">{isExpanded ? "−" : "+"}</i>
      </button>
      {!isExpanded && preview && <p className="reasoning-preview">{preview}</p>}
      {isExpanded && (
        <div id={contentId} className="reasoning-text">
          {presentation.text}
        </div>
      )}
      {mode === "trajectory" && <ReasoningMetrics presentation={presentation} />}
    </section>
  );
}

/** Show persisted reasoning timing and token evidence only in the trajectory view. */
function ReasoningMetrics({ presentation }: { presentation: ReasoningPresentation }) {
  const snapshot = presentation as Partial<ReasoningBlockSnapshot>;
  const metrics = [
    ["开始", formatReasoningTime(snapshot.started_at)],
    ["首个可见 Token", formatReasoningTime(snapshot.first_visible_token_at)],
    ["完成", formatReasoningTime(snapshot.completed_at)],
    ["Reasoning Token", presentation.reasoning_tokens?.toString() ?? null],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  if (metrics.length === 0) return null;
  return (
    <dl className="reasoning-metrics">
      {metrics.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Format one persisted reasoning timestamp without inventing missing timing data. */
function formatReasoningTime(value: string | null | undefined): string | null {
  if (!value) return null;
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

/** Make TypeScript reject an unhandled ReasoningPresentation variant. */
function assertNever(value: never): never {
  throw new Error(`Unsupported reasoning presentation: ${JSON.stringify(value)}`);
}
