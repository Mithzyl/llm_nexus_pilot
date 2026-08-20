import assert from "node:assert/strict";
import test from "node:test";

import { applyReasoningStreamEvent, reasoningPreview } from "../lib/reasoning-stream";
import type { StreamEvent } from "../lib/types";

/** Build the minimum v2 envelope needed by pure reasoning projection tests. */
function event(type: string, sequence: number, data: StreamEvent["data"]): StreamEvent {
  return {
    schema_version: "conversation-stream.v2",
    event_id: `event-${sequence}`,
    response_id: "attempt-1",
    run_id: "run-1",
    turn_id: null,
    step_id: null,
    timestamp_ms: sequence,
    type,
    sequence,
    data,
  };
}

test("projects raw deltas and lets the final block replace temporary text", () => {
  let blocks = applyReasoningStreamEvent(
    [],
    event("reasoning.started", 1, {
      block_id: "attempt-1:reasoning-0",
      kind: "reasoning.raw",
    }),
  );
  assert.equal(blocks[0].kind, "reasoning.status");

  blocks = applyReasoningStreamEvent(
    blocks,
    event("reasoning.raw.delta", 2, {
      block_id: "attempt-1:reasoning-0",
      delta: "first\nlatest",
    }),
  );
  assert.equal(blocks[0].kind, "reasoning.raw");
  assert.equal(reasoningPreview(blocks[0]), "latest");

  blocks = applyReasoningStreamEvent(
    blocks,
    event("reasoning.completed", 3, {
      block: {
        kind: "reasoning.raw",
        block_id: "attempt-1:reasoning-0",
        status: "completed",
        text: "authoritative final text",
      },
    }),
  );
  assert.equal(blocks[0].kind, "reasoning.raw");
  assert.equal(blocks[0].kind === "reasoning.raw" && blocks[0].text, "authoritative final text");
  assert.equal(reasoningPreview(blocks[0]), "authoritative final text");
});

test("keeps status presentations text-free and replaces stream state from response completion", () => {
  const blocks = applyReasoningStreamEvent(
    [],
    event("response.completed", 1, {
      response: {
        id: "attempt-1",
        object: "response",
        status: "completed",
        provider: "openai",
        model: "reasoning-model",
        output_text: "answer",
        tool_calls: [],
        structured_output: null,
        finish_reason: "stop",
        reasoning_blocks: [
          {
            kind: "reasoning.status",
            block_id: "attempt-1:reasoning-0",
            status: "completed",
          },
        ],
        usage: {
          input_tokens: 1,
          output_tokens: 2,
          cached_tokens: 0,
          reasoning_tokens: 1,
          estimated_cost: null,
        },
        latency_ms: 3,
        provider_request_id: null,
      },
    }),
  );
  assert.deepEqual(blocks, [
    {
      kind: "reasoning.status",
      block_id: "attempt-1:reasoning-0",
      status: "completed",
    },
  ]);
});

test("ignores an unknown reasoning kind before the exhaustive renderer", () => {
  const current = [
    {
      kind: "reasoning.status" as const,
      block_id: "attempt-1:reasoning-0",
      status: "running" as const,
    },
  ];
  const unsafeEvent = event("reasoning.completed", 2, {
    block: {
      kind: "reasoning.future",
      block_id: "attempt-1:reasoning-0",
      status: "completed",
      text: "unsupported",
    } as never,
  });

  assert.equal(applyReasoningStreamEvent(current, unsafeEvent), current);
});
