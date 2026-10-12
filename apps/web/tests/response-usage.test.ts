import assert from "node:assert/strict";
import test from "node:test";
import { quickResponseUsageFromRun } from "../lib/response-usage";
import type { ModelAttempt, RunDetail } from "../lib/types";

/** Reloaded multi-round responses sum real calls without turning missing accounting into zero. */
test("restores file-tool totals and preserves partial or unknown usage", () => {
  const attempt = { request_type: "file_tool_round", run_id: "run", task_id: null,
    status: "completed", input_tokens: 30, output_tokens: 10, estimated_cost: "0.01",
  } as ModelAttempt;
  const run = { run_id: "run", attempts_has_more: false, cost_used: "0.03",
    attempts: [attempt, attempt, attempt],
  } as RunDetail;
  assert.deepEqual(quickResponseUsageFromRun(run), { input: 90, output: 30, cost: "0.03" });
  assert.deepEqual(quickResponseUsageFromRun({ ...run, attempts_has_more: true }), {
    input: null, output: null, cost: "—",
  });
  assert.equal(quickResponseUsageFromRun({ ...run, attempts: [
    attempt, { ...attempt, input_tokens: null },
  ] }).input, null);
});
