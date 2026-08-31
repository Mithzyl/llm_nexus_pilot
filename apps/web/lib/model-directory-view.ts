import type { ModelDirectoryEntry, ModelUnavailableReason } from "./types";

const UNAVAILABLE_REASON_LABELS: Record<ModelUnavailableReason, string> = {
  model_disabled: "平台已停用",
  provider_unavailable: "Provider 当前不可用",
  model_not_allowed: "当前环境未允许",
  capability_unknown: "能力尚未确认",
  streaming_unsupported: "不支持流式回复",
  structured_output_unsupported: "不支持结构化输出",
  tools_unsupported: "不支持工具调用",
  vision_unsupported: "不支持视觉输入",
  reasoning_profile_invalid: "思考策略无效",
};

const REASONING_EFFORT_LABELS = {
  minimal: "极简",
  low: "较低",
  medium: "中等",
  high: "较高",
  xhigh: "很高",
  max: "最高",
} as const;

/** Convert a model availability fact into a stable, non-sensitive user-facing label. */
export function modelAvailabilityLabel(model: ModelDirectoryEntry): string {
  if (model.selectable) return "可用于对话";
  if (model.unavailable_reason) return UNAVAILABLE_REASON_LABELS[model.unavailable_reason];
  return "当前不可选择";
}

/** List only catalog-confirmed capabilities for the read-only model settings page. */
export function modelCapabilityLabels(model: ModelDirectoryEntry): string[] {
  const capabilities: string[] = [];
  if (model.supports_streaming) capabilities.push("流式回复");
  if (model.supports_structured_output) capabilities.push("结构化输出");
  if (model.supports_tools) capabilities.push("工具调用");
  if (model.supports_vision) capabilities.push("视觉输入");
  if (model.supports_caching) capabilities.push("输入缓存");
  return capabilities;
}

/** Translate the portable reasoning policy into concise read-only directory facts. */
export function modelReasoningPolicyLabels(model: ModelDirectoryEntry): {
  defaultLabel: string;
  supportedLabel: string;
  disableLabel: string;
} {
  return {
    defaultLabel: model.reasoning_default_enabled
      ? REASONING_EFFORT_LABELS[model.reasoning_default_effort]
      : "默认关闭",
    supportedLabel: model.reasoning_supported_efforts.length > 0
      ? model.reasoning_supported_efforts
          .map((effort) => REASONING_EFFORT_LABELS[effort])
          .join(" / ")
      : "不支持调节",
    disableLabel: model.reasoning_supports_disable ? "支持关闭" : "必须启用",
  };
}
