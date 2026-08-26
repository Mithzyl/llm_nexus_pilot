"use client";

import {
  cancelAgentWorkflow,
  createMessage,
  createConversationTurn,
  createSession,
  DEVELOPMENT_USER_ID,
  ensureDevelopmentUser,
  getAgentWorkflowNode,
  getAgentWorkflowResult,
  getAgentWorkflowSummary,
  getLatestRun,
  getReasoningBlocks,
  getRun,
  getRunAgentWorkflow,
  listLatestMessagePage,
  listProviders,
  listSessions,
  NexusApiError,
  nexusFetch,
  openAgentWorkflowStream,
  replayAgentWorkflowEvents,
  replayModelResponseEvents,
} from "../lib/api";
import type {
  AgentWorkflowEvent,
  AgentWorkflowNodeResult,
  AgentWorkflowResult,
  AgentWorkflowSummary,
  Message,
  MessageRole,
  MessageSummary,
  ProviderCatalog,
  ProviderName,
  ReasoningPresentation,
  ReasoningBlockSnapshot,
  Run,
  RunDetail,
  Session,
} from "../lib/types";
import { readStreamEvents, replayCommittedStreamEvents } from "../lib/sse";
import {
  applyReasoningStreamEvent,
  isReasoningPresentation,
} from "../lib/reasoning-stream";
import {
  agentStructuredOutputMode,
  appendAgentWorkflowEvent,
  readAgentWorkflowEventStream,
  type AgentWorkflowEventState,
} from "../lib/agent-workflow";
import { isAgentWorkflowCancellable } from "../lib/agent-workflow-view";
import { hasSavedAssistantForLatestTurn } from "../lib/history";
import {
  AgentWorkflowPanel,
  agentWorkflowStatusLabel,
} from "../components/agent-workflow/AgentWorkflowPanel";
import { ModelPicker } from "../components/model-picker/ModelPicker";
import { CompactSelect } from "../components/composer/CompactSelect";
import { ReasoningView } from "../components/reasoning/ReasoningView";
import { MarkdownContent } from "../components/markdown/MarkdownContent";
import {
  composerConversationClearance,
  composerTextareaHeight,
} from "../lib/composer-layout";
import {
  isModelSelectionAllowed,
  resolveInitialModelSelection,
} from "../lib/model-catalog";
import {
  conversationPath,
  resolveWorkspaceRoute,
  runPath,
  terminalConversationNavigationTarget,
  workspaceRouteKey,
  type NexusWorkspaceRoute,
} from "../lib/routes";
import {
  isPersistedResponseOutputTruncated,
  isResponseOutputTruncated,
  responseMessageMetadata,
} from "../lib/response-outcome";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

const EMPTY_PROVIDER_CATALOG: ProviderCatalog = {
  providers: [],
  models_by_provider: {},
  reasoning_capabilities_by_provider_model: {},
};
const EMPTY_AGENT_EVENT_STATE: AgentWorkflowEventState = {
  events: [],
  lastSequence: 0,
  hasGap: false,
};
const PROVIDER_CATALOG_TIMEOUT_MS = 10_000;

type ExecutionMode = "response" | "agent";
type ReviewPolicy = "always" | "on_verification_failure" | "never";
type AgentParallelism = 1 | 2;
type ReasoningDisplayPolicy = "hidden" | "summary-only" | "provider-visible";

type LocalMessage = MessageSummary & {
  local_id: string;
  content: string;
  pending?: boolean;
  saving?: boolean;
  saveFailed?: boolean;
  failed?: boolean;
  cancelled?: boolean;
  agentWorkflow?: boolean;
  truncated?: boolean;
  reasoningBlocks: ReasoningPresentation[];
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
    truncated: isPersistedResponseOutputTruncated(message.metadata_json),
    reasoningBlocks: (message.reasoning_blocks ?? []).filter((block) =>
      isReasoningPresentation(block),
    ),
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
    source_model_attempt_id: null,
    parent_message_id: null,
    role,
    content_type: "text",
    content_preview: content.slice(0, 160),
    content_uri: null,
    sequence: 0,
    token_count: null,
    created_at: now,
    content,
    reasoningBlocks: [],
  };
}

/**
 * Render the first NexusPilot conversation workspace and coordinate its durable API flow.
 */
