import assert from "node:assert/strict";
import test from "node:test";

import {
  conversationPath,
  resolveWorkspaceRoute,
  runPath,
  workspaceRouteKey,
} from "../lib/routes";

test("resolves home, conversation, and run routes from App Router params", () => {
  assert.deepEqual(resolveWorkspaceRoute({}), { kind: "home" });
  assert.deepEqual(resolveWorkspaceRoute({ sessionId: "session-1" }), {
    kind: "conversation",
    sessionId: "session-1",
  });
  assert.deepEqual(resolveWorkspaceRoute({ runId: "run-1" }), {
    kind: "run",
    runId: "run-1",
  });
  assert.deepEqual(resolveWorkspaceRoute({ sessionId: ["invalid"] }), { kind: "home" });
});

test("builds encoded stable URLs and keys for navigation deduplication", () => {
  assert.equal(conversationPath("session/with space"), "/c/session%2Fwith%20space");
  assert.equal(runPath("run/with space"), "/runs/run%2Fwith%20space");
  assert.equal(
    workspaceRouteKey({ kind: "conversation", sessionId: "session-1" }),
    "conversation:session-1",
  );
  assert.equal(workspaceRouteKey({ kind: "run", runId: "run-1" }), "run:run-1");
});
