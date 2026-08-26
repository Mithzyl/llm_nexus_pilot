import type { Message } from "./types";

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
