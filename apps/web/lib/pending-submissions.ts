import type { AgentWorkflowCreate } from "./types";

export type PendingSubmission = {
  version: 1; id: string; owner: string; sessionId: string | null;
  sessionKey: string; turnKey: string; input: string; fileIds: string[];
  workflow: AgentWorkflowCreate;
};
type SubmissionStorage = Pick<Storage, "getItem" | "setItem" | "removeItem" | "length" | "key">;

/** Keep authenticated owners separate; this identifier is not an access credential. */
function key(owner: string): string { return `nexuspilot:pending-submissions:${encodeURIComponent(owner)}`; }

/** Validate both legacy lists and individual records before using any saved identity. */
function parseRecords(raw: string | null, owner: string): PendingSubmission[] {
  if (!raw) return [];
  if (raw.length > 2_000_000) throw new Error("待恢复请求记录过大，请先处理原请求。");
  const values = JSON.parse(raw) as PendingSubmission[];
  if (!Array.isArray(values) || values.length > 50 || values.some((item) =>
    !item || item.version !== 1 || item.owner !== owner || typeof item.id !== "string" ||
    typeof item.input !== "string" || typeof item.sessionKey !== "string" ||
    typeof item.turnKey !== "string" || !Array.isArray(item.fileIds) ||
    !item.workflow?.idempotency_key || !(item.sessionId === null || typeof item.sessionId === "string")
  )) throw new Error("无法读取待恢复请求，请勿重复提交。");
  return values;
}

/** Merge older list records with independent per-submission writes from other tabs. */
export function readPendingSubmissions(storage: SubmissionStorage, owner: string): PendingSubmission[] {
  const prefix = key(owner) + ":item:";
  const records = new Map(parseRecords(storage.getItem(key(owner)), owner).map(item => [item.id, item]));
  const names = Array.from({length:storage.length}, (_, index) => storage.key(index));
  for (const name of names) {
    if (!name?.startsWith(prefix)) continue;
    for (const item of parseRecords(storage.getItem(name), owner)) records.set(item.id, item);
  }
  const values = [...records.values()];
  if (values.length > 50 || JSON.stringify(values).length > 2_000_000) {
    throw new Error("待恢复请求记录过大，请先处理原请求。");
  }
  return values;
}

/** Persist before the first session/turn creation request; storage denial prevents sending. */
export function savePendingSubmission(storage: SubmissionStorage, value: PendingSubmission): void {
  const values = readPendingSubmissions(storage, value.owner).filter((item) => item.id !== value.id);
  values.push(value);
  const raw = JSON.stringify(values);
  if (values.length > 50 || raw.length > 2_000_000) throw new Error("请先处理待恢复请求。");
  // Separate keys avoid a read/modify/write race between unrelated conversations.
  storage.setItem(key(value.owner) + ":item:" + encodeURIComponent(value.id), JSON.stringify([value]));
}

/** Clear only the exact submission whose terminal workflow facts were confirmed. */
export function finishPendingSubmission(storage: SubmissionStorage, owner: string, id: string): void {
  const legacy = parseRecords(storage.getItem(key(owner)), owner);
  if (legacy.some(item => item.id === id)) {
    const remaining = legacy.filter(item => item.id !== id);
    if (remaining.length) storage.setItem(key(owner), JSON.stringify(remaining));
    else storage.removeItem(key(owner));
  }
  storage.removeItem(key(owner) + ":item:" + encodeURIComponent(id));
}
