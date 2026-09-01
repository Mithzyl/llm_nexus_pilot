import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  FilePickerButton,
  PendingUploadFiles,
} from "../components/composer/FilePickerButton";
import { ContextEvidencePanel } from "../components/context/ContextEvidencePanel";

test("renders one direct file picker button without a deprecated context menu", () => {
  const pickerMarkup = renderToStaticMarkup(
    createElement(FilePickerButton, {
      selectedFileCount: 1,
      disabled: false,
      onFilesSelected: () => undefined,
    }),
  );
  const fileMarkup = renderToStaticMarkup(
    createElement(PendingUploadFiles, {
      files: [{ name: "design.pdf", size: 2048, lastModified: 1 }],
      disabled: false,
      onRemove: () => undefined,
    }),
  );
  const markup = `${pickerMarkup}${fileMarkup}`;

  assert.match(pickerMarkup, />＋<\/span>文件/);
  assert.match(pickerMarkup, /type="file"/);
  assert.match(pickerMarkup, /aria-label="选择文件"/);
  assert.doesNotMatch(pickerMarkup, /上下文|添加文件|aria-expanded|popover/i);
  assert.match(markup, /design\.pdf/);
  assert.match(markup, /2 KB/);
  assert.match(markup, /待上传/);
  assert.doesNotMatch(markup, /Prompt/i);
  assert.doesNotMatch(markup, /artifact[_ -]?id/i);
});

test("renders Context evidence metadata without source content or identifiers", () => {
  const markup = renderToStaticMarkup(
    createElement(ContextEvidencePanel, {
      state: "ready",
      errorMessage: null,
      evidence: {
        context_build_id: "context-secret-id",
        catalog_version_id: "catalog-version-id",
        user_id: "nexuspilot-web",
        session_id: "session-1",
        run_id: "run-1",
        current_user_message_id: "message-1",
        provider: "openai",
        model: "test-model",
        policy_version: "context_policy.v2",
        token_budget: 8192,
        reserved_output_tokens: 0,
        input_token_estimate: 42,
        status: "completed",
        created_at: "2026-08-27T00:00:00Z",
        sources: [
          {
            source_type: "prompt_release",
            source_id: "source-secret-id",
            source_version: "3",
            trust_level: "platform_instruction",
            is_required: true,
            token_estimate: 42,
            selection_status: "selected",
            exclusion_reason: null,
            content_hash: "secret-hash",
          },
          {
            source_type: "message",
            source_id: "message-secret-id",
            source_version: null,
            trust_level: "user_input",
            is_required: true,
            token_estimate: 12,
            selection_status: "selected",
            exclusion_reason: null,
            content_hash: "message-secret-hash",
          },
        ],
      },
    }),
  );

  assert.match(markup, /模型上下文/);
  assert.match(markup, /会话消息/);
  assert.match(markup, /user_input/);
  assert.doesNotMatch(markup, /Prompt/i);
  assert.doesNotMatch(markup, /platform_instruction/);
  assert.doesNotMatch(markup, /source-secret-id/);
  assert.doesNotMatch(markup, /secret-hash/);
  assert.doesNotMatch(markup, /context-secret-id/);
});
