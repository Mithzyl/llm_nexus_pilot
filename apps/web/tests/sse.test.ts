import assert from "node:assert/strict";
import test from "node:test";

import {
  parseSseBlock,
  readStreamEvents,
  replayCommittedStreamEvents,
  StreamSequenceGapError,
} from "../lib/sse";
import type { StreamEvent } from "../lib/types";

/** Build one complete event envelope for durable replay tests. */
function replayEvent(type: string, sequence: number): StreamEvent {
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
    data: {},
  };
}

test("parses a normalized SSE event and ignores incomplete blocks", () => {
  const parsed = parseSseBlock(
    'event: response.text.delta\ndata: {"sequence":2,"data":{"delta":"hello"}}',
  );
  assert.deepEqual(parsed, {
    type: "response.text.delta",
    sequence: 2,
    data: { delta: "hello" },
  });
  assert.equal(parseSseBlock('data: {"sequence":2,"data":{}}'), null);
});

test("deduplicates event sequences while retaining failure events", async () => {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode('event: response.started\ndata: {"sequence":1,"data":{}}\n\n'));
      controller.enqueue(encoder.encode('event: response.text.delta\ndata: {"sequence":2,"data":{"delta":"a"}}\n\n'));
      controller.enqueue(encoder.encode('event: response.text.delta\ndata: {"sequence":2,"data":{"delta":"duplicate"}}\n\n'));
      controller.enqueue(encoder.encode('event: response.failed\ndata: {"sequence":3,"data":{"error":{"message":"failed"}}}\n\n'));
      controller.close();
    },
  });
  const events: string[] = [];
  await readStreamEvents(new Response(body), (event) => {
    events.push(event.type);
  });
  assert.deepEqual(events, ["response.started", "response.text.delta", "response.failed"]);
});

test("propagates a stream read failure instead of converting it into success", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.error(new Error("connection lost"));
    },
  });
  await assert.rejects(
    readStreamEvents(new Response(body), () => undefined),
    /connection lost/,
  );
});

test("rejects clean EOF when no terminal response event was received", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: response.text.delta\ndata: {"sequence":1,"data":{"delta":"partial"}}\n\n'));
      controller.close();
    },
  });
  await assert.rejects(
    readStreamEvents(new Response(body), () => undefined),
    /终止事件前结束/,
  );
});

test("rejects sequence gaps so callers can request durable replay", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: response.started\ndata: {"sequence":1,"data":{}}\n\n'));
      controller.enqueue(new TextEncoder().encode('event: response.completed\ndata: {"sequence":3,"data":{}}\n\n'));
      controller.close();
    },
  });
  await assert.rejects(
    readStreamEvents(new Response(body), () => undefined),
    (error: unknown) =>
      error instanceof StreamSequenceGapError &&
      error.lastSequence === 1 &&
      error.receivedSequence === 3,
  );
});

test("replays committed pages after the last acknowledged sequence until completion", async () => {
  const requestedAfterSequences: number[] = [];
  const appliedSequences: number[] = [];
  const finalSequence = await replayCommittedStreamEvents(
    async (afterSequence) => {
      requestedAfterSequences.push(afterSequence);
      if (afterSequence === 1) {
        return {
          items: [replayEvent("response.text.delta", 2)],
          has_more: true,
        };
      }
      return {
        items: [replayEvent("response.completed", 3)],
        has_more: false,
      };
    },
    (event) => {
      appliedSequences.push(event.sequence);
    },
    { afterSequence: 1, timeoutMs: 100, pollIntervalMs: 0 },
  );

  assert.equal(finalSequence, 3);
  assert.deepEqual(requestedAfterSequences, [1, 2]);
  assert.deepEqual(appliedSequences, [2, 3]);
});

test("rejects a durable replay gap instead of skipping missing content", async () => {
  await assert.rejects(
    replayCommittedStreamEvents(
      async () => ({ items: [replayEvent("response.completed", 4)], has_more: false }),
      () => undefined,
      { afterSequence: 2, timeoutMs: 100, pollIntervalMs: 0 },
    ),
    (error: unknown) =>
      error instanceof StreamSequenceGapError &&
      error.lastSequence === 2 &&
      error.receivedSequence === 4,
  );
});
