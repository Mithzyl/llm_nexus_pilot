import assert from "node:assert/strict";
import test from "node:test";

import { runEvidenceTargetForMessage } from "../lib/run-evidence";
import type { MessageSummary } from "../lib/types";

/** Build one message summary with the durable Run association used by the evidence drawer. */
function message(role: MessageSummary["role"], runId: string | null): MessageSummary {
  return {
    message_id: "message-1",
    session_id: "session-1",
    run_id: runId,
    parent_message_id: null,
    role,
    content_type: "text",
    content_preview: "answer",
    content_uri: null,
    sequence: 1,
    token_count: null,
    created_at: "2026-08-03T00:00:00Z",
  };
}

test("targets the clicked Assistant message Run instead of the current workspace Run", () => {
  const clickedMessage = message("assistant", "run-old-answer");

  assert.equal(runEvidenceTargetForMessage(clickedMessage), "run-old-answer");
  assert.notEqual(runEvidenceTargetForMessage(clickedMessage), "run-current-workflow");
  assert.equal(runEvidenceTargetForMessage(message("assistant", null)), null);
  assert.equal(runEvidenceTargetForMessage(message("user", "run-user")), null);
});
