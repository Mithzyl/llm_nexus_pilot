import type { PromptOption } from "../../lib/types";

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

/** Render optional Prompt binding controls without exposing Prompt source text or file IDs. */
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
  const selectedPrompt =
    promptOptions.find((prompt) => prompt.template_name === selectedPromptName) ?? null;
  const bindingCount = selectedPrompt ? 1 : 0;

  return (
    <details className="context-bindings">
      <summary>
        <span>上下文</span>
        <small>{bindingCount > 0 ? `已选 ${bindingCount}` : "可选"}</small>
      </summary>
      <div className="context-binding-body">
        <label className="context-binding-field">
          <span>Prompt</span>
          <select
            value={selectedPromptName}
            disabled={disabled || promptOptionsState !== "ready"}
            onChange={(event) => onPromptChange(event.target.value)}
          >
            <option value="">
              {promptOptionsState === "loading"
                ? "正在读取…"
                : promptOptionsState === "error"
                  ? "暂不可用"
                  : "不使用 Prompt"}
            </option>
            {promptOptions.map((prompt) => (
              <option key={prompt.template_name} value={prompt.template_name}>
                {prompt.template_name} · v{prompt.current_version_number}
              </option>
            ))}
          </select>
        </label>
        {promptOptionsState === "error" && (
          <button className="context-binding-retry" type="button" onClick={onRetry}>
            重试
          </button>
        )}
        {selectedPrompt && Object.keys(selectedPrompt.variable_schema).length > 0 && (
          <div className="context-variable-list">
            {Object.entries(selectedPrompt.variable_schema).map(([variableName, variableType]) => (
              <label className="context-binding-field" key={variableName}>
                <span>{variableName}</span>
                {variableType === "boolean" ? (
                  <select
                    value={promptVariableDrafts[variableName] ?? ""}
                    disabled={disabled}
                    onChange={(event) => onVariableChange(variableName, event.target.value)}
                  >
                    <option value="">请选择</option>
                    <option value="true">是</option>
                    <option value="false">否</option>
                  </select>
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
          <span>文件</span>
          <small>等待上传功能</small>
        </div>
      </div>
    </details>
  );
}
