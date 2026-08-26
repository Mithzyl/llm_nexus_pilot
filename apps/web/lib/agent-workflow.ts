import type { AgentWorkflowEvent, ProviderName } from "./types";

const TERMINAL_WORKFLOW_EVENTS = new Set([
  "agent.workflow.completed",
  "agent.workflow.failed",
  "agent.workflow.cancelled",
  "agent.workflow.outcome_unknown",
]);
const WORKFLOW_EVENT_TYPES = new Set([
  "agent.workflow.started",
  "agent.workflow.completed",
  "agent.workflow.failed",
  "agent.workflow.cancelled",
  "agent.workflow.outcome_unknown",
  "agent.node.started",
  "agent.node.completed",
  "agent.node.failed",
  "agent.node.cancelled",
  "agent.node.outcome_unknown",
]);
const WORKFLOW_STATUSES = new Set([
  "pending",
  "running",
  "waiting_for_input",
  "completed",
  "failed",
  "cancelled",
  "outcome_unknown",
]);
const NODE_STATUSES = new Set([
  "pending",
  "running",
  "completed",
  "skipped",
  "blocked",
  "failed",
  "cancelled",
  "outcome_unknown",
]);

export type AgentWorkflowEventState = {
  events: AgentWorkflowEvent[];
  lastSequence: number;
  hasGap: boolean;
};

/** Select prompted JSON when the registered provider does not expose native JSON Schema. */
export function agentStructuredOutputMode(
  provider: ProviderName,
): "native_schema" | "prompted_json" {
  return provider === "deepseek" ? "prompted_json" : "native_schema";
}

/** Validate one persisted Agent Workflow SSE block and reject mismatched projections. */
export function parseAgentWorkflowSseBlock(block: string): AgentWorkflowEvent | null {
  const eventName = block.match(/^event:\s*(.+)$/m)?.[1];
  const eventId = block.match(/^id:\s*(.+)$/m)?.[1];
  const data = block.match(/^data:\s*(.+)$/m)?.[1];
  if (!eventName || !data) return null;

  try {
    const payload = JSON.parse(data) as Partial<AgentWorkflowEvent>;
    if (
      typeof payload.event_sequence !== "number" ||
      typeof payload.event_id !== "string" ||
      typeof payload.workflow_execution_id !== "string" ||
      typeof payload.run_id !== "string" ||
      (payload.node_execution_id !== null && typeof payload.node_execution_id !== "string") ||
      typeof payload.event_type !== "string" ||
      !WORKFLOW_EVENT_TYPES.has(payload.event_type) ||
      payload.event_type !== eventName ||
      typeof payload.workflow_status !== "string" ||
      !WORKFLOW_STATUSES.has(payload.workflow_status) ||
      (payload.node_status !== null &&
        (typeof payload.node_status !== "string" || !NODE_STATUSES.has(payload.node_status))) ||
      typeof payload.public_summary !== "string" ||
      typeof payload.occurred_at !== "string" ||
      !payload.public_payload ||
      typeof payload.public_payload !== "object" ||
      Array.isArray(payload.public_payload) ||
      (payload.trace_id !== null && typeof payload.trace_id !== "string") ||
      (eventId !== undefined && Number(eventId) !== payload.event_sequence)
    ) {
      return null;
    }
    return payload as AgentWorkflowEvent;
  } catch {
    return null;
  }
}

/** Append one monotonic workflow event while retaining evidence of any missing sequence. */
export function appendAgentWorkflowEvent(
  state: AgentWorkflowEventState,
  event: AgentWorkflowEvent,
): AgentWorkflowEventState {
  if (event.event_sequence <= state.lastSequence) return state;
  return {
    events: [...state.events, event],
    lastSequence: event.event_sequence,
    hasGap: state.hasGap || event.event_sequence > state.lastSequence + 1,
  };
}

/** Read live or finite Agent Workflow SSE without treating replay EOF as completion. */
export async function readAgentWorkflowEventStream(
  response: Response,
  onEvent: (event: AgentWorkflowEvent) => Promise<void> | void,
  options: { requireTerminalEvent: boolean },
): Promise<void> {
  if (!response.body) throw new Error("Agent Workflow 响应没有可读取的流。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const seenSequences = new Set<number>();
  let buffer = "";
  let hasTerminalEvent = false;

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = done ? "" : blocks.pop() ?? "";

    for (const block of blocks) {
      const event = parseAgentWorkflowSseBlock(block);
      if (!event || seenSequences.has(event.event_sequence)) continue;
      seenSequences.add(event.event_sequence);
      if (TERMINAL_WORKFLOW_EVENTS.has(event.event_type)) hasTerminalEvent = true;
      await onEvent(event);
    }
    if (done) {
      if (options.requireTerminalEvent && !hasTerminalEvent) {
        throw new Error("Agent Workflow SSE 流在收到终止事件前结束。");
      }
      return;
    }
  }
}
