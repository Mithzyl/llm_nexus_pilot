"use client";

import { FileLibrary } from "../components/composer/FileLibrary";
import { ClaimEvidencePanel } from "../components/agent-workflow/ClaimEvidencePanel";
import { selectClaimEntries } from "../lib/claim-evidence";
import { messageFiles, uploadUserFile, validateUploadFiles, type UserFile } from "../lib/user-files";

import {
  browserCsrfHeaders,
  cancelAgentWorkflow,
  createMessage,
  createConversationTurn,
  createSession,
  getCurrentWorkspacePrincipal,
  getAgentWorkflowNode,
  getAgentWorkflowResult,
  getAgentWorkflowSummary,
  getContextBuildEvidence,
  getLatestRun,
  getReasoningBlocks,
  getRun,
  getRunAgentWorkflow,
  listLatestMessagePage,
  listModels,
  listSessions,
  logoutCurrentUser,
  NexusApiError,
  openAgentWorkflowStream,
  replayAgentWorkflowEvents,
  replayModelResponseEvents,
  updateSessionTitle,
} from "../lib/api";
import type {
  AgentWorkflowEvent,
  AgentWorkflowNodeResult,
  AgentWorkflowResult,
  AgentWorkflowSummary,
  ContextBuildEvidence,
  Message,
  MessageRole,
  MessageSummary,
  ProviderCatalog,
  ProviderName,
  ModelReasoningSelection,
  ReasoningPresentation,
  ReasoningBlockSnapshot,
  Run,
  RunDetail,
  Session,
  WorkspacePrincipal,
} from "../lib/types";
import { readStreamEvents, replayCommittedStreamEvents } from "../lib/sse";
import { quickResponseUsageFromRun } from "../lib/response-usage";
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
import {
  isAgentWorkflowAssistantMessage,
  isAgentWorkflowCancellable,
  cancelledMessageStatusLabel,
} from "../lib/agent-workflow-view";
import {
  hasSavedAssistantForLatestTurn,
  assistantProjectionForLatestTurn,
  isPersistedAgentMessage,
} from "../lib/history";
import {
  AgentWorkflowActivityFeed,
  AgentWorkflowPanel,
  agentWorkflowStatusLabel,
} from "../components/agent-workflow/AgentWorkflowPanel";
import { ModelPicker } from "../components/model-picker/ModelPicker";
import { CompactSelect } from "../components/composer/CompactSelect";
import { ReasoningSettings } from "../components/composer/ReasoningSettings";
import { ReasoningView } from "../components/reasoning/ReasoningView";
import { MarkdownContent } from "../components/markdown/MarkdownContent";
import {
  FilePickerButton,
  PendingUploadFiles,
  pendingUploadFileKey,
} from "../components/composer/FilePickerButton";
import { ContextEvidencePanel } from "../components/context/ContextEvidencePanel";
import { AuthenticationGate } from "../components/auth/AuthenticationGate";
import Link from "next/link";
import {
  composerConversationClearance,
  composerTextareaHeight,
  shouldSendComposerKey,
} from "../lib/composer-layout";
import {
  createReasoningConfiguration,
  resolveReasoningDisplayPolicy,
  type ReasoningDisplayPolicy,
  type ReasoningPresentationCapability,
} from "../lib/reasoning-settings";
import {
  isModelSelectionAllowed,
  modelDirectoryToProviderCatalog,
  resolveModelReasoningSelection,
  resolveInitialModelSelection,
} from "../lib/model-catalog";
import {
  conversationPath,
  resolveWorkspaceRoute,
  terminalConversationNavigationTarget,
  workspaceRouteKey,
} from "../lib/routes";
import {
  isPersistedResponseOutputTruncated,
  isResponseOutputTruncated,
  responseMessageMetadata,
} from "../lib/response-outcome";
import {
  clearConversationDraft,
  conversationDraftStorageKey,
  readConversationDraft,
  writeConversationDraft,
} from "../lib/conversation-drafts";
import {
  isConversationExecutionActive,
  isConversationSyncEvent,
  runWithConversationSubmissionLock,
  type ConversationSyncEvent,
} from "../lib/conversation-submission";
import { repeatableUserPrompt } from "../lib/message-actions";
import { runEvidenceTargetForMessage } from "../lib/run-evidence";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { followWorkflowResult } from "../lib/workflow-recovery";
import type { CSSProperties, KeyboardEvent } from "react";

