import type { MessageSummary } from "./types";

/**
 * Resolve evidence from the clicked persisted Assistant response only.
 * It deliberately has no active-Run fallback because that would display unrelated execution facts.
 */
export function runEvidenceTargetForMessage(
  message: Pick<MessageSummary, "role" | "run_id">,
): string | null {
  return message.role === "assistant" ? message.run_id : null;
}
