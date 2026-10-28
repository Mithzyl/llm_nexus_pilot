/** Return a readable account mark without storing any account data. */
export function personalAccountInitial(displayName: string): string {
  const normalized = displayName.trim();
  return normalized ? Array.from(normalized)[0].toUpperCase() : "N";
}

/** Translate product-memory failures without presenting internal codes as UI copy. */
export function memoryErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "";
  const labels: Record<string, string> = {
    memory_disabled: "记忆已关闭，请先开启后再新增或确认。",
    memory_confirmation_required: "此内容需要你明确确认敏感信息保存。",
    memory_version_conflict: "这条记忆或设置已改变，请刷新后再操作。",
    memory_request_conflict: "此操作的提交标识已使用，请重新确认内容。",
    memory_key_unavailable: "记忆存储尚未配置完成，请联系管理员。",
    memory_storage_unavailable: "记忆暂时无法保存，请重试。",
    memory_content_unavailable: "记忆内容暂时无法读取，请重试。",
    memory_not_found: "这条记忆已不存在或已被忘记。",
    memory_scope_capacity: "当前记忆数量已达到上限，请先整理已有内容。",
    memory_credentials_forbidden: "请不要将密码、密钥或访问令牌保存为记忆。",
  };
  return labels[message] ?? "记忆操作未完成，请刷新确认状态后重试。";
}
