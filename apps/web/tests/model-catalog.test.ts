import assert from "node:assert/strict";
import test from "node:test";

import {
  filterProviderModels,
  isModelSelectionAllowed,
  modelDirectoryToProviderCatalog,
  resolveModelReasoningSelection,
  resolveInitialModelSelection,
} from "../lib/model-catalog";
import type { ModelDirectoryEntry, ProviderCatalog } from "../lib/types";

const catalog: ProviderCatalog = {
  providers: ["deepseek", "openai_compatible"],
  models_by_provider: {
    deepseek: ["deepseek-v4-flash", "deepseek-v4-pro"],
    openai_compatible: [],
  },
};

test("selects the first configured provider model from the server catalog", () => {
  assert.deepEqual(resolveInitialModelSelection(catalog), {
    provider: "deepseek",
    model: "deepseek-v4-flash",
  });
});

test("filters only the active provider's configured model options", () => {
  assert.deepEqual(filterProviderModels(catalog, "deepseek", "PRO"), [
    "deepseek-v4-pro",
  ]);
  assert.deepEqual(filterProviderModels(catalog, "openai_compatible", "anything"), []);
});

test("treats an empty allowlist as an unrestricted custom-model provider", () => {
  assert.equal(
    isModelSelectionAllowed(catalog, "openai_compatible", "local-model"),
    true,
  );
  assert.equal(isModelSelectionAllowed(catalog, "deepseek", "unknown-model"), false);
  assert.equal(isModelSelectionAllowed(catalog, "", "deepseek-v4-flash"), false);
  assert.equal(
    isModelSelectionAllowed(
      { providers: ["deepseek"], models_by_provider: {} },
      "deepseek",
      "unknown-model",
    ),
    false,
  );
});

test("builds selectable provider options only from stable available model records", () => {
  const baseEntry: ModelDirectoryEntry = {
    model_id: "model-1",
    provider: "deepseek",
    model_identifier: "deepseek-v4-flash",
    display_name: "DeepSeek V4 Flash",
    description: null,
    status: "enabled",
    sort_order: 10,
    metadata_json: {},
    registry_available: true,
    selectable: true,
    unavailable_reason: null,
    catalog_version_id: "catalog-1",
    catalog_version: "2026-08-27",
    context_window: 65_536,
    supports_tools: true,
    supports_structured_output: true,
    supports_vision: false,
    supports_caching: true,
    supports_streaming: true,
    tokenizer_name: "deepseek",
    tokenizer_version: "v1",
    reasoning_capabilities: {
      presentation: "raw",
      supports_streaming_presentation: true,
      continuation: "raw-reasoning-replay",
      supports_reasoning_tokens: true,
    },
    reasoning_supported_efforts: ["minimal", "medium", "xhigh"],
    reasoning_supports_disable: true,
    reasoning_default_enabled: true,
    reasoning_default_effort: "medium",
    reasoning_profile_version: "reasoning-policy.v1",
  };
  const unavailableEntry: ModelDirectoryEntry = {
    ...baseEntry,
    model_id: "model-2",
    model_identifier: "deepseek-v4-pro",
    selectable: false,
    unavailable_reason: "capability_unknown",
    catalog_version_id: null,
    catalog_version: null,
  };

  assert.deepEqual(modelDirectoryToProviderCatalog([baseEntry, unavailableEntry]), {
    providers: ["deepseek"],
    models_by_provider: { deepseek: ["deepseek-v4-flash"] },
    reasoning_capabilities_by_provider_model: {
      deepseek: {
        "deepseek-v4-flash": baseEntry.reasoning_capabilities,
      },
    },
    reasoning_policies_by_provider_model: {
      deepseek: {
        "deepseek-v4-flash": {
          supported_efforts: ["minimal", "medium", "xhigh"],
          supports_disable: true,
          default_enabled: true,
          default_effort: "medium",
          profile_version: "reasoning-policy.v1",
        },
      },
    },
  });
});

test("uses the registered model default when the previous reasoning choice is unsupported", () => {
  const policy = {
    supported_efforts: ["minimal", "medium", "xhigh"] as const,
    supports_disable: false,
    default_enabled: true,
    default_effort: "medium" as const,
    profile_version: "reasoning-policy.v1",
  };

  assert.equal(resolveModelReasoningSelection(policy, "high"), "medium");
  assert.equal(resolveModelReasoningSelection(policy, "minimal"), "minimal");
  assert.equal(resolveModelReasoningSelection(policy, "disabled"), "medium");
});

test("preserves disabled reasoning only for models that explicitly support it", () => {
  const policy = {
    supported_efforts: ["medium"] as const,
    supports_disable: true,
    default_enabled: false,
    default_effort: "medium" as const,
    profile_version: "reasoning-policy.v1",
  };

  assert.equal(resolveModelReasoningSelection(policy, "disabled"), "disabled");
  assert.equal(resolveModelReasoningSelection(policy, "xhigh"), "disabled");
});
