import type {
  ModelReasoningSelection,
  ReasoningConfiguration,
} from "./types";

/** Convert the Composer selection into the provider-neutral backend contract. */
export function createReasoningConfiguration(
  selection: ModelReasoningSelection,
): ReasoningConfiguration {
  return selection === "disabled"
    ? { enabled: false }
    : { enabled: true, effort: selection };
}
