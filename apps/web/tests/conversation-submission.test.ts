import assert from "node:assert/strict";
import test from "node:test";
import type { MessageSummary, ModelAttempt, RunDetail } from "../lib/types";

import {
  conversationSubmissionLockName,
  isConversationExecutionActive,
  isConversationSyncEvent,
  runWithConversationSubmissionLock,
} from "../lib/conversation-submission";

test("uses a stable encoded lock name per persisted conversation", () => {
  assert.equal(
    conversationSubmissionLockName("session/1"),
    "nexuspilot:conversation-submit:session%2F1",
  );
});

test("rejects a same-conversation submission when another tab owns the browser lock", async () => {
  let submitted = false;
  const lockManager = {
    async request<T>(
      _name: string,
      _options: { mode: "exclusive"; ifAvailable: true },
      callback: (lock: unknown | null) => Promise<T>,
    ): Promise<T> {
      return callback(null);
    },
  };
  const result = await runWithConversationSubmissionLock(lockManager, "session-1", async () => {
    submitted = true;
  });
  assert.deepEqual(result, { acquired: false });
  assert.equal(submitted, false);
});

test("allows new conversations and validates only bounded cross-tab messages", async () => {
  const result = await runWithConversationSubmissionLock(null, null, async () => "sent");
  assert.deepEqual(result, { acquired: true, value: "sent" });
  assert.equal(isConversationSyncEvent({
    type: "submission-finished",
    sessionId: "session-1",
    senderId: "tab-1",
  }), true);
  assert.equal(isConversationSyncEvent({ type: "submission-finished", sessionId: "" }), false);
});

/** Build the persisted single-call quick-response facts, not a local optimistic message. */
function savedQuickResponse() {
  const attempt = {
    attempt_id: "attempt-1", run_id: "run-1", task_id: null, status: "completed",
  } as ModelAttempt;
  const run = {
    run_id: "run-1", session_id: "session-1", status: "pending",
    tasks: [], tasks_has_more: false, attempts: [attempt], attempts_has_more: false,
  } as unknown as RunDetail;
  const messages = [
    { message_id: "user-1", session_id: "session-1", run_id: "run-1", role: "user", sequence: 1 },
    { message_id: "assistant-1", session_id: "session-1", run_id: "run-1", role: "assistant",
      sequence: 2, source_model_attempt_id: "attempt-1" },
  ] as MessageSummary[];
  return { run, workflowStatus: null, messages, workflowAbsentForRunId: "run-1" };
}

/** Active/unknown server facts must still block another billable submission. */
test("keeps restored active executions blocked unless a quick response is durably complete", () => {
  const facts = savedQuickResponse();
  assert.equal(isConversationExecutionActive({ ...facts, messages: [] }), true);
  for (const status of ["running", "cancel_requested"] as const) {
    assert.equal(isConversationExecutionActive({ ...facts, run: { ...facts.run, status } }), true);
  }
  for (const status of ["completed", "failed", "cancelled"] as const) {
    assert.equal(isConversationExecutionActive({ ...facts, run: { ...facts.run, status } }), false);
  }
  assert.equal(isConversationExecutionActive({ ...facts, run: null }), false);
});

/** Reproduce pending Run containers trapping a saved and finished quick-response turn. */
test("allows a saved single-call quick response after reload without rewriting pending Run status", () => {
  const facts = savedQuickResponse();
  assert.equal(isConversationExecutionActive(facts), false);
  assert.equal(facts.run.status, "pending");
});

/** A returned answer cannot overrule an active workflow or unfinished discovery. */
test("keeps active workflows and unknown workflow lookups blocked even with a saved answer", () => {
  const facts = savedQuickResponse();
  for (const workflowStatus of ["pending", "running", "waiting_for_input", "completed"] as const) {
    assert.equal(isConversationExecutionActive({ ...facts, workflowStatus }), true);
  }
  for (const workflowAbsentForRunId of [null, "another-run"]) {
    assert.equal(isConversationExecutionActive({ ...facts, workflowAbsentForRunId }), true);
  }
});

/** Only the latest user turn's same-Run, same-Attempt saved answer releases submission. */
test("rejects unrelated older or unbound saved answers as proof of quick-response completion", () => {
  const facts = savedQuickResponse();
  for (const changed of [
    { run_id: "another-run" }, { session_id: "another-session" },
    { source_model_attempt_id: "another-attempt" }, { source_model_attempt_id: null },
    { sequence: 0 },
  ]) {
    const messages = [facts.messages[0], { ...facts.messages[1], ...changed }];
    assert.equal(isConversationExecutionActive({ ...facts, messages }), true);
  }
  assert.equal(isConversationExecutionActive({ ...facts, messages: [facts.messages[1]] }), true);
  assert.equal(isConversationExecutionActive({ ...facts, messages: [
    ...facts.messages, { ...facts.messages[0], run_id: "newer-run", sequence: 3 },
  ] }), true);
});

/** Generic multi-call/Task Runs and incomplete queries are not inferred complete. */
test("does not release submission from failed running partial or multi-call attempt evidence", () => {
  const facts = savedQuickResponse();
  for (const status of ["started", "failed", "cancelled", "timed_out", "outcome_unknown"] as const) {
    assert.equal(isConversationExecutionActive({ ...facts, run: {
      ...facts.run, attempts: [{ ...facts.run.attempts[0], status }],
    } }), true);
  }
  for (const changed of [
    { attempts: [] }, { attempts_has_more: true }, { tasks_has_more: true },
    { tasks: [{ task_id: "task-1" }] },
    { attempts: [{ ...facts.run.attempts[0], task_id: "task-1" }] },
    { attempts: [{ ...facts.run.attempts[0], run_id: "another-run" }] },
    { attempts: [...facts.run.attempts, { ...facts.run.attempts[0], attempt_id: "later", status: "started" as const }] },
  ]) {
    assert.equal(isConversationExecutionActive({ ...facts, run: { ...facts.run, ...changed } }), true);
  }
});
