import assert from "node:assert/strict";
import test from "node:test";

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

test("keeps a restored conversation read-only while its latest server execution is active", () => {
  assert.equal(isConversationExecutionActive("pending", null), true);
  assert.equal(isConversationExecutionActive("running", "running"), true);
  assert.equal(isConversationExecutionActive("cancel_requested", "waiting_for_input"), true);
  assert.equal(isConversationExecutionActive("completed", "completed"), false);
  assert.equal(isConversationExecutionActive("failed", "failed"), false);
  assert.equal(isConversationExecutionActive(null, null), false);
});
