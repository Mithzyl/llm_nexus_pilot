import assert from "node:assert/strict";
import test from "node:test";

import { isAllowedProxyRoute } from "../lib/proxy-policy";

test("allows only the UI resource and method combinations", () => {
  assert.equal(isAllowedProxyRoute("GET", ["providers"]), true);
  assert.equal(isAllowedProxyRoute("POST", ["responses"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["sessions", "session-1", "messages"]), true);
  assert.equal(isAllowedProxyRoute("GET", ["sessions", "session-1", "messages", "latest"]), true);
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
  assert.equal(isAllowedProxyRoute("POST", ["agent-workflows", "workflow-1", "cancel"]), false);
  assert.equal(isAllowedProxyRoute("PATCH", ["sessions", "session-1"]), false);
  assert.equal(isAllowedProxyRoute("GET", ["memory", "facts"]), false);
  assert.equal(isAllowedProxyRoute("POST", ["projects"]), false);
});
