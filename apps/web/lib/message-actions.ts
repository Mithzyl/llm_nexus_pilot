type ConversationMessage = {
  local_id: string;
  role: string;
  content: string;
};

/** Find the user prompt immediately preceding one Assistant message in immutable history. */
export function repeatableUserPrompt(
  messages: readonly ConversationMessage[],
  assistantMessageId: string,
): string | null {
  const assistantIndex = messages.findIndex((message) => message.local_id === assistantMessageId);
  if (assistantIndex < 0) return null;
  for (let index = assistantIndex - 1; index >= 0; index -= 1) {
    if (messages[index].role === "user") return messages[index].content;
  }
  return null;
}
