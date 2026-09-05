import assert from "node:assert/strict";
import test from "node:test";

import {
  clearConversationDraft,
  conversationDraftStorageKey,
  readConversationDraft,
  writeConversationDraft,
} from "../lib/conversation-drafts";

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); }
  removeItem(key: string) { this.values.delete(key); }
}

test("keeps new and persisted conversation drafts in separate tab-local keys", () => {
  assert.equal(conversationDraftStorageKey(null), "nexuspilot:draft:new");
  assert.equal(
    conversationDraftStorageKey("session/with space"),
    "nexuspilot:draft:session%2Fwith%20space",
  );
});

test("persists, bounds, and clears unsent drafts without retaining empty values", () => {
  const storage = new MemoryStorage();
  const key = conversationDraftStorageKey("session-1");
  writeConversationDraft(storage, key, "unfinished question");
  assert.equal(readConversationDraft(storage, key), "unfinished question");
  writeConversationDraft(storage, key, "x".repeat(100_100));
  assert.equal(readConversationDraft(storage, key).length, 100_000);
  clearConversationDraft(storage, key);
  assert.equal(readConversationDraft(storage, key), "");
});

test("does not restore another account's draft in the same browser tab", () => {
  const storage = new MemoryStorage();
  writeConversationDraft(storage, conversationDraftStorageKey(null, "alice"), "Alice private draft");
  assert.equal(readConversationDraft(storage, conversationDraftStorageKey(null, "bob")), "");
  assert.equal(readConversationDraft(storage, conversationDraftStorageKey(null, "alice")), "Alice private draft");
});
