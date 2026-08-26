import assert from "node:assert/strict";
import test from "node:test";

import {
  filterProviderModels,
  isModelSelectionAllowed,
  resolveInitialModelSelection,
} from "../lib/model-catalog";
import type { ProviderCatalog } from "../lib/types";

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
