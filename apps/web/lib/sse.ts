import type { StreamEvent } from "./types";

/**
 * Decode one complete Server-Sent Event block into the normalized API event shape.
 */
export function parseSseBlock(block: string): StreamEvent | null {
  const eventName = block.match(/^event:\s*(.+)$/m)?.[1];
  const data = block.match(/^data:\s*(.+)$/m)?.[1];
  if (!eventName || !data) return null;
  const payload = JSON.parse(data) as Omit<StreamEvent, "type">;
  return { ...payload, type: eventName };
}

/**
 * Read normalized SSE events, preserving order and ignoring duplicate sequence numbers.
 */
export async function readStreamEvents(
  response: Response,
  onEvent: (event: StreamEvent) => Promise<void> | void,
): Promise<void> {
  if (!response.body) throw new Error("响应没有可读取的流");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const seenSequences = new Set<number>();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = done ? "" : blocks.pop() ?? "";

    for (const block of blocks) {
      const event = parseSseBlock(block);
      if (!event || seenSequences.has(event.sequence)) continue;
      seenSequences.add(event.sequence);
      await onEvent(event);
    }
    if (done) break;
  }
}
