import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ModelPicker } from "../components/model-picker/ModelPicker";
import type { ProviderCatalog } from "../lib/types";

const EMPTY_CATALOG: ProviderCatalog = {
  providers: [],
  models_by_provider: {},
  reasoning_capabilities_by_provider_model: {},
};

test("distinguishes a provider catalog failure from an active load", () => {
  const loadingMarkup = renderToStaticMarkup(
    createElement(ModelPicker, {
      catalog: EMPTY_CATALOG,
      catalogState: "loading",
      selectedProvider: "",
      selectedModel: "",
      onChange: () => undefined,
      onRetry: () => undefined,
    }),
  );
  const failedMarkup = renderToStaticMarkup(
    createElement(ModelPicker, {
      catalog: EMPTY_CATALOG,
      catalogState: "error",
      selectedProvider: "",
      selectedModel: "",
      onChange: () => undefined,
      onRetry: () => undefined,
    }),
  );

  assert.match(loadingMarkup, /正在读取模型/);
  assert.match(loadingMarkup, /is-loading/);
  assert.match(loadingMarkup, /disabled=""/);
  assert.match(failedMarkup, /模型读取失败/);
  assert.match(failedMarkup, /点击重试/);
  assert.match(failedMarkup, /is-error/);
  assert.doesNotMatch(failedMarkup, /disabled=""/);
});

test("reports a completed empty catalog without pretending it is still loading", () => {
  const markup = renderToStaticMarkup(
    createElement(ModelPicker, {
      catalog: EMPTY_CATALOG,
      catalogState: "ready",
      selectedProvider: "",
      selectedModel: "",
      onChange: () => undefined,
    }),
  );

  assert.match(markup, /没有可用模型/);
  assert.match(markup, /is-empty/);
  assert.doesNotMatch(markup, /正在读取模型/);
});
