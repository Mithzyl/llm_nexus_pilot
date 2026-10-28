import { expect, test, type Page, type Route } from "@playwright/test";

/** Fulfill one browser-facing API request with deterministic fixture data. */
async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

const MODEL = {
  model_id: "model-1",
  provider: "deepseek",
  model_identifier: "deepseek-v4-flash",
  display_name: "DeepSeek V4 Flash",
  description: "Deterministic browser test model",
  status: "enabled",
  sort_order: 10,
  metadata_json: {},
  registry_available: true,
  selectable: true,
  unavailable_reason: null,
  catalog_version_id: "catalog-1",
  catalog_version: "2026-08-30",
  context_window: 65_536,
  supports_tools: true,
  supports_structured_output: true,
  supports_vision: false,
  supports_caching: true,
  supports_streaming: true,
  tokenizer_name: "deepseek",
  tokenizer_version: "v1",
  reasoning_capabilities: {
    presentation: "raw",
    supports_streaming_presentation: true,
    continuation: "raw-reasoning-replay",
    supports_reasoning_tokens: true,
  },
  reasoning_supported_efforts: ["minimal", "medium", "xhigh"],
  reasoning_supports_disable: true,
  reasoning_default_enabled: true,
  reasoning_default_effort: "medium",
  reasoning_profile_version: "reasoning-policy.v1",
};

const SESSION = {
  session_id: "session-1",
  project_id: "p1",
  user_id: "nexuspilot-web",
  title: "项目引用验证",
  status: "active",
  created_at: "2026-10-27T00:00:00Z",
  updated_at: "2026-10-27T01:00:00Z",
};

/** Install workspace authentication and session-list facts shared by both focused flows. */
async function installWorkspaceBase(page: Page, sessions: unknown[]) {
  await page.route("**/api/nexus/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/nexus/", "");
    if (path === "auth/me") {
      return fulfillJson(route, {
        user_id: "nexuspilot-web",
        display_name: "Browser Test",
        authentication_method: "browser_session",
        login_session_id: "login-session-1",
        scopes: ["model:invoke", "sessions:read", "sessions:write"],
      });
    }
    if (path === "models") return fulfillJson(route, [MODEL]);
    if (path === "sessions") {
      return fulfillJson(route, {
        items: sessions,
        next_cursor: null,
        has_more: false,
        limit: 50,
      });
    }
    if (path === "files/capabilities") {
      return fulfillJson(route, {
        accepted_extensions: [".txt", ".md"],
        executor_status: "available",
        max_file_size_bytes: 1048576,
        unavailable_reason: null,
      });
    }
    if (path === "projects/p1") {
      return fulfillJson(route, { project_id: "p1", project_name: "研究项目" });
    }
    return fulfillJson(route, { detail: `Unhandled fixture route: ${path}` }, 404);
  });
}

/** Build one complete persisted candidate node for citation projection. */
function candidateNode(text: string) {
  return {
    schema_version: "agent_workflow_node_result.v1",
    workflow_execution_id: "workflow-1",
    workflow_name: "general_agent",
    workflow_version: "1.1.0",
    node_key: "general_candidate",
    node_type: "general_candidate",
    node_version: "1.0.0",
    node_execution_id: "candidate-node",
    node_sequence: 1,
    node_attempt: 1,
    parent_node_execution_id: null,
    run_id: "run-1",
    task_id: "task-1",
    agent_run_id: null,
    agent_turn_id: null,
    agent_role: null,
    status: "completed",
    input: {
      schema_version: "general_candidate_input.v1",
      workflow_round: 1,
      input_hash: "fixture",
      source_node_execution_ids: [],
      message_ids: ["message-user"],
      artifact_ids: [],
      handoff_ids: [],
      context_build_id: null,
    },
    output_type: "general_candidate",
    output_schema_version: "general_candidate.v2",
    output: {
      schema_version: "general_candidate.v2",
      text,
      sources: {
        S1: {
          tool_call_id: "web-read",
          result_id: "web-result",
          source_kind: "web",
          web_result_id: "web-result",
          file_id: null,
          block_ids: ["web:1"],
        },
      },
      validation_issues: [],
      units: [{
        unit_id: "u1",
        kind: "prose",
        start_char: 0,
        end_char: Array.from(text).length,
        text,
        source_aliases: ["S1"],
        unknown_source_aliases: [],
        citation_status: "present",
        content_verified: false,
      }],
    },
    transition: {
      selected_transition: "complete",
      next_node_keys: [],
      skipped_node_keys: [],
      condition_summary: "fixture",
    },
    evidence: {
      model_attempt_ids: [],
      tool_call_ids: ["web-read"],
      evaluation_ids: [],
      artifact_ids: [],
      handoff_ids: [],
    },
    usage: {
      input_tokens: 0,
      output_tokens: 0,
      cached_tokens: 0,
      estimated_cost: "0",
      model_call_count: 0,
      tool_call_count: 1,
    },
    budget: {
      cost_limit: null,
      cost_used_before: "0",
      cost_used_after: "0",
      reserved_estimated_cost: "0",
      remaining_model_calls: null,
      remaining_nodes: 0,
      remaining_wall_time_ms: 0,
      remaining_cost: null,
    },
    timing: {
      started_at: "2026-10-27T00:00:00Z",
      completed_at: "2026-10-27T00:00:01Z",
      duration_ms: 1000,
    },
    public_view: {
      status_label: "完成",
      summary: "已提交候选回答",
      progress_current: 1,
      progress_total: 1,
    },
    observability: { trace_id: null, span_id: null },
    warnings: [],
    error: null,
    created_at: "2026-10-27T00:00:01Z",
  };
}

