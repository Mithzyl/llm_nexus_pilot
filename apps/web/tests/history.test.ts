import assert from "node:assert/strict";
import test from "node:test";

import { hasSavedAssistantForLatestTurn } from "../lib/history";
import type { Message } from "../lib/types";

/** Build the minimal persisted message fixture needed by the turn-state rule. */
function message(role: Message["role"], sequence: number): Message {
  return {
    message_id: `m-${sequence}`,
    session_id: "s-1",
    run_id: null,
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
