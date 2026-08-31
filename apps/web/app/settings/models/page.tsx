"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { listModels } from "../../../lib/api";
import {
  modelAvailabilityLabel,
  modelCapabilityLabels,
  modelReasoningPolicyLabels,
} from "../../../lib/model-directory-view";
import type { ModelDirectoryEntry } from "../../../lib/types";

/** Render the internal read-only stable model directory without exposing management credentials. */
export default function ModelSettingsPage() {
  const [models, setModels] = useState<ModelDirectoryEntry[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");

  /** Reload server-owned model facts without inferring availability from model names. */
  function reloadDirectory() {
    const controller = new AbortController();
    setState("loading");
    void listModels(controller.signal, false)
      .then((entries) => {
        setModels(entries);
        setState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setState("error");
      });
    return controller;
  }

  useEffect(() => {
    const controller = new AbortController();
    void listModels(controller.signal, false)
      .then((entries) => {
        setModels(entries);
        setState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setState("error");
      });
    return () => controller.abort();
  }, []);

  return (
    <main className="settings-page">
      <header className="settings-header">
        <div>
          <p className="eyebrow">内部模型目录</p>
          <h1>模型与能力</h1>
          <p>可用状态和能力来自服务端稳定模型记录；此页面不修改模型或供应商凭据。</p>
        </div>
        <Link href="/">返回对话</Link>
      </header>

      {state === "loading" && <p className="route-state" role="status">正在读取模型目录…</p>}
      {state === "error" && (
        <section className="route-state" role="alert">
          <strong>模型目录暂不可用</strong>
          <p>对话历史不受影响。确认 API 配置后可以重新读取。</p>
          <button type="button" onClick={() => reloadDirectory()}>重新读取</button>
        </section>
      )}
      {state === "ready" && models.length === 0 && (
        <p className="route-state">平台尚未登记模型。</p>
      )}
      {state === "ready" && models.length > 0 && (
        <div className="model-directory-list" aria-label="模型目录">
          {models.map((model) => {
            const capabilities = modelCapabilityLabels(model);
            const reasoningPolicy = modelReasoningPolicyLabels(model);
            return (
              <article className="model-directory-item" key={model.model_id}>
                <div className="model-directory-heading">
                  <div>
                    <small>{model.provider}</small>
                    <h2>{model.display_name}</h2>
                    <code>{model.model_identifier}</code>
                  </div>
                  <span data-selectable={model.selectable}>{modelAvailabilityLabel(model)}</span>
                </div>
                {model.description && <p>{model.description}</p>}
                <dl>
                  <div><dt>能力版本</dt><dd>{model.catalog_version ?? "尚未确认"}</dd></div>
                  <div><dt>上下文窗口</dt><dd>{model.context_window?.toLocaleString("zh-CN") ?? "未知"}</dd></div>
                  <div><dt>推理展示</dt><dd>{model.reasoning_capabilities.presentation}</dd></div>
                  <div><dt>Tokenizer</dt><dd>{model.tokenizer_name ?? "未知"}</dd></div>
                  <div><dt>默认思考</dt><dd>{reasoningPolicy.defaultLabel}</dd></div>
                  <div><dt>可选程度</dt><dd>{reasoningPolicy.supportedLabel}</dd></div>
                  <div><dt>关闭能力</dt><dd>{reasoningPolicy.disableLabel}</dd></div>
                  <div><dt>策略版本</dt><dd>{model.reasoning_profile_version}</dd></div>
                </dl>
                <ul aria-label="已确认能力">
                  {(capabilities.length > 0 ? capabilities : ["暂无已确认能力"]).map((capability) => (
                    <li key={capability}>{capability}</li>
                  ))}
                </ul>
              </article>
            );
          })}
        </div>
      )}
    </main>
  );
}