export default function Home() {
  const router = useRouter();
  const routeParams = useParams<{ sessionId?: string; runId?: string }>();
  const workspaceRoute = resolveWorkspaceRoute(routeParams);
  const currentRouteKey = workspaceRouteKey(workspaceRoute);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [providerCatalog, setProviderCatalog] = useState<ProviderCatalog>(
    EMPTY_PROVIDER_CATALOG,
  );
  const [providerCatalogState, setProviderCatalogState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [selectedProvider, setSelectedProvider] = useState<ProviderName | "">("");
  const [selectedModel, setSelectedModel] = useState("");
  const [runModelFacts, setRunModelFacts] = useState<{ provider: string; model: string } | null>(
    null,
  );
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("response");
  const [reviewPolicy, setReviewPolicy] = useState<ReviewPolicy>("always");
  const [maxParallelAgents, setMaxParallelAgents] = useState<AgentParallelism>(1);
  const [reasoningDisplayPolicy, setReasoningDisplayPolicy] =
    useState<ReasoningDisplayPolicy>("hidden");
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<Run | RunDetail | null>(null);
  const [agentWorkflow, setAgentWorkflow] = useState<
    AgentWorkflowSummary | AgentWorkflowResult | null
  >(null);
  const [agentWorkflowNodes, setAgentWorkflowNodes] = useState<AgentWorkflowNodeResult[]>([]);
  const [reasoningBlocksByAttemptId, setReasoningBlocksByAttemptId] = useState<
    Record<string, ReasoningBlockSnapshot[]>
  >({});
  const [agentEventState, setAgentEventState] = useState<AgentWorkflowEventState>(
    EMPTY_AGENT_EVENT_STATE,
  );
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [runUsage, setRunUsage] = useState<{ input: number | null; output: number | null; cost: string } | null>(null);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false);
  const [isInspectorOpen, setIsInspectorOpen] = useState(true);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [isDark, setIsDark] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isCancellingWorkflow, setIsCancellingWorkflow] = useState(false);
  const [responseCompleted, setResponseCompleted] = useState(false);
  const [assistantSaved, setAssistantSaved] = useState(false);
  const [assistantSaveFailed, setAssistantSaveFailed] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("checking");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [composerClearancePx, setComposerClearancePx] = useState(180);
  const latestAssistantText = useRef("");
  const latestReasoningBlocks = useRef<ReasoningPresentation[]>([]);
  const assistantProjectionFrame = useRef<number | null>(null);
  const conversationScroll = useRef<HTMLDivElement | null>(null);
  const composerWrap = useRef<HTMLDivElement | null>(null);
  const composerTextarea = useRef<HTMLTextAreaElement | null>(null);
  const shouldAutoFollowConversation = useRef(true);
  const activeResponseAttemptId = useRef<string | null>(null);
  const lastResponseSequence = useRef(0);
  const activeRequestController = useRef<AbortController | null>(null);
  const restoredRouteKey = useRef<string | null>(null);
  const providerCatalogRequestId = useRef(0);
  const providerCatalogRequestController = useRef<AbortController | null>(null);

  const selectedReasoningCapabilities =
    selectedProvider && selectedModel
      ? providerCatalog.reasoning_capabilities_by_provider_model?.[selectedProvider]?.[
          selectedModel
        ]
      : undefined;

  /** Batch text and reasoning token projections into one browser animation frame. */
  function scheduleAssistantProjection(assistantLocalId: string) {
    if (assistantProjectionFrame.current !== null) return;
    assistantProjectionFrame.current = window.requestAnimationFrame(() => {
      assistantProjectionFrame.current = null;
      setMessages((current) =>
        current.map((message) =>
          message.local_id === assistantLocalId
            ? {
                ...message,
                content: latestAssistantText.current,
                content_preview: latestAssistantText.current,
                reasoningBlocks: latestReasoningBlocks.current,
              }
            : message,
        ),
      );
    });
  }

  useEffect(() => {
    const savedTheme = window.localStorage.getItem("nexuspilot-theme");
    // localStorage is the explicitly local theme preference; sync it once after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsDark(savedTheme === "dark");
  }, []);

  useEffect(() => {
    const scrollContainer = conversationScroll.current;
    if (!scrollContainer || !shouldAutoFollowConversation.current) return;
    scrollContainer.scrollTop = scrollContainer.scrollHeight;
  }, [messages]);

  useEffect(() => {
    const modelAttemptIds = Array.from(
      new Set(
        agentWorkflowNodes.flatMap((node) => node.evidence.model_attempt_ids),
      ),
    );
    if (modelAttemptIds.length === 0) return;
    let isCurrent = true;
    void Promise.all(
      modelAttemptIds.map(async (modelAttemptId) => [
        modelAttemptId,
        (await getReasoningBlocks(modelAttemptId)).filter((block) =>
          isReasoningPresentation(block),
        ),
      ] as const),
    )
      .then((entries) => {
        if (isCurrent) setReasoningBlocksByAttemptId(Object.fromEntries(entries));
      })
      .catch(() => {
        if (isCurrent) setReasoningBlocksByAttemptId({});
      });
    return () => {
      isCurrent = false;
    };
  }, [agentWorkflowNodes]);

  useEffect(() => {
    window.localStorage.setItem("nexuspilot-theme", isDark ? "dark" : "light");
  }, [isDark]);

  useEffect(() => {
    const textarea = composerTextarea.current;
    if (!textarea) return;
    textarea.style.overflowY = "hidden";
    textarea.style.height = "0px";
    const nextHeight = composerTextareaHeight(textarea.scrollHeight);
    textarea.style.height = `${nextHeight}px`;
    textarea.style.overflowY = textarea.scrollHeight > nextHeight ? "auto" : "hidden";
  }, [draft]);

  useEffect(() => {
    const composerElement = composerWrap.current;
    if (!composerElement) return;

    /** Keep the last message actions scrollable above the full current Composer height. */
    const updateConversationClearance = () => {
      setComposerClearancePx(
        composerConversationClearance(composerElement.getBoundingClientRect().height),
      );
    };
    updateConversationClearance();
    const composerResizeObserver = new ResizeObserver(updateConversationClearance);
    composerResizeObserver.observe(composerElement);
    return () => composerResizeObserver.disconnect();
  }, []);

  useEffect(() => {
    /** Cancel an uncommitted visual projection when the workspace unmounts. */
    return () => {
      if (assistantProjectionFrame.current !== null) {
        window.cancelAnimationFrame(assistantProjectionFrame.current);
      }
    };
  }, []);

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
    void listSessions()
      .then((sessionPage) => {
        if (!isMounted) return;
        setSessions(sessionPage.items.filter((session) => session.status === "active"));
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

  /**
   * Start one bounded provider-catalog request and invalidate any older request.
   * The timeout prevents a stalled upstream connection from leaving the selector
   * in a permanent loading state.
   */
  function startProviderCatalogRequest() {
    providerCatalogRequestController.current?.abort();
    const controller = new AbortController();
    providerCatalogRequestController.current = controller;
    const requestId = providerCatalogRequestId.current + 1;
    providerCatalogRequestId.current = requestId;
    const timeoutId = window.setTimeout(
      () => controller.abort(),
      PROVIDER_CATALOG_TIMEOUT_MS,
    );
    return { controller, requestId, timeoutId };
  }

  /** Load the provider catalog independently from conversation history for an explicit retry. */
  async function loadProviderCatalog() {
    const { controller, requestId, timeoutId } = startProviderCatalogRequest();
    setProviderCatalogState("loading");
    try {
      const catalog = await listProviders(controller.signal);
      if (providerCatalogRequestId.current !== requestId) return;
      setProviderCatalog(catalog);
      const initialSelection = resolveInitialModelSelection(catalog);
      setSelectedProvider(initialSelection.provider);
      setSelectedModel(initialSelection.model);
      setProviderCatalogState("ready");
    } catch {
      if (providerCatalogRequestId.current !== requestId) return;
      setProviderCatalog(EMPTY_PROVIDER_CATALOG);
      setSelectedProvider("");
      setSelectedModel("");
      setProviderCatalogState("error");
    } finally {
      window.clearTimeout(timeoutId);
      if (providerCatalogRequestController.current === controller) {
        providerCatalogRequestController.current = null;
      }
    }
  }

  useEffect(() => {
    const { controller, requestId, timeoutId } = startProviderCatalogRequest();
    void listProviders(controller.signal)
      .then((catalog) => {
        if (providerCatalogRequestId.current !== requestId) return;
        setProviderCatalog(catalog);
        const initialSelection = resolveInitialModelSelection(catalog);
        setSelectedProvider(initialSelection.provider);
        setSelectedModel(initialSelection.model);
        setProviderCatalogState("ready");
      })
      .catch(() => {
        if (providerCatalogRequestId.current !== requestId) return;
        setProviderCatalog(EMPTY_PROVIDER_CATALOG);
        setSelectedProvider("");
        setSelectedModel("");
        setProviderCatalogState("error");
      })
      .finally(() => {
        window.clearTimeout(timeoutId);
        if (providerCatalogRequestController.current === controller) {
          providerCatalogRequestController.current = null;
        }
      });
    return () => {
      controller.abort();
      providerCatalogRequestId.current += 1;
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
      setRunModelFacts({ provider: latestAttempt.provider, model: latestAttempt.model });
    } else if (runDetail) {
      setRunUsage({ input: null, output: null, cost: String(runDetail.cost_used ?? "—") });
      setRunModelFacts(null);
    } else {
      setRunUsage(null);
      setRunModelFacts(null);
    }
  }

  /** Clear Agent Workflow projections without changing the selected conversation or Run. */
  function resetAgentWorkflowFacts() {
    setAgentWorkflow(null);
    setAgentWorkflowNodes([]);
    setReasoningBlocksByAttemptId({});
    setAgentEventState(EMPTY_AGENT_EVENT_STATE);
  }

  /** Insert or replace one complete node while preserving durable node-sequence order. */
  function mergeAgentWorkflowNode(node: AgentWorkflowNodeResult) {
    setAgentWorkflowNodes((current) =>
      [...current.filter((item) => item.node_execution_id !== node.node_execution_id), node].sort(
        (left, right) => left.node_sequence - right.node_sequence,
      ),
    );
  }

  /** Apply one authoritative workflow snapshot to metrics and the optional local answer. */
  function applyAgentWorkflowResult(result: AgentWorkflowResult, assistantLocalId?: string) {
    setAgentWorkflow((current) =>
      current && current.snapshot_version > result.snapshot_version ? current : result,
    );
    setAgentWorkflowNodes(result.nodes);
    setRunUsage({
      input: result.aggregate_usage.input_tokens,
      output: result.aggregate_usage.output_tokens,
      cost: String(result.aggregate_usage.estimated_cost),
    });
    setAttemptId(result.completion?.model_attempt_ids.at(-1) ?? null);

    if (!assistantLocalId) return;
    if (["pending", "running", "waiting_for_input"].includes(result.status)) return;
    const finalText = result.final_output?.final_text ?? "";
    const isCompleted = result.status === "completed" && finalText.length > 0;
    setResponseCompleted(isCompleted);
    setAssistantSaved(Boolean(result.final_output?.assistant_message_id));
    setAssistantSaveFailed(isCompleted && !result.final_output?.assistant_message_id);
    setMessages((current) =>
      current.map((message) =>
        message.local_id === assistantLocalId
          ? {
              ...message,
              content: finalText || message.content || "工作流没有生成可用的最终回答。",
              content_preview: finalText || message.content_preview,
              pending: false,
              saving: false,
              failed: result.status === "failed" || result.status === "outcome_unknown",
              cancelled: result.status === "cancelled",
              saveFailed: isCompleted && !result.final_output?.assistant_message_id,
            }
          : message,
      ),
    );
    if (result.error) setErrorMessage(result.error.public_message);
  }

  /** Restore Workflow events and the latest bounded result for one persisted Run. */
  async function restoreAgentWorkflowForRun(runId: string, assistantLocalId?: string) {
    resetAgentWorkflowFacts();
    let summary: AgentWorkflowSummary;
    try {
      summary = await getRunAgentWorkflow(runId);
    } catch (error) {
      if (error instanceof NexusApiError && error.status === 404) return;
      setErrorMessage(error instanceof Error ? error.message : "无法读取 Agent Workflow。");
      return;
    }

    setExecutionMode("agent");
    setAgentWorkflow(summary);
    let restoredEvents = EMPTY_AGENT_EVENT_STATE;
    try {
      const replay = await replayAgentWorkflowEvents(summary.workflow_execution_id, 0);
      await readAgentWorkflowEventStream(
        replay,
        (event) => {
          restoredEvents = appendAgentWorkflowEvent(restoredEvents, event);
        },
        { requireTerminalEvent: false },
      );
      setAgentEventState(restoredEvents);
    } catch (error) {
      setErrorMessage(
        `工作流事件回放不可用，当前以查询快照为准：${error instanceof Error ? error.message : "未知错误"}`,
      );
    }

    try {
      const result = await getAgentWorkflowResult(summary.workflow_execution_id);
      applyAgentWorkflowResult(result, assistantLocalId);
    } catch (error) {
      setErrorMessage(
        `工作流摘要已恢复，但完整节点暂不可用：${error instanceof Error ? error.message : "未知错误"}`,
      );
    }
  }

  /**
   * Execute the current Run through the model-only workflow and return its
   * terminal snapshot without duplicating the backend-created final Message.
   */
  async function executeAgentWorkflow(
    createdRun: Run,
    assistantMessage: LocalMessage,
  ): Promise<AgentWorkflowResult> {
    resetAgentWorkflowFacts();
    const binding = {
      provider: selectedProvider as ProviderName,
      model: selectedModel,
      structured_output_mode: agentStructuredOutputMode(selectedProvider as ProviderName),
      timeout_seconds: 60,
    };
    const controller = new AbortController();
    activeRequestController.current = controller;
    const response = await openAgentWorkflowStream(
      createdRun.run_id,
      {
        workflow_name: "model_only",
        workflow_version: "1.0.0",
        execution_profile: "model_only_v1",
        idempotency_key: crypto.randomUUID(),
        role_bindings: {
          controller: binding,
          planner: binding,
          ...(reviewPolicy === "never" ? {} : { reviewer: binding }),
        },
        review_policy: reviewPolicy,
        max_nodes: 32,
        max_model_calls: 16,
        max_parallel_agents: maxParallelAgents,
        wall_time_limit_ms: 600_000,
        stream: true,
      },
      controller.signal,
    );

    await readAgentWorkflowEventStream(
      response,
      async (event: AgentWorkflowEvent) => {
        setAgentEventState((current) => appendAgentWorkflowEvent(current, event));
        if (event.event_type === "agent.workflow.started") {
          try {
            setAgentWorkflow(await getAgentWorkflowSummary(event.workflow_execution_id));
          } catch (error) {
            setErrorMessage(
              `工作流已启动，但摘要暂不可用：${error instanceof Error ? error.message : "未知错误"}`,
            );
          }
        } else {
          setAgentWorkflow((current) =>
            current
              ? {
                  ...current,
                  status: event.workflow_status,
                  current_stage:
                    typeof event.public_payload.node_key === "string"
                      ? event.public_payload.node_key
                      : current.current_stage,
                }
              : current,
          );
        }

        if (
          event.node_execution_id &&
          event.event_type !== "agent.node.started"
        ) {
          try {
            mergeAgentWorkflowNode(
              await getAgentWorkflowNode(
                event.workflow_execution_id,
                event.node_execution_id,
              ),
            );
          } catch (error) {
            setErrorMessage(
              `工作流仍在运行，但节点证据暂不可用：${error instanceof Error ? error.message : "未知错误"}`,
            );
          }
        }
      },
      { requireTerminalEvent: true },
    );

    const summary = await getRunAgentWorkflow(createdRun.run_id);
    const result = await getAgentWorkflowResult(summary.workflow_execution_id);
    applyAgentWorkflowResult(result, assistantMessage.local_id);
    setRun(await getRun(createdRun.run_id));
    return result;
  }

  /**
   * Load one immutable conversation history and either its latest or explicitly requested Run.
   */
  async function restoreSession(sessionId: string, requestedRun?: RunDetail | null) {
    setErrorMessage(null);
    setActiveSessionId(sessionId);
    setIsSidebarOpen(false);
    setRun(null);
    resetAgentWorkflowFacts();
    setResponseCompleted(false);
    setAssistantSaved(false);
    setAssistantSaveFailed(false);
    setRunUsage(null);
    setRunModelFacts(null);
    setAttemptId(null);
    setMessageCursor(null);
    setHasMoreMessages(false);
    try {
      const [page, latestRun] = await Promise.all([
        listLatestMessagePage(sessionId),
        requestedRun !== undefined
          ? Promise.resolve(requestedRun)
          : getLatestRun(sessionId).catch((error: unknown) => {
              if (error instanceof NexusApiError && error.status === 404) return null;
              throw error;
            }),
      ]);
      setMessages(page.items.map(messageFromMessage));
      setMessageCursor(page.next_cursor);
      setHasMoreMessages(page.has_more);
      restoreRunFacts(latestRun);
      if (latestRun) await restoreAgentWorkflowForRun(latestRun.run_id);
      const hasPersistedAssistant = hasSavedAssistantForLatestTurn(page.items);
      setResponseCompleted(hasPersistedAssistant);
      setAssistantSaved(hasPersistedAssistant);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法读取会话消息");
      setMessages([]);
      setMessageCursor(null);
      setHasMoreMessages(false);
      if (requestedRun !== undefined) restoreRunFacts(requestedRun);
    }
  }

  /** Navigate to and restore one owned conversation from its stable URL. */
  async function handleSelectSession(sessionId: string) {
    restoredRouteKey.current = workspaceRouteKey({ kind: "conversation", sessionId });
    router.push(conversationPath(sessionId));
    await restoreSession(sessionId);
  }

  /** Restore an exact owned Run and its conversation rather than substituting the latest Run. */
  async function restoreRunRoute(runId: string) {
    setErrorMessage(null);
    setIsInspectorOpen(true);
    setActiveSessionId(null);
    setMessages([]);
    setMessageCursor(null);
    setHasMoreMessages(false);
    restoreRunFacts(null);
    resetAgentWorkflowFacts();
    try {
      const runDetail = await getRun(runId);
      if (runDetail.session_id) {
        await restoreSession(runDetail.session_id, runDetail);
        return;
      }

      restoreRunFacts(runDetail);
      await restoreAgentWorkflowForRun(runDetail.run_id);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法读取运行详情");
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
  function resetConversationView() {
    setActiveSessionId(null);
    setMessages([]);
    setRun(null);
    resetAgentWorkflowFacts();
    setAttemptId(null);
    setRunUsage(null);
    setRunModelFacts(null);
    setResponseCompleted(false);
    setAssistantSaved(false);
    setAssistantSaveFailed(false);
    setMessageCursor(null);
    setHasMoreMessages(false);
    setErrorMessage(null);
    setDraft("");
    setIsSidebarOpen(false);
  }

  /** Navigate to the stable empty-workspace route without deleting persisted data. */
  function handleNewConversation() {
    restoredRouteKey.current = "home";
    router.push("/");
    resetConversationView();
  }

  /** Keep browser history navigation synchronized with persisted Session and Run facts. */
  useEffect(() => {
    const restoreFrame = window.requestAnimationFrame(() => {
      if (restoredRouteKey.current === currentRouteKey) return;
      restoredRouteKey.current = currentRouteKey;

      if (workspaceRoute.kind === "home") {
        resetConversationView();
        return;
      }
      if (workspaceRoute.kind === "conversation") {
        void restoreSession(workspaceRoute.sessionId);
        return;
      }
      void restoreRunRoute(workspaceRoute.runId);
    });
    return () => window.cancelAnimationFrame(restoreFrame);
    // The stable route key is the intended reload trigger; state setters and helpers are local.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRouteKey]);

  /** Open the current persisted Run at a shareable route while retaining loaded evidence. */
  function handleOpenRunEvidence() {
    setIsInspectorOpen(true);
    if (!run) return;
    const route: NexusWorkspaceRoute = { kind: "run", runId: run.run_id };
    restoredRouteKey.current = workspaceRouteKey(route);
    router.push(runPath(run.run_id));
  }

  /**
   * Copy assistant text through the browser clipboard and provide visible feedback.
   */
  async function handleCopy(message: LocalMessage) {
    await navigator.clipboard.writeText(message.content);
    setCopiedMessageId(message.local_id);
    window.setTimeout(() => setCopiedMessageId(null), 1400);
  }

  /** Durably cancel future Agent nodes while keeping any active Provider call auditable. */
  async function handleCancelAgentWorkflow() {
    if (!run || isCancellingWorkflow) return;
    const workflowRunId = run.run_id;
    setIsCancellingWorkflow(true);
    setErrorMessage(null);
    try {
      const currentWorkflow = agentWorkflow ?? await getRunAgentWorkflow(workflowRunId);
      setAgentWorkflow((current) =>
        current?.run_id && current.run_id !== workflowRunId ? current : currentWorkflow,
      );
      if (!isAgentWorkflowCancellable(currentWorkflow.status)) return;

      const cancelledWorkflow = await cancelAgentWorkflow(
        currentWorkflow.workflow_execution_id,
      );
      setAgentWorkflow((current) =>
        current?.run_id && current.run_id !== workflowRunId ? current : cancelledWorkflow,
      );
      setMessages((current) =>
        current.map((message) =>
          message.run_id === workflowRunId && message.agentWorkflow && message.pending
            ? {
                ...message,
                content: message.content || "工作流已取消，未生成最终回答。",
                content_preview: message.content_preview || "工作流已取消，未生成最终回答。",
                pending: false,
                cancelled: true,
              }
          : message,
        ),
      );
      const refreshedRun = await getRun(workflowRunId);
      setRun((current) =>
        current?.run_id === workflowRunId ? refreshedRun : current,
      );
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "无法取消 Agent Workflow。",
      );
    } finally {
      setIsCancellingWorkflow(false);
    }
  }

  /** Cancel an Agent Workflow explicitly, or abort only the ordinary Response stream. */
  function handleStop() {
    if (executionMode === "agent" && run) {
      void handleCancelAgentWorkflow();
      return;
    }
    activeRequestController.current?.abort();
  }

  /** Follow streaming output only while the reader remains at the message-list bottom. */
  function trackConversationBottom() {
    const scrollContainer = conversationScroll.current;
    if (!scrollContainer) return;
    const remainingScrollPx =
      scrollContainer.scrollHeight - scrollContainer.scrollTop - scrollContainer.clientHeight;
    shouldAutoFollowConversation.current = remainingScrollPx <= 48;
  }

  /**
   * Submit one atomic Run/Message turn, then stream its anchored quick or Agent response.
   */
  async function handleSend() {
    const input = draft.trim();
    if (
      !input ||
      isSending ||
      !isModelSelectionAllowed(providerCatalog, selectedProvider, selectedModel)
    ) return;
    const submittedMode = executionMode;
    let submittedRunId: string | null = null;
    let submittedAssistantLocalId: string | null = null;
    let submittedSessionId: string | null = null;
    let hasRestorableTerminalFacts = false;

    setIsSending(true);
    setErrorMessage(null);
    setResponseCompleted(false);
    setAssistantSaved(false);
    setAssistantSaveFailed(false);
    resetAgentWorkflowFacts();
    latestAssistantText.current = "";
    latestReasoningBlocks.current = [];
    activeResponseAttemptId.current = null;
    lastResponseSequence.current = 0;
    if (assistantProjectionFrame.current !== null) {
      window.cancelAnimationFrame(assistantProjectionFrame.current);
      assistantProjectionFrame.current = null;
    }
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
      submittedSessionId = session.session_id;

      const createdTurn = await createConversationTurn(session.session_id, {
        user_id: DEVELOPMENT_USER_ID,
        content_text: input,
      });
      const createdRun = createdTurn.run;
      submittedRunId = createdRun.run_id;
      setRun(createdRun);
      setRunModelFacts({ provider: selectedProvider, model: selectedModel });

      const assistantMessage = createLocalMessage("assistant", "", createdRun.run_id);
      submittedAssistantLocalId = assistantMessage.local_id;
      assistantMessage.pending = true;
      assistantMessage.agentWorkflow = submittedMode === "agent";
      setMessages((current) => [...current, assistantMessage]);

      if (submittedMode === "agent") {
        const workflowResult = await executeAgentWorkflow(createdRun, assistantMessage);
        hasRestorableTerminalFacts = Boolean(workflowResult.final_output?.assistant_message_id);
        return;
      }

      const responseController = new AbortController();
      activeRequestController.current = responseController;
      const response = await fetch("/api/nexus/responses", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        signal: responseController.signal,
        body: JSON.stringify({
          run_id: createdRun.run_id,
          current_user_message_id: createdTurn.message.message_id,
          provider: selectedProvider,
          model: selectedModel,
          input,
          timeout_seconds: 60,
          reasoning_display_policy: reasoningDisplayPolicy,
          idempotency_key: crypto.randomUUID(),
          stream: true,
        }),
      });

      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(body?.detail ?? `模型请求失败（${response.status}）`);
      }

      /** Apply one live or replayed response event exactly once in sequence order. */
      const applyResponseEvent = async (event: Parameters<typeof applyReasoningStreamEvent>[1]) => {
        const data = event.data;
        const eventAttemptId = data.attempt_id ?? event.response_id;
        if (eventAttemptId) {
          activeResponseAttemptId.current = eventAttemptId;
          setAttemptId(eventAttemptId);
        }
        const nextReasoningBlocks = applyReasoningStreamEvent(
          latestReasoningBlocks.current,
          event,
        );
        if (nextReasoningBlocks !== latestReasoningBlocks.current) {
          latestReasoningBlocks.current = nextReasoningBlocks;
          scheduleAssistantProjection(assistantMessage.local_id);
        }
        if (event.type === "response.text.delta" && data.delta) {
          latestAssistantText.current += data.delta;
          scheduleAssistantProjection(assistantMessage.local_id);
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
          const isTruncated = isResponseOutputTruncated(responseResult?.finish_reason);
          latestAssistantText.current = completedText;
          latestReasoningBlocks.current = responseResult?.reasoning_blocks ?? latestReasoningBlocks.current;
          if (assistantProjectionFrame.current !== null) {
            window.cancelAnimationFrame(assistantProjectionFrame.current);
            assistantProjectionFrame.current = null;
          }
          setAttemptId(data.attempt_id ?? responseResult?.id ?? null);
          setMessages((current) =>
            current.map((message) =>
              message.local_id === assistantMessage.local_id
                ? {
                    ...message,
                    content: completedText,
                    content_preview: completedText,
                    reasoningBlocks: latestReasoningBlocks.current,
                    pending: false,
                    saving: true,
                    truncated: isTruncated,
                  }
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
              source_model_attempt_id: responseResult?.id,
              metadata_json: responseMessageMetadata(responseResult?.finish_reason),
            });
            hasRestorableTerminalFacts = true;
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
        lastResponseSequence.current = event.sequence;
      };

      try {
        await readStreamEvents(response, applyResponseEvent);
      } catch (streamError) {
        const responseAttemptId = activeResponseAttemptId.current;
        if (responseController.signal.aborted || !responseAttemptId) throw streamError;
        await replayCommittedStreamEvents(
          (afterSequence) => replayModelResponseEvents(responseAttemptId, afterSequence),
          applyResponseEvent,
          { afterSequence: lastResponseSequence.current },
        );
      }
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
      if (submittedMode === "agent" && submittedRunId) {
        await restoreAgentWorkflowForRun(
          submittedRunId,
          submittedAssistantLocalId ?? undefined,
        );
      }
    } finally {
      activeRequestController.current = null;
      setIsSending(false);
      if (submittedSessionId) {
        const navigationTarget = terminalConversationNavigationTarget(
          workspaceRoute,
          submittedSessionId,
          hasRestorableTerminalFacts,
        );
        if (navigationTarget) {
          restoredRouteKey.current = workspaceRouteKey({
            kind: "conversation",
            sessionId: submittedSessionId,
          });
          router.replace(navigationTarget);
        }
      }
    }
  }

  const statusLabel = agentWorkflow
    ? agentWorkflowStatusLabel(agentWorkflow.status)
    : isSending
      ? "生成中"
      : assistantSaveFailed
        ? "保存失败"
        : assistantSaved
          ? lastAssistantMessage?.truncated ? "已截断并保存" : "已保存"
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

        <div
          className="conversation-stage"
          style={{ "--composer-clearance": `${composerClearancePx}px` } as CSSProperties}
        >
          <div
            className="conversation-scroll"
            ref={conversationScroll}
            onScroll={trackConversationBottom}
          >
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
                        <small><i className={message.failed || message.cancelled || message.saveFailed || message.truncated ? "failed-dot" : ""} /> {message.cancelled ? "已停止 · 等待服务端最终状态" : message.saveFailed ? "已生成 · 保存失败" : message.failed ? (message.agentWorkflow ? "工作流失败" : "生成失败") : message.pending ? (message.agentWorkflow ? "Agent Workflow 运行中" : "生成中 · 状态来自服务端事件") : message.saving ? "已生成 · 正在保存" : message.truncated ? "达到输出上限 · 已保存部分回复" : message.agentWorkflow ? "工作流已完成" : "已完成"}</small>
                      </div>
                    </div>
                    <div className="assistant-content">
                      {message.reasoningBlocks.map((presentation) => (
                        <ReasoningView
                          key={presentation.block_id}
                          presentation={presentation}
                        />
                      ))}
                      {message.content ? <MarkdownContent content={message.content} /> : <p className="typing-line"><span /> <span /> <span /></p>}
                      {message.pending && <p className="stream-note">正在接收增量响应，不会把部分文本标记为完成。</p>}
                      {message.saving && <p className="stream-note">模型响应已完成，正在写入会话记录。</p>}
                      {message.truncated && !message.pending && <p className="stream-note error-note">模型达到输出 Token 上限，以上是已保存的部分回复；可以要求模型继续，或重新生成更短的回答。</p>}
                      {message.saveFailed && <p className="stream-note error-note">响应已生成，但会话保存失败；已保留当前文本。</p>}
                      {(message.failed || message.cancelled) && <p className="stream-note error-note">已保留收到的部分文本，请从运行详情查看服务端状态。</p>}
                    </div>
                    {!message.pending && !message.saving && (
                      <div className="message-actions">
                        <button onClick={() => void handleCopy(message)}>{copiedMessageId === message.local_id ? "已复制" : "复制"}</button>
                        <button onClick={() => setDraft(lastUserMessage?.content ?? "")}>重新生成</button>
                        <button onClick={handleOpenRunEvidence}>查看运行证据 ↗</button>
                      </div>
                    )}
                  </>
                )}
                  </article>
                ))}
              </>
            )}
          </div>

          <div className="composer-wrap" ref={composerWrap}>
            <div className="composer">
              <textarea
                ref={composerTextarea}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="继续对话…"
                aria-label="消息内容"
                rows={1}
              />
              <div className="composer-toolbar">
                <div className="composer-config-controls">
                  <div className="composer-mode-row" aria-label="执行模式">
                    <button
                      className={executionMode === "response" ? "active" : ""}
                      disabled={isSending}
                      onClick={() => setExecutionMode("response")}
                    >
                      快速回复
                    </button>
                    <button
                      className={executionMode === "agent" ? "active" : ""}
                      disabled={isSending}
                      onClick={() => setExecutionMode("agent")}
                    >
                      Agent
                    </button>
                  </div>
                  {executionMode === "agent" && (
                    <div className="agent-option-controls">
                      <CompactSelect
                        label="工作 Agent 最大并行数"
                        value={String(maxParallelAgents)}
                        options={[
                          { value: "1", label: "串行" },
                          { value: "2", label: "并行 2" },
                        ]}
                        disabled={isSending}
                        onChange={(value) => setMaxParallelAgents(value === "2" ? 2 : 1)}
                      />
                      <CompactSelect
                        label="Agent 审核策略"
                        value={reviewPolicy}
                        options={[
                          { value: "always", label: "始终审核" },
                          { value: "on_verification_failure", label: "失败时审核" },
                          { value: "never", label: "不审核" },
                        ]}
                        disabled={isSending}
                        onChange={(value) => setReviewPolicy(value as ReviewPolicy)}
                      />
                    </div>
                  )}
                  {executionMode === "response" &&
                    selectedReasoningCapabilities?.presentation !== "none" && (
                      <CompactSelect
                        label="推理展示策略"
                        value={reasoningDisplayPolicy}
                        options={[
                          { value: "hidden", label: "隐藏推理" },
                          { value: "summary-only", label: "仅摘要" },
                          { value: "provider-visible", label: "显示推理" },
                        ]}
                        disabled={isSending}
                        onChange={(value) =>
                          setReasoningDisplayPolicy(value as ReasoningDisplayPolicy)
                        }
                      />
                    )}
                </div>
                <div className="composer-actions">
                  <ModelPicker
                    catalog={providerCatalog}
                    catalogState={providerCatalogState}
                    selectedProvider={selectedProvider}
                    selectedModel={selectedModel}
                    disabled={isSending}
                    onRetry={() => void loadProviderCatalog()}
                    onChange={(provider, model) => {
                      setSelectedProvider(provider);
                      setSelectedModel(model);
                    }}
                  />
                  <button className="send-button" disabled={isCancellingWorkflow || (!isSending && (!draft.trim() || !isModelSelectionAllowed(providerCatalog, selectedProvider, selectedModel)))} onClick={() => (isSending ? handleStop() : void handleSend())} aria-label={isSending ? executionMode === "agent" ? "取消工作流" : "停止生成" : "发送消息"}>{isSending ? "■" : "↑"}</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside className={`inspector ${isInspectorOpen ? "is-open" : ""}`} aria-label="运行详情">
        <div className="inspector-header">
          <div><p className="eyebrow">运行详情</p><h2>执行证据</h2></div>
          <button className="icon-button" onClick={() => setIsInspectorOpen(false)} aria-label="关闭运行详情">×</button>
        </div>

        <div className="run-state">
          <div className={`status-emblem ${run?.status === "failed" || agentWorkflow?.status === "failed" || agentWorkflow?.status === "outcome_unknown" ? "is-failed" : ""}`}>{run?.status === "failed" || agentWorkflow?.status === "failed" || agentWorkflow?.status === "outcome_unknown" ? "!" : run ? "✓" : "·"}</div>
          <div><strong>{agentWorkflow ? agentWorkflowStatusLabel(agentWorkflow.status) : isSending ? "生成进行中" : run?.status === "failed" ? "生成失败" : assistantSaveFailed ? "响应已生成，保存失败" : assistantSaved ? lastAssistantMessage?.truncated ? "响应达到上限，部分内容已保存" : "响应已保存" : responseCompleted ? "响应已完成，待保存" : run ? "运行已创建" : "等待首次运行"}</strong><p>{agentWorkflow ? "完整节点与事件均来自阶段3持久化事实" : "状态仅来自服务端响应与运行记录"}</p></div>
          <time>{run ? formatTime(run.updated_at) : "—"}</time>
        </div>

        <div className="metric-grid">
          <div><span>总耗时</span><strong>{run ? formatDuration(run) : "—"}</strong></div>
          <div><span>总 Token</span><strong>{formatTokenTotal(runUsage)}</strong></div>
          <div><span>预估费用</span><strong>{runUsage?.cost ?? "—"}</strong></div>
          <div><span>{agentWorkflow ? "模型调用" : "Attempt"}</span><strong>{agentWorkflow ? `${agentWorkflow.model_call_count} / ${agentWorkflow.max_model_calls}` : attemptId ? "已记录" : "—"}</strong></div>
        </div>

        {agentWorkflow ? (
          <AgentWorkflowPanel
            workflow={agentWorkflow}
            events={agentEventState.events}
            nodes={agentWorkflowNodes}
            reasoningBlocksByAttemptId={reasoningBlocksByAttemptId}
            hasEventGap={agentEventState.hasGap}
            isCancelling={isCancellingWorkflow}
            onCancel={() => void handleCancelAgentWorkflow()}
          />
        ) : (
          <>
        <section className="inspector-section">
          <div className="section-title"><h3>模型调用</h3><span>{attemptId ? "已关联" : "等待"}</span></div>
          <dl className="detail-list">
            <div><dt>Provider</dt><dd>{(runModelFacts?.provider ?? selectedProvider) || "—"}</dd></div>
            <div><dt>Model</dt><dd>{(runModelFacts?.model ?? selectedModel) || "—"}</dd></div>
            <div><dt>输入 Token</dt><dd>{runUsage?.input ?? "—"}</dd></div>
            <div><dt>输出 Token</dt><dd>{runUsage?.output ?? "—"}</dd></div>
            <div><dt>请求状态</dt><dd className={run?.status === "failed" ? "error-text" : "success-text"}>{statusLabel}</dd></div>
          </dl>
          {lastAssistantMessage?.reasoningBlocks.map((presentation) => (
            <ReasoningView
              key={`trajectory-${presentation.block_id}`}
              presentation={presentation}
              mode="trajectory"
            />
          ))}
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

        <div className="future-note"><span>预留</span><p><strong>Agent Workflow</strong>切换到 Agent 模式后显示真实节点；工具执行仍等待后续合同。</p></div>
          </>
        )}
        {errorMessage && <div className="error-banner" role="alert"><strong>需要处理</strong><p>{errorMessage}</p></div>}
        {lastAssistantMessage && !lastAssistantMessage.pending && !lastAssistantMessage.saving && <p className="inspector-footnote">当前查看：{lastAssistantMessage.content.length} 个字符的 Assistant 响应</p>}
      </aside>
    </main>
  );
}
