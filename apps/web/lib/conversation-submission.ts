import type { AgentWorkflowStatus, MessageSummary, Run, RunDetail } from "./types";

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
 * Keep submissions blocked while persisted execution facts are active or insufficient.
 * A pending generic Run alone is not evidence that a saved quick response is still executing.
 */
export function isConversationExecutionActive({ run, workflowStatus, messages, workflowAbsentForRunId }: {
  run: Run | RunDetail | null;
  workflowStatus: AgentWorkflowStatus | null;
  messages: readonly MessageSummary[];
  workflowAbsentForRunId: string | null;
}): boolean {
  if (
    workflowStatus === "pending" ||
    workflowStatus === "running" ||
    workflowStatus === "waiting_for_input"
  ) return true;
  if (!run || !["pending", "running", "cancel_requested"].includes(run.status)) return false;

  // Only the single-call quick-response case can finish while its generic Run stays pending.
  // Workflow discovery errors, partial lists and generic Task/multi-call Runs remain blocked.
  if (
    run.status !== "pending" || workflowStatus !== null ||
    workflowAbsentForRunId !== run.run_id || !run.session_id ||
    !("attempts" in run) || run.attempts_has_more || run.tasks_has_more ||
    run.tasks.length !== 0 || run.attempts.length !== 1
  ) return true;
  const attempt = run.attempts[0];
  if (attempt.status !== "completed" || attempt.task_id !== null || attempt.run_id !== run.run_id) {
    return true;
  }
  const latestUser = [...messages].reverse().find((message) => message.role === "user");
  if (latestUser?.run_id !== run.run_id || latestUser.session_id !== run.session_id) return true;
  return !messages.some((message) =>
    message.role === "assistant" && message.run_id === run.run_id &&
    message.session_id === run.session_id && message.sequence > latestUser.sequence &&
    message.source_model_attempt_id === attempt.attempt_id,
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
