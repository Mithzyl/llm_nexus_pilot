import { nexusFetch } from "./api";

/** Persist only the selected scope and version, never sensitive memory contents. */
export type MemoryDisclosureSelection = {
  entry_id: string;
  version: number;
  scope: "personal" | "project";
};

/** Finish explicit per-Run grants before generation; failure/abort propagates to the submitter. */
export async function grantSelectedMemories(
  runId: string, selected: readonly MemoryDisclosureSelection[], signal?: AbortSignal,
): Promise<void> {
  for (const item of selected) {
    signal?.throwIfAborted();
    await nexusFetch(`runs/${encodeURIComponent(runId)}/user-memories`, {
      method: "POST", signal,
      body: JSON.stringify({action: "disclose", scope: item.scope, entry_id: item.entry_id,
        expected_version: item.version, confirmed_sensitive: true,
        key: `disclose:${runId}:${item.entry_id}:${item.version}`}),
    });
  }
  signal?.throwIfAborted();
}
