import assert from "node:assert/strict";
import test from "node:test";

import {
  hasSavedAssistantForLatestTurn,
  pendingAssistantRunIdForLatestTurn,
} from "../lib/history";
import type { Message, Run } from "../lib/types";

/** Build the minimal persisted message fixture needed by the turn-state rule. */
function message(role: Message["role"], sequence: number, runId: string | null = null): Message {
  return {
    message_id: `m-${sequence}`,
    session_id: "s-1",
    run_id: runId,
    parent_message_id: null,
    role,
    content_type: "text",
    content_text: `${role}-${sequence}`,
    content_uri: null,
    sequence,
    token_count: null,
    metadata_json: {},
    created_at: "2026-08-03T00:00:00Z",
  };
}

test("does not mark a newest user turn saved from an older assistant message", () => {
  assert.equal(
    hasSavedAssistantForLatestTurn([message("assistant", 2), message("user", 3)]),
    false,
  );
  assert.equal(
    hasSavedAssistantForLatestTurn([message("user", 3), message("assistant", 4)]),
    true,
  );
});

test("restores one pending Assistant projection only for an unsatisfied active latest Run", () => {
  const activeRun: Run = {
    run_id: "run-active",
    user_id: "user-1",
    session_id: "s-1",
    user_request: "latest question",
    run_type: "agent_workflow",
    status: "running",
    budget_limit: null,
    cost_used: "0",
    started_at: "2026-08-03T00:00:00Z",
    completed_at: null,
    created_at: "2026-08-03T00:00:00Z",
    updated_at: "2026-08-03T00:00:00Z",
  };

  assert.equal(
    pendingAssistantRunIdForLatestTurn(
      [message("assistant", 2, "run-old"), message("user", 3, "run-active")],
      activeRun,
    ),
    "run-active",
  );
  assert.equal(
    pendingAssistantRunIdForLatestTurn(
      [message("user", 3, "run-active"), message("assistant", 4, "run-active")],
      activeRun,
    ),
    null,
  );
  assert.equal(
    pendingAssistantRunIdForLatestTurn(
      [message("user", 3, "run-active")],
      { ...activeRun, status: "completed" },
    ),
    null,
  );
});
