import type { AgentWorkflowCreate, ProviderName, ReasoningConfiguration, RunStatus } from "./types";
import type { MemoryDisclosureSelection } from "./memory-disclosure";

type SubmissionIdentity = {
  id: string; owner: string; sessionId: string | null;
  sessionKey: string; turnKey: string; input: string; fileIds: string[];
  memoryDisclosures?: MemoryDisclosureSelection[];
};
export type QuickResponseParameters = {
  provider: ProviderName; model: string; timeout_seconds: number | null;
  reasoning?: ReasoningConfiguration;
  reasoning_display_policy: "hidden" | "summary-only" | "provider-visible";
};
export type PendingSubmission = SubmissionIdentity & (
  | {version: 1; mode?: "agent"; workflow: AgentWorkflowCreate}
  | {version: 2; mode: "agent"; workflow: AgentWorkflowCreate}
  | {version: 2; mode: "quick"; responseKey: string; response: QuickResponseParameters}
);

/** Assemble a retry from persisted parameters, never from a changed model picker. */
export function quickResponseRequest(value: PendingSubmission, runId: string, messageId: string) {
  if (value.mode !== "quick") throw new Error("待恢复请求不是快速回复。");
  return {...value.response, run_id: runId, current_user_message_id: messageId,
    input: value.input, idempotency_key: value.responseKey, stream: true};
}

/** Recognize existing Agent records and reject incomplete quick records before any network write. */
function validExecution(value: PendingSubmission): boolean {
  if (value.mode !== "quick") {
    return (value.version === 1 || (value.version === 2 && value.mode === "agent")) &&
      Boolean(value.workflow?.idempotency_key);
  }
  const parameters = value.response;
  return value.version === 2 && typeof value.responseKey === "string" && value.responseKey.length >= 8 &&
    Boolean(parameters && typeof parameters.provider === "string" && parameters.provider.length &&
      typeof parameters.model === "string" && parameters.model.length &&
      ["hidden", "summary-only", "provider-visible"].includes(parameters.reasoning_display_policy) &&
      (parameters.timeout_seconds === null || (Number.isFinite(parameters.timeout_seconds) &&
        parameters.timeout_seconds >= 1 && parameters.timeout_seconds <= 600)));
}
type SubmissionStorage = Pick<Storage, "getItem" | "setItem" | "removeItem" | "length" | "key">;

/** Keep authenticated owners separate; this identifier is not an access credential. */
function key(owner: string): string { return `nexuspilot:pending-submissions:${encodeURIComponent(owner)}`; }

/** Validate both legacy lists and individual records before using any saved identity. */
function parseRecords(raw: string | null, owner: string): PendingSubmission[] {
  if (!raw) return [];
  if (raw.length > 2_000_000) throw new Error("待恢复请求记录过大，请先处理原请求。");
  const values = JSON.parse(raw) as PendingSubmission[];
  if (!Array.isArray(values) || values.length > 50 || values.some((item) =>
    !item || !validExecution(item) || item.owner !== owner || typeof item.id !== "string" ||
    typeof item.input !== "string" || typeof item.sessionKey !== "string" ||
    typeof item.turnKey !== "string" || !Array.isArray(item.fileIds) ||
    !(item.sessionId === null || typeof item.sessionId === "string") ||
    (item.memoryDisclosures !== undefined && (!Array.isArray(item.memoryDisclosures) ||
      item.memoryDisclosures.length > 400 || item.memoryDisclosures.some(grant =>
        !grant || typeof grant.entry_id !== "string" || grant.entry_id.length !== 36 ||
        !Number.isInteger(grant.version) || grant.version < 1 ||
        !["personal", "project"].includes(grant.scope) ||
        (grant.grant_id !== undefined && (typeof grant.grant_id !== "string" ||
          grant.grant_id.length !== 36 || !Number.isInteger(grant.grant_version) || grant.grant_version! < 1)))))
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

/** Remove only exact turns proven terminal; missing or failed reads never imply completion. */
export async function reconcilePendingSubmissions(
  storage: SubmissionStorage, owner: string, records: PendingSubmission[],
  readStatus: (record: PendingSubmission) => Promise<RunStatus | null>,
): Promise<PendingSubmission[]> {
  const remaining: PendingSubmission[] = [];
  for (const record of records) {
    if (record.owner !== owner) throw new Error("待恢复请求不属于当前用户。");
    const status = record.sessionId ? await readStatus(record) : null;
    if (status && ["completed", "failed", "cancelled"].includes(status)) {
      finishPendingSubmission(storage, owner, record.id);
    } else remaining.push(record);
  }
  return remaining;
}
