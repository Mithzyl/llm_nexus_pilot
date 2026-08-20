import type { StreamEvent } from "./types";

type ModelResponseEventPage = {
  items: StreamEvent[];
  has_more: boolean;
};

type ReplayStreamOptions = {
  afterSequence: number;
  timeoutMs?: number;
  pollIntervalMs?: number;
};

export class StreamSequenceGapError extends Error {
  lastSequence: number;
  receivedSequence: number;

  /** Describe the exact acknowledged and received sequence around one stream gap. */
  constructor(lastSequence: number, receivedSequence: number) {
    super(`SSE 事件序号存在缺口：已确认 ${lastSequence}，收到 ${receivedSequence}。`);
    this.name = "StreamSequenceGapError";
    this.lastSequence = lastSequence;
    this.receivedSequence = receivedSequence;
  }
}

/**
 * Decode one complete Server-Sent Event block into the normalized API event shape.
 */
export function parseSseBlock(block: string): StreamEvent | null {
  const eventName = block.match(/^event:\s*(.+)$/m)?.[1];
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!eventName || !data) return null;
  const payload = JSON.parse(data) as Omit<StreamEvent, "type">;
  return { ...payload, type: eventName };
}

/**
 * Read normalized SSE events, preserving order and ignoring duplicate sequence numbers.
 * A clean stream must contain a terminal completed or failed event; otherwise
 * callers receive an interruption error instead of mistaking EOF for success.
 */
export async function readStreamEvents(
  response: Response,
  onEvent: (event: StreamEvent) => Promise<void> | void,
): Promise<void> {
  if (!response.body) throw new Error("响应没有可读取的流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let lastSequence = 0;
  let hasTerminalEvent = false;
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = done ? "" : blocks.pop() ?? "";

    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (!event || event.sequence <= lastSequence) continue;
      if (lastSequence > 0 && event.sequence !== lastSequence + 1) {
        throw new StreamSequenceGapError(lastSequence, event.sequence);
      }
      lastSequence = event.sequence;
      if (event.type === "response.completed" || event.type === "response.failed") {
        hasTerminalEvent = true;
      }
      await onEvent(event);
    }
    if (done) {
      if (!hasTerminalEvent) throw new Error("SSE 流在收到终止事件前结束。");
      break;
    }
  }
}

/**
 * Recover a disconnected response from committed event pages until a terminal
 * event is observed. Duplicate pages are harmless, while a durable sequence
 * gap is rejected so the UI never presents an incomplete response as final.
 */
export async function replayCommittedStreamEvents(
  loadPage: (afterSequence: number) => Promise<ModelResponseEventPage>,
  onEvent: (event: StreamEvent) => Promise<void> | void,
  options: ReplayStreamOptions,
): Promise<number> {
  const timeoutMs = options.timeoutMs ?? 65_000;
  const pollIntervalMs = options.pollIntervalMs ?? 750;
  const deadlineAt = Date.now() + timeoutMs;
  let lastSequence = options.afterSequence;

  while (Date.now() <= deadlineAt) {
    const page = await loadPage(lastSequence);
    let hasTerminalEvent = false;
    for (const event of page.items) {
      if (event.sequence <= lastSequence) continue;
      if (event.sequence !== lastSequence + 1) {
        throw new StreamSequenceGapError(lastSequence, event.sequence);
      }
      await onEvent(event);
      lastSequence = event.sequence;
      if (event.type === "response.completed" || event.type === "response.failed") {
        hasTerminalEvent = true;
      }
    }
    if (hasTerminalEvent) return lastSequence;
    if (page.has_more) continue;
    await new Promise<void>((resolve) => window.setTimeout(resolve, pollIntervalMs));
  }

  throw new Error("响应事件回放超时，运行详情仍以服务端状态为准。");
}
