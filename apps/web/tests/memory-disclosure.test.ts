import assert from "node:assert/strict";
import test from "node:test";
import { grantSelectedMemories } from "../lib/memory-disclosure";

/** Shared consent targets the displayed grant version and never submits the source body. */
test("shared disclosure preserves its grant identity on retry", async () => {
  const original = globalThis.fetch;
  const calls: Array<{url: string; body: unknown}> = [];
  globalThis.fetch = (async (url, init) => {
    calls.push({url: String(url), body: JSON.parse(String(init?.body))});
    return Response.json({});
  }) as typeof fetch;
  try {
    const selected = [{entry_id:"source",version:2,scope:"personal" as const,
      grant_id:"grant",grant_version:3}];
    await grantSelectedMemories("run", selected);
    await grantSelectedMemories("run", selected);
    assert.deepEqual(calls[0], calls[1]);
    assert.equal(calls[0].url, "/api/nexus/runs/run/shared-memory-disclosures");
    assert.deepEqual(calls[0].body, {grant_id:"grant",expected_version:3,confirmed:true,
      key:"share-disclose:run:grant:3"});
  } finally { globalThis.fetch = original; }
});

/** Recovery must reuse the same operation identity without storing sensitive memory text. */
test("disclosure uses selected versions and stable per-run keys", async () => {
  const original = globalThis.fetch;
  const calls: Array<{url: string; body: unknown}> = [];
  globalThis.fetch = (async (url, init) => {
    calls.push({url: String(url), body: JSON.parse(String(init?.body))});
    return Response.json({status: "active"});
  }) as typeof fetch;
  try {
    const selected = [{entry_id: "memory-a", version: 3, scope: "personal" as const}];
    await grantSelectedMemories("run-a", selected);
    await grantSelectedMemories("run-a", selected);
    assert.deepEqual(calls[0], calls[1]);
    assert.deepEqual(calls[0].body, {action: "disclose", scope: "personal", entry_id: "memory-a",
      expected_version: 3, confirmed_sensitive: true, key: "disclose:run-a:memory-a:3"});
    assert.equal(calls[0].url, "/api/nexus/runs/run-a/user-memories");
  } finally { globalThis.fetch = original; }
});

/** A failed or cancelled grant must prevent the caller from starting model generation. */
test("disclosure propagates denial and abort without granting remaining selections", async () => {
  const original = globalThis.fetch;
  let count = 0;
  globalThis.fetch = (async () => { count++; return Response.json({detail: "denied"}, {status: 403}); }) as typeof fetch;
  try {
    const selected = [{entry_id: "a", version: 1, scope: "personal" as const},
      {entry_id: "b", version: 1, scope: "personal" as const}];
    await assert.rejects(grantSelectedMemories("run", selected));
    assert.equal(count, 1);
    const controller = new AbortController(); controller.abort();
    await assert.rejects(grantSelectedMemories("run", selected, controller.signal));
    assert.equal(count, 1);
  } finally { globalThis.fetch = original; }
});
