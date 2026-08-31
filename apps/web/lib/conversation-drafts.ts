const CONVERSATION_DRAFT_KEY_PREFIX = "nexuspilot:draft:";
const MAX_CONVERSATION_DRAFT_CHARACTERS = 100_000;

type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** Build the tab-local storage key for a persisted conversation or the new-conversation screen. */
export function conversationDraftStorageKey(sessionId: string | null): string {
  return `${CONVERSATION_DRAFT_KEY_PREFIX}${sessionId ? encodeURIComponent(sessionId) : "new"}`;
}

/** Read a bounded unsent draft; malformed or unavailable storage is treated as an empty draft. */
export function readConversationDraft(storage: DraftStorage, storageKey: string): string {
  try {
    return (storage.getItem(storageKey) ?? "").slice(0, MAX_CONVERSATION_DRAFT_CHARACTERS);
  } catch {
    return "";
  }
}

/** Persist one unsent draft in tab-local session storage without retaining empty values. */
export function writeConversationDraft(
  storage: DraftStorage,
  storageKey: string,
  draft: string,
): void {
  try {
    if (!draft) {
      storage.removeItem(storageKey);
      return;
    }
    storage.setItem(storageKey, draft.slice(0, MAX_CONVERSATION_DRAFT_CHARACTERS));
  } catch {
    // Draft persistence is a convenience; storage denial must not block the Composer.
  }
}

/** Remove a draft only after its user message has been durably committed. */
export function clearConversationDraft(storage: DraftStorage, storageKey: string): void {
  try {
    storage.removeItem(storageKey);
  } catch {
    // Keep the durable turn successful even when browser storage cleanup is unavailable.
  }
}
