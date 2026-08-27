import type { ModelDirectoryEntry, ProviderCatalog, ProviderName } from "./types";

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
  for (const entry of entries) {
    if (!entry.selectable) continue;
    if (!providers.includes(entry.provider)) {
      providers.push(entry.provider);
      modelsByProvider[entry.provider] = [];
      reasoningByProviderModel[entry.provider] = {};
    }
    modelsByProvider[entry.provider]?.push(entry.model_identifier);
    const providerReasoning = reasoningByProviderModel[entry.provider];
    if (providerReasoning) {
      providerReasoning[entry.model_identifier] = entry.reasoning_capabilities;
    }
  }
  return {
    providers,
    models_by_provider: modelsByProvider,
    reasoning_capabilities_by_provider_model: reasoningByProviderModel,
  };
}

/** Resolve a usable first selection without inventing models outside the server catalog. */
export function resolveInitialModelSelection(catalog: ProviderCatalog): ModelSelection {
  for (const provider of catalog.providers) {
    const firstConfiguredModel = catalog.models_by_provider[provider]?.[0];
    if (firstConfiguredModel) return { provider, model: firstConfiguredModel };
  }
  return { provider: catalog.providers[0] ?? "", model: "" };
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
