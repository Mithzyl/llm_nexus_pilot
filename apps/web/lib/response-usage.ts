import type { RunDetail } from "./types";

/** Restore quick-response totals across file-tool rounds; incomplete/unknown totals stay unknown. */
export function quickResponseUsageFromRun(run: RunDetail) {
  const latest = run.attempts.at(-1);
  if (!latest) return { input: null, output: null, cost: String(run.cost_used ?? "—") };
  if (latest.request_type !== "file_tool_round") return {
    input: latest.input_tokens, output: latest.output_tokens,
    cost: String(latest.estimated_cost ?? run.cost_used ?? "—"),
  };
  const complete = !run.attempts_has_more && run.attempts.every((attempt) =>
    attempt.request_type === "file_tool_round" && attempt.task_id === null &&
    attempt.run_id === run.run_id && attempt.status === "completed",
  );
  return {
    input: complete && run.attempts.every((a) => a.input_tokens !== null)
      ? run.attempts.reduce((total, a) => total + a.input_tokens!, 0) : null,
    output: complete && run.attempts.every((a) => a.output_tokens !== null)
      ? run.attempts.reduce((total, a) => total + a.output_tokens!, 0) : null,
    cost: complete && run.attempts.every((a) => a.estimated_cost !== null)
      ? String(run.cost_used ?? "—") : "—",
  };
}
