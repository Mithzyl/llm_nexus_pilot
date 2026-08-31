import type {
  ModelReasoningCapabilities,
  ModelReasoningSelection,
  ReasoningConfiguration,
} from "./types";

export type ReasoningDisplayPolicy = "hidden" | "summary-only" | "provider-visible";
export type ReasoningPresentationCapability = ModelReasoningCapabilities["presentation"];

const DISPLAY_POLICIES_BY_PRESENTATION: Record<
  ReasoningPresentationCapability,
  readonly ReasoningDisplayPolicy[]
> = {
  raw: ["hidden", "provider-visible"],
  summary: ["hidden", "summary-only"],
  hidden: [],
  none: [],
};

/** Convert the Composer selection into the provider-neutral backend contract. */
export function createReasoningConfiguration(
  selection: ModelReasoningSelection,
): ReasoningConfiguration {
  return selection === "disabled"
    ? { enabled: false }
    : { enabled: true, effort: selection };
}

/** Return the display policies that have truthful visible behavior for one model capability. */
export function availableReasoningDisplayPolicies(
  presentation: ReasoningPresentationCapability,
): readonly ReasoningDisplayPolicy[] {
  return DISPLAY_POLICIES_BY_PRESENTATION[presentation];
}

/** Choose the most informative safe Web default supported by one model capability. */
export function defaultReasoningDisplayPolicy(
  presentation: ReasoningPresentationCapability,
): ReasoningDisplayPolicy {
  if (presentation === "raw") return "provider-visible";
  if (presentation === "summary") return "summary-only";
  return "hidden";
}

/**
 * Resolve an optional user preference for a new model without exposing more detail than requested.
 * A raw preference may degrade to a summary, while a summary-only preference never upgrades to raw.
 */
export function resolveReasoningDisplayPolicy(
  presentation: ReasoningPresentationCapability,
  preferredPolicy: ReasoningDisplayPolicy | null,
): ReasoningDisplayPolicy {
  if (preferredPolicy === null) return defaultReasoningDisplayPolicy(presentation);
  if (preferredPolicy === "hidden") return "hidden";
  const availablePolicies = availableReasoningDisplayPolicies(presentation);
  if (availablePolicies.includes(preferredPolicy)) return preferredPolicy;
  if (preferredPolicy === "provider-visible" && presentation === "summary") {
    return "summary-only";
  }
  return "hidden";
}
