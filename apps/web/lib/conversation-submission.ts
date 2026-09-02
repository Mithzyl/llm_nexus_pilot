export type ConversationSyncEvent = {
  type: "submission-started" | "submission-finished";
  sessionId: string;
  senderId: string;
};

type BrowserLockManager = {
  request<T>(
    name: string,
    options: { mode: "exclusive"; ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T>,
  ): Promise<T>;
};

export type SubmissionLockResult<T> =
  | { acquired: true; value: T }
  | { acquired: false };

/**
 * Report whether the latest persisted Run or Agent Workflow still owns the conversation.
 * A restored browser must not accept another submission while these server facts are active.
 */
export function isConversationExecutionActive(
  runStatus: RunStatus | null,
  workflowStatus: AgentWorkflowStatus | null,
): boolean {
  return (
    runStatus === "pending" ||
    runStatus === "running" ||
    runStatus === "cancel_requested" ||
    workflowStatus === "pending" ||
    workflowStatus === "running" ||
    workflowStatus === "waiting_for_input"
  );
}

/** Build one stable cross-tab Web Lock name for a persisted conversation. */
export function conversationSubmissionLockName(sessionId: string): string {
  return `nexuspilot:conversation-submit:${encodeURIComponent(sessionId)}`;
}

/** Run a submission under an atomic browser lock, or report that another tab owns it. */
export async function runWithConversationSubmissionLock<T>(
  lockManager: BrowserLockManager | null,
  sessionId: string | null,
  submit: () => Promise<T>,
): Promise<SubmissionLockResult<T>> {
  if (!lockManager || !sessionId) {
    return { acquired: true, value: await submit() };
  }
  return lockManager.request(
    conversationSubmissionLockName(sessionId),
    { mode: "exclusive", ifAvailable: true },
    async (lock) => lock
      ? { acquired: true, value: await submit() }
      : { acquired: false },
  );
}

/** Reject malformed cross-tab messages before they can change conversation state. */
export function isConversationSyncEvent(value: unknown): value is ConversationSyncEvent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ConversationSyncEvent>;
  return (
    (candidate.type === "submission-started" || candidate.type === "submission-finished") &&
    typeof candidate.sessionId === "string" &&
    candidate.sessionId.length > 0 &&
    typeof candidate.senderId === "string" &&
    candidate.senderId.length > 0
  );
}
import type { AgentWorkflowStatus, RunStatus } from "./types";
