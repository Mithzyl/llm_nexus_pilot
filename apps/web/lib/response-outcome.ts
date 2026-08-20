/** Identify the normalized Provider outcome that means the answer hit its output limit. */
export function isResponseOutputTruncated(finishReason: string | null | undefined): boolean {
  return finishReason === "length";
}

/**
 * Build durable Message metadata so a truncated response remains identifiable
 * after navigation or a full page refresh.
 */
export function responseMessageMetadata(
  finishReason: string | null | undefined,
  requestedMaxOutputTokens?: number,
): Record<string, unknown> {
  return {
    model_response_finish_reason: finishReason ?? "unknown",
    ...(requestedMaxOutputTokens === undefined
      ? {}
      : { requested_max_output_tokens: requestedMaxOutputTokens }),
  };
}

/** Read the persisted response outcome without trusting unrelated metadata values. */
export function isPersistedResponseOutputTruncated(
  metadata: Record<string, unknown> | null | undefined,
): boolean {
  return metadata?.model_response_finish_reason === "length";
}
