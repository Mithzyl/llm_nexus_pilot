import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { ContextBindings } from "../components/context/ContextBindings";
import { ContextEvidencePanel } from "../components/context/ContextEvidencePanel";
import {
  createContextSourceReferences,
  ContextBindingValidationError,
} from "../lib/context-bindings";

test("creates version-pinned Prompt references with typed variables", () => {
  const references = createContextSourceReferences(
    {
      template_name: "explain-topic",
      purpose: "Explain one topic",
      current_version_number: 3,
      variable_schema: {
        topic: "string",
        count: "integer",
        confidence: "number",
        concise: "boolean",
      },
    },
    {
      topic: "cache behavior",
      count: "4",
      confidence: "0.75",
      concise: "true",
    },
  );

  assert.deepEqual(references, {
    prompt: {
      template_name: "explain-topic",
      version_number: 3,
      variables: {
        topic: "cache behavior",
        count: 4,
        confidence: 0.75,
        concise: true,
      },
    },
    artifact_ids: [],
  });
});

test("rejects incomplete or invalid Prompt variables before creating a Run", () => {
  const prompt = {
    template_name: "count-items",
    purpose: null,
    current_version_number: 1,
    variable_schema: { count: "integer" as const },
  };

  assert.throws(
    () => createContextSourceReferences(prompt, { count: "three" }),
    ContextBindingValidationError,
  );
  assert.throws(
    () => createContextSourceReferences(prompt, {}),
    ContextBindingValidationError,
  );
});

test("renders Prompt controls while keeping file binding unavailable without upload support", () => {
  const markup = renderToStaticMarkup(
    createElement(ContextBindings, {
      promptOptions: [
        {
          template_name: "explain-topic",
          purpose: "Explain one topic",
          current_version_number: 3,
          variable_schema: { topic: "string" },
        },
      ],
      promptOptionsState: "ready",
      selectedPromptName: "explain-topic",
      promptVariableDrafts: { topic: "cache" },
      disabled: false,
      onPromptChange: () => undefined,
      onVariableChange: () => undefined,
      onRetry: () => undefined,
    }),
  );

  assert.match(markup, /explain-topic · v3/);
  assert.match(markup, /等待上传功能/);
  assert.doesNotMatch(markup, /type="file"/);
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
        ],
      },
    }),
  );

  assert.match(markup, /模型上下文/);
  assert.match(markup, /platform_instruction/);
  assert.doesNotMatch(markup, /source-secret-id/);
  assert.doesNotMatch(markup, /secret-hash/);
  assert.doesNotMatch(markup, /context-secret-id/);
});
