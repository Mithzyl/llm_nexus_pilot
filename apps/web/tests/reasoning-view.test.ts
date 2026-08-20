import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ReasoningView } from "../components/reasoning/ReasoningView";

test("renders raw and summary disclosures collapsed with accessible state", () => {
  const rawMarkup = renderToStaticMarkup(
    createElement(ReasoningView, {
      presentation: {
        kind: "reasoning.raw",
        block_id: "attempt-1:raw",
        status: "completed",
        text: "<img src=x onerror=alert(1)>",
      },
    }),
  );
  const summaryMarkup = renderToStaticMarkup(
    createElement(ReasoningView, {
      presentation: {
        kind: "reasoning.summary",
        block_id: "attempt-1:summary",
        status: "running",
        text: "latest summary",
      },
    }),
  );

  assert.match(rawMarkup, /思考过程/);
  assert.match(rawMarkup, /aria-expanded="false"/);
  assert.doesNotMatch(rawMarkup, /<img src=x/);
  assert.match(rawMarkup, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(summaryMarkup, /思考摘要/);
  assert.match(summaryMarkup, /latest summary/);
});

test("hides completed status in chat and retains it in trajectory", () => {
  const completedStatus = {
    kind: "reasoning.status" as const,
    block_id: "attempt-1:hidden",
    status: "completed" as const,
    reasoning_tokens: 8,
  };
  const chatMarkup = renderToStaticMarkup(
    createElement(ReasoningView, { presentation: completedStatus }),
  );
  const trajectoryMarkup = renderToStaticMarkup(
    createElement(ReasoningView, {
      presentation: completedStatus,
      mode: "trajectory",
    }),
  );

  assert.equal(chatMarkup, "");
  assert.match(trajectoryMarkup, /推理内容不可见/);
  assert.match(trajectoryMarkup, /Reasoning Token/);
  assert.match(trajectoryMarkup, />8</);
});
