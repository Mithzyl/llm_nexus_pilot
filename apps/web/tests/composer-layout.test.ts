import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { CompactSelect } from "../components/composer/CompactSelect";
import {
  composerConversationClearance,
  composerTextareaHeight,
  nextComposerOptionIndex,
  shouldSendComposerKey,
} from "../lib/composer-layout";

/** Send only focused, unmodified Enter; preserve newline, IME selection and repeat safety. */
test("composer Enter only submits a focused eligible draft outside IME composition", () => {
  const key = { key: "Enter", shiftKey: false, altKey: false, ctrlKey: false, metaKey: false,
    isComposing: false, keyCode: 13, repeat: false, focused: true, canSend: true };
  assert.equal(shouldSendComposerKey(key), true);
  for (const override of [{ shiftKey: true }, { altKey: true }, { ctrlKey: true }, { metaKey: true },
    { isComposing: true }, { keyCode: 229 }, { repeat: true }, { focused: false },
    { canSend: false }, { key: "a" }]) {
    assert.equal(shouldSendComposerKey({ ...key, ...override }), false);
  }
});

test("expands the composer for long drafts while keeping a bounded internal scroll area", () => {
  assert.equal(composerTextareaHeight(18), 44);
  assert.equal(composerTextareaHeight(156), 156);
  assert.equal(composerTextareaHeight(480), 320);
});

test("keeps conversation actions above the complete measured composer", () => {
  assert.equal(composerConversationClearance(128), 152);
  assert.equal(composerConversationClearance(326.4), 351);
});

test("moves compact listbox selection without escaping the available options", () => {
  assert.equal(nextComposerOptionIndex(0, 3, 1), 1);
  assert.equal(nextComposerOptionIndex(2, 3, 1), 0);
  assert.equal(nextComposerOptionIndex(0, 3, -1), 2);
  assert.equal(nextComposerOptionIndex(1, 0, 1), -1);
});

test("renders one concise accessible trigger without native select chrome", () => {
  const markup = renderToStaticMarkup(
    createElement(CompactSelect, {
      label: "Agent 审核策略",
      value: "always",
      options: [
        { value: "always", label: "始终审核" },
        { value: "never", label: "不审核" },
      ],
      onChange: () => undefined,
    }),
  );

  assert.match(markup, /aria-label="Agent 审核策略"/);
  assert.match(markup, /aria-haspopup="listbox"/);
  assert.match(markup, />始终审核</);
  assert.doesNotMatch(markup, /<select/);
});