test("opens the matching web evidence from a persisted citation without another run", async ({
  page,
}) => {
  const answer = "根据资料，示例结论 [S1]。";
  const messages = [
    {
      message_id: "message-old-user",
      session_id: "session-1",
      run_id: "run-2",
      role: "user",
      content_type: "text",
      content_text: "旧回答的问题。",
      content_uri: null,
      sequence: 1,
      token_count: 5,
      metadata_json: {},
      created_at: "2026-10-27T00:00:00Z",
    },
    {
      message_id: "message-old-assistant",
      session_id: "session-1",
      run_id: "run-2",
      source_model_attempt_id: null,
      parent_message_id: null,
      role: "assistant",
      content_type: "text",
      content_text: "旧回答需要先查看证据。",
      content_uri: null,
      sequence: 2,
      token_count: 4,
      metadata_json: { source: "agent_workflow" },
      reasoning_blocks: [],
      created_at: "2026-10-27T00:00:01Z",
    },
    {
      message_id: "message-user",
      session_id: "session-1",
      run_id: "run-1",
      role: "user",
      content_type: "text",
      content_text: "请说明示例结论。",
      content_uri: null,
      sequence: 3,
      token_count: 5,
      metadata_json: {},
      created_at: "2026-10-27T00:00:02Z",
    },
    {
      message_id: "message-assistant",
      session_id: "session-1",
      run_id: "run-1",
      source_model_attempt_id: null,
      parent_message_id: null,
      role: "assistant",
      content_type: "text",
      content_text: answer,
      content_uri: null,
      sequence: 4,
      token_count: 8,
      metadata_json: { source: "agent_workflow" },
      reasoning_blocks: [],
      created_at: "2026-10-27T00:00:03Z",
    },
  ];
  const run = {
    run_id: "run-1",
    user_id: "nexuspilot-web",
    session_id: "session-1",
    user_request: "请说明示例结论。",
    run_type: "chat",
    status: "completed",
    budget_limit: null,
    cost_used: "0",
    started_at: "2026-10-27T00:00:00Z",
    completed_at: "2026-10-27T00:00:01Z",
    created_at: "2026-10-27T00:00:00Z",
    updated_at: "2026-10-27T00:00:01Z",
    attempts: [],
    tasks: [],
    artifacts: [],
    tasks_has_more: false,
    attempts_has_more: false,
    artifacts_has_more: false,
  };
  const oldRun = {
    ...run,
    run_id: "run-2",
    user_request: "旧回答的问题。",
    updated_at: "2026-10-27T00:00:01Z",
  };
  const summary = {
    workflow_execution_id: "workflow-1",
    run_id: "run-1",
    workflow_name: "general_agent",
    workflow_version: "1.1.0",
    execution_profile: "general_tools_v1",
    status: "completed",
    version: 1,
    snapshot_version: 2,
    current_stage: "workflow_completion",
    primary_node_execution_id: "candidate-node",
    active_node_execution_ids: [],
    model_call_count: 1,
    max_model_calls: 0,
    node_count: 1,
    max_nodes: 30,
    started_at: "2026-10-27T00:00:00Z",
    completed_at: "2026-10-27T00:00:01Z",
    created_at: "2026-10-27T00:00:00Z",
    updated_at: "2026-10-27T00:00:01Z",
    error: null,
  };
  const result = {
    ...summary,
    nodes: [candidateNode(answer)],
    aggregate_usage: {
      input_tokens: 10,
      output_tokens: 5,
      cached_tokens: 0,
      estimated_cost: "0",
      model_call_count: 1,
      tool_call_count: 1,
    },
    final_output: {
      answer_type: "text",
      final_text: answer,
      completed_objectives: [],
      unresolved_items: [],
      warnings: [],
      recommended_next_actions: [],
      source_agent_run_ids: [],
      source_handoff_ids: [],
      source_artifact_ids: [],
      source_evaluation_ids: [],
      assistant_message_id: "message-assistant",
    },
    completion: {
      model_attempt_ids: [],
      total_duration_ms: 1000,
    },
  };
  const longUrl = `https://example.com/research/${"long-segment-".repeat(14)}summary`;
  let sourceReads = 0;
  let writeRequests = 0;
  let oldEvidenceRequested = false;
  let releaseOldEvidence!: () => void;
  const oldEvidenceGate = new Promise<void>((resolve) => {
    releaseOldEvidence = resolve;
  });
  page.on("request", (request) => {
    if (request.url().includes("/api/nexus/") && request.method() !== "GET") {
      writeRequests += 1;
    }
  });

  await installWorkspaceBase(page, []);
  await page.route("**/api/nexus/sessions/session-1/messages/latest?*", (route) =>
    fulfillJson(route, { items: messages, next_cursor: null, has_more: false, limit: 100 }));
  await page.route("**/api/nexus/sessions/session-1", (route) => fulfillJson(route, SESSION));
  await page.route("**/api/nexus/sessions/session-1/latest-run", (route) => fulfillJson(route, run));
  await page.route("**/api/nexus/runs/run-1/agent-workflow", (route) => fulfillJson(route, summary));
  await page.route("**/api/nexus/agent-workflows/workflow-1/events?*", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }));
  await page.route("**/api/nexus/agent-workflows/workflow-1/result", (route) =>
    fulfillJson(route, result));
  await page.route("**/api/nexus/runs/run-1", (route) => fulfillJson(route, run));
  await page.route("**/api/nexus/runs/run-2", async (route) => {
    oldEvidenceRequested = true;
    await oldEvidenceGate;
    return fulfillJson(route, oldRun);
  });
  await page.route("**/api/nexus/artifacts?*", (route) =>
    fulfillJson(route, { items: [], next_cursor: null }));
  await page.route("**/api/nexus/agent-workflows/workflow-1/nodes/candidate-node/claim-sources?*", (route) => {
    sourceReads += 1;
    return fulfillJson(route, {
      sources: [{
        filename: "Web search results",
        source_kind: "web",
        url: longUrl,
        read_by: "worker",
        source_valid: true,
        has_more: false,
        total_blocks: 1,
        extraction_status: "complete",
        warnings: [],
        blocks: [{
          title: "网页片段标题",
          url: longUrl,
          text: "这是服务端保存的网页片段。",
          cells: null,
          line_start: null,
          line_end: null,
          paragraph_number: null,
          page_number: null,
          table_number: null,
          row_number: null,
          sheet_name: null,
          cell_range: null,
          slide_number: null,
        }],
      }],
    });
  });

  await page.goto("/c/session-1");
  await expect(page.getByText(answer, { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /研究项目/ })).toHaveAttribute(
    "href",
    "/projects?project=p1",
  );
  const citation = page.getByRole("link", { name: "查看引用 S1 的来源" });
  await expect(citation).toBeVisible();
  await page.locator("article.assistant-message")
    .filter({ hasText: "旧回答需要先查看证据。" })
    .getByRole("button", { name: "查看运行证据 ↗" })
    .click();
  await expect.poll(() => oldEvidenceRequested).toBe(true);

  const closeInspector = page.getByRole("button", { name: "关闭运行详情" });
  await closeInspector.click();
  await expect(page.locator('aside[aria-label="运行详情"]')).not.toHaveClass(/is-open/);
  if ((page.viewportSize()?.width ?? 0) < 580) {
    await page.getByRole("button", { name: "收起输入框", exact: true }).click();
  }
  await citation.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await citation.click();
  releaseOldEvidence();

  await expect.poll(() => sourceReads).toBeGreaterThan(0);
  await expect(page.locator(".claim-item.is-open")).toContainText(answer);
  await expect(page.locator(".inspector .eyebrow").first()).toHaveText("当前回复");
  await expect(page.locator(".claim-source-block-title")).toContainText("网页片段标题");
  await expect(page.getByText(/当前接口未返回二者明确类型/)).toBeVisible();
  await expect(page.getByText(/文件已解析|文件结论/)).toHaveCount(0);
  const sourceUrl = page.locator(".claim-source-url").first();
  await expect(sourceUrl).toHaveAttribute("href", longUrl);
  expect(await sourceUrl.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  expect(writeRequests).toBe(0);

  await page.screenshot({
    path: `../../output/playwright/web-citation-${test.info().project.name}.png`,
    fullPage: true,
  });
});

