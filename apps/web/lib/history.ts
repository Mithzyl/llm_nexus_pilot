import type { Message, Run } from "./types";

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

/**
 * Return the latest active Run that needs a temporary Assistant projection after restoration.
 * The projection is created only when its durable User Message has no later Assistant response.
 */
export function pendingAssistantRunIdForLatestTurn(
  messagePage: Message[],
  latestRun: Run | null,
): string | null {
  if (!latestRun || !["pending", "running", "cancel_requested"].includes(latestRun.status)) {
    return null;
  }
  const latestUserMessage = [...messagePage].reverse().find((message) => message.role === "user");
  if (!latestUserMessage || latestUserMessage.run_id !== latestRun.run_id) return null;
  return hasSavedAssistantForLatestTurn(messagePage) ? null : latestRun.run_id;
}
