import type { Message, Run } from "./types";

/** Identify saved Agent answers from server-authored metadata, including older turns. */
export function isPersistedAgentMessage(message: Pick<Message, "role" | "metadata_json">): boolean {
  return message.role === "assistant" && message.metadata_json?.source === "agent_workflow";
}

/** Rebuild an unsaved reply's visible state from the latest owned Run, never mark it saved. */
export function assistantProjectionForLatestTurn(messagePage: Message[], latestRun: Run | null) {
  const user = [...messagePage].reverse().find((message) => message.role === "user");
  if (!latestRun || !user || user.run_id !== latestRun.run_id ||
    user.session_id !== latestRun.session_id || hasSavedAssistantForLatestTurn(messagePage)) return null;
  const pending = ["pending", "running", "cancel_requested"].includes(latestRun.status);
  const failed = latestRun.status === "failed";
  const cancelled = latestRun.status === "cancelled";
  if (!pending && !failed && !cancelled) return null;
  return { runId: latestRun.run_id, pending, failed, cancelled,
    content: failed ? "本次执行失败，未生成最终回答。请查看运行详情。"
      : cancelled ? "本次执行已停止，未生成最终回答。" : "" };
}

/**
 * Determine whether the newest user turn has a persisted Assistant response.
 * Older Assistant messages must not make a newer unsatisfied turn look saved.
 */
export function hasSavedAssistantForLatestTurn(messagePage: Message[]): boolean {
  const latestUserMessage = [...messagePage].reverse().find((message) => message.role === "user");
  const latestAssistantMessage = [...messagePage]
    .reverse()
    .find((message) => message.role === "assistant");
  return Boolean(
    latestAssistantMessage &&
      (!latestUserMessage || latestAssistantMessage.sequence > latestUserMessage.sequence),
  );
}
