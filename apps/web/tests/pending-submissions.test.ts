import assert from "node:assert/strict";
import test from "node:test";
import { createGeneralAgentWorkflowRequest } from "../lib/agent-workflow";
import { finishPendingSubmission, readPendingSubmissions, savePendingSubmission,
  type PendingSubmission } from "../lib/pending-submissions";

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
