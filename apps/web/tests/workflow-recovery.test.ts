import assert from "node:assert/strict";
import test from "node:test";
import { followWorkflowResult } from "../lib/workflow-recovery";

/** Cancellation requests remain active until server cleanup confirms a terminal state. */
test("Run recovery waits for cancellation confirmation", async () => {
  const seen: string[] = [];
  await followWorkflowResult({
    signal: new AbortController().signal, intervalMs: 1,
    read: async () => ({ status: seen.length ? "cancelled" : "cancel_requested" }),
    apply: (result) => { seen.push(result.status); },
    onError: (error) => { throw error; },
  });
  assert.deepEqual(seen, ["cancel_requested", "cancelled"]);
});

/** Recovery reads serial snapshots until terminal state without submitting another workflow. */
test("recovery keeps reading through transient failure and stops on completion", async () => {
  let calls = 0;
  const seen: string[] = [];
  const errors: unknown[] = [];
  await followWorkflowResult({
    signal: new AbortController().signal,
    intervalMs: 1,
    read: async () => {
      calls += 1;
      if (calls === 2) throw new Error("temporary");
      return { status: calls === 4 ? "completed" : "running" };
    },
    apply: (result) => { seen.push(result.status); },
    onError: (error) => { errors.push(error); },
  });
  assert.equal(calls, 4);
  assert.deepEqual(seen, ["running", "running", "completed"]);
  assert.equal(errors.length, 1);
});

/** Navigating away invalidates an in-flight result even if the transport ignores abort. */
test("cancelled recovery cannot apply late results", async () => {
  const controller = new AbortController();
  let applied = false;
  await followWorkflowResult({
    signal: controller.signal,
    intervalMs: 1,
    read: async () => { controller.abort(); return { status: "completed" }; },
    apply: () => { applied = true; },
    onError: () => { throw new Error("Abort is not a failure"); },
  });
  assert.equal(applied, false);
});

/** Failed and cancelled executions are terminal and must not cause additional reads. */
test("recovery stops on every terminal state", async () => {
  for (const status of ["failed", "cancelled", "outcome_unknown", "completed"]) {
    let calls = 0;
    await followWorkflowResult({
      signal: new AbortController().signal,
      intervalMs: 1,
      read: async () => { calls += 1; return { status }; },
      apply: () => {},
      onError: () => {},
    });
    assert.equal(calls, 1);
  }
});
