import assert from "node:assert/strict";
import test from "node:test";

import {
  hasSavedAssistantForLatestTurn,
  assistantProjectionForLatestTurn,
  isPersistedAgentMessage,
} from "../lib/history";
import type { Message, Run } from "../lib/types";

/** Failed/cancelled latest turns need a visible projection, never a fabricated saved answer. */
test("restores a terminal missing reply without duplicating an existing answer", () => {
  const run = { run_id: "r", session_id: "s-1", status: "failed" } as Run;
  const page = [message("user", 1, "r")];
  assert.deepEqual(assistantProjectionForLatestTurn(page, run), {
    runId: "r", pending: false, failed: true, cancelled: false,
    content: "本次执行失败，未生成最终回答。请查看运行详情。",
  });
  assert.equal(assistantProjectionForLatestTurn([...page, message("assistant", 2, "r")], run), null);
  assert.equal(assistantProjectionForLatestTurn(page, { ...run, run_id: "other" }), null);
  assert.equal(assistantProjectionForLatestTurn(page, { ...run, session_id: "other" }), null);
  assert.equal(assistantProjectionForLatestTurn(page, { ...run, status: "completed" }), null);
  assert.equal(assistantProjectionForLatestTurn(page, { ...run, status: "running" })?.pending, true);
  assert.equal(assistantProjectionForLatestTurn(page, { ...run, status: "cancelled" })?.cancelled, true);
});

/** Restore historical Agent controls using server-authored message metadata. */
test("recognizes saved Agent answers independently of the currently selected run", () => {
  const saved = { ...message("assistant", 2, "old"), metadata_json: { source: "agent_workflow" } };
  assert.equal(isPersistedAgentMessage(saved), true);
  assert.equal(isPersistedAgentMessage({ ...saved, role: "user" }), false);
  assert.equal(isPersistedAgentMessage({ ...saved, metadata_json: {} }), false);
});

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

/** Existing running-turn restoration must remain intact alongside terminal failure recovery. */
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
    assistantProjectionForLatestTurn(
      [message("assistant", 2, "run-old"), message("user", 3, "run-active")],
      activeRun,
    )?.runId,
    "run-active",
  );
  assert.equal(
    assistantProjectionForLatestTurn(
      [message("user", 3, "run-active"), message("assistant", 4, "run-active")],
      activeRun,
    ),
    null,
  );
  assert.equal(
    assistantProjectionForLatestTurn(
      [message("user", 3, "run-active")],
      { ...activeRun, status: "completed" },
    ),
    null,
  );
});
