import assert from "node:assert/strict";
import test from "node:test";

import {
  agentStructuredOutputMode,
  appendAgentWorkflowEvent,
  parseAgentWorkflowSseBlock,
  readAgentWorkflowEventStream,
} from "../lib/agent-workflow";
import type { AgentWorkflowEvent } from "../lib/types";

/** Build one complete workflow event for sequence and stream tests. */
function workflowEvent(
  sequence: number,
  eventType: AgentWorkflowEvent["event_type"] = "agent.node.started",
): AgentWorkflowEvent {
  return {
    event_id: `event-${sequence}`,
    event_sequence: sequence,
    workflow_execution_id: "workflow-1",
    run_id: "run-1",
    node_execution_id: eventType.startsWith("agent.node") ? `node-${sequence}` : null,
    event_type: eventType,
    workflow_status: eventType === "agent.workflow.completed" ? "completed" : "running",
    node_status: eventType.startsWith("agent.node") ? "running" : null,
    occurred_at: "2026-08-10T00:00:00Z",
    public_summary: `event ${sequence}`,
    public_payload: {},
    trace_id: null,
  };
}

test("parses the persisted workflow event rather than the Responses SSE shape", () => {
  const event = workflowEvent(4, "agent.node.completed");
  const parsed = parseAgentWorkflowSseBlock(
    `id: 4\nevent: agent.node.completed\ndata: ${JSON.stringify(event)}`,
  );
  assert.deepEqual(parsed, event);
  assert.equal(parseAgentWorkflowSseBlock(`event: agent.node.completed\ndata: {}`), null);
  assert.equal(
    parseAgentWorkflowSseBlock(
      `id: 4\nevent: agent.node.future\ndata: ${JSON.stringify({ ...event, event_type: "agent.node.future" })}`,
    ),
    null,
  );
});

test("uses prompted JSON for DeepSeek and native schema for supported providers", () => {
  assert.equal(agentStructuredOutputMode("deepseek"), "prompted_json");
  assert.equal(agentStructuredOutputMode("openai"), "native_schema");
});

test("deduplicates workflow events and reports sequence gaps without reordering facts", () => {
  const first = appendAgentWorkflowEvent(
    { events: [], lastSequence: 0, hasGap: false },
    workflowEvent(1),
  );
  const duplicate = appendAgentWorkflowEvent(first, workflowEvent(1));
  const gap = appendAgentWorkflowEvent(duplicate, workflowEvent(3));

  assert.equal(duplicate.events.length, 1);
  assert.equal(gap.events.length, 2);
  assert.equal(gap.lastSequence, 3);
  assert.equal(gap.hasGap, true);
  assert.deepEqual(gap.events.map((event) => event.event_sequence), [1, 3]);
});

test("requires a terminal workflow event for a live POST stream", async () => {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const event = workflowEvent(1, "agent.workflow.started");
      controller.enqueue(
        encoder.encode(`id: 1\nevent: ${event.event_type}\ndata: ${JSON.stringify(event)}\n\n`),
      );
      controller.close();
    },
  });

  await assert.rejects(
    readAgentWorkflowEventStream(new Response(body), () => undefined, {
      requireTerminalEvent: true,
    }),
    /终止事件前结束/,
  );
});

test("allows finite persisted replay to end without a terminal event", async () => {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const event = workflowEvent(2);
      controller.enqueue(
        encoder.encode(`id: 2\nevent: ${event.event_type}\ndata: ${JSON.stringify(event)}\n\n`),
      );
      controller.close();
    },
  });
  const sequences: number[] = [];

  await readAgentWorkflowEventStream(
    new Response(body),
    (event) => {
      sequences.push(event.event_sequence);
    },
    { requireTerminalEvent: false },
  );
  assert.deepEqual(sequences, [2]);
});
