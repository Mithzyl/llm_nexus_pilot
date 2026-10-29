import assert from "node:assert/strict";
import test from "node:test";
import { createGeneralAgentWorkflowRequest } from "../lib/agent-workflow";
import { finishPendingSubmission, readPendingSubmissions, savePendingSubmission, quickResponseRequest,
  type PendingSubmission } from "../lib/pending-submissions";

/** Quick recovery reuses saved model parameters and derives only server-returned message identity. */
test("quick submission preserves response identity and model across reload", () => {
  const db = storage();
  const request: PendingSubmission = {
    version: 2, mode: "quick", id: "quick-id", owner: "alice", sessionId: null,
    sessionKey: "session-key", turnKey: "turn-key", input: "hello", fileIds: [],
    responseKey: "response-key",
    response: {provider: "opencode", model: "saved-model", timeout_seconds: null,
      reasoning_display_policy: "provider-visible", reasoning: {enabled: true, effort: "medium"}},
  };
  savePendingSubmission(db, request);
  const restored = readPendingSubmissions(db, "alice")[0];
  assert.deepEqual(restored, request);
  const payload = quickResponseRequest(restored, "server-run", "server-message");
  assert.equal(payload.idempotency_key, "response-key");
  assert.equal(payload.model, "saved-model");
  assert.equal(payload.run_id, "server-run");
  assert.equal(payload.current_user_message_id, "server-message");
  assert.equal(payload.stream, true);
  assert.deepEqual(readPendingSubmissions(db, "bob"), []);
});

/** Corrupt quick parameters must stop recovery rather than silently use current UI defaults. */
test("quick recovery rejects missing model or response key", () => {
  for (const response of [{provider: "opencode"}, {provider: "opencode", model: "saved-model"}]) {
    const db = storage();
    db.setItem("nexuspilot:pending-submissions:alice", JSON.stringify([{
      version: 2, mode: "quick", id: "quick-id", owner: "alice", sessionId: null,
      sessionKey: "session", turnKey: "turn", input: "hello", fileIds: [], response,
    }]));
    assert.throws(() => readPendingSubmissions(db, "alice"), /无法读取/);
  }
});

/** Use a persistent in-memory store across simulated page instances. */
function storage() {
  const values = new Map<string, string>();
  return { get length() { return values.size; }, key: (index: number) => [...values.keys()][index] ?? null,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); } };
}

/** Model/session/turn identities survive reload and are isolated by authenticated owner. */
test("stable submission survives new-session response loss and remains until terminal", () => {
  const db = storage();
  const request = { version: 1, id: "id", owner: "alice", sessionId: null,
    sessionKey: "session-key", turnKey: "turn-key", input: "hello", fileIds: [],
    workflow: createGeneralAgentWorkflowRequest({binding: {provider:"opencode", model:"test"},
      reviewPolicy: "never", maxParallelAgents: 1, idempotencyKey: "workflow-key"}) } as PendingSubmission;
  savePendingSubmission(db, request);
  assert.deepEqual(readPendingSubmissions(db, "alice"), JSON.parse(JSON.stringify([request])));
  const disclosed = {...request, memoryDisclosures: [{entry_id: "12345678-1234-1234-1234-123456789abc",
    version: 2, scope: "personal" as const}]};
  savePendingSubmission(db, disclosed);
  assert.deepEqual(readPendingSubmissions(db, "alice")[0].memoryDisclosures, disclosed.memoryDisclosures);
  assert.equal(JSON.stringify(disclosed.memoryDisclosures).includes("text"), false);
  savePendingSubmission(db, request);
  assert.deepEqual(readPendingSubmissions(db, "bob"), []);
  savePendingSubmission(db, { ...request, sessionId: "returned-session" });
  assert.equal(readPendingSubmissions(db, "alice").length, 1);
  assert.equal(readPendingSubmissions(db, "alice")[0].turnKey, "turn-key");
  finishPendingSubmission(db, "alice", "other-id");
  assert.equal(readPendingSubmissions(db, "alice").length, 1);
  finishPendingSubmission(db, "alice", "id");
  assert.deepEqual(readPendingSubmissions(db, "alice"), []);
});

/** A denied browser write must be visible before any HTTP side effect. */
test("storage denial is not swallowed", () => {
  const db = storage();
  db.setItem = () => { throw new Error("quota"); };
  assert.throws(() => savePendingSubmission(db,
    { version: 1, id: "id", owner: "alice" } as PendingSubmission), /quota/);
});

/** Interleaved writes from different tabs must not replace an unrelated pending request. */
test("concurrent session records do not overwrite each other", () => {
  const db = storage();
  const request = {version:1,id:"first",owner:"alice",sessionId:null,sessionKey:"session-key",
    turnKey:"turn-key",input:"hello",fileIds:[],workflow:createGeneralAgentWorkflowRequest({
      binding:{provider:"opencode",model:"test"},reviewPolicy:"never",maxParallelAgents:1,
      idempotencyKey:"workflow-key",
    })} as PendingSubmission;
  const write = db.setItem;
  let interleave = true;
  db.setItem = (key, value) => {
    if (interleave) {
      interleave = false;
      savePendingSubmission(db, {...request,id:"second",sessionKey:"second-session"});
    }
    write(key, value);
  };
  savePendingSubmission(db, request);
  assert.deepEqual(readPendingSubmissions(db,"alice").map(item => item.id).sort(), ["first","second"]);
  finishPendingSubmission(db,"alice","first");
  assert.equal(readPendingSubmissions(db,"alice")[0].id,"second");
});
