import assert from "node:assert/strict";
import test from "node:test";
import { createPersonalApiKey, listPersonalApiKeys, revokePersonalApiKey } from "../lib/api";
const originalFetch = globalThis.fetch;

test("loads and creates personal API keys through owner-scoped routes", async () => {
  const requests: Array<{ url: string; method: string | undefined; body: unknown }> = [];
  globalThis.fetch = (async (input, init) => {
    const request = {
      url: String(input),
      method: init?.method,
      body: init?.body ? JSON.parse(String(init.body)) : null,
    };
    requests.push(request);
    if (request.method === "POST") {
      return Response.json({
        api_key_id: "key-1",
        name: "Automation",
        masked_key: "np_key-1…1234",
        scopes: ["read", "execute"],
        created_at: "2026-10-27T00:00:00Z",
        expires_at: null,
        last_used_at: null,
        revoked_at: null,
        key: "np_key-1.secret",
      }, { status: 201 });
    }
    return Response.json([]);
  }) as typeof fetch;

  try {
    assert.deepEqual(await listPersonalApiKeys(), []);
    const created = await createPersonalApiKey({
      name: "Automation",
      scopes: ["read", "execute"],
      expires_at: null,
    });
    assert.equal(created.key, "np_key-1.secret");
    assert.deepEqual(requests, [
      { url: "/api/nexus/me/api-keys", method: undefined, body: null },
      {
        url: "/api/nexus/me/api-keys",
        method: "POST",
        body: { name: "Automation", scopes: ["read", "execute"], expires_at: null },
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("revokes one personal API key without sending a replacement body", async () => {
  let request: { url: string; method: string | undefined; body: string | null | undefined } | null = null;
  globalThis.fetch = (async (input, init) => {
    request = { url: String(input), method: init?.method, body: init?.body as string | null | undefined };
    return new Response(null, { status: 204 });
  }) as typeof fetch;

  try {
    await revokePersonalApiKey("key/1");
    assert.deepEqual(request, {
      url: "/api/nexus/me/api-keys/key%2F1",
      method: "DELETE",
      body: undefined,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
import { getMemorySettings, listProductMemories, mutateProductMemory } from "../lib/api";

/** Settings never create a Run and use one product protocol for personal and project scopes. */
test("memory settings send scoped versioned operations and retain the caller retry key", async () => {
  const original = globalThis.fetch;
  const requests: Array<{url: string; body: unknown}> = [];
  globalThis.fetch = (async (input, init) => {
    requests.push({url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null});
    return Response.json({items: [], next_offset: null});
  }) as typeof fetch;
  try {
    await getMemorySettings();
    await listProductMemories(undefined, 20);
    await mutateProductMemory({action: "save", text: "偏好", key: "stable"});
    await mutateProductMemory({action: "correct", text: "规则", key: "project-edit", entry_id: "entry", expected_version: 2}, "project");
    assert.deepEqual(requests, [
      {url: "/api/nexus/me/memory-settings", body: null},
      {url: "/api/nexus/me/user-memories?offset=20&limit=20", body: null},
      {url: "/api/nexus/me/user-memories", body: {action: "save", text: "偏好", key: "stable", scope: "personal"}},
      {url: "/api/nexus/projects/project/user-memories", body: {action: "correct", text: "规则", key: "project-edit", entry_id: "entry", expected_version: 2, scope: "project"}},
    ]);
  } finally { globalThis.fetch = original; }
});

/** API error envelopes keep the product code available for readable error messages. */
test("memory error code survives the same-origin client", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => Response.json({detail: {code: "memory_version_conflict"}}, {status: 409})) as typeof fetch;
  try { await assert.rejects(getMemorySettings(), /memory_version_conflict/); }
  finally { globalThis.fetch = original; }
});
