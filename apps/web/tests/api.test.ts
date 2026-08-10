import assert from "node:assert/strict";
import test from "node:test";

import { getLatestRun, listLatestMessagePage, listSessions } from "../lib/api";

const originalFetch = globalThis.fetch;

test("filters session list by the development user", async () => {
  const requests: string[] = [];
  globalThis.fetch = (async (input) => {
    requests.push(String(input));
    return Response.json({ items: [], next_cursor: null, has_more: false, limit: 50 });
  }) as typeof fetch;

  try {
    await listSessions();
    assert.match(requests[0], /sessions\?user_id=nexuspilot-web&limit=50$/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("loads one complete newest message page without per-message fetches", async () => {
  const requests: string[] = [];
  globalThis.fetch = (async (input) => {
    const url = String(input);
    requests.push(url);
    if (url.includes("sessions/s-1/messages/latest")) {
      return Response.json({
        items: [
          {
            message_id: "m-1",
            session_id: "s-1",
            run_id: null,
            parent_message_id: null,
            role: "user",
            content_type: "text",
            content_text: "完整消息内容，超过预览长度也应保持不变。",
            content_uri: null,
            sequence: 1,
            token_count: 4,
            metadata_json: {},
            created_at: "2026-08-03T00:00:00Z",
          },
        ],
        next_cursor: "cursor-2",
        has_more: true,
        limit: 100,
      });
    }
    throw new Error(`unexpected request: ${url}`);
  }) as typeof fetch;

  try {
    const page = await listLatestMessagePage("s-1");
    assert.equal(page.items[0].content_text, "完整消息内容，超过预览长度也应保持不变。");
    assert.equal(page.has_more, true);
    assert.equal(page.next_cursor, "cursor-2");
    assert.equal(requests.length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("restores the latest run detail contract including attempt facts", async () => {
  const requests: string[] = [];
  globalThis.fetch = (async (input) => {
    requests.push(String(input));
    return Response.json({
      run_id: "run-2",
      user_id: "nexuspilot-web",
      session_id: "s-1",
      user_request: "latest",
      run_type: "general",
      status: "completed",
      budget_limit: null,
      cost_used: "0.42",
      started_at: "2026-08-03T00:00:01Z",
      completed_at: "2026-08-03T00:00:02Z",
      created_at: "2026-08-03T00:00:00Z",
      updated_at: "2026-08-03T00:00:02Z",
      tasks: [],
      attempts: [
        {
          attempt_id: "attempt-2",
          run_id: "run-2",
          task_id: null,
          provider: "deepseek",
          model: "deepseek-v4-flash",
          request_type: "generation",
          retry_count: 0,
          status: "completed",
          input_tokens: 120,
          output_tokens: 44,
          cached_tokens: null,
          estimated_cost: "0.42",
          latency_ms: 800,
          error_code: null,
          started_at: "2026-08-03T00:00:01Z",
          completed_at: "2026-08-03T00:00:02Z",
        },
      ],
      artifacts: [],
      tasks_has_more: false,
      attempts_has_more: false,
      artifacts_has_more: false,
    });
  }) as typeof fetch;

  try {
    const run = await getLatestRun("s-1");
    assert.match(requests[0], /sessions\/s-1\/latest-run$/);
    assert.equal(run.attempts[0].provider, "deepseek");
    assert.equal(run.attempts[0].model, "deepseek-v4-flash");
    assert.equal(run.attempts[0].input_tokens, 120);
    assert.equal(run.attempts[0].output_tokens, 44);
    assert.equal(run.attempts[0].estimated_cost, "0.42");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
