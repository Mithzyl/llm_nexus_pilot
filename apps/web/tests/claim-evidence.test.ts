import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ClaimEvidencePanel } from "../components/agent-workflow/ClaimEvidencePanel";
import { claimVerdictLabel, selectClaimEntries, sourceBlockLocation } from "../lib/claim-evidence";
import { isAllowedProxyRoute } from "../lib/proxy-policy";
import type { AgentWorkflowNodeResult, ClaimSources } from "../lib/types";

/** Create minimal persisted node fixtures for source-selection rules. */
function nodes(): AgentWorkflowNodeResult[] {
  return [
    { node_execution_id: "worker", task_id: "a", node_sequence: 1, status: "completed", output_type: "agent_model_execution", output: { structured_output: { schema_version: "worker_evidence.v2", claims: [{ claim_key: "same", text: "First", source_type: "file" }] } } },
    { node_execution_id: "other", task_id: "b", node_sequence: 2, status: "completed", output_type: "agent_model_execution", output: { structured_output: { schema_version: "worker_evidence.v2", claims: [{ claim_key: "same", text: "Second", source_type: "file" }] } } },
    { node_sequence: 3, status: "completed", output_type: "independent_review", output: { claim_assessments: [{ task_id: "b", claim_key: "same", verdict: "supported" }] } },
  ] as unknown as AgentWorkflowNodeResult[];
}

/** Use candidate identity, not the reused task and unit ID, when adapting new Agent results. */
test("general sources show latest located candidate and only its matching review", () => {
  const reference = { tool_call_id: "read", block_ids: ["line:1"] };
  const candidate = { node_execution_id: "new", task_id: "same-task", node_sequence: 3,
    status: "completed", output_type: "general_candidate", output: {
      schema_version: "general_candidate.v2", text: "New paragraph [S1]", sources: {S1: reference},
      units: [{unit_id: "u1", kind: "prose", text: "New paragraph [S1]", citation_status: "present", source_aliases: ["S1"]}],
    }};
  const records = [
    {...candidate, node_execution_id: "old", node_sequence: 1},
    {node_sequence: 2, status: "completed", output_type: "general_review", output: {
      candidate_node_id: "old", decisions: [{unit_id: "u1", verdict: "supported", feedback: "Old decision", source_aliases: []}], sources: {},
    }}, candidate,
  ] as unknown as AgentWorkflowNodeResult[];
  let entries = selectClaimEntries(records);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].nodeId, "new");
  assert.equal(claimVerdictLabel(entries[0]), "尚未审核");
  records.push({node_sequence: 4, status: "completed", output_type: "general_review", output: {
    candidate_node_id: "new", decisions: [{unit_id: "u1", verdict: "revise", feedback: "Clarify scope", source_aliases: ["S1"]}], sources: {S1: reference},
  }} as unknown as AgentWorkflowNodeResult);
  entries = selectClaimEntries(records);
  assert.equal(claimVerdictLabel(entries[0]), "审核有异议");
  assert.equal(entries[0].assessment?.explanation, "Clarify scope");
});

/** Delegated prose is provisional process evidence, not a reviewed source claim. */
test("keeps delegated candidates out of reviewed claim sources", () => {
  const records = nodes();
  records.push({
    node_execution_id: "delegated", task_id: "delegated-task", node_sequence: 4,
    status: "completed", output_type: "general_delegated_candidate",
    output: {
      schema_version: "general_candidate.v2", text: "Delegated prose", sources: {},
      validation_issues: [],
      units: [{ unit_id: "delegated-unit", kind: "prose", text: "Delegated prose",
        citation_status: "present", source_aliases: [] }],
    },
  } as unknown as AgentWorkflowNodeResult);
  const entries = selectClaimEntries(records);
  assert.equal(entries.length, 2);
  assert.equal(entries.some((entry) => entry.nodeId === "delegated"), false);
});

/** Match claim keys within their task, not globally across workers. */
test("review matches the task and claim pair, never another worker with the same key", () => {
  const entries = selectClaimEntries(nodes());
  assert.equal(claimVerdictLabel(entries[0]), "尚未审核");
  assert.equal(claimVerdictLabel(entries[1]), "原文支持");
});

/** Ignore unsuccessful reviewer output without manufacturing an assessment. */
test("failed review output and historical absent fields do not invent support", () => {
  const records = nodes();
  records[2].status = "failed";
  assert.equal(claimVerdictLabel(selectClaimEntries(records)[1]), "尚未审核");
  assert.deepEqual(selectClaimEntries([]), []);
});

/** Only describe locations supplied by a parser. */
test("block labels use real locations and preserve unknown page information", () => {
  const block = { text: "Original", line_start: 2, line_end: 4 } as ClaimSources["sources"][number]["blocks"][number];
  assert.equal(sourceBlockLocation(block), "第 2–4 行");
  assert.equal(sourceBlockLocation({ text: "Original" } as typeof block), "引用片段");
});

/** Keep the source projection read-only in the browser proxy. */
test("claim source proxy only permits the specific read-only route", () => {
  const route = ["agent-workflows", "workflow", "nodes", "node", "claim-sources"];
  assert.equal(isAllowedProxyRoute("GET", route), true);
  assert.equal(isAllowedProxyRoute("POST", route), false);
});

/** Render initial claims without downloading excerpts or treating references as validation. */
test("initial panel escapes model text and does not assert source validity before expansion", () => {
  const records = nodes();
  const worker = records[0];
  if (worker.output_type !== "agent_model_execution" || !worker.output) throw new Error("fixture");
  worker.output.structured_output.claims![0].text = "<script>untrusted</script>";
  const html = renderToStaticMarkup(createElement(ClaimEvidencePanel, {workflowId: "workflow", nodes: records, running: false}));
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|引用有效/);
  assert.match(html, /aria-expanded="false"/);
});

/** Preserve an explicit historical empty state and distinguish pending work. */
test("legacy records and running empty results have distinct honest explanations", () => {
  const html = renderToStaticMarkup(createElement(ClaimEvidencePanel, {workflowId: "workflow", nodes: [], running: false}));
  assert.match(html, /此记录未保存逐条来源/);
  const pending = renderToStaticMarkup(createElement(ClaimEvidencePanel, {workflowId: "workflow", nodes: [], running: true}));
  assert.match(pending, /工作流运行中/);
  const records = nodes();
  const worker = records[0];
  if (worker.output_type !== "agent_model_execution" || !worker.output) throw new Error("fixture");
  delete worker.output.structured_output.schema_version;
  assert.equal(selectClaimEntries(records).length, 1);
});
