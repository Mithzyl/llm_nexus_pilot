import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ClaimEvidencePanel } from "../components/agent-workflow/ClaimEvidencePanel";
import { MarkdownContent } from "../components/markdown/MarkdownContent";
import {
  citationTargetsForContent,
  claimEvidenceActionLabel,
  claimVerdictLabel,
  safeWebSourceUrl,
  selectClaimEntries,
  sourceBlockLocation,
  visibleClaimBlocks,
  webSourceDisplayTitle,
  type ClaimEntry,
} from "../lib/claim-evidence";
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

/** Claims use the same Markdown renderer while keeping their evidence collapsed. */
test("renders claim Markdown and keeps source evidence collapsed", () => {
  const records = nodes();
  const worker = records[0];
  if (worker.output_type !== "agent_model_execution" || !worker.output) throw new Error("fixture");
  worker.output.structured_output.claims![0].text = "**定义**：Prompt chaining 是顺序提示。";
  worker.output.structured_output.claims![0].evidence_refs = [{
    tool_call_id: "read",
    result_id: "result",
    source_kind: "file",
    block_ids: ["line:1"],
  }];
  const html = renderToStaticMarkup(createElement(ClaimEvidencePanel, {workflowId: "workflow", nodes: records, running: false}));
  assert.match(html, /<strong>定义<\/strong>/);
  assert.doesNotMatch(html, /\*\*定义\*\*/);
  assert.match(html, /查看原文/);
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /引用原文/);
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

/** Blank parser rows are not useful evidence and should not become empty cards. */
test("filters blank source blocks while retaining table and text blocks", () => {
  const blocks = [
    { text: "" },
    { text: "   " },
    { text: "", cells: ["A"] },
    { text: "Real source" },
  ] as ClaimSources["sources"][number]["blocks"];
  assert.deepEqual(visibleClaimBlocks(blocks), [blocks[2], blocks[3]]);
});

/** Keep web URLs navigable only for HTTP(S) and never invent a missing page title. */
test("web source helpers expose safe URLs and honest fallback titles", () => {
  assert.equal(safeWebSourceUrl("https://example.com/article?q=1"), "https://example.com/article?q=1");
  assert.equal(safeWebSourceUrl("javascript:alert(1)"), null);
  assert.equal(safeWebSourceUrl("/relative"), null);
  assert.equal(
    webSourceDisplayTitle(
      { filename: "Web search results", url: "https://example.com/a" },
      { title: "Saved page title", url: "https://example.com/b" },
    ),
    "Saved page title",
  );
  assert.equal(
    webSourceDisplayTitle({ filename: "Web search results", url: "https://example.com/a" }),
    "example.com",
  );
  assert.equal(webSourceDisplayTitle({ filename: "网页来源" }), "网页来源");
  assert.equal(claimEvidenceActionLabel("web"), "查看网页依据");
});

/** Count code points so a citation after an emoji still maps to persisted candidate offsets. */
test("citation targets preserve multiple aliases and Unicode code-point offsets", () => {
  const claimText = "😀依据 [S1]，再次 [S1]。";
  const content = `回答：${claimText}`;
  const startChar = Array.from("回答：").length;
  const entry: ClaimEntry = {
    nodeId: "candidate-node",
    taskId: "task",
    claim: {
      claim_key: "u1",
      text: claimText,
      source_type: "file",
      kind: "assertion",
      scope: "read_blocks",
      evidence_refs: [],
    },
    sourceKind: "web",
    candidateStartChar: startChar,
    candidateEndChar: startChar + Array.from(claimText).length,
    candidateKind: "prose",
    candidateSourceAliases: ["S1"],
  };
  const targets = citationTargetsForContent(content, [entry]);
  assert.equal(targets.length, 2);
  assert.notEqual(targets[0].key, targets[1].key);
  assert.equal(
    targets[0].contentStart,
    "回答：".length + "😀依据 ".length,
  );
  const html = renderToStaticMarkup(createElement(MarkdownContent, {
    content,
    citationTargets: targets,
  }));
  assert.equal((html.match(/class="citation-reference"/g) ?? []).length, 2);
  assert.match(html, /href="#citation:0"/);
  assert.match(html, /href="#citation:1"/);
});

/** A raw token or legacy claim without persisted unit coordinates must stay plain text. */
test("citation links require matching persisted spans and skip inline code", () => {
  const historical: ClaimEntry = {
    nodeId: "legacy",
    taskId: "task",
    claim: {
      claim_key: "same",
      text: "旧回答 [S1]",
      source_type: "file",
      kind: "assertion",
      scope: "read_blocks",
      evidence_refs: [],
    },
    sourceKind: "file",
  };
  assert.deepEqual(citationTargetsForContent("旧回答 [S1]", [historical]), []);

  const claimText = "代码 `[S1]`，依据 [S1]\n\n[网页链接 [S1]](https://example.com)";
  const content = claimText;
  const persisted: ClaimEntry = {
    ...historical,
    claim: { ...historical.claim, text: claimText },
    candidateStartChar: 0,
    candidateEndChar: Array.from(claimText).length,
    candidateKind: "prose",
    candidateSourceAliases: ["S1"],
  };
  const targets = citationTargetsForContent(content, [persisted]);
  assert.equal(targets.length, 2);
  const html = renderToStaticMarkup(createElement(MarkdownContent, {
    content,
    citationTargets: targets,
  }));
  assert.equal((html.match(/class="citation-reference"/g) ?? []).length, 1);
  assert.match(html, /<code>\[S1\]<\/code>/);
  assert.match(html, /href="https:\/\/example.com"/);
  assert.match(html, /网页链接 \[S1\]/);
});

/** A same-alias citation without persisted coordinates must not consume the mapped later one. */
test("citation links use source position instead of matching aliases by order", () => {
  const content = "未建立映射的旧引用 [S1]。\n\n有保存映射的结论 [S1]。";
  const mappedText = "有保存映射的结论 [S1]。";
  const mappedStart = Array.from("未建立映射的旧引用 [S1]。\n\n").length;
  const entry: ClaimEntry = {
    nodeId: "candidate-node",
    taskId: "task",
    claim: {
      claim_key: "u2",
      text: mappedText,
      source_type: "file",
      kind: "assertion",
      scope: "read_blocks",
      evidence_refs: [],
    },
    sourceKind: "file",
    candidateStartChar: mappedStart,
    candidateEndChar: mappedStart + Array.from(mappedText).length,
    candidateKind: "prose",
    candidateSourceAliases: ["S1"],
  };
  const targets = citationTargetsForContent(content, [entry]);
  assert.equal(targets.length, 1);
  const html = renderToStaticMarkup(createElement(MarkdownContent, {
    content,
    citationTargets: targets,
  }));
  assert.match(html, /<p>未建立映射的旧引用 \[S1\]。<\/p>/);
  assert.match(
    html,
    /<p>有保存映射的结论 <a[^>]*class="citation-reference"[^>]*>\[S1\]<\/a>。<\/p>/,
  );
});

/** A decoded character reference before a citation must not offset its source match. */
test("citation links survive a character reference before the persisted token", () => {
  const content = "正文 &amp; 内容 [S1]。";
  const entry: ClaimEntry = {
    nodeId: "candidate-node",
    taskId: "task",
    claim: {
      claim_key: "u3",
      text: content,
      source_type: "file",
      kind: "assertion",
      scope: "read_blocks",
      evidence_refs: [],
    },
    sourceKind: "file",
    candidateStartChar: 0,
    candidateEndChar: Array.from(content).length,
    candidateKind: "prose",
    candidateSourceAliases: ["S1"],
  };
  const targets = citationTargetsForContent(content, [entry]);
  assert.equal(targets.length, 1);
  const html = renderToStaticMarkup(createElement(MarkdownContent, {
    content,
    citationTargets: targets,
  }));
  assert.match(
    html,
    /<p>正文 &amp; 内容 <a[^>]*class="citation-reference"[^>]*>\[S1\]<\/a>。<\/p>/,
  );
});

/** A decoded escape before a citation must not offset its source match. */
test("citation links survive a character escape before the persisted token", () => {
  const content = "正文 \\* 内容 [S1]。";
  const entry: ClaimEntry = {
    nodeId: "candidate-node",
    taskId: "task",
    claim: {
      claim_key: "u4",
      text: content,
      source_type: "file",
      kind: "assertion",
      scope: "read_blocks",
      evidence_refs: [],
    },
    sourceKind: "file",
    candidateStartChar: 0,
    candidateEndChar: Array.from(content).length,
    candidateKind: "prose",
    candidateSourceAliases: ["S1"],
  };
  const targets = citationTargetsForContent(content, [entry]);
  assert.equal(targets.length, 1);
  const html = renderToStaticMarkup(createElement(MarkdownContent, {
    content,
    citationTargets: targets,
  }));
  assert.match(
    html,
    /<p>正文 \* 内容 <a[^>]*class="citation-reference"[^>]*>\[S1\]<\/a>。<\/p>/,
  );
});

/** Web evidence uses web copy while unknown aliases do not crash or become file evidence. */
test("web claims and unknown source aliases keep separate source labels", () => {
  const webReference = { tool_call_id: "search", block_ids: ["web:1"], source_kind: "web" as const };
  const webUnit = {
    unit_id: "u1",
    kind: "prose",
    start_char: 0,
    end_char: 8,
    text: "网页结论 [S1]",
    citation_status: "present",
    source_aliases: ["S1"],
    unknown_source_aliases: [],
  };
  const records = [{
    node_execution_id: "candidate-node",
    task_id: "task",
    node_sequence: 1,
    status: "completed",
    output_type: "general_candidate",
    output: {
      schema_version: "general_candidate.v2",
      text: "网页结论 [S1]",
      sources: { S1: webReference },
      units: [webUnit],
    },
  }] as unknown as AgentWorkflowNodeResult[];
  const webHtml = renderToStaticMarkup(createElement(ClaimEvidencePanel, {
    workflowId: "workflow",
    nodes: records,
    running: false,
  }));
  assert.match(webHtml, /查看网页依据/);
  assert.match(webHtml, /网页依据/);
  assert.doesNotMatch(webHtml, /文件结论/);

  const unknownAliasRecords = [{
    ...records[0],
    output: {
      ...records[0].output,
      sources: {},
      units: [{ ...webUnit, source_aliases: ["S404"] }],
    },
  }] as unknown as AgentWorkflowNodeResult[];
  const entries = selectClaimEntries(unknownAliasRecords);
  assert.equal(entries[0].sourceKind, "none");
  assert.doesNotThrow(() => claimVerdictLabel(entries[0]));
});
