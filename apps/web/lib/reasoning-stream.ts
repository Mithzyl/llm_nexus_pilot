import type { ReasoningPresentation, StreamEvent } from "./types";

/** Validate untrusted history or event JSON before it reaches the exhaustive renderer. */
export function isReasoningPresentation(value: unknown): value is ReasoningPresentation {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  if (typeof candidate.block_id !== "string" || !candidate.block_id) return false;
  if (!["running", "completed", "interrupted"].includes(String(candidate.status))) {
    return false;
  }
  if (candidate.kind === "reasoning.status") return candidate.text == null;
  if (candidate.kind === "reasoning.raw" || candidate.kind === "reasoning.summary") {
    return typeof candidate.text === "string" && candidate.text.length > 0;
  }
  return false;
}

/** Apply one sanitized stream event to the current authoritative reasoning projection. */
export function applyReasoningStreamEvent(
  currentBlocks: ReasoningPresentation[],
  event: StreamEvent,
): ReasoningPresentation[] {
  if (event.type === "reasoning.started" && event.data.block_id) {
    if (currentBlocks.some((block) => block.block_id === event.data.block_id)) {
      return currentBlocks;
    }
    return [
      ...currentBlocks,
      {
        kind: "reasoning.status",
        block_id: event.data.block_id,
        status: "running",
      },
    ];
  }

  if (
    (event.type === "reasoning.raw.delta" ||
      event.type === "reasoning.summary.delta") &&
    event.data.block_id &&
    event.data.delta
  ) {
    const targetKind =
      event.type === "reasoning.raw.delta" ? "reasoning.raw" : "reasoning.summary";
    const existingBlock = currentBlocks.find(
      (block) => block.block_id === event.data.block_id,
    );
    const previousText =
      existingBlock?.kind === targetKind ? existingBlock.text : "";
    const nextBlock: ReasoningPresentation = {
      kind: targetKind,
      block_id: event.data.block_id,
      status: "running",
      text: previousText + event.data.delta,
      reasoning_tokens: existingBlock?.reasoning_tokens,
    };
    return replaceReasoningBlock(currentBlocks, nextBlock);
  }

  if (
    (event.type === "reasoning.completed" ||
      event.type === "reasoning.interrupted") &&
    isReasoningPresentation(event.data.block)
  ) {
    return replaceReasoningBlock(currentBlocks, event.data.block);
  }

  if (event.type === "response.completed" && event.data.response) {
    return event.data.response.reasoning_blocks.filter((block) =>
      isReasoningPresentation(block),
    );
  }
  return currentBlocks;
}

/** Return the compact live or stable preview shown while a reasoning block is collapsed. */
export function reasoningPreview(presentation: ReasoningPresentation): string | null {
  if (presentation.kind === "reasoning.status") return null;
  const nonEmptyLines = presentation.text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (nonEmptyLines.length === 0) return null;
  return presentation.status === "running"
    ? nonEmptyLines[nonEmptyLines.length - 1]
    : nonEmptyLines[0];
}

/** Replace one block by ID while preserving the Provider event order of all other blocks. */
function replaceReasoningBlock(
  currentBlocks: ReasoningPresentation[],
  nextBlock: ReasoningPresentation,
): ReasoningPresentation[] {
  const blockIndex = currentBlocks.findIndex(
    (block) => block.block_id === nextBlock.block_id,
  );
  if (blockIndex < 0) return [...currentBlocks, nextBlock];
  return currentBlocks.map((block, index) =>
    index === blockIndex ? nextBlock : block,
  );
}
