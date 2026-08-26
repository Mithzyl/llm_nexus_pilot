import type { ProviderCatalog, ProviderName } from "./types";

export type ModelSelection = {
  provider: ProviderName | "";
  model: string;
};

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
