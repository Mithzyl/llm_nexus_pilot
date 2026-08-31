"use client";

import { useEffect, useRef, useState } from "react";

import type {
  ModelReasoningEffort,
  ModelReasoningSelection,
} from "../../lib/types";
import {
  availableReasoningDisplayPolicies,
  type ReasoningDisplayPolicy,
  type ReasoningPresentationCapability,
} from "../../lib/reasoning-settings";

export type { ReasoningDisplayPolicy } from "../../lib/reasoning-settings";

type ReasoningSettingsProps = {
  effort: ModelReasoningSelection;
  supportedEfforts: readonly ModelReasoningEffort[];
  supportsDisable: boolean;
  displayPolicy: ReasoningDisplayPolicy;
  presentationCapability: ReasoningPresentationCapability;
  disabled?: boolean;
  onEffortChange: (effort: ModelReasoningSelection) => void;
  onDisplayPolicyChange: (policy: ReasoningDisplayPolicy) => void;
};

const EFFORT_LABELS: Record<ModelReasoningSelection, string> = {
  disabled: "关闭",
  minimal: "最少",
  low: "较低",
  medium: "中等",
  high: "较高",
  xhigh: "极高",
  max: "最大",
};

const DISPLAY_LABELS: Record<ReasoningDisplayPolicy, string> = {
  hidden: "隐藏",
  "summary-only": "显示摘要",
  "provider-visible": "显示推理",
};

/** Render effort and presentation controls behind one stable Composer entry. */
export function ReasoningSettings({
  effort,
  supportedEfforts,
  supportsDisable,
  displayPolicy,
  presentationCapability,
  disabled = false,
  onEffortChange,
  onDisplayPolicyChange,
}: ReasoningSettingsProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const effortOptions: ModelReasoningSelection[] = [
    ...supportedEfforts,
    ...(supportsDisable ? (["disabled"] as const) : []),
  ];
  const displayPolicies = availableReasoningDisplayPolicies(presentationCapability);

  useEffect(() => {
    if (!isOpen) return;
    /** Close the settings shelf without changing the current reasoning choice. */
    const closeFromOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    };
    window.addEventListener("pointerdown", closeFromOutsidePointer);
    return () => window.removeEventListener("pointerdown", closeFromOutsidePointer);
  }, [isOpen]);

  return (
    <div className="reasoning-settings" ref={rootRef}>
      <button
        type="button"
        className="reasoning-settings-trigger"
        aria-label="思考设置"
        aria-expanded={isOpen}
        disabled={disabled || effortOptions.length === 0}
        onClick={() => setIsOpen((current) => !current)}
      >
        <span>思考</span>
        <strong>{EFFORT_LABELS[effort]}</strong>
        <i aria-hidden="true">⌄</i>
      </button>
      <div className="reasoning-settings-popover" hidden={!isOpen}>
        <section aria-labelledby="reasoning-effort-heading">
          <div className="reasoning-settings-heading">
            <strong id="reasoning-effort-heading">思考程度</strong>
            <small>按模型能力提供</small>
          </div>
          <div className="reasoning-option-list" role="listbox" aria-label="思考程度">
            {effortOptions.map((option) => (
              <button
                type="button"
                key={option}
                role="option"
                aria-selected={option === effort}
                className={option === effort ? "is-selected" : ""}
                onClick={() => onEffortChange(option)}
              >
                <span>{EFFORT_LABELS[option]}</span>
                {option === effort && <i aria-hidden="true">✓</i>}
              </button>
            ))}
          </div>
        </section>
        {displayPolicies.length > 0 && (
          <section aria-labelledby="reasoning-display-heading">
            <div className="reasoning-settings-heading">
              <strong id="reasoning-display-heading">推理展示</strong>
              <small>只控制可见内容</small>
            </div>
            <div className="reasoning-option-list" role="listbox" aria-label="推理展示">
              {displayPolicies.map((policy) => (
                <button
                  type="button"
                  key={policy}
                  role="option"
                  aria-selected={policy === displayPolicy}
                  className={policy === displayPolicy ? "is-selected" : ""}
                  onClick={() => onDisplayPolicyChange(policy)}
                >
                  <span>{DISPLAY_LABELS[policy]}</span>
                  {policy === displayPolicy && <i aria-hidden="true">✓</i>}
                </button>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
