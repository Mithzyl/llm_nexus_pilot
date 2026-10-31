import { nexusFetch } from "./api";

/** Persist only the selected scope and version, never sensitive memory contents. */
export type MemoryDisclosureSelection = {
  entry_id: string;
  version: number;
  scope: "personal" | "project";
  grant_id?: string;
  grant_version?: number;
};

/** Finish explicit per-Run grants before generation; failure/abort propagates to the submitter. */
export async function grantSelectedMemories(
  runId: string, selected: readonly MemoryDisclosureSelection[], signal?: AbortSignal,
): Promise<void> {
  for (const item of selected) {
    signal?.throwIfAborted();
    if (item.grant_id) {
      if (!Number.isInteger(item.grant_version) || item.grant_version! < 1) throw new Error("Invalid sharing version");
      await nexusFetch(`runs/${encodeURIComponent(runId)}/shared-memory-disclosures`, {
        method: "POST", signal, body: JSON.stringify({grant_id: item.grant_id,
          expected_version: item.grant_version, confirmed: true,
          key: `share-disclose:${runId}:${item.grant_id}:${item.grant_version}`}),
      });
      continue;
    }
    await nexusFetch(`runs/${encodeURIComponent(runId)}/user-memories`, {
      method: "POST", signal,
      body: JSON.stringify({action: "disclose", scope: item.scope, entry_id: item.entry_id,
        expected_version: item.version, confirmed_sensitive: true,
        key: `disclose:${runId}:${item.entry_id}:${item.version}`}),
    });
  }
  signal?.throwIfAborted();
}
