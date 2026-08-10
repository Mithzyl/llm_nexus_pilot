import assert from "node:assert/strict";
import test from "node:test";

import { parseSseBlock, readStreamEvents } from "../lib/sse";

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
