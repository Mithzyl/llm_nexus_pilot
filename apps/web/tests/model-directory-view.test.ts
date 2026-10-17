import assert from "node:assert/strict";
import test from "node:test";

import {
  modelAvailabilityLabel,
  modelCapabilityLabels,
  modelReasoningPolicyLabels,
} from "../lib/model-directory-view";
import type { ModelDirectoryEntry } from "../lib/types";

const model: ModelDirectoryEntry = {
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
  supports_tools: false,
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

test("presents only server-confirmed model availability and capabilities", () => {
  assert.equal(modelAvailabilityLabel(model), "可用于对话");
  assert.deepEqual(modelCapabilityLabels(model), ["流式回复", "结构化输出", "输入缓存"]);
  assert.deepEqual(modelReasoningPolicyLabels(model), {
    defaultLabel: "中等",
    supportedLabel: "极简 / 中等 / 很高",
    disableLabel: "支持关闭",
  });
  assert.equal(
    modelAvailabilityLabel({ ...model, selectable: false, unavailable_reason: "capability_unknown" }),
    "能力尚未确认",
  );
});

test("does not label provider defaults as medium or forced thinking", () => {
  assert.deepEqual(modelReasoningPolicyLabels({
    ...model, provider: "strata", reasoning_supported_efforts: [], reasoning_supports_disable: false,
  }), { defaultLabel: "服务默认", supportedLabel: "不支持调节", disableLabel: "未提供开关" });
});
