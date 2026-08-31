import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ReasoningSettings } from "../components/composer/ReasoningSettings";
import { createReasoningConfiguration } from "../lib/reasoning-settings";

test("creates one provider-neutral reasoning request for response and Agent modes", () => {
  assert.deepEqual(createReasoningConfiguration("xhigh"), {
    enabled: true,
    effort: "xhigh",
  });
  assert.deepEqual(createReasoningConfiguration("disabled"), { enabled: false });
});

test("renders effort and presentation choices behind one Composer entry", () => {
  const markup = renderToStaticMarkup(
    createElement(ReasoningSettings, {
      effort: "medium",
      supportedEfforts: ["minimal", "medium", "xhigh"],
      supportsDisable: true,
      displayPolicy: "summary-only",
      showDisplayPolicy: true,
      disabled: false,
      onEffortChange: () => undefined,
      onDisplayPolicyChange: () => undefined,
    }),
  );

  assert.match(markup, /aria-label="思考设置"/);
  assert.match(markup, /中等/);
  assert.match(markup, /推理展示/);
  assert.match(markup, /仅摘要/);
  assert.match(markup, /关闭/);
});
