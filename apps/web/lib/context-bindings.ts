import type {
  PromptOption,
  PromptVariableType,
  PromptVariableValue,
  RuntimeContextSourceReferences,
} from "./types";

export class ContextBindingValidationError extends Error {
  /** Create one user-correctable Prompt variable validation error. */
  constructor(message: string) {
    super(message);
    this.name = "ContextBindingValidationError";
  }
}

/** Convert one text control value into the Prompt version's declared scalar type. */
function parsePromptVariableValue(
  variableName: string,
  variableType: PromptVariableType,
  rawValue: string | undefined,
): PromptVariableValue {
  if (rawValue === undefined) {
    throw new ContextBindingValidationError(`请填写 Prompt 变量：${variableName}`);
  }
  if (variableType === "string") return rawValue;
  if (variableType === "boolean") {
    if (rawValue === "true") return true;
    if (rawValue === "false") return false;
    throw new ContextBindingValidationError(`Prompt 变量 ${variableName} 必须是布尔值。`);
  }
  if (variableType === "integer") {
    if (!/^-?\d+$/.test(rawValue)) {
      throw new ContextBindingValidationError(`Prompt 变量 ${variableName} 必须是整数。`);
    }
    const integerValue = Number(rawValue);
    if (!Number.isSafeInteger(integerValue)) {
      throw new ContextBindingValidationError(`Prompt 变量 ${variableName} 超出安全整数范围。`);
    }
    return integerValue;
  }
  const numberValue = Number(rawValue);
  if (!Number.isFinite(numberValue) || rawValue.trim() === "") {
    throw new ContextBindingValidationError(`Prompt 变量 ${variableName} 必须是数字。`);
  }
  return numberValue;
}

/** Build stable Context source references without rendering Prompt text in the browser. */
export function createContextSourceReferences(
  selectedPrompt: PromptOption | null,
  promptVariableDrafts: Record<string, string>,
  artifactIds: string[] = [],
): RuntimeContextSourceReferences {
  if (!selectedPrompt) return { artifact_ids: [...artifactIds] };
  const variables = Object.fromEntries(
    Object.entries(selectedPrompt.variable_schema).map(([variableName, variableType]) => [
      variableName,
      parsePromptVariableValue(
        variableName,
        variableType,
        promptVariableDrafts[variableName],
      ),
    ]),
  );
  return {
    prompt: {
      template_name: selectedPrompt.template_name,
      version_number: selectedPrompt.current_version_number,
      variables,
    },
    artifact_ids: [...artifactIds],
  };
}
