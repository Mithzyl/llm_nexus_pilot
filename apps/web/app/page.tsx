"use client";

import {
  createMessage,
  createRun,
  createSession,
  DEVELOPMENT_USER_ID,
  ensureDevelopmentUser,
  getLatestRun,
  getRun,
  listLatestMessagePage,
  listProviders,
  listSessions,
  NexusApiError,
  nexusFetch,
} from "../lib/api";
import type {
  Message,
  MessageRole,
  MessageSummary,
  ProviderName,
  Run,
  RunDetail,
  Session,
} from "../lib/types";
import { readStreamEvents } from "../lib/sse";
import { hasSavedAssistantForLatestTurn } from "../lib/history";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

const DEFAULT_MODEL = "deepseek-v4-flash";

type LocalMessage = MessageSummary & {
  local_id: string;
  content: string;
  pending?: boolean;
  saving?: boolean;
  saveFailed?: boolean;
  failed?: boolean;
  cancelled?: boolean;
};

type ConnectionState = "checking" | "connected" | "unavailable";

/**
 * Format server timestamps into the compact time label used by the workspace.
 */
function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/**
 * Calculate a run duration only when both server timestamps are available.
 */
function formatDuration(run: Run | RunDetail | null): string {
  if (!run?.started_at || !run.completed_at) return "等待服务端确认";
  const duration = new Date(run.completed_at).getTime() - new Date(run.started_at).getTime();
  return `${Math.max(0, duration) / 1000} 秒`;
}

/** Format restored token usage without inventing values when an attempt is incomplete. */
function formatTokenTotal(usage: { input: number | null; output: number | null } | null): string {
  if (!usage || usage.input === null || usage.output === null) return "—";
  return String(usage.input + usage.output);
}

/**
 * Convert the full persisted API message into a renderable local message.
 */
function messageFromMessage(message: Message): LocalMessage {
  return {
    ...message,
    local_id: message.message_id,
    content_preview: message.content_text?.slice(0, 160) ?? null,
    content: message.content_text ?? "内容已保存为外部产物，请从运行详情查看。",
  };
}

/**
 * Create an optimistic local message while the durable API write is in flight.
 */
function createLocalMessage(
  role: Exclude<MessageRole, "system" | "tool">,
  content: string,
  runId?: string,
): LocalMessage {
  const now = new Date().toISOString();
  const localId = `local-${crypto.randomUUID()}`;
  return {
    message_id: localId,
    local_id: localId,
    session_id: "local",
    run_id: runId ?? null,
    parent_message_id: null,
    role,
    content_type: "text",
    content_preview: content.slice(0, 160),
    content_uri: null,
    sequence: 0,
    token_count: null,
    created_at: now,
    content,
  };
}

/**
 * Render the first NexusPilot conversation workspace and coordinate its durable API flow.
 */
