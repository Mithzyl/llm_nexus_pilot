import assert from "node:assert/strict";
import test from "node:test";

import { isAllowedProxyRoute } from "../lib/proxy-policy";

/** Saved execution files allow reads and downloads, never browser writes or deletion. */
test("execution artifacts expose read-only routes", () => {
  for (const path of [["artifacts"], ["artifacts", "id"], ["artifacts", "id", "content"]]) {
    assert.equal(isAllowedProxyRoute("GET", path), true);
    assert.equal(isAllowedProxyRoute("POST", path), false);
    assert.equal(isAllowedProxyRoute("DELETE", path), false);
  }
});

test("allows only the UI resource and method combinations", () => {
  assert.equal(isAllowedProxyRoute("GET", ["providers"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["models"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["prompt-templates"]), false);
  assert.equal(
    isAllowedProxyRoute("GET", ["context-builds", "context-1", "evidence"]),
    true,
  );
  assert.equal(isAllowedProxyRoute("POST", ["responses"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["attempts", "attempt-1", "events"]), true);
  assert.equal(
    isAllowedProxyRoute("GET", ["attempts", "attempt-1", "reasoning-blocks"]),
    true,
  );
  assert.equal(isAllowedProxyRoute("POST", ["attempts", "attempt-1", "events"]), false);
  assert.equal(isAllowedProxyRoute("GET", ["sessions", "session-1", "messages"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["sessions", "session-1", "messages", "latest"]), true);
  assert.equal(isAllowedProxyRoute("POST", ["sessions", "session-1", "turns"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["sessions", "session-1", "latest-run"]), true);
  assert.equal(isAllowedProxyRoute("POST", ["runs", "run-1", "agent-workflows"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["runs", "run-1", "agent-workflow"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["agent-workflows", "workflow-1"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["agent-workflows", "workflow-1", "result"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["agent-workflows", "workflow-1", "nodes"]), true);
  assert.equal(
    isAllowedProxyRoute("GET", ["agent-workflows", "workflow-1", "nodes", "node-1"]),
    true,
  );
  assert.equal(isAllowedProxyRoute("GET", ["agent-workflows", "workflow-1", "events"]), true);
  assert.equal(isAllowedProxyRoute("POST", ["agent-workflows", "workflow-1", "cancel"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["agent-workflows", "workflow-1", "cancel"]), false);
  assert.equal(isAllowedProxyRoute("PATCH", ["sessions", "session-1"]), true);
  assert.equal(isAllowedProxyRoute("PATCH", ["sessions", "session-1", "messages"]), false);
  assert.equal(isAllowedProxyRoute("GET", ["memory", "facts"]), false);
  assert.equal(isAllowedProxyRoute("POST", ["projects"]), true);
});

/** Project product routes never expose internal Memory policy or arbitrary workspace binding. */
test("projects expose explicit copies and conversations but not memory administration", () => {
  assert.equal(isAllowedProxyRoute("POST", ["projects", "p", "files"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["projects", "p", "files", "f", "content"]), true);
  assert.equal(isAllowedProxyRoute("POST", ["projects", "p", "files", "f", "materialize"]), true);
  assert.equal(isAllowedProxyRoute("POST", ["projects", "p", "sessions"]), true);
  assert.equal(isAllowedProxyRoute("PATCH", ["projects", "p", "memory-policy"]), false);
  assert.equal(isAllowedProxyRoute("POST", ["projects", "p", "workspaces"]), false);
  assert.equal(isAllowedProxyRoute("DELETE", ["projects", "p", "files", "f"]), false);
});
