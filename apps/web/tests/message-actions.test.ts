import assert from "node:assert/strict";
import test from "node:test";

import { repeatableUserPrompt } from "../lib/message-actions";

test("restores the prompt paired with the selected Assistant response", () => {
  const messages = [
    { local_id: "u1", role: "user", content: "first" },
    { local_id: "a1", role: "assistant", content: "answer one" },
    { local_id: "u2", role: "user", content: "second" },
    { local_id: "a2", role: "assistant", content: "answer two" },
  ];
  assert.equal(repeatableUserPrompt(messages, "a1"), "first");
  assert.equal(repeatableUserPrompt(messages, "a2"), "second");
  assert.equal(repeatableUserPrompt(messages, "missing"), null);
});