const EMPTY_PROVIDER_CATALOG: ProviderCatalog = {
  providers: [],
  models_by_provider: {},
  reasoning_capabilities_by_provider_model: {},
  reasoning_policies_by_provider_model: {},
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

type LocalMessage = MessageSummary & {
  metadata_json?: Record<string, unknown> | null;
  local_id: string;
  content: string;
  pending?: boolean;
  saving?: boolean;
  saveFailed?: boolean;
  failed?: boolean;
  cancelled?: boolean;
  cancellationConfirmed?: boolean;
  agentWorkflow?: boolean;
  truncated?: boolean;
  reasoningBlocks: ReasoningPresentation[];
};

type ConnectionState = "checking" | "connected" | "unavailable";

type RunEvidenceSnapshot = {
  run: RunDetail;
  workflow: AgentWorkflowSummary | AgentWorkflowResult | null;
  nodes: AgentWorkflowNodeResult[];
  eventState: AgentWorkflowEventState;
  reasoningBlocksByAttemptId: Record<string, ReasoningBlockSnapshot[]>;
  attemptId: string | null;
  usage: { input: number | null; output: number | null; cost: string };
  modelFacts: { provider: string; model: string } | null;
  contextEvidence: ContextBuildEvidence | null;
  contextEvidenceError: string | null;
};

/**
 * Format server timestamps into the compact time label used by the workspace.
 */
function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** Derive a compact file-type badge without rewriting the original filename. */
function messageFileKind(filename: string): string {
  const match = /\.([a-z0-9]{1,5})$/i.exec(filename);
  return match ? match[1].toUpperCase() : "FILE";
}

/**
 * Calculate a run duration only when both server timestamps are available.
 */
function formatDuration(run: Run | RunDetail | null): string {
  if (!run?.started_at || !run.completed_at) return "待确认";
  const duration = new Date(run.completed_at).getTime() - new Date(run.started_at).getTime();
  return `${Math.max(0, duration) / 1000} 秒`;
}

/** Keep historical run status in the same language as the rest of the inspector. */
function runStatusLabel(status: Run["status"]): string {
  const labels: Record<Run["status"], string> = {
    pending: "等待执行",
    running: "生成中",
    completed: "已完成",
    failed: "失败",
    cancel_requested: "取消中",
    cancelled: "已取消",
  };
  return labels[status];
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
    agentWorkflow: isPersistedAgentMessage(message),
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
 * Render the workspace and coordinate durable API flow, distinguishing confirmed cancellation
 * from a local response abort whose server-side outcome is not yet known.
 */
export default function Home() {
  const router = useRouter();
  const routeParams = useParams<{ sessionId?: string; runId?: string }>();
  const workspaceRoute = resolveWorkspaceRoute(routeParams);
  const currentRouteKey = workspaceRouteKey(workspaceRoute);
  const initialDraftSessionId = workspaceRoute.kind === "conversation"
    ? workspaceRoute.sessionId
    : null;
  const [sessions, setSessions] = useState<Session[]>([]);
  const [workspacePrincipal, setWorkspacePrincipal] =
    useState<WorkspacePrincipal | null>(null);
  const [authenticationState, setAuthenticationState] = useState<
    "checking" | "authenticated" | "anonymous"
  >("checking");
  const [workspaceAccessError, setWorkspaceAccessError] = useState<string | null>(null);
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
  const [pendingUploadFiles, setPendingUploadFiles] = useState<File[]>([]);
  const [uploadProgress, setUploadProgress] = useState("");
  const uploadedFileCache = useRef(new Map<string, UserFile>());
  const [runModelFacts, setRunModelFacts] = useState<{ provider: string; model: string } | null>(
    null,
  );
  const [executionMode, setExecutionMode] = useState<ExecutionMode>("response");
  const [reviewPolicy, setReviewPolicy] = useState<ReviewPolicy>("always");
  const [maxParallelAgents, setMaxParallelAgents] = useState<AgentParallelism>(1);
  const [reasoningDisplayPolicy, setReasoningDisplayPolicy] =
    useState<ReasoningDisplayPolicy>("hidden");
  const [reasoningEffort, setReasoningEffort] =
    useState<ModelReasoningSelection>("medium");
  const [draft, setDraft] = useState("");
  const [run, setRun] = useState<Run | RunDetail | null>(null);
  const [agentWorkflow, setAgentWorkflow] = useState<
    AgentWorkflowSummary | AgentWorkflowResult | null
  >(null);
  const [workflowAbsentForRunId, setWorkflowAbsentForRunId] = useState<string | null>(null);
  const [agentWorkflowNodes, setAgentWorkflowNodes] = useState<AgentWorkflowNodeResult[]>([]);
  const [reasoningBlocksByAttemptId, setReasoningBlocksByAttemptId] = useState<
    Record<string, ReasoningBlockSnapshot[]>
  >({});
  const [agentEventState, setAgentEventState] = useState<AgentWorkflowEventState>(
    EMPTY_AGENT_EVENT_STATE,
  );
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [contextEvidenceResult, setContextEvidenceResult] = useState<{
    contextBuildId: string;
    evidence: ContextBuildEvidence | null;
    errorMessage: string | null;
  } | null>(null);
  const [runUsage, setRunUsage] = useState<{ input: number | null; output: number | null; cost: string } | null>(null);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  const [hasMoreMessages, setHasMoreMessages] = useState(false);
  const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false);
  const [isInspectorOpen, setIsInspectorOpen] = useState(true);
  const [evidenceTab, setEvidenceTab] = useState<"sources" | "process">("sources");
  const [inspectedRunEvidence, setInspectedRunEvidence] =
    useState<RunEvidenceSnapshot | null>(null);
  const [inspectedRunEvidenceState, setInspectedRunEvidenceState] =
    useState<"idle" | "loading" | "ready" | "error">("idle");
  const [inspectedRunEvidenceError, setInspectedRunEvidenceError] =
    useState<string | null>(null);
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
  const [isRenamingSession, setIsRenamingSession] = useState(false);
  const [sessionTitleDraft, setSessionTitleDraft] = useState("");
  const [isSavingSessionTitle, setIsSavingSessionTitle] = useState(false);
  const [remoteBusySessionIds, setRemoteBusySessionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [composerClearancePx, setComposerClearancePx] = useState(180);
  const [isComposerMinimized, setIsComposerMinimized] = useState(false);
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
  const sessionRestoreRequestId = useRef(0);
  const workflowRestoreRequestId = useRef(0);
  const workflowRecoveryController = useRef<AbortController | null>(null);

  /** Release recovery reads on unmount; this never cancels server-side execution. */
  useEffect(() => () => {
    workflowRestoreRequestId.current += 1;
    workflowRecoveryController.current?.abort();
  }, []);
  const providerCatalogRequestId = useRef(0);
  const providerCatalogRequestController = useRef<AbortController | null>(null);
  const reasoningDisplayPreference = useRef<ReasoningDisplayPolicy | null>(null);
  const draftStorageKey = useRef(conversationDraftStorageKey(initialDraftSessionId));
  const draftValue = useRef("");
  const submissionInProgress = useRef(false);
  const conversationSyncChannel = useRef<BroadcastChannel | null>(null);
  const conversationSyncSenderId = useRef<string | null>(null);
  const remoteBusyTimers = useRef<Map<string, number>>(new Map());
  const activeSessionIdValue = useRef<string | null>(null);
  const isSendingValue = useRef(false);
  const restoreSessionHandler = useRef<(sessionId: string) => Promise<void>>(async () => {});
  const inspectedRunEvidenceRequestId = useRef(0);

  const selectedReasoningCapabilities =
    selectedProvider && selectedModel
      ? providerCatalog.reasoning_capabilities_by_provider_model?.[selectedProvider]?.[
          selectedModel
        ]
      : undefined;
  const selectedReasoningPolicy =
    selectedProvider && selectedModel
      ? providerCatalog.reasoning_policies_by_provider_model?.[selectedProvider]?.[
          selectedModel
        ]
      : undefined;
  const latestContextBuildId =
    run && "attempts" in run ? run.attempts.at(-1)?.context_build_id ?? null : null;
  const contextEvidence =
    contextEvidenceResult?.contextBuildId === latestContextBuildId
      ? contextEvidenceResult.evidence
      : null;
  const contextEvidenceError =
    contextEvidenceResult?.contextBuildId === latestContextBuildId
      ? contextEvidenceResult.errorMessage
      : null;

  const contextEvidenceState = !latestContextBuildId
    ? "empty"
    : contextEvidenceResult?.contextBuildId !== latestContextBuildId
      ? "loading"
      : contextEvidenceError
        ? "error"
        : "ready";

  const isInspectingHistoricalRun = inspectedRunEvidenceState !== "idle";
  const evidenceRun = isInspectingHistoricalRun ? inspectedRunEvidence?.run ?? null : run;
  const evidenceWorkflow = isInspectingHistoricalRun
    ? inspectedRunEvidence?.workflow ?? null
    : agentWorkflow;
  const evidenceWorkflowNodes = isInspectingHistoricalRun
    ? inspectedRunEvidence?.nodes ?? []
    : agentWorkflowNodes;
  const evidenceEventState = isInspectingHistoricalRun
    ? inspectedRunEvidence?.eventState ?? EMPTY_AGENT_EVENT_STATE
    : agentEventState;
  const evidenceReasoningBlocks = isInspectingHistoricalRun
    ? inspectedRunEvidence?.reasoningBlocksByAttemptId ?? {}
    : reasoningBlocksByAttemptId;
  const evidenceAttemptId = isInspectingHistoricalRun
    ? inspectedRunEvidence?.attemptId ?? null
    : attemptId;
  const evidenceUsage = isInspectingHistoricalRun
    ? inspectedRunEvidence?.usage ?? null
    : runUsage;
  const evidenceModelFacts = isInspectingHistoricalRun
    ? inspectedRunEvidence?.modelFacts ?? null
    : runModelFacts;
  /** Keep empty-state placeholders out of the inspector without changing layout rules. */
  const isEvidenceEmpty =
    !evidenceRun && !evidenceWorkflow && !isSending && !isInspectingHistoricalRun;
  const displayedContextEvidence = isInspectingHistoricalRun
    ? inspectedRunEvidence?.contextEvidence ?? null
    : contextEvidence;
  const displayedContextEvidenceError = isInspectingHistoricalRun
    ? inspectedRunEvidence?.contextEvidenceError ?? null
    : contextEvidenceError;
  const displayedContextEvidenceState = isInspectingHistoricalRun
    ? inspectedRunEvidenceState === "loading"
      ? "loading"
      : inspectedRunEvidence?.contextEvidenceError
        ? "error"
        : inspectedRunEvidence?.contextEvidence
          ? "ready"
          : "empty"
    : contextEvidenceState;

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
    let isMounted = true;
    void getCurrentWorkspacePrincipal()
      .then((principal) => {
        if (!isMounted) return;
        setWorkspacePrincipal(principal);
        setAuthenticationState("authenticated");
      })
      .catch((error) => {
        if (!isMounted) return;
        setWorkspacePrincipal(null);
        if (!(error instanceof NexusApiError) || error.status !== 401) {
          setWorkspaceAccessError(error instanceof Error ? error.message : "无法确认工作台访问方式。");
        }
        setAuthenticationState("anonymous");
      });
    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    const savedTheme = window.localStorage.getItem("nexuspilot-theme");
    // localStorage is the explicitly local theme preference; sync it once after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsDark(savedTheme === "dark");
  }, []);

  useEffect(() => {
    if (!workspacePrincipal) return;
    if (draftValue.current) return;
    draftStorageKey.current = conversationDraftStorageKey(initialDraftSessionId, workspacePrincipal.user_id);
    const restoredDraft = readConversationDraft(window.sessionStorage, draftStorageKey.current);
    draftValue.current = restoredDraft;
    setDraft(restoredDraft);
  }, [workspacePrincipal, initialDraftSessionId]);

  useEffect(() => {
    conversationSyncSenderId.current = crypto.randomUUID();
    if (!("BroadcastChannel" in window)) return;
    const channel = new BroadcastChannel("nexuspilot:conversation-sync");
    const busyTimers = remoteBusyTimers.current;
    conversationSyncChannel.current = channel;
    channel.onmessage = (event: MessageEvent<unknown>) => {
      if (!isConversationSyncEvent(event.data)) return;
      const syncEvent = event.data;
      if (syncEvent.senderId === conversationSyncSenderId.current) return;
      const sessionId = syncEvent.sessionId;
      const previousTimer = remoteBusyTimers.current.get(sessionId);
      if (previousTimer !== undefined) window.clearTimeout(previousTimer);
      setRemoteBusySessionIds((current) => {
        const next = new Set(current);
        if (syncEvent.type === "submission-started") next.add(sessionId);
        else next.delete(sessionId);
        return next;
      });
      if (syncEvent.type === "submission-started") {
        const timer = window.setTimeout(() => {
          remoteBusyTimers.current.delete(sessionId);
          setRemoteBusySessionIds((current) => {
            const next = new Set(current);
            next.delete(sessionId);
            return next;
          });
        }, 660_000);
        remoteBusyTimers.current.set(sessionId, timer);
        return;
      }
      remoteBusyTimers.current.delete(sessionId);
      if (activeSessionIdValue.current === sessionId && !isSendingValue.current) {
        void restoreSessionHandler.current(sessionId);
      }
    };
    return () => {
      channel.close();
      conversationSyncChannel.current = null;
      for (const timer of busyTimers.values()) window.clearTimeout(timer);
      busyTimers.clear();
    };
  }, []);

  useEffect(() => {
    if (!latestContextBuildId) return;
    const controller = new AbortController();
    void getContextBuildEvidence(latestContextBuildId, controller.signal)
      .then((evidence) => {
        setContextEvidenceResult({
          contextBuildId: latestContextBuildId,
          evidence,
          errorMessage: null,
        });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setContextEvidenceResult({
            contextBuildId: latestContextBuildId,
            evidence: null,
            errorMessage:
              error instanceof Error ? error.message : "上下文证据暂不可用。",
          });
        }
      });
    return () => controller.abort();
  }, [latestContextBuildId]);

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
  }, [draft, authenticationState, isComposerMinimized]);

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
  }, [authenticationState]);

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
    if (authenticationState !== "authenticated") return;
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
  }, [authenticationState]);

  /**
   * Start one bounded provider-catalog request and invalidate any older request.
   * The timeout prevents a stalled upstream connection from leaving the selector
   * in a permanent loading state.
   */
  const startProviderCatalogRequest = useCallback(() => {
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
  }, []);

  /** Apply the model-matched default or a safe compatible form of the user's explicit preference. */
  const applyReasoningDisplayPolicyForModel = useCallback((
    presentation: ReasoningPresentationCapability,
  ) => {
    setReasoningDisplayPolicy(
      resolveReasoningDisplayPolicy(presentation, reasoningDisplayPreference.current),
    );
  }, []);

  /** Apply the catalog's first usable model with its effort and presentation defaults. */
  const applyInitialModelSelection = useCallback((catalog: ProviderCatalog) => {
    const initialSelection = resolveInitialModelSelection(catalog);
    setSelectedProvider(initialSelection.provider);
    setSelectedModel(initialSelection.model);
    if (!initialSelection.provider || !initialSelection.model) {
      applyReasoningDisplayPolicyForModel("none");
      return;
    }
    const initialReasoningPolicy =
      catalog.reasoning_policies_by_provider_model?.[initialSelection.provider]?.[
        initialSelection.model
      ];
    if (initialReasoningPolicy) {
      setReasoningEffort(
        initialReasoningPolicy.default_enabled
          ? initialReasoningPolicy.default_effort
          : "disabled",
      );
    }
    const initialReasoningCapabilities =
      catalog.reasoning_capabilities_by_provider_model?.[initialSelection.provider]?.[
        initialSelection.model
      ];
    applyReasoningDisplayPolicyForModel(
      initialReasoningCapabilities?.presentation ?? "none",
    );
  }, [
    applyReasoningDisplayPolicyForModel,
    setReasoningEffort,
    setSelectedModel,
    setSelectedProvider,
  ]);

  /** Record an explicit display preference so model switches do not silently increase disclosure. */
  function handleReasoningDisplayPolicyChange(policy: ReasoningDisplayPolicy) {
    reasoningDisplayPreference.current = policy;
    setReasoningDisplayPolicy(policy);
  }

  /** Load the provider catalog independently from conversation history for an explicit retry. */
  async function loadProviderCatalog() {
    const { controller, requestId, timeoutId } = startProviderCatalogRequest();
    setProviderCatalogState("loading");
    try {
      const modelDirectory = await listModels(controller.signal);
      const catalog = modelDirectoryToProviderCatalog(modelDirectory);
      if (providerCatalogRequestId.current !== requestId) return;
      setProviderCatalog(catalog);
      applyInitialModelSelection(catalog);
      setProviderCatalogState("ready");
    } catch {
      if (providerCatalogRequestId.current !== requestId) return;
      setProviderCatalog(EMPTY_PROVIDER_CATALOG);
      setSelectedProvider("");
      setSelectedModel("");
      setReasoningDisplayPolicy("hidden");
      setProviderCatalogState("error");
    } finally {
      window.clearTimeout(timeoutId);
      if (providerCatalogRequestController.current === controller) {
        providerCatalogRequestController.current = null;
      }
    }
  }

  /** Add newly selected files without duplicating the same browser-local file identity. */
  function handlePendingFilesSelected(files: File[]) {
    setPendingUploadFiles((currentFiles) => {
      const currentFileKeys = new Set(currentFiles.map(pendingUploadFileKey));
      const nextFiles = [...currentFiles];
      for (const file of files) {
        const fileKey = pendingUploadFileKey(file);
        if (currentFileKeys.has(fileKey)) continue;
        currentFileKeys.add(fileKey);
        nextFiles.push(file);
      }
      return nextFiles;
    });
    setErrorMessage(null);
  }

  /** Remove a pending selection; persisted leftovers remain explicitly manageable in My Files. */
  function handlePendingFileRemove(fileKey: string) {
    uploadedFileCache.current.delete(fileKey);
    setPendingUploadFiles((currentFiles) =>
      currentFiles.filter((file) => pendingUploadFileKey(file) !== fileKey),
    );
  }

  useEffect(() => {
    if (authenticationState !== "authenticated") return;
    const { controller, requestId, timeoutId } = startProviderCatalogRequest();
    void listModels(controller.signal)
      .then((modelDirectory) => {
        if (providerCatalogRequestId.current !== requestId) return;
        const catalog = modelDirectoryToProviderCatalog(modelDirectory);
        setProviderCatalog(catalog);
        applyInitialModelSelection(catalog);
        setProviderCatalogState("ready");
      })
      .catch(() => {
        if (providerCatalogRequestId.current !== requestId) return;
        setProviderCatalog(EMPTY_PROVIDER_CATALOG);
        setSelectedProvider("");
        setSelectedModel("");
        setReasoningDisplayPolicy("hidden");
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
  }, [applyInitialModelSelection, authenticationState, startProviderCatalogRequest]);

  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeSessionId) ?? null,
    [activeSessionId, sessions],
  );

  const isActiveSessionBusyInAnotherTab = Boolean(
    activeSessionId && remoteBusySessionIds.has(activeSessionId),
  );
  const isPersistedConversationExecutionActive = Boolean(
    activeSessionId && isConversationExecutionActive({
      run,
      workflowStatus: agentWorkflow?.status ?? null,
      messages,
      workflowAbsentForRunId,
    }),
  );
  const isConversationSubmissionBlocked =
    isActiveSessionBusyInAnotherTab || isPersistedConversationExecutionActive;

  const lastAssistantMessage = [...messages].reverse().find((message) => message.role === "assistant");

  /** Save the current draft and restore the draft belonging to the next conversation route. */
  function switchDraftContext(sessionId: string | null) {
    const nextStorageKey = conversationDraftStorageKey(sessionId, workspacePrincipal?.user_id);
    if (draftStorageKey.current !== nextStorageKey) {
      writeConversationDraft(window.sessionStorage, draftStorageKey.current, draftValue.current);
    }
    draftStorageKey.current = nextStorageKey;
    const restoredDraft = readConversationDraft(window.sessionStorage, nextStorageKey);
    draftValue.current = restoredDraft;
    setDraft(restoredDraft);
  }

  /** Update the visible draft and its tab-local recovery copy together. */
  function updateDraft(nextDraft: string) {
    draftValue.current = nextDraft;
    setDraft(nextDraft);
    writeConversationDraft(window.sessionStorage, draftStorageKey.current, nextDraft);
  }

  /** Remove the submitted draft only after the current User Message is durable. */
  function clearCommittedDraft() {
    clearConversationDraft(window.sessionStorage, draftStorageKey.current);
    draftValue.current = "";
    setDraft("");
  }

  /** Publish a bounded same-session refresh hint without transferring message content. */
  function publishConversationSyncEvent(
    type: ConversationSyncEvent["type"],
    sessionId: string,
  ) {
    const senderId = conversationSyncSenderId.current;
    if (!senderId) return;
    conversationSyncChannel.current?.postMessage({ type, sessionId, senderId });
  }

  /** Restore final-attempt identity and accumulated quick file-tool usage from persisted facts. */
  function restoreRunFacts(runDetail: RunDetail | null) {
    setRun(runDetail);
    const latestAttempt = runDetail?.attempts.at(-1);
    setAttemptId(latestAttempt?.attempt_id ?? null);
    if (latestAttempt) {
      setRunUsage(quickResponseUsageFromRun(runDetail!));
      setRunModelFacts({ provider: latestAttempt.provider, model: latestAttempt.model });
    } else if (runDetail) {
      setRunUsage({ input: null, output: null, cost: String(runDetail.cost_used ?? "—") });
      setRunModelFacts(null);
    } else {
      setRunUsage(null);
      setRunModelFacts(null);
    }
  }

  /** Invalidate stale workflow reads and clear projections without changing the current Run. */
  function resetAgentWorkflowFacts() {
    workflowRecoveryController.current?.abort();
    workflowRecoveryController.current = null;
    workflowRestoreRequestId.current += 1;
    setAgentWorkflow(null);
    setWorkflowAbsentForRunId(null);
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

  /** Apply persisted metrics and answer state, including server-confirmed cancellation. */
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
              cancellationConfirmed: result.status === "cancelled",
              saveFailed: isCompleted && !result.final_output?.assistant_message_id,
            }
          : message,
      ),
    );
    if (result.error) setErrorMessage(result.error.public_message);
  }

  /** Restore current Run facts, then follow nonterminal snapshots without re-executing it. */
  async function restoreAgentWorkflowForRun(runId: string, assistantLocalId?: string) {
    resetAgentWorkflowFacts();
    const requestId = workflowRestoreRequestId.current;
    let summary: AgentWorkflowSummary;
    try {
      summary = await getRunAgentWorkflow(runId);
      if (workflowRestoreRequestId.current !== requestId) return;
    } catch (error) {
      if (workflowRestoreRequestId.current !== requestId) return;
      if (error instanceof NexusApiError && error.status === 404) {
        setWorkflowAbsentForRunId(runId);
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : "无法读取 Agent Workflow。");
      return;
    }

    setExecutionMode("agent");
    setAgentWorkflow(summary);
    setMessages((current) => current.map((message) =>
      message.local_id === assistantLocalId
        || isAgentWorkflowAssistantMessage(message, runId)
        ? { ...message, agentWorkflow: true }
        : message,
    ));
    let restoredEvents = EMPTY_AGENT_EVENT_STATE;
    try {
      const replay = await replayAgentWorkflowEvents(summary.workflow_execution_id, 0);
      if (workflowRestoreRequestId.current !== requestId) return;
      await readAgentWorkflowEventStream(
        replay,
        (event) => {
          restoredEvents = appendAgentWorkflowEvent(restoredEvents, event);
        },
        { requireTerminalEvent: false },
      );
      if (workflowRestoreRequestId.current !== requestId) return;
      setAgentEventState(restoredEvents);
    } catch (error) {
      if (workflowRestoreRequestId.current !== requestId) return;
      setErrorMessage(
        `工作流事件回放不可用，当前以查询快照为准：${error instanceof Error ? error.message : "未知错误"}`,
      );
    }

    try {
      const result = await getAgentWorkflowResult(summary.workflow_execution_id);
      if (workflowRestoreRequestId.current !== requestId) return;
      applyAgentWorkflowResult(result, assistantLocalId);
      if (!["pending", "running", "waiting_for_input"].includes(result.status)) return;
    } catch (error) {
      if (workflowRestoreRequestId.current !== requestId) return;
      setErrorMessage(
        `工作流摘要已恢复，但完整节点暂不可用：${error instanceof Error ? error.message : "未知错误"}`,
      );
    }
    if (workflowRestoreRequestId.current !== requestId) return;
    const recovery = new AbortController();
    workflowRecoveryController.current = recovery;
    void followWorkflowResult({
      signal: recovery.signal,
      read: (signal) => getAgentWorkflowResult(summary.workflow_execution_id, signal),
      apply: async (result, signal) => {
        if (workflowRestoreRequestId.current !== requestId) return;
        // Apply the durable answer even when event replay is temporarily unavailable.
        applyAgentWorkflowResult(result, assistantLocalId);
        setErrorMessage((current) => current === "暂时无法更新执行进度，正在重试读取；请勿重复提交。" ? null : current);
        try {
          const replay = await replayAgentWorkflowEvents(
            summary.workflow_execution_id, restoredEvents.lastSequence, signal,
          );
          await readAgentWorkflowEventStream(replay, (event) => {
            restoredEvents = appendAgentWorkflowEvent(restoredEvents, event);
          }, { requireTerminalEvent: false });
          if (workflowRestoreRequestId.current !== requestId) return;
          setAgentEventState(restoredEvents);
        } catch (error) {
          if (signal.aborted || workflowRestoreRequestId.current !== requestId) return;
          setErrorMessage(`执行结果已更新，过程记录暂不可用：${error instanceof Error ? error.message : "未知错误"}`);
        }
        if (!["pending", "running", "waiting_for_input"].includes(result.status)) {
          const updatedRun = await getRun(runId);
          if (workflowRestoreRequestId.current === requestId) setRun(updatedRun);
        }
      },
      onError: (error) => {
        if (workflowRestoreRequestId.current === requestId) {
          if (error instanceof NexusApiError && [401, 403, 404].includes(error.status)) {
            recovery.abort();
            setErrorMessage("无法继续读取执行结果，请确认登录状态和访问权限。");
            return;
          }
          setErrorMessage("暂时无法更新执行进度，正在重试读取；请勿重复提交。");
        }
      },
    });
  }

  /**
   * Execute up to two reviewed rounds with backend-derived attachment permissions;
   * consume the terminal snapshot without duplicating the backend-created final Message.
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
        workflow_version: "1.1.0",
        idempotency_key: crypto.randomUUID(),
        role_bindings: {
          controller: binding,
          planner: binding,
          ...(reviewPolicy === "never" ? {} : { reviewer: binding }),
        },
        reasoning: createReasoningConfiguration(reasoningEffort, selectedReasoningPolicy),
        review_policy: reviewPolicy,
        max_nodes: 32,
        max_model_calls: 16,
        max_parallel_agents: maxParallelAgents,
        max_workflow_rounds: 2,
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
   * Ignore late reads after another conversation, Run route or submission replaces this view.
   * Restored cancellation is confirmed by the persisted Run, not a local response abort.
   */
  async function restoreSession(sessionId: string, requestedRun?: RunDetail | null) {
    const requestId = ++sessionRestoreRequestId.current;
    const nextDraftStorageKey = conversationDraftStorageKey(sessionId, workspacePrincipal?.user_id);
    if (draftStorageKey.current !== nextDraftStorageKey) switchDraftContext(sessionId);
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
      if (sessionRestoreRequestId.current !== requestId) return;
      const restoredMessages = page.items.map(messageFromMessage);
      const projection = assistantProjectionForLatestTurn(page.items, latestRun);
      const pendingAssistant = projection
        ? { ...createLocalMessage("assistant", projection.content, projection.runId),
            pending: projection.pending, failed: projection.failed, cancelled: projection.cancelled,
            cancellationConfirmed: projection.cancelled }
        : null;
      setMessages(pendingAssistant ? [...restoredMessages, pendingAssistant] : restoredMessages);
      setMessageCursor(page.next_cursor);
      setHasMoreMessages(page.has_more);
      restoreRunFacts(latestRun);
      if (latestRun) {
        await restoreAgentWorkflowForRun(
          latestRun.run_id,
          pendingAssistant?.local_id,
        );
      }
      if (sessionRestoreRequestId.current !== requestId) return;
      const hasPersistedAssistant = hasSavedAssistantForLatestTurn(page.items);
      setResponseCompleted(hasPersistedAssistant);
      setAssistantSaved(hasPersistedAssistant);
    } catch (error) {
      if (sessionRestoreRequestId.current !== requestId) return;
      setErrorMessage(error instanceof Error ? error.message : "无法读取会话消息");
      setMessages([]);
      setMessageCursor(null);
      setHasMoreMessages(false);
      if (requestedRun !== undefined) restoreRunFacts(requestedRun);
    }
  }

  /** Return the inspector to live workbench facts without changing any execution state. */
  function showCurrentRunEvidence() {
    inspectedRunEvidenceRequestId.current += 1;
    setInspectedRunEvidence(null);
    setInspectedRunEvidenceError(null);
    setInspectedRunEvidenceState("idle");
  }

  /** Navigate to and restore one owned conversation from its stable URL. */
  async function handleSelectSession(sessionId: string) {
    showCurrentRunEvidence();
    switchDraftContext(sessionId);
    restoredRouteKey.current = workspaceRouteKey({ kind: "conversation", sessionId });
    router.push(conversationPath(sessionId));
    await restoreSession(sessionId);
  }

  /** Restore the requested owned Run, ignoring older navigation reads that arrive later. */
  async function restoreRunRoute(runId: string) {
    const requestId = ++sessionRestoreRequestId.current;
    showCurrentRunEvidence();
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
      if (sessionRestoreRequestId.current !== requestId) return;
      if (runDetail.session_id) {
        await restoreSession(runDetail.session_id, runDetail);
        return;
      }

      restoreRunFacts(runDetail);
      await restoreAgentWorkflowForRun(runDetail.run_id);
    } catch (error) {
      if (sessionRestoreRequestId.current !== requestId) return;
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
   * Invalidate previous reads and reset the view without deleting any persisted session.
   */
  function resetConversationView() {
    sessionRestoreRequestId.current += 1;
    showCurrentRunEvidence();
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
    setIsSidebarOpen(false);
  }

  /** Navigate to the stable empty-workspace route without deleting persisted data. */
  function handleNewConversation() {
    switchDraftContext(null);
    restoredRouteKey.current = "home";
    router.push("/");
    resetConversationView();
  }

  /** Keep browser history navigation synchronized with persisted Session and Run facts. */
  useEffect(() => {
    if (authenticationState !== "authenticated") return;
    const restoreFrame = window.requestAnimationFrame(() => {
      if (restoredRouteKey.current === currentRouteKey) return;
      restoredRouteKey.current = currentRouteKey;
      setPendingUploadFiles([]);
      uploadedFileCache.current.clear();

      if (workspaceRoute.kind === "home") {
        switchDraftContext(null);
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
  }, [authenticationState, currentRouteKey]);

  useEffect(() => {
    activeSessionIdValue.current = activeSessionId;
    isSendingValue.current = isSending;
    restoreSessionHandler.current = restoreSession;
  });

  /**
   * Read an immutable evidence snapshot without mutating the active conversation or Workflow stream.
   * Partial evidence failures remain scoped to the inspector and never replace workbench errors.
   */
  async function readRunEvidenceSnapshot(runId: string): Promise<RunEvidenceSnapshot> {
    const runDetail = await getRun(runId);
    let workflow: AgentWorkflowSummary | AgentWorkflowResult | null = null;
    let nodes: AgentWorkflowNodeResult[] = [];
    let eventState = EMPTY_AGENT_EVENT_STATE;

    try {
      const summary = await getRunAgentWorkflow(runId);
      workflow = summary;
      try {
        const replay = await replayAgentWorkflowEvents(summary.workflow_execution_id, 0);
        await readAgentWorkflowEventStream(
          replay,
          (event) => {
            eventState = appendAgentWorkflowEvent(eventState, event);
          },
          { requireTerminalEvent: false },
        );
      } catch {
        // The result snapshot below remains authoritative when public event replay is unavailable.
      }
      try {
        const result = await getAgentWorkflowResult(summary.workflow_execution_id);
        workflow = result;
        nodes = result.nodes;
      } catch {
        // A running Workflow may expose only its summary until the bounded result is readable.
      }
    } catch (error) {
      if (!(error instanceof NexusApiError && error.status === 404)) throw error;
    }

    const latestAttempt = runDetail.attempts.at(-1) ?? null;
    const attemptIds = Array.from(new Set([
      ...nodes.flatMap((node) => node.evidence.model_attempt_ids),
      ...(latestAttempt ? [latestAttempt.attempt_id] : []),
    ]));
    const reasoningEntries = await Promise.all(
      attemptIds.map(async (modelAttemptId) => {
        try {
          const blocks = (await getReasoningBlocks(modelAttemptId)).filter((block) =>
            isReasoningPresentation(block),
          );
          return [modelAttemptId, blocks] as const;
        } catch {
          return [modelAttemptId, []] as const;
        }
      }),
    );

    const contextBuildId = latestAttempt?.context_build_id ?? null;
    let snapshotContextEvidence: ContextBuildEvidence | null = null;
    let snapshotContextEvidenceError: string | null = null;
    if (contextBuildId) {
      try {
        snapshotContextEvidence = await getContextBuildEvidence(contextBuildId);
      } catch (error) {
        snapshotContextEvidenceError =
          error instanceof Error ? error.message : "上下文证据暂不可用。";
      }
    }

    const workflowResult = workflow && "nodes" in workflow ? workflow : null;
    return {
      run: runDetail,
      workflow,
      nodes,
      eventState,
      reasoningBlocksByAttemptId: Object.fromEntries(reasoningEntries),
      attemptId:
        workflowResult?.completion?.model_attempt_ids.at(-1) ?? latestAttempt?.attempt_id ?? null,
      usage: workflowResult
        ? {
            input: workflowResult.aggregate_usage.input_tokens,
            output: workflowResult.aggregate_usage.output_tokens,
            cost: String(workflowResult.aggregate_usage.estimated_cost),
          }
        : {
            input: latestAttempt?.input_tokens ?? null,
            output: latestAttempt?.output_tokens ?? null,
            cost: String(latestAttempt?.estimated_cost ?? runDetail.cost_used ?? "—"),
          },
      modelFacts: latestAttempt
        ? { provider: latestAttempt.provider, model: latestAttempt.model }
        : null,
      contextEvidence: snapshotContextEvidence,
      contextEvidenceError: snapshotContextEvidenceError,
    };
  }

  /** Open evidence for the clicked Assistant response without changing the workspace route. */
  async function handleOpenRunEvidence(message: LocalMessage, tab: "sources" | "process" = "process") {
    const targetRunId = runEvidenceTargetForMessage(message);
    setEvidenceTab(tab);
    setIsInspectorOpen(true);
    if (!targetRunId) {
      setInspectedRunEvidenceError("这条回复没有关联可读取的运行证据。");
      setInspectedRunEvidenceState("error");
      return;
    }

    const requestId = inspectedRunEvidenceRequestId.current + 1;
    inspectedRunEvidenceRequestId.current = requestId;
    setInspectedRunEvidence(null);
    setInspectedRunEvidenceError(null);
    setInspectedRunEvidenceState("loading");
    try {
      const snapshot = await readRunEvidenceSnapshot(targetRunId);
      if (inspectedRunEvidenceRequestId.current !== requestId) return;
      setInspectedRunEvidence(snapshot);
      setInspectedRunEvidenceState("ready");
    } catch (error) {
      if (inspectedRunEvidenceRequestId.current !== requestId) return;
      setInspectedRunEvidenceError(
        error instanceof Error ? error.message : "无法读取这条回复的运行证据。",
      );
      setInspectedRunEvidenceState("error");
    }
  }

  /**
   * Copy assistant text through the browser clipboard and provide visible feedback.
   */
  async function handleCopy(message: LocalMessage) {
    await navigator.clipboard.writeText(message.content);
    setCopiedMessageId(message.local_id);
    window.setTimeout(() => setCopiedMessageId(null), 1400);
  }

  /** Restore the prompt paired with one Assistant response without submitting or mutating history. */
  function handleRepeatQuestion(message: LocalMessage) {
    const prompt = repeatableUserPrompt(messages, message.local_id);
    if (prompt === null) {
      setErrorMessage("找不到这条回复对应的用户问题。");
      return;
    }
    updateDraft(prompt);
    expandComposer();
  }

  /** Reveal the preserved draft and focus it after the hidden input becomes visible. */
  function expandComposer() {
    setIsComposerMinimized(false);
    window.requestAnimationFrame(() => composerTextarea.current?.focus());
  }

  /** Hide editor controls without clearing state and keep keyboard focus on the restore action. */
  function minimizeComposer() {
    setIsComposerMinimized(true);
    window.requestAnimationFrame(() =>
      composerWrap.current?.querySelector<HTMLButtonElement>(".composer-restore")?.focus(),
    );
  }

  /** Scope Enter submission to the input itself, leaving IME and Shift+Enter untouched. */
  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!shouldSendComposerKey({ ...event, isComposing: event.nativeEvent.isComposing,
      keyCode: event.nativeEvent.keyCode, focused: document.activeElement === event.currentTarget,
      canSend: !isSending && !submissionInProgress.current && !isConversationSubmissionBlocked &&
        Boolean(draft.trim()) && isModelSelectionAllowed(providerCatalog, selectedProvider, selectedModel),
    })) return;
    event.preventDefault();
    void handleSend();
  }

  /** Open the active conversation title as an explicit bounded rename form. */
  function beginSessionRename() {
    if (!activeSession) return;
    setSessionTitleDraft(activeSession.title ?? "");
    setIsRenamingSession(true);
  }

  /** Persist an owned conversation title without changing its active lifecycle state. */
  async function saveSessionTitle() {
    if (!activeSession || isSavingSessionTitle) return;
    const title = sessionTitleDraft.trim();
    if (!title || title.length > 255) {
      setErrorMessage("会话标题必须为 1 至 255 个字符。");
      return;
    }
    setIsSavingSessionTitle(true);
    setErrorMessage(null);
    try {
      const updatedSession = await updateSessionTitle(activeSession.session_id, title);
      setSessions((current) => current.map((session) =>
        session.session_id === updatedSession.session_id ? updatedSession : session,
      ));
      setIsRenamingSession(false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法更新会话标题。");
    } finally {
      setIsSavingSessionTitle(false);
    }
  }

  /** Confirm cancellation only after the server accepts it; active Provider calls remain auditable. */
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
                cancellationConfirmed: true,
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

  /** Abort file preparation before any workflow exists; otherwise cancel the active execution. */
  function handleStop() {
    if (uploadProgress) {
      activeRequestController.current?.abort();
      return;
    }
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
   * Replace optimistic messages with saved records before deciding whether the turn is finished.
   * Keep the previous execution facts until uploads and new-turn persistence succeed.
   */
  async function submitCurrentDraft() {
    const input = draft.trim();
    if (
      !input ||
      isSending ||
      isPersistedConversationExecutionActive ||
      !isModelSelectionAllowed(providerCatalog, selectedProvider, selectedModel)
    ) return;
    try { validateUploadFiles(pendingUploadFiles); }
    catch (error) { setErrorMessage(error instanceof Error ? error.message : "文件不符合要求。"); return; }
    sessionRestoreRequestId.current += 1;
    const submittedMode = executionMode;
    let submittedRunId: string | null = null;
    let submittedAssistantLocalId: string | null = null;
    let submittedSessionId: string | null = null;
    let hasRestorableTerminalFacts = false;
    let syncNotificationSessionId = activeSessionId;

    if (syncNotificationSessionId) {
      publishConversationSyncEvent("submission-started", syncNotificationSessionId);
    }

    setIsSending(true);
    setErrorMessage(null);
    const userMessage = createLocalMessage("user", input);

    try {
      const uploadController = new AbortController();
      activeRequestController.current = uploadController;
      const fileIds: string[] = [];
      for (const file of pendingUploadFiles) {
        setUploadProgress(`正在上传并解析：${file.name}`);
        const key = pendingUploadFileKey(file);
        let uploaded = uploadedFileCache.current.get(key);
        if (!uploaded) {
          uploaded = await uploadUserFile(file, crypto.randomUUID(), uploadController.signal);
          uploadedFileCache.current.set(key, uploaded);
        }
        fileIds.push(uploaded.file_id);
      }
      if (uploadController.signal.aborted) return;
      setUploadProgress("");
      setMessages((current) => [...current, userMessage]);
      let session = activeSession;
      if (!session) {
        session = await createSession(input.slice(0, 42));
        setSessions((current) => [session as Session, ...current]);
        setActiveSessionId(session.session_id);
        syncNotificationSessionId = session.session_id;
        publishConversationSyncEvent("submission-started", session.session_id);
      }
      submittedSessionId = session.session_id;

      const createdTurn = await createConversationTurn(session.session_id, {
        content_text: input,
        ...(fileIds.length ? { file_ids: fileIds } : {}),
      });
      // Upload failure must preserve the previous saved response and its no-workflow proof.
      // Replace execution facts only after the server has created the new conversation turn.
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
      setPendingUploadFiles([]);
      uploadedFileCache.current.clear();
      clearCommittedDraft();
      const createdRun = createdTurn.run;
      setMessages((current) => current.map((message) =>
        message.local_id === userMessage.local_id
          ? { ...messageFromMessage(createdTurn.message), local_id: userMessage.local_id }
          : message,
      ));
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
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...browserCsrfHeaders() },
        signal: responseController.signal,
        body: JSON.stringify({
          run_id: createdRun.run_id,
          current_user_message_id: createdTurn.message.message_id,
          provider: selectedProvider,
          model: selectedModel,
          input,
          timeout_seconds: 60,
          reasoning: createReasoningConfiguration(reasoningEffort, selectedReasoningPolicy),
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
            input: data.usage.input_tokens ?? null,
            output: data.usage.output_tokens ?? null,
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
          setAttemptId(responseResult?.id ?? data.attempt_id ?? null);
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
            input: responseResult?.usage.input_tokens ?? null,
            output: responseResult?.usage.output_tokens ?? null,
            cost: responseResult?.usage.estimated_cost ?? "—",
          });
          try {
            if (!completedText) throw new Error("响应没有可保存的文本。");
            const savedMessage = await createMessage(session.session_id, {
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
                message.local_id === assistantMessage.local_id
                  ? { ...message, ...messageFromMessage(savedMessage), local_id: message.local_id, saving: false }
                  : message,
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
          await restoreAgentWorkflowForRun(createdRun.run_id);
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
      setUploadProgress("");
      activeRequestController.current = null;
      setIsSending(false);
      if (syncNotificationSessionId) {
        publishConversationSyncEvent("submission-finished", syncNotificationSessionId);
      }
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

  /** Serialize same-session submissions across tabs before entering the billable request flow. */
  async function handleSend() {
    if (submissionInProgress.current) return;
    if (isConversationSubmissionBlocked) {
      setErrorMessage(
        isActiveSessionBusyInAnotherTab
          ? "此会话正在另一个标签页生成回复，请等待完成后再发送。"
          : "此会话仍有服务端运行正在处理，请等待运行结束后再发送。",
      );
      return;
    }
    submissionInProgress.current = true;
    try {
      const lockManager = "locks" in navigator ? navigator.locks : null;
      const result = await runWithConversationSubmissionLock(
        lockManager,
        activeSessionId,
        submitCurrentDraft,
      );
      if (!result.acquired) {
        setErrorMessage("此会话正在另一个标签页生成回复，请等待完成后再发送。");
      }
    } finally {
      submissionInProgress.current = false;
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

  /** Revoke the current browser session before returning to the login boundary. */
  async function handleLogout() {
    try {
      await logoutCurrentUser();
      window.location.replace("/");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "退出失败，请重试。");
    }
  }

  if (authenticationState !== "authenticated" || !workspacePrincipal) {
    return (
      <AuthenticationGate
        isChecking={authenticationState === "checking"}
        unavailableMessage={workspaceAccessError}
        onAuthenticated={(principal) => {
          setWorkspacePrincipal(principal);
          setAuthenticationState("authenticated");
        }}
      />
    );
  }

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
                </span>
                <time>{formatTime(session.updated_at)}</time>
              </button>
            ))}
          </nav>
        </div>

        <div className="sidebar-footer">
          <div className="authenticated-user">
            <span>{workspacePrincipal.display_name}</span>
            {workspacePrincipal.authentication_method === "browser_session" && (
              <button type="button" onClick={() => void handleLogout()}>退出</button>
            )}
          </div>
          <Link className="settings-link" href="/settings/models">模型目录</Link>
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
          <div className="conversation-title-block">
            <p className="eyebrow">当前会话</p>
            {isRenamingSession && activeSession ? (
              <form
                className="conversation-title-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void saveSessionTitle();
                }}
              >
                <input
                  value={sessionTitleDraft}
                  onChange={(event) => setSessionTitleDraft(event.target.value)}
                  maxLength={255}
                  autoFocus
                  aria-label="会话标题"
                />
                <button type="submit" disabled={isSavingSessionTitle}>保存</button>
                <button type="button" onClick={() => setIsRenamingSession(false)}>取消</button>
              </form>
            ) : (
              <div className="conversation-title-row">
                <h1>{activeSession?.title ?? "新对话"}</h1>
                {activeSession && <button type="button" onClick={beginSessionRename}>重命名</button>}
              </div>
            )}
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
                    <button key={suggestion} onClick={() => updateDraft(suggestion)}>{suggestion}</button>
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
                    {messageFiles(message.metadata_json).length > 0 && (
                      <div className="message-file-list">
                        {messageFiles(message.metadata_json).map((file) => (
                          <a
                            className="message-file"
                            key={file.file_id}
                            href={`/api/nexus/files/${encodeURIComponent(file.file_id)}/content`}
                            title={file.filename}
                            aria-label={`下载 ${file.filename}`}
                          >
                            <span className="message-file-kind" aria-hidden="true">{messageFileKind(file.filename)}</span>
                            <span className="message-file-name">{file.filename}</span>
                            <span className="message-file-action" aria-hidden="true">↓</span>
                          </a>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    <div className="assistant-heading">
                      <div className="assistant-avatar"><span /></div>
                      <div>
                        <strong>NexusPilot</strong>
                        <small><i className={message.failed || message.cancelled || message.saveFailed || message.truncated ? "failed-dot" : ""} /> {message.cancelled ? cancelledMessageStatusLabel(message.cancellationConfirmed) : message.saveFailed ? "已生成 · 保存失败" : message.failed ? (message.agentWorkflow ? "工作流失败" : "生成失败") : message.pending ? (message.agentWorkflow ? "Agent Workflow 运行中" : "生成中") : message.saving ? "已生成 · 正在保存" : message.truncated ? "达到输出上限 · 已保存部分回复" : message.agentWorkflow ? "工作流已完成" : "已完成"}</small>
                      </div>
                    </div>
                    <div className="assistant-content">
                      {message.reasoningBlocks.map((presentation) => (
                        <ReasoningView
                          key={presentation.block_id}
                          presentation={presentation}
                        />
                      ))}
                      {message.agentWorkflow && message.run_id === run?.run_id && (
                        <AgentWorkflowActivityFeed events={agentEventState.events} />
                      )}
                      {message.content ? <MarkdownContent content={message.content} /> : <p className="typing-line"><span /> <span /> <span /></p>}
                      {message.saving && <p className="stream-note">模型响应已完成，正在写入会话记录。</p>}
                      {message.truncated && !message.pending && <p className="stream-note error-note">模型达到输出 Token 上限，以上是已保存的部分回复；可以要求模型继续，或重新生成更短的回答。</p>}
                      {message.saveFailed && <p className="stream-note error-note">响应已生成，但会话保存失败；已保留当前文本。</p>}
                      {(message.failed || message.cancelled) && <p className="stream-note error-note">此状态不代表已生成最终答案；可查看运行详情或再次提问。</p>}
                    </div>
                    {!message.pending && !message.saving && (
                      <div className="message-actions">
                        <button onClick={() => void handleCopy(message)}>{copiedMessageId === message.local_id ? "已复制" : "复制"}</button>
                        <button onClick={() => handleRepeatQuestion(message)}>再次提问</button>
                        {message.agentWorkflow && <button onClick={() => void handleOpenRunEvidence(message, "sources")}>
                          查看结论与来源{message.run_id === run?.run_id && selectClaimEntries(agentWorkflowNodes).length > 0 ? ` · ${selectClaimEntries(agentWorkflowNodes).length} 条` : ""}
                        </button>}
                        <button onClick={() => void handleOpenRunEvidence(message)}>查看运行证据 ↗</button>
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
            <div className={`composer ${isComposerMinimized ? "is-minimized" : ""}`}>
              {isComposerMinimized && <div className="composer-minimized-row">
                <button type="button" className="composer-restore" aria-expanded={false} aria-controls="composer-editor" onClick={expandComposer}>
                  展开输入框{draft.trim() ? " · 有草稿" : ""}{pendingUploadFiles.length ? ` · ${pendingUploadFiles.length} 个文件` : ""}
                </button>
                {isSending && <button type="button" className="composer-quiet-button" disabled={isCancellingWorkflow} onClick={handleStop}>{executionMode === "agent" ? "取消工作流" : "停止生成"}</button>}
              </div>}
              <div id="composer-editor" hidden={isComposerMinimized}>
              <span id="composer-keyboard-hint" className="composer-keyboard-hint">Enter 发送 · Shift+Enter 换行</span>
              <button type="button" className="composer-minimize" aria-label="收起输入框" aria-expanded={true} aria-controls="composer-editor" onClick={minimizeComposer}>收起</button>
              <textarea
                ref={composerTextarea}
                value={draft}
                onChange={(event) => updateDraft(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                aria-describedby="composer-keyboard-hint"
                placeholder="输入问题，或粘贴内容…"
                aria-label="消息内容"
                rows={1}
              />
              {isActiveSessionBusyInAnotherTab && (
                <p className="composer-sync-note" role="status">
                  此会话正在另一个标签页生成；完成后会自动读取最新消息。
                </p>
              )}
              {!isActiveSessionBusyInAnotherTab && isPersistedConversationExecutionActive && !isSending && (
                <p className="composer-sync-note" role="status">
                  服务端仍在处理此会话；当前不可重复提交，可在运行详情中查看状态。
                </p>
              )}
              <PendingUploadFiles
                files={pendingUploadFiles}
                disabled={isSending}
                onRemove={handlePendingFileRemove}
              />
              {pendingUploadFiles.length > 0 && (
                <p className="composer-file-notice" role="status">
                  {uploadProgress || "发送时上传并解析 · 每个文件不超过 20 MiB · 暂不支持扫描件与图片"}
                </p>
              )}
              {executionMode === "agent" && (
                <div className="agent-config-shelf" aria-label="Agent 工作流设置">
                  <div className="agent-config-item">
                    <span>执行</span>
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
                  </div>
                  <div className="agent-config-item">
                    <span>审核</span>
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
                </div>
              )}
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
                  <div className="composer-file-control">
                    <FilePickerButton
                      selectedFileCount={pendingUploadFiles.length}
                      disabled={isSending}
                      onFilesSelected={handlePendingFilesSelected}
                    />
                    <FileLibrary disabled={isSending} compact />
                  </div>
                  {selectedReasoningPolicy && (selectedReasoningPolicy.supported_efforts.length > 0 || selectedReasoningPolicy.supports_disable) && (
                    <ReasoningSettings
                      effort={reasoningEffort}
                      supportedEfforts={selectedReasoningPolicy.supported_efforts}
                      supportsDisable={selectedReasoningPolicy.supports_disable}
                      displayPolicy={reasoningDisplayPolicy}
                      presentationCapability={
                        executionMode === "response"
                          ? selectedReasoningCapabilities?.presentation ?? "none"
                          : "none"
                      }
                      disabled={isSending}
                      onEffortChange={setReasoningEffort}
                      onDisplayPolicyChange={handleReasoningDisplayPolicyChange}
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
                      const nextReasoningPolicy =
                        providerCatalog.reasoning_policies_by_provider_model?.[provider]?.[model];
                      if (nextReasoningPolicy) {
                        setReasoningEffort((current) =>
                          resolveModelReasoningSelection(nextReasoningPolicy, current),
                        );
                      }
                      const nextReasoningCapabilities =
                        providerCatalog.reasoning_capabilities_by_provider_model?.[provider]?.[
                          model
                        ];
                      applyReasoningDisplayPolicyForModel(
                        nextReasoningCapabilities?.presentation ?? "none",
                      );
                    }}
                  />
                  <button className="send-button" disabled={isCancellingWorkflow || (!isSending && (isConversationSubmissionBlocked || !draft.trim() || !isModelSelectionAllowed(providerCatalog, selectedProvider, selectedModel)))} onClick={() => (isSending ? handleStop() : void handleSend())} aria-label={isSending ? executionMode === "agent" ? "取消工作流" : "停止生成" : "发送消息"}>{isSending ? "■" : "↑"}</button>
                </div>
              </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <aside className={`inspector ${isInspectorOpen ? "is-open" : ""}`} aria-label="运行详情" inert={!isInspectorOpen}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            setIsInspectorOpen(false);
            document.querySelector<HTMLButtonElement>(".detail-toggle")?.focus();
          }
        }}>
        <div className="inspector-header">
          <div>
            <h2>运行详情</h2>
            <p className="eyebrow">{isInspectingHistoricalRun && evidenceRun?.run_id !== run?.run_id ? "历史回复" : "当前回复"}</p>
            {isInspectingHistoricalRun && (
              <button className="evidence-current-button" onClick={showCurrentRunEvidence}>
                返回当前运行
              </button>
            )}
          </div>
          <button className="icon-button" onClick={() => setIsInspectorOpen(false)} aria-label="关闭运行详情">×</button>
        </div>

        {isInspectingHistoricalRun && inspectedRunEvidenceState === "loading" && (
          <p className="evidence-load-state" role="status">正在读取所选回复的持久化证据…</p>
        )}
        {isInspectingHistoricalRun && inspectedRunEvidenceState === "error" && (
          <div className="error-banner" role="alert">
            <strong>运行证据读取失败</strong>
            <p>{inspectedRunEvidenceError}</p>
          </div>
        )}

        {evidenceWorkflow && <>
          <div className="evidence-tabs" aria-label="运行详情视图">
            <button type="button" aria-pressed={evidenceTab === "sources"} onClick={() => setEvidenceTab("sources")}>结论与来源</button>
            <button type="button" aria-pressed={evidenceTab === "process"} onClick={() => setEvidenceTab("process")}>运行过程</button>
          </div>
          {isInspectorOpen && evidenceTab === "sources" && <>
            {["failed", "cancelled", "outcome_unknown"].includes(evidenceWorkflow.status) && <p className="claim-source-warning claim-execution-warning" role="status">本次执行未正常完成。以下仅展示已提交的阶段结论，详情请查看运行过程。</p>}
            <ClaimEvidencePanel key={evidenceWorkflow.workflow_execution_id} workflowId={evidenceWorkflow.workflow_execution_id}
              nodes={evidenceWorkflowNodes} running={["pending", "running"].includes(evidenceWorkflow.status)} />
            <button type="button" className="claim-technical-button" onClick={() => setEvidenceTab("process")}>运行过程与技术记录</button>
          </>}
        </>}
        {(!evidenceWorkflow || evidenceTab === "process") && <>
        <div className="run-state">
          <div className={`status-emblem ${evidenceRun?.status === "failed" || evidenceWorkflow?.status === "failed" || evidenceWorkflow?.status === "outcome_unknown" ? "is-failed" : ""}`}>{evidenceRun?.status === "failed" || evidenceWorkflow?.status === "failed" || evidenceWorkflow?.status === "outcome_unknown" ? "!" : evidenceRun ? "✓" : "·"}</div>
          <div>
            <strong>{evidenceWorkflow ? agentWorkflowStatusLabel(evidenceWorkflow.status) : isInspectingHistoricalRun ? evidenceRun?.status === "completed" ? "响应已保存" : evidenceRun?.status === "failed" ? "生成失败" : evidenceRun?.status === "cancelled" ? "已取消" : evidenceRun ? "运行处理中" : "等待证据" : isSending ? "生成进行中" : evidenceRun?.status === "failed" ? "生成失败" : assistantSaveFailed ? "响应已生成，保存失败" : assistantSaved ? lastAssistantMessage?.truncated ? "响应达到上限，部分内容已保存" : "响应已保存" : responseCompleted ? "响应已完成，待保存" : evidenceRun ? "运行已创建" : "等待首次运行"}</strong>
            {isInspectingHistoricalRun && <p>所选回复的只读快照，不改变当前工作台</p>}
          </div>
          {evidenceRun && <time>{formatTime(evidenceRun.updated_at)}</time>}
        </div>

        <div className="metric-grid">
          <div>{!isEvidenceEmpty && <><span>总耗时</span><strong>{evidenceRun ? formatDuration(evidenceRun) : "—"}</strong></>}</div>
          <div>{!isEvidenceEmpty && <><span>总 Token</span><strong>{formatTokenTotal(evidenceUsage)}</strong></>}</div>
          <div>{!isEvidenceEmpty && <><span>预估费用</span><strong>{evidenceUsage?.cost ?? "—"}</strong></>}</div>
          <div>{!isEvidenceEmpty && <><span>{evidenceWorkflow ? "模型调用" : "Attempt"}</span><strong>{evidenceWorkflow ? `${evidenceWorkflow.model_call_count} / ${evidenceWorkflow.max_model_calls}` : evidenceAttemptId ? "已记录" : "—"}</strong></>}</div>
        </div>

        {(displayedContextEvidenceState !== "empty" || isEvidenceEmpty) && (
          <ContextEvidencePanel
            evidence={displayedContextEvidence}
            state={displayedContextEvidenceState}
            errorMessage={displayedContextEvidenceError}
          />
        )}

        {evidenceWorkflow ? (
          <AgentWorkflowPanel
            workflow={evidenceWorkflow}
            events={evidenceEventState.events}
            nodes={evidenceWorkflowNodes}
            reasoningBlocksByAttemptId={evidenceReasoningBlocks}
            hasEventGap={evidenceEventState.hasGap}
            isCancelling={isCancellingWorkflow}
            onCancel={isInspectingHistoricalRun ? undefined : () => void handleCancelAgentWorkflow()}
          />
        ) : (
          <>
        <section className="inspector-section">
          <div className="section-title"><h3>模型调用</h3>{!isEvidenceEmpty && <span>{evidenceAttemptId ? "已关联" : "等待"}</span>}</div>
          {!isEvidenceEmpty && <dl className="detail-list">
            <div><dt>Provider</dt><dd>{(evidenceModelFacts?.provider ?? (isInspectingHistoricalRun ? "" : selectedProvider)) || "—"}</dd></div>
            <div><dt>Model</dt><dd>{(evidenceModelFacts?.model ?? (isInspectingHistoricalRun ? "" : selectedModel)) || "—"}</dd></div>
            <div><dt>输入 Token</dt><dd>{evidenceUsage?.input ?? "—"}</dd></div>
            <div><dt>输出 Token</dt><dd>{evidenceUsage?.output ?? "—"}</dd></div>
            <div><dt>请求状态</dt><dd className={evidenceRun?.status === "failed" ? "error-text" : "success-text"}>{isInspectingHistoricalRun ? evidenceRun ? runStatusLabel(evidenceRun.status) : "等待" : statusLabel}</dd></div>
          </dl>}
          {(isInspectingHistoricalRun
            ? evidenceAttemptId ? evidenceReasoningBlocks[evidenceAttemptId] ?? [] : []
            : lastAssistantMessage?.reasoningBlocks ?? []).map((presentation) => (
            <ReasoningView
              key={`trajectory-${presentation.block_id}`}
              presentation={presentation}
              mode="trajectory"
            />
          ))}
        </section>

        <section className="inspector-section timeline-section">
          <div className="section-title"><h3>运行时间线</h3>{!isEvidenceEmpty && <span>{evidenceRun ? "服务端事实" : "暂无事件"}</span>}</div>
          <ol className="timeline">
            <li className={evidenceRun ? "complete" : "pending"}><i /><div><strong>请求已接收</strong>{!isEvidenceEmpty && <small>{evidenceRun ? formatTime(evidenceRun.created_at) : "等待"}</small>}</div></li>
            <li className={evidenceRun?.started_at ? "complete" : "pending"}><i /><div><strong>模型生成</strong>{!isEvidenceEmpty && <small>{evidenceRun?.status === "running" ? "服务端处理中" : evidenceRun?.started_at ? "已开始" : "等待"}</small>}</div></li>
            <li className={evidenceUsage ? "complete" : "pending"}><i /><div><strong>用量已确认</strong>{!isEvidenceEmpty && <small>{evidenceUsage ? `${formatTokenTotal(evidenceUsage)} tokens` : "等待"}</small>}</div></li>
            <li className={evidenceRun?.status === "completed" ? "complete" : evidenceRun?.status === "failed" ? "failed" : "pending"}><i /><div><strong>模型响应完成</strong>{!isEvidenceEmpty && <small>{evidenceRun?.status === "completed" ? "已完成" : evidenceRun?.status === "failed" ? "失败" : "等待"}</small>}</div></li>
            <li className={isInspectingHistoricalRun ? evidenceRun?.status === "completed" ? "complete" : "pending" : assistantSaveFailed ? "failed" : assistantSaved ? "complete" : "pending"}><i /><div><strong>{!isInspectingHistoricalRun && assistantSaveFailed ? "响应保存失败" : "响应已保存"}</strong>{!isEvidenceEmpty && <small>{isInspectingHistoricalRun ? evidenceRun?.status === "completed" ? "已完成" : "等待" : assistantSaveFailed ? "需要重试" : assistantSaved ? "已完成" : "等待"}</small>}</div></li>
          </ol>
        </section>

          </>
        )}
        </>}
        {!isInspectingHistoricalRun && errorMessage && <div className="error-banner" role="alert"><strong>需要处理</strong><p>{errorMessage}</p></div>}
      </aside>
    </main>
  );
}
