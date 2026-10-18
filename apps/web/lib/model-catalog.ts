import type {
  ModelDirectoryEntry,
  ModelReasoningPolicy,
  ModelReasoningSelection,
  ProviderCatalog,
  ProviderName,
} from "./types";

export type ModelSelection = {
  provider: ProviderName | "";
  model: string;
};

/** Convert selectable stable model records into the existing picker projection. */
export function modelDirectoryToProviderCatalog(
  entries: ModelDirectoryEntry[],
): ProviderCatalog {
  const providers: ProviderName[] = [];
  const modelsByProvider: ProviderCatalog["models_by_provider"] = {};
  const reasoningByProviderModel: NonNullable<
    ProviderCatalog["reasoning_capabilities_by_provider_model"]
  > = {};
  const reasoningPoliciesByProviderModel: NonNullable<
    ProviderCatalog["reasoning_policies_by_provider_model"]
  > = {};
  for (const entry of entries) {
    if (!entry.selectable) continue;
    if (!providers.includes(entry.provider)) {
      providers.push(entry.provider);
      modelsByProvider[entry.provider] = [];
      reasoningByProviderModel[entry.provider] = {};
      reasoningPoliciesByProviderModel[entry.provider] = {};
    }
    modelsByProvider[entry.provider]?.push(entry.model_identifier);
    const providerReasoning = reasoningByProviderModel[entry.provider];
    if (providerReasoning) {
      providerReasoning[entry.model_identifier] = entry.reasoning_capabilities;
    }
    const providerReasoningPolicies = reasoningPoliciesByProviderModel[entry.provider];
    if (providerReasoningPolicies) {
      providerReasoningPolicies[entry.model_identifier] = {
        supported_efforts: entry.reasoning_supported_efforts,
        supports_disable: entry.reasoning_supports_disable,
        default_enabled: entry.reasoning_default_enabled,
        default_effort: entry.reasoning_default_effort,
        profile_version: entry.reasoning_profile_version,
      };
    }
  }
  return {
    providers,
    models_by_provider: modelsByProvider,
    reasoning_capabilities_by_provider_model: reasoningByProviderModel,
    reasoning_policies_by_provider_model: reasoningPoliciesByProviderModel,
  };
}

/** Keep a reasoning choice only when the newly selected model supports it. */
export function resolveModelReasoningSelection(
  policy: ModelReasoningPolicy,
  currentSelection: ModelReasoningSelection,
): ModelReasoningSelection {
  if (currentSelection === "disabled") {
    if (policy.supports_disable) return currentSelection;
  } else if (policy.supported_efforts.includes(currentSelection)) {
    return currentSelection;
  }
  return policy.default_enabled ? policy.default_effort : "disabled";
}

/** Prefer the user's available model, otherwise select the first model in the current catalog. */
export function resolveInitialModelSelection(catalog: ProviderCatalog, preferred?: ModelSelection | null): ModelSelection {
  if (preferred?.provider && catalog.models_by_provider[preferred.provider]?.includes(preferred.model)) {
    return preferred;
  }
  for (const provider of catalog.providers) {
    const firstConfiguredModel = catalog.models_by_provider[provider]?.[0];
    if (firstConfiguredModel) return { provider, model: firstConfiguredModel };
  }
  return { provider: catalog.providers[0] ?? "", model: "" };
}

/** Read a user-scoped model preference; blocked or malformed storage cannot prevent startup. */
export function readModelPreference(storage: Pick<Storage, "getItem">, userId: string): ModelSelection | null {
  try {
    const value = JSON.parse(storage.getItem(`nexuspilot:model:${encodeURIComponent(userId)}`) ?? "null");
    return value && typeof value.provider === "string" && typeof value.model === "string" ? value : null;
  } catch {
    return null;
  }
}

/** Save an explicit model choice for this user without storing credentials or conversation text. */
export function writeModelPreference(storage: Pick<Storage, "setItem">, userId: string, selection: ModelSelection): void {
  try {
    storage.setItem(`nexuspilot:model:${encodeURIComponent(userId)}`, JSON.stringify(selection));
  } catch {
    // Model selection remains usable when browser storage is unavailable.
  }
}

/** Filter the active provider's configured options with a case-insensitive model query. */
export function filterProviderModels(
  catalog: ProviderCatalog,
  provider: ProviderName | "",
  query: string,
): string[] {
  if (!provider) return [];
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const models = catalog.models_by_provider[provider] ?? [];
  if (!normalizedQuery) return models;
  return models.filter((model) => model.toLocaleLowerCase().includes(normalizedQuery));
}

/** Check a selection against the allowlist; an empty list means the provider is unrestricted. */
export function isModelSelectionAllowed(
  catalog: ProviderCatalog,
  provider: ProviderName | "",
  model: string,
): boolean {
  const normalizedModel = model.trim();
  if (!provider || !normalizedModel || !catalog.providers.includes(provider)) return false;
  const configuredModels = catalog.models_by_provider[provider];
  if (!configuredModels) return false;
  return configuredModels.length === 0 || configuredModels.includes(normalizedModel);
}