test("keeps composer send and stop controls clear of the 1200px inspector", async ({
  page,
}) => {
  test.skip(test.info().project.name !== "chromium", "One explicit 1200px desktop viewport");
  await page.setViewportSize({ width: 1200, height: 800 });
  const run = {
    run_id: "run-1",
    user_id: "nexuspilot-web",
    session_id: "session-1",
    user_request: "Long running task",
    run_type: "agent",
    status: "running",
    budget_limit: null,
    cost_used: "0",
    started_at: "2026-10-27T00:00:00Z",
    completed_at: null,
    created_at: "2026-10-27T00:00:00Z",
    updated_at: "2026-10-27T00:00:01Z",
    attempts: [],
    tasks: [],
    artifacts: [],
    tasks_has_more: false,
    attempts_has_more: false,
    artifacts_has_more: false,
  };
  const summary = {
    workflow_execution_id: "workflow-running",
    run_id: "run-1",
    workflow_name: "general_agent",
    workflow_version: "1.1.0",
    execution_profile: "general_tools_v1",
    status: "running",
    version: 1,
    snapshot_version: 1,
    current_stage: "worker_execution",
    primary_node_execution_id: null,
    active_node_execution_ids: [],
    model_call_count: 1,
    max_model_calls: 0,
    node_count: 0,
    max_nodes: 30,
    started_at: "2026-10-27T00:00:00Z",
    completed_at: null,
    created_at: "2026-10-27T00:00:00Z",
    updated_at: "2026-10-27T00:00:01Z",
    error: null,
  };
  await installWorkspaceBase(page, []);
  await page.route("**/api/nexus/sessions", (route) => route.request().method() === "POST"
    ? fulfillJson(route, { ...SESSION, title: "运行中的长任务" }, 201)
    : route.fallback());
  await page.route("**/api/nexus/sessions/session-1", (route) => fulfillJson(route, {
    ...SESSION,
    title: "运行中的长任务",
  }));
  await page.route("**/api/nexus/sessions/session-1/turns", (route) => fulfillJson(route, {
    run: { ...run, status: "pending" },
    message: {
      message_id: "message-user",
      session_id: "session-1",
      run_id: "run-1",
      role: "user",
      content_type: "text",
      content_text: route.request().postDataJSON().content_text,
      content_uri: null,
      sequence: 1,
      token_count: 4,
      metadata_json: {},
      created_at: "2026-10-27T00:00:00Z",
    },
  }, 201));
  await page.route("**/api/nexus/runs/run-1/agent-workflows", (route) =>
    route.fulfill({ status: 201, contentType: "text/event-stream", body: "" }));
  await page.route("**/api/nexus/runs/run-1/agent-workflow", (route) => fulfillJson(route, summary));
  await page.route("**/api/nexus/agent-workflows/workflow-running/events?*", (route) =>
    route.fulfill({ status: 200, contentType: "text/event-stream", body: "" }));
  await page.route("**/api/nexus/agent-workflows/workflow-running/result", (route) =>
    fulfillJson(route, {
      ...summary,
      nodes: [],
      aggregate_usage: {
        input_tokens: 1,
        output_tokens: 0,
        cached_tokens: 0,
        estimated_cost: "0",
        model_call_count: 1,
        tool_call_count: 0,
      },
      final_output: null,
      completion: null,
    }));
  await page.route("**/api/nexus/runs/run-1", (route) => fulfillJson(route, run));

  await page.goto("/");
  const draft = Array.from({ length: 8 }, (_, index) => `第 ${index + 1} 行仍需保留的草稿`).join("\n");
  const composer = page.getByRole("textbox", { name: "消息内容" });
  await composer.fill(draft);
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(page.getByRole("button", { name: "关闭运行详情" })).toBeVisible();
  await page.getByRole("button", { name: "关闭运行详情" }).click();
  await expect(composer).toHaveValue(draft);
  await page.getByRole("button", { name: "运行详情", exact: true }).click();
  await expect(composer).toHaveValue(draft);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();

  const stop = page.getByRole("button", { name: "取消工作流" });
  await expect(stop).toBeVisible();
  const geometry = await page.evaluate(() => {
    const sendButton = document.querySelector<HTMLElement>(".send-button");
    const inspector = document.querySelector<HTMLElement>(".inspector");
    if (!sendButton || !inspector) return null;
    const send = sendButton.getBoundingClientRect();
    const panel = inspector.getBoundingClientRect();
    const overlaps = send.left < panel.right && send.right > panel.left
      && send.top < panel.bottom && send.bottom > panel.top;
    const topElement = document.elementFromPoint(
      send.left + send.width / 2,
      send.top + send.height / 2,
    );
    return {
      overlaps,
      sendInViewport: send.left >= 0 && send.right <= window.innerWidth
        && send.top >= 0 && send.bottom <= window.innerHeight,
      topmost: topElement?.closest(".send-button") === sendButton,
    };
  });
  expect(geometry).toEqual({ overlaps: false, sendInViewport: true, topmost: true });

  await page.screenshot({
    path: "../../output/playwright/inspector-1200-running.png",
    fullPage: true,
  });
});
