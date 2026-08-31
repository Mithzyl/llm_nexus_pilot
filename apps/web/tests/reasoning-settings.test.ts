import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ReasoningSettings } from "../components/composer/ReasoningSettings";
import {
  availableReasoningDisplayPolicies,
  createReasoningConfiguration,
  defaultReasoningDisplayPolicy,
  resolveReasoningDisplayPolicy,
} from "../lib/reasoning-settings";

test("creates one provider-neutral reasoning request for response and Agent modes", () => {
  assert.deepEqual(createReasoningConfiguration("xhigh"), {
    enabled: true,
    effort: "xhigh",
  });
  assert.deepEqual(createReasoningConfiguration("disabled"), { enabled: false });
});

test("matches the default display policy to the model presentation capability", () => {
  assert.equal(defaultReasoningDisplayPolicy("raw"), "provider-visible");
  assert.equal(defaultReasoningDisplayPolicy("summary"), "summary-only");
  assert.equal(defaultReasoningDisplayPolicy("hidden"), "hidden");
  assert.equal(defaultReasoningDisplayPolicy("none"), "hidden");

  assert.deepEqual(availableReasoningDisplayPolicies("raw"), [
    "hidden",
    "provider-visible",
  ]);
  assert.deepEqual(availableReasoningDisplayPolicies("summary"), [
    "hidden",
    "summary-only",
  ]);
  assert.deepEqual(availableReasoningDisplayPolicies("hidden"), []);
});

test("preserves an explicit display preference without exposing more detail", () => {
  assert.equal(resolveReasoningDisplayPolicy("raw", "hidden"), "hidden");
  assert.equal(
    resolveReasoningDisplayPolicy("summary", "provider-visible"),
    "summary-only",
  );
  assert.equal(resolveReasoningDisplayPolicy("raw", "summary-only"), "hidden");
  assert.equal(resolveReasoningDisplayPolicy("none", "provider-visible"), "hidden");
});

test("renders effort and presentation choices behind one Composer entry", () => {
  const markup = renderToStaticMarkup(
    createElement(ReasoningSettings, {
      effort: "medium",
      supportedEfforts: ["minimal", "medium", "xhigh"],
      supportsDisable: true,
      displayPolicy: "summary-only",
      presentationCapability: "summary",
      disabled: false,
      onEffortChange: () => undefined,
      onDisplayPolicyChange: () => undefined,
    }),
  );

  assert.match(markup, /aria-label="思考设置"/);
  assert.match(markup, /中等/);
  assert.match(markup, /推理展示/);
  assert.match(markup, /显示摘要/);
  assert.doesNotMatch(markup, /显示推理/);
  assert.match(markup, /关闭/);
});