export default function Home() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [providers, setProviders] = useState<string[]>([]);
  const [selectedProvider, setSelectedProvider] = useState<ProviderName | "">("");
  const [selectedModel, setSelectedModel] = useState(DEFAULT_MODEL);
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<Run | RunDetail | null>(null);
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [runUsage, setRunUsage] = useState<{ input: number | null; output: number | null; cost: string } | null>(null);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false);
  const [isInspectorOpen, setIsInspectorOpen] = useState(true);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isDark, setIsDark] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [responseCompleted, setResponseCompleted] = useState(false);
  const [assistantSaved, setAssistantSaved] = useState(false);
  const [assistantSaveFailed, setAssistantSaveFailed] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("checking");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const latestAssistantText = useRef("");
  const activeRequestController = useRef<AbortController | null>(null);

  useEffect(() => {
    const savedTheme = window.localStorage.getItem("nexuspilot-theme");
    // localStorage is the explicitly local theme preference; sync it once after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsDark(savedTheme === "dark");
  }, []);

  useEffect(() => {
    window.localStorage.setItem("nexuspilot-theme", isDark ? "dark" : "light");
  }, [isDark]);

  useEffect(() => {
    const mobileQuery = window.matchMedia("(max-width: 880px)");
    /** Keep the detail overlay closed on narrow screens while preserving desktop visibility. */
    const syncInspectorVisibility = () => setIsInspectorOpen(!mobileQuery.matches);
    // The media query is the source of truth for the initial responsive panel state.
    syncInspectorVisibility();
    mobileQuery.addEventListener("change", syncInspectorVisibility);
    return () => mobileQuery.removeEventListener("change", syncInspectorVisibility);
  }, []);

  useEffect(() => {
    let isMounted = true;
    void Promise.all([listSessions(), listProviders()])
      .then(([sessionPage, providerPayload]) => {
        if (!isMounted) return;
        setSessions(sessionPage.items.filter((session) => session.status === "active"));
        setProviders(providerPayload.providers);
        setSelectedProvider((providerPayload.providers[0] as ProviderName | undefined) ?? "");
        setConnectionState("connected");
      })
      .catch((error: unknown) => {
        if (!isMounted) return;
        setConnectionState("unavailable");
        setErrorMessage(error instanceof Error ? error.message : "无法连接 NexusPilot API");
      });

    return () => {
      isMounted = false;
    };
  }, []);

  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeSessionId) ?? null,
    [activeSessionId, sessions],
  );

  const lastUserMessage = [...messages].reverse().find((message) => message.role === "user");
  const lastAssistantMessage = [...messages].reverse().find((message) => message.role === "assistant");

  /** Restore the inspector from the newest persisted attempt rather than local defaults. */
  function restoreRunFacts(runDetail: RunDetail | null) {
    setRun(runDetail);
    const latestAttempt = runDetail?.attempts.at(-1);
    setAttemptId(latestAttempt?.attempt_id ?? null);
    if (latestAttempt) {
      setRunUsage({
        input: latestAttempt.input_tokens,
        output: latestAttempt.output_tokens,
        cost: String(latestAttempt.estimated_cost ?? runDetail?.cost_used ?? "—"),
      });
      setSelectedModel(latestAttempt.model);
      setProviders((current) =>
        current.includes(latestAttempt.provider) ? current : [latestAttempt.provider, ...current],
      );
      setSelectedProvider(latestAttempt.provider as ProviderName);
    } else if (runDetail) {
      setRunUsage({ input: null, output: null, cost: String(runDetail.cost_used ?? "—") });
    } else {
      setRunUsage(null);
    }
  }

  /**
   * Load one immutable conversation history and close the mobile navigation overlay.
   */
  async function handleSelectSession(sessionId: string) {
    setErrorMessage(null);
    setActiveSessionId(sessionId);
    setIsSidebarOpen(false);
    setRun(null);
    setResponseCompleted(false);
    setAssistantSaved(false);
    setAssistantSaveFailed(false);
    setRunUsage(null);
    setAttemptId(null);
    setMessageCursor(null);
    setHasMoreMessages(false);
    try {
      const [page, latestRun] = await Promise.all([
        listLatestMessagePage(sessionId),
        getLatestRun(sessionId).catch((error: unknown) => {
          if (error instanceof NexusApiError && error.status === 404) return null;
          throw error;
        }),
      ]);
      setMessages(page.items.map(messageFromMessage));
      setMessageCursor(page.next_cursor);
      setHasMoreMessages(page.has_more);
      restoreRunFacts(latestRun);
      const hasPersistedAssistant = hasSavedAssistantForLatestTurn(page.items);
      setResponseCompleted(hasPersistedAssistant);
      setAssistantSaved(hasPersistedAssistant);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法读取会话消息");
      setMessages([]);
      setMessageCursor(null);
      setHasMoreMessages(false);
    }
  }

  /**
   * Continue the explicit cursor pagination instead of silently dropping
   * messages after the first server page.
   */
  async function handleLoadMoreMessages() {
    if (!activeSessionId || !messageCursor || isLoadingMoreMessages) return;
    setIsLoadingMoreMessages(true);
    try {
      const page = await listLatestMessagePage(activeSessionId, messageCursor);
      setMessages((current) => [...page.items.map(messageFromMessage), ...current]);
      setMessageCursor(page.next_cursor);
      setHasMoreMessages(page.has_more);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法继续读取会话消息");
    } finally {
      setIsLoadingMoreMessages(false);
    }
  }

  /**
   * Reset the local conversation view without deleting any persisted session.
   */
  function handleNewConversation() {
    setActiveSessionId(null);
    setMessages([]);
    setRun(null);
    setAttemptId(null);
    setRunUsage(null);
    setResponseCompleted(false);
    setAssistantSaved(false);
    setAssistantSaveFailed(false);
    setMessageCursor(null);
    setHasMoreMessages(false);
    setErrorMessage(null);
    setDraft("");
    setIsSidebarOpen(false);
  }

  /**
   * Copy assistant text through the browser clipboard and provide visible feedback.
   */
  async function handleCopy(message: LocalMessage) {
    await navigator.clipboard.writeText(message.content);
    setCopiedMessageId(message.local_id);
    window.setTimeout(() => setCopiedMessageId(null), 1400);
  }

  /**
   * Abort the active browser stream and leave the run available for server-side inspection.
   */
  function handleStop() {
    activeRequestController.current?.abort();
  }

  /**
   * Submit one user turn through the durable user, session, run, message, and SSE flow.
   */
  async function handleSend() {
    const input = draft.trim();
    if (!input || isSending || !selectedProvider) return;

    setIsSending(true);
    setErrorMessage(null);
    setResponseCompleted(false);
    setAssistantSaved(false);
    setAssistantSaveFailed(false);
    latestAssistantText.current = "";
    const userMessage = createLocalMessage("user", input);
    setMessages((current) => [...current, userMessage]);
    setDraft("");

    try {
      try {
        await nexusFetch<unknown>(`users/${DEVELOPMENT_USER_ID}`);
      } catch (error) {
        if (!(error instanceof Error && "status" in error && error.status === 404)) throw error;
        await ensureDevelopmentUser(DEVELOPMENT_USER_ID);
      }

      let session = activeSession;
      if (!session) {
        session = await createSession(DEVELOPMENT_USER_ID, input.slice(0, 42));
        setSessions((current) => [session as Session, ...current]);
        setActiveSessionId(session.session_id);
      }

      await createMessage(session.session_id, {
        role: "user",
        content_text: input,
      });
      const createdRun = await createRun({
        user_id: DEVELOPMENT_USER_ID,
        session_id: session.session_id,
        user_request: input,
      });
      setRun(createdRun);

      const assistantMessage = createLocalMessage("assistant", "", createdRun.run_id);
      assistantMessage.pending = true;
      setMessages((current) => [...current, assistantMessage]);

      const response = await fetch("/api/nexus/responses", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        signal: (activeRequestController.current = new AbortController()).signal,
        body: JSON.stringify({
          run_id: createdRun.run_id,
          provider: selectedProvider,
          model: selectedModel,
          input,
          max_output_tokens: 1200,
          timeout_seconds: 60,
          idempotency_key: crypto.randomUUID(),
          stream: true,
        }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(body?.detail ?? `模型请求失败（${response.status}）`);
      }

      await readStreamEvents(response, async (event) => {
        const data = event.data;
        if (data.attempt_id) setAttemptId(data.attempt_id);
        if (event.type === "response.text.delta" && data.delta) {
          latestAssistantText.current += data.delta;
          setMessages((current) =>
            current.map((message) =>
              message.local_id === assistantMessage.local_id
                ? { ...message, content: latestAssistantText.current, content_preview: latestAssistantText.current }
                : message,
            ),
          );
        }
        if (event.type === "response.usage" && data.usage) {
          setRunUsage({
            input: data.usage.input_tokens ?? 0,
            output: data.usage.output_tokens ?? 0,
            cost: data.usage.estimated_cost ?? "—",
          });
        }
        if (event.type === "response.completed") {
          setResponseCompleted(true);
          const responseResult = data.response;
          const completedText = responseResult?.output_text ?? latestAssistantText.current;
          latestAssistantText.current = completedText;
          setAttemptId(data.attempt_id ?? responseResult?.id ?? null);
          setMessages((current) =>
            current.map((message) =>
              message.local_id === assistantMessage.local_id
                ? { ...message, content: completedText, content_preview: completedText, pending: false, saving: true }
                : message,
            ),
          );
          setRunUsage({
            input: responseResult?.usage.input_tokens ?? 0,
            output: responseResult?.usage.output_tokens ?? 0,
            cost: responseResult?.usage.estimated_cost ?? "—",
          });
          try {
            if (!completedText) throw new Error("响应没有可保存的文本。");
            await createMessage(session.session_id, {
              role: "assistant",
              content_text: completedText,
              run_id: createdRun.run_id,
            });
            setAssistantSaved(true);
            setMessages((current) =>
              current.map((message) =>
                message.local_id === assistantMessage.local_id ? { ...message, saving: false } : message,
              ),
            );
          } catch (error) {
            setAssistantSaveFailed(true);
            setErrorMessage(
              `响应已生成，但会话保存失败：${error instanceof Error ? error.message : "请稍后重试。"}`,
            );
            setMessages((current) =>
              current.map((message) =>
                message.local_id === assistantMessage.local_id
                  ? { ...message, saving: false, saveFailed: true }
                  : message,
              ),
            );
          }
          setRun(await getRun(createdRun.run_id));
        }
        if (event.type === "response.failed") {
          const message = data.error?.message ?? "模型生成失败，已保留已收到的部分文本。";
          setErrorMessage(message);
          setResponseCompleted(false);
          setAssistantSaved(false);
          setAssistantSaveFailed(false);
          setMessages((current) =>
            current.map((item) =>
              item.local_id === assistantMessage.local_id
                ? { ...item, pending: false, saving: false, failed: true }
                : item,
            ),
          );
        }
      });
    } catch (error) {
      if (activeRequestController.current?.signal.aborted) {
        setErrorMessage("已请求停止，运行详情仍以服务端最终状态为准。");
      } else {
        setErrorMessage(error instanceof Error ? error.message : "发送失败，请稍后重试。");
      }
      setMessages((current) =>
        current.map((item) =>
          item.pending
            ? {
                ...item,
                pending: false,
                failed: !(activeRequestController.current?.signal.aborted),
                cancelled: Boolean(activeRequestController.current?.signal.aborted),
              }
            : item.saving
              ? { ...item, saving: false, saveFailed: true, failed: true }
            : item,
        ),
      );
    } finally {
      activeRequestController.current = null;
      setIsSending(false);
    }
  }

  /**
   * Keep Enter for sending and Shift+Enter for a multiline composer value.
   */
  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void handleSend();
    }
  }

  const statusLabel = isSending
    ? "生成中"
    : assistantSaveFailed
      ? "保存失败"
      : assistantSaved
        ? "已保存"
        : responseCompleted
          ? "已生成，待保存"
          : run?.status === "failed"
            ? "失败"
            : "等待运行";

  return (
    <main className={`app-shell ${isDark ? "theme-dark" : ""}`}>
      <aside className={`sidebar ${isSidebarOpen ? "is-open" : ""}`} aria-label="会话导航">
        <div className="brand-row">
          <div className="brand-mark" aria-hidden="true"><span /></div>
          <span className="brand-name">NexusPilot</span>
          <button className="icon-button sidebar-close" onClick={() => setIsSidebarOpen(false)} aria-label="关闭会话列表">×</button>
        </div>

        <button className="new-chat" onClick={handleNewConversation}>
          <span aria-hidden="true">＋</span>
          新对话
          <kbd>⌘ K</kbd>
        </button>

        <div className="session-group">
          <p className="eyebrow">最近会话</p>
          <nav>
            {sessions.length === 0 ? (
              <p className="empty-sessions">发送第一条消息后，会话会出现在这里。</p>
            ) : sessions.map((session) => (
              <button
                key={session.session_id}
                className={`session-item ${activeSessionId === session.session_id ? "active" : ""}`}
                onClick={() => void handleSelectSession(session.session_id)}
              >
                <span className="session-copy">
                  <strong>{session.title ?? "未命名会话"}</strong>
                  <small>{session.status === "active" ? "可继续对话" : "已归档"}</small>
                </span>
                <time>{formatTime(session.updated_at)}</time>
              </button>
            ))}
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="environment-dot"><span />开发环境</div>
          <span className={`connection-label ${connectionState}`}>
            {connectionState === "connected" ? "API 已连接" : connectionState === "checking" ? "检查中" : "API 未连接"}
          </span>
        </div>
      </aside>

      {isSidebarOpen && <button className="mobile-scrim" onClick={() => setIsSidebarOpen(false)} aria-label="关闭会话列表" />}

      <section className="workspace">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setIsSidebarOpen(true)} aria-label="打开会话列表">☰</button>
          <div>
            <p className="eyebrow">当前会话</p>
            <h1>{activeSession?.title ?? "新对话"}</h1>
          </div>
          <div className="topbar-actions">
            <button className="icon-button" onClick={() => setIsDark((current) => !current)} aria-label="切换明暗主题">{isDark ? "☀" : "◐"}</button>
            <button className={`detail-toggle ${isInspectorOpen ? "active" : ""}`} onClick={() => setIsInspectorOpen((current) => !current)}>
              <span className="status-dot" />运行详情
            </button>
          </div>
        </header>

        <div className="conversation-stage">
          <div className="conversation-scroll">
            {messages.length === 0 ? (
              <div className="empty-state">
                <div className="empty-state-mark" aria-hidden="true">N</div>
                <p className="eyebrow">可审计的模型工作台</p>
                <h2>从一个明确的问题开始</h2>
                <p>每次请求都会关联会话、运行与模型调用证据。当前上下文只来自你明确提交的内容。</p>
                <div className="suggestion-row" aria-label="快速开始建议">
                  {["比较当前模型路由策略", "解释一次请求的失败边界", "规划长文本任务的模型选择"].map((suggestion) => (
                    <button key={suggestion} onClick={() => setDraft(suggestion)}>{suggestion}</button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                {hasMoreMessages && (
                  <button className="history-more" onClick={() => void handleLoadMoreMessages()} disabled={isLoadingMoreMessages}>
                    {isLoadingMoreMessages ? "正在加载消息…" : "加载更多消息"}
                  </button>
                )}
                {messages.map((message) => (
                  <article className={`message ${message.role === "user" ? "user-message" : "assistant-message"}`} key={message.local_id}>
                {message.role === "user" ? (
                  <>
                    <div className="message-meta"><span>你</span><time>{formatTime(message.created_at)}</time></div>
                    <p className="user-bubble">{message.content}</p>
                  </>
                ) : (
                  <>
                    <div className="assistant-heading">
                      <div className="assistant-avatar"><span /></div>
                      <div>
                        <strong>NexusPilot</strong>
                        <small><i className={message.failed || message.cancelled || message.saveFailed ? "failed-dot" : ""} /> {message.cancelled ? "已停止 · 等待服务端最终状态" : message.saveFailed ? "已生成 · 保存失败" : message.failed ? "生成失败" : message.pending ? "生成中 · 状态来自服务端事件" : message.saving ? "已生成 · 正在保存" : "已完成"}</small>
                      </div>
                    </div>
                    <div className="assistant-content">
                      {message.content ? <p className="assistant-text">{message.content}</p> : <p className="typing-line"><span /> <span /> <span /></p>}
                      {message.pending && <p className="stream-note">正在接收增量响应，不会把部分文本标记为完成。</p>}
                      {message.saving && <p className="stream-note">模型响应已完成，正在写入会话记录。</p>}
                      {message.saveFailed && <p className="stream-note error-note">响应已生成，但会话保存失败；已保留当前文本。</p>}
                      {(message.failed || message.cancelled) && <p className="stream-note error-note">已保留收到的部分文本，请从运行详情查看服务端状态。</p>}
                    </div>
                    {!message.pending && !message.saving && (
                      <div className="message-actions">
                        <button onClick={() => void handleCopy(message)}>{copiedMessageId === message.local_id ? "已复制" : "复制"}</button>
                        <button onClick={() => setDraft(lastUserMessage?.content ?? "")}>重新生成</button>
                        <button onClick={() => setIsInspectorOpen(true)}>查看运行证据 ↗</button>
                      </div>
                    )}
                  </>
                )}
                  </article>
                ))}
              </>
            )}
          </div>

          <div className="composer-wrap">
            <div className="composer">
              <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleComposerKeyDown} placeholder="继续对话…" aria-label="消息内容" rows={1} />
              <div className="composer-tools">
                <div className="composer-context"><span className="context-lock" aria-hidden="true">⌁</span><span>上下文仅来自当前输入与已保存消息</span></div>
                <div className="composer-actions">
                  <label className="composer-model-picker" aria-label="选择 Provider 和模型">
                    <span className="provider-orb" aria-hidden="true" />
                    <span className="model-fields">
                      <select className="provider-select" value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value as ProviderName | "")} aria-label="Provider">
                        <option value="">未连接</option>
                        {providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
                      </select>
                      <input value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} aria-label="模型名称" />
                    </span>
                  </label>
                  <button className="send-button" disabled={!draft.trim() && !isSending || !selectedProvider} onClick={() => (isSending ? handleStop() : void handleSend())} aria-label={isSending ? "停止生成" : "发送消息"}>{isSending ? "■" : "↑"}</button>
                </div>
              </div>
            </div>
            <p className="composer-note">Enter 发送 · Shift + Enter 换行 · 运行状态来自后端事实</p>
          </div>
        </div>
      </section>

      <aside className={`inspector ${isInspectorOpen ? "is-open" : ""}`} aria-label="运行详情">
        <div className="inspector-header">
          <div><p className="eyebrow">运行详情</p><h2>执行证据</h2></div>
          <button className="icon-button" onClick={() => setIsInspectorOpen(false)} aria-label="关闭运行详情">×</button>
        </div>

        <div className="run-state">
          <div className={`status-emblem ${run?.status === "failed" ? "is-failed" : ""}`}>{run?.status === "failed" ? "!" : run ? "✓" : "·"}</div>
          <div><strong>{isSending ? "生成进行中" : run?.status === "failed" ? "生成失败" : assistantSaveFailed ? "响应已生成，保存失败" : assistantSaved ? "响应已保存" : responseCompleted ? "响应已完成，待保存" : run ? "运行已创建" : "等待首次运行"}</strong><p>状态仅来自服务端响应与运行记录</p></div>
          <time>{run ? formatTime(run.updated_at) : "—"}</time>
        </div>

        <div className="metric-grid">
          <div><span>总耗时</span><strong>{run ? formatDuration(run) : "—"}</strong></div>
          <div><span>总 Token</span><strong>{formatTokenTotal(runUsage)}</strong></div>
          <div><span>预估费用</span><strong>{runUsage?.cost ?? "—"}</strong></div>
          <div><span>Attempt</span><strong>{attemptId ? "已记录" : "—"}</strong></div>
        </div>

        <section className="inspector-section">
          <div className="section-title"><h3>模型调用</h3><span>{attemptId ? "已关联" : "等待"}</span></div>
          <dl className="detail-list">
            <div><dt>Provider</dt><dd>{selectedProvider || "—"}</dd></div>
            <div><dt>Model</dt><dd>{selectedModel || "—"}</dd></div>
            <div><dt>输入 Token</dt><dd>{runUsage?.input ?? "—"}</dd></div>
            <div><dt>输出 Token</dt><dd>{runUsage?.output ?? "—"}</dd></div>
            <div><dt>请求状态</dt><dd className={run?.status === "failed" ? "error-text" : "success-text"}>{statusLabel}</dd></div>
          </dl>
        </section>

        <section className="inspector-section timeline-section">
          <div className="section-title"><h3>运行时间线</h3><span>{run ? "服务端事实" : "暂无事件"}</span></div>
          <ol className="timeline">
            <li className={run ? "complete" : "pending"}><i /><div><strong>请求已接收</strong><small>{run ? formatTime(run.created_at) : "等待"}</small></div></li>
            <li className={isSending ? "complete" : "pending"}><i /><div><strong>模型生成</strong><small>{isSending ? "正在流式接收" : "等待"}</small></div></li>
            <li className={runUsage ? "complete" : "pending"}><i /><div><strong>用量已确认</strong><small>{runUsage ? `${formatTokenTotal(runUsage)} tokens` : "等待"}</small></div></li>
            <li className={responseCompleted ? "complete" : "pending"}><i /><div><strong>模型响应完成</strong><small>{responseCompleted ? "已完成" : "等待"}</small></div></li>
            <li className={assistantSaveFailed ? "failed" : assistantSaved ? "complete" : "pending"}><i /><div><strong>{assistantSaveFailed ? "响应保存失败" : "响应已保存"}</strong><small>{assistantSaveFailed ? "需要重试" : assistantSaved ? "已完成" : "等待"}</small></div></li>
          </ol>
        </section>

        <div className="future-note"><span>预留</span><p><strong>Agent 与工具执行轨迹</strong>正式事件合同完成后显示；当前界面不会伪造执行状态。</p></div>
        {errorMessage && <div className="error-banner" role="alert"><strong>需要处理</strong><p>{errorMessage}</p></div>}
        {lastAssistantMessage && !lastAssistantMessage.pending && !lastAssistantMessage.saving && <p className="inspector-footnote">当前查看：{lastAssistantMessage.content.length} 个字符的 Assistant 响应</p>}
      </aside>
    </main>
  );
}
