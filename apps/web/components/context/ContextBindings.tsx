"use client";

import { useEffect, useRef, useState } from "react";

import type { PromptOption } from "../../lib/types";
import { CompactSelect } from "../composer/CompactSelect";

type ContextBindingsProps = {
  promptOptions: PromptOption[];
  promptOptionsState: "loading" | "ready" | "error";
  selectedPromptName: string;
  promptVariableDrafts: Record<string, string>;
  disabled: boolean;
  onPromptChange: (templateName: string) => void;
  onVariableChange: (variableName: string, value: string) => void;
  onRetry: () => void;
};

type SelectedPromptChipProps = {
  prompt: PromptOption;
  disabled: boolean;
  onRemove: () => void;
};

/** Render the selected Prompt as a removable context chip below the message draft. */
export function SelectedPromptChip({
  prompt,
  disabled,
  onRemove,
}: SelectedPromptChipProps) {
  return (
    <div className="context-chip">
      <span>已选 Prompt</span>
      <strong>{prompt.template_name}</strong>
      <small>v{prompt.current_version_number}</small>
      <button
        type="button"
        aria-label={`移除 Prompt ${prompt.template_name}`}
        disabled={disabled}
        onClick={onRemove}
      >
        ×
      </button>
    </div>
  );
}

/** Render the toolbar context trigger and the product-safe Prompt binding popover. */
export function ContextBindings({
  promptOptions,
  promptOptionsState,
  selectedPromptName,
  promptVariableDrafts,
  disabled,
  onPromptChange,
  onVariableChange,
  onRetry,
}: ContextBindingsProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const selectedPrompt =
    promptOptions.find((prompt) => prompt.template_name === selectedPromptName) ?? null;

  useEffect(() => {
    if (!isOpen) return;
    /** Close the drawer after a pointer interaction outside the context controls. */
    const closeFromOutsidePointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    };
    window.addEventListener("pointerdown", closeFromOutsidePointer);
    return () => window.removeEventListener("pointerdown", closeFromOutsidePointer);
  }, [isOpen]);

  return (
    <div className="context-bindings" ref={rootRef}>
      <button
        type="button"
        className="context-binding-trigger"
        aria-label="上下文设置"
        aria-expanded={isOpen}
        disabled={disabled}
        onClick={() => setIsOpen((current) => !current)}
      >
        <span aria-hidden="true">＋</span>
        上下文
        {selectedPrompt && <i aria-hidden="true">1</i>}
      </button>

      <div className="context-binding-popover" hidden={!isOpen}>
        <div className="context-binding-heading">
          <div>
            <strong>添加上下文</strong>
            <small>选择一个已启用 Prompt</small>
          </div>
          <button type="button" aria-label="关闭上下文设置" onClick={() => setIsOpen(false)}>
            ×
          </button>
        </div>

        {promptOptionsState === "loading" && (
          <p className="context-binding-state" role="status">正在读取 Prompt…</p>
        )}
        {promptOptionsState === "error" && (
          <div className="context-binding-state" role="alert">
            <span>Prompt 暂不可用</span>
            <button type="button" onClick={onRetry}>重试</button>
          </div>
        )}
        <div className="context-prompt-list" role="listbox" aria-label="Prompt">
          {promptOptions.map((prompt) => (
            <button
              type="button"
              role="option"
              key={prompt.template_name}
              aria-selected={prompt.template_name === selectedPromptName}
              className={prompt.template_name === selectedPromptName ? "is-selected" : ""}
              disabled={disabled}
              onClick={() => onPromptChange(prompt.template_name)}
            >
              <span className="context-source-icon" aria-hidden="true">P</span>
              <span className="context-source-copy">
                <strong>{prompt.template_name} · v{prompt.current_version_number}</strong>
                <small>{prompt.purpose ?? "已启用 Prompt"}</small>
              </span>
              {prompt.template_name === selectedPromptName && <i aria-hidden="true">✓</i>}
            </button>
          ))}
        </div>

        {selectedPrompt && Object.keys(selectedPrompt.variable_schema).length > 0 && (
          <div className="context-variable-list">
            {Object.entries(selectedPrompt.variable_schema).map(([variableName, variableType]) => (
              <label className="context-binding-field" key={variableName}>
                <span>{variableName}</span>
                {variableType === "boolean" ? (
                  <CompactSelect
                    label={`${variableName} 布尔值`}
                    value={promptVariableDrafts[variableName] ?? ""}
                    options={[
                      { value: "", label: "请选择" },
                      { value: "true", label: "是" },
                      { value: "false", label: "否" },
                    ]}
                    disabled={disabled}
                    onChange={(value) => onVariableChange(variableName, value)}
                  />
                ) : (
                  <input
                    type={variableType === "string" ? "text" : "number"}
                    step={variableType === "integer" ? "1" : "any"}
                    value={promptVariableDrafts[variableName] ?? ""}
                    disabled={disabled}
                    onChange={(event) => onVariableChange(variableName, event.target.value)}
                  />
                )}
              </label>
            ))}
          </div>
        )}

        <div className="context-file-status" aria-disabled="true">
          <span className="context-source-icon" aria-hidden="true">F</span>
          <span className="context-source-copy">
            <strong>文件</strong>
            <small>等待上传功能</small>
          </span>
          <i aria-hidden="true">—</i>
        </div>
      </div>
    </div>
  );
}
