import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  isPersistedResponseOutputTruncated,
  isResponseOutputTruncated,
  responseMessageMetadata,
} from "../lib/response-outcome";

test("does not apply a frontend output token limit to response or Agent requests", () => {
  const workspaceSource = readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(workspaceSource, /max_output_tokens\s*:/);
});

test("recognizes provider-normalized length termination as truncated output", () => {
  assert.equal(isResponseOutputTruncated("length"), true);
  assert.equal(isResponseOutputTruncated("stop"), false);
  assert.equal(isResponseOutputTruncated(undefined), false);
});

test("persists and restores truncation evidence with the assistant message", () => {
  const metadata = responseMessageMetadata("length");

  assert.deepEqual(metadata, {
    model_response_finish_reason: "length",
  });
  assert.equal(isPersistedResponseOutputTruncated(metadata), true);
  assert.equal(isPersistedResponseOutputTruncated({}), false);
});

test("retains an explicitly requested output limit in metadata for future policy use", () => {
  assert.deepEqual(responseMessageMetadata("stop", 8_192), {
    model_response_finish_reason: "stop",
    requested_max_output_tokens: 8_192,
  });
});
