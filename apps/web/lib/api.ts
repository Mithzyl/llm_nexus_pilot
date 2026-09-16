import type {
  AgentWorkflowCreate,
  AgentWorkflowNodeResult,
  AgentWorkflowResult,
  AgentWorkflowSummary,
  WorkspacePrincipal,
  CursorPage,
  ContextBuildEvidence,
  ConversationTurn,
  Message,
  ModelDirectoryEntry,
  ModelResponseEventPage,
  ProviderCatalog,
  ReasoningBlockSnapshot,
  Run,
  RunDetail,
  Session,
} from "./types";

export const MESSAGE_PAGE_SIZE = 100;

/** Read only the public CSRF cookie to prove a browser script initiated a mutation. */
export function browserCsrfHeaders(): Record<string, string> {
  if (typeof document === "undefined") return {};
  const csrfCookie = document.cookie.split(";").map((entry) => entry.trim())
    .find((entry) => entry.startsWith("nexuspilot_csrf="));
  return csrfCookie ? { "X-CSRF-Token": csrfCookie.slice("nexuspilot_csrf=".length) } : {};
}

export class NexusApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "NexusApiError";
    this.status = status;
  }
}

/**
 * Call the same-origin server proxy so the browser never receives the API key.
 */
export async function nexusFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/nexus/${path.replace(/^\//, "")}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...browserCsrfHeaders(),
      ...init?.headers,
    },
    cache: "no-store",
    credentials: "same-origin",
  });

  if (!response.ok) {
    let message = `NexusPilot API 请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string; error?: { message?: string } };
      message = body.detail ?? body.error?.message ?? message;
    } catch {
      // Keep the stable status message when the upstream body is not JSON.
    }
    throw new NexusApiError(message, response.status);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Authenticate a provisioned account and establish server-managed browser cookies. */
export function loginWithPassword(payload: {
  email: string;
  password: string;
  device_name?: string;
}): Promise<WorkspacePrincipal> {
  return nexusFetch<WorkspacePrincipal>("auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Resolve the backend-selected local owner or authenticated browser identity. */
export function getCurrentWorkspacePrincipal(): Promise<WorkspacePrincipal> {
  return nexusFetch<WorkspacePrincipal>("auth/me");
}

/** Revoke the current browser session and expire both authentication cookies. */
export function logoutCurrentUser(): Promise<void> {
  return nexusFetch<void>("auth/logout", { method: "POST" });
}

/**
 * Load the first bounded page of conversations for the sidebar.
 */
export function listSessions(): Promise<CursorPage<Session>> {
  return nexusFetch<CursorPage<Session>>("sessions?limit=50");
}

/**
 * Read provider names reported by the server-side registry.
 */
export function listProviders(signal?: AbortSignal): Promise<ProviderCatalog> {
  return nexusFetch<ProviderCatalog>("providers", { signal });
}

/** Load the stable model directory, optionally restricted to the conversation streaming contract. */
export function listModels(
  signal?: AbortSignal,
  requiresStreaming = true,
): Promise<ModelDirectoryEntry[]> {
  const query = requiresStreaming ? "?requires_streaming=true" : "";
  return nexusFetch<ModelDirectoryEntry[]>(`models${query}`, { signal });
}

/** Read the content-free Context evidence projection for the authenticated user. */
export function getContextBuildEvidence(
  contextBuildId: string,
  signal?: AbortSignal,
): Promise<ContextBuildEvidence> {
  return nexusFetch<ContextBuildEvidence>(
    `context-builds/${encodeURIComponent(contextBuildId)}/evidence`,
    { signal },
  );
}

/**
 * Create a new active session owned by the authenticated identity.
 */
export function createSession(title: string): Promise<Session> {
  return nexusFetch<Session>("sessions", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

/** Rename one owned active conversation without changing its lifecycle status. */
export function updateSessionTitle(sessionId: string, title: string): Promise<Session> {
  return nexusFetch<Session>(`sessions/${encodeURIComponent(sessionId)}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

/**
 * Load one bounded newest-message page with complete bodies in a single
 * upstream request. The cursor returned by the API points to older rows.
 */
export function listLatestMessagePage(
  sessionId: string,
  cursor?: string | null,
): Promise<CursorPage<Message>> {
  const cursorQuery = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
  return nexusFetch<CursorPage<Message>>(
    `sessions/${encodeURIComponent(sessionId)}/messages/latest?limit=${MESSAGE_PAGE_SIZE}${cursorQuery}`,
  );
}

/**
 * Load one complete message page in sequence order for callers that need to
 * replay the history from its beginning.
 */
export function listMessagePage(
  sessionId: string,
  cursor?: string | null,
): Promise<CursorPage<Message>> {
  const cursorQuery = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
  return nexusFetch<CursorPage<Message>>(
    `sessions/${encodeURIComponent(sessionId)}/messages/full?limit=${MESSAGE_PAGE_SIZE}${cursorQuery}`,
  );
}

/**
 * Restore the newest run detail, including the latest bounded attempt facts.
 */
export function getLatestRun(sessionId: string): Promise<RunDetail> {
  return nexusFetch<RunDetail>(`sessions/${encodeURIComponent(sessionId)}/latest-run`);
}

/**
 * Load a persisted run and its bounded model-attempt snapshot for the inspector.
 */
export function getRun(runId: string): Promise<RunDetail> {
  return nexusFetch<RunDetail>(`runs/${encodeURIComponent(runId)}`);
}

/**
 * Create an auditable run before the model request is sent.
 */
export function createRun(payload: {
  session_id: string;
  user_request: string;
}): Promise<Run> {
  return nexusFetch<Run>("runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Persist a keyed turn, retrying transport/5xx uncertainty once with identical bytes.
 * This retries only the database submission, never a Provider or workflow execution.
 * The key lasts for this call unless supplied by a caller; cross-reload retry is separate.
 */
export async function createConversationTurn(
  sessionId: string,
  payload: { content_text: string; idempotency_key?: string; file_ids?: string[] },
): Promise<ConversationTurn> {
  const path = `sessions/${encodeURIComponent(sessionId)}/turns`;
  const init = {
    method: "POST",
    body: JSON.stringify({ ...payload, idempotency_key: payload.idempotency_key ?? crypto.randomUUID() }),
  };
  try {
    return await nexusFetch<ConversationTurn>(path, init);
  } catch (error) {
    const retryable = error instanceof NexusApiError
      ? error.status >= 500
      : error instanceof TypeError || error instanceof SyntaxError;
    if (!retryable) throw error;
    return nexusFetch<ConversationTurn>(path, init);
  }
}

/**
 * Append one immutable message to a persisted conversation.
 */
export function createMessage(
  sessionId: string,
  payload: {
    role: "user" | "assistant";
    content_text: string;
    run_id?: string;
    source_model_attempt_id?: string;
    metadata_json?: Record<string, unknown>;
  },
): Promise<Message> {
  return nexusFetch<Message>(`sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content_type: "text", ...payload }),
  });
}

/** Read authoritative public reasoning blocks for one owned model invocation. */
export function getReasoningBlocks(attemptId: string): Promise<ReasoningBlockSnapshot[]> {
  return nexusFetch<ReasoningBlockSnapshot[]>(
    `attempts/${encodeURIComponent(attemptId)}/reasoning-blocks`,
  );
}

/** Replay a bounded page of committed response events after one acknowledged sequence. */
export function replayModelResponseEvents(
  attemptId: string,
  afterSequence: number,
): Promise<ModelResponseEventPage> {
  return nexusFetch<ModelResponseEventPage>(
    `attempts/${encodeURIComponent(attemptId)}/events?after_sequence=${afterSequence}&limit=100`,
  );
}

/** Convert a failed raw stream response into the same safe API error used by JSON calls. */
async function requireSuccessfulStreamResponse(response: Response): Promise<Response> {
  if (response.ok) return response;
  let message = `NexusPilot API 请求失败（${response.status}）`;
  try {
    const body = (await response.json()) as { detail?: string; error?: { message?: string } };
    message = body.detail ?? body.error?.message ?? message;
  } catch {
    // Preserve the stable status message when an upstream stream error is not JSON.
  }
  throw new NexusApiError(message, response.status);
}

/** Start or idempotently replay one model-only Agent Workflow as a POST SSE stream. */
export async function openAgentWorkflowStream(
  runId: string,
  payload: AgentWorkflowCreate,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(
    `/api/nexus/runs/${encodeURIComponent(runId)}/agent-workflows`,
    {
      method: "POST",
      headers: { Accept: "text/event-stream", "Content-Type": "application/json", ...browserCsrfHeaders() },
      body: JSON.stringify({ ...payload, stream: true }),
      signal,
      cache: "no-store",
    },
  );
  return requireSuccessfulStreamResponse(response);
}

/** Discover the single Agent Workflow associated with one owned Run. */
export function getRunAgentWorkflow(runId: string): Promise<AgentWorkflowSummary> {
  return nexusFetch<AgentWorkflowSummary>(
    `runs/${encodeURIComponent(runId)}/agent-workflow`,
  );
}

/** Read the current summary and server versions for one Agent Workflow. */
export function getAgentWorkflowSummary(
  workflowExecutionId: string,
): Promise<AgentWorkflowSummary> {
  return nexusFetch<AgentWorkflowSummary>(
    `agent-workflows/${encodeURIComponent(workflowExecutionId)}`,
  );
}

/** Durably cancel future nodes for one owned running Agent Workflow. */
export function cancelAgentWorkflow(
  workflowExecutionId: string,
): Promise<AgentWorkflowSummary> {
  return nexusFetch<AgentWorkflowSummary>(
    `agent-workflows/${encodeURIComponent(workflowExecutionId)}/cancel`,
    { method: "POST" },
  );
}

/** Read the authoritative bounded result snapshot for one Agent Workflow. */
export function getAgentWorkflowResult(
  workflowExecutionId: string,
): Promise<AgentWorkflowResult> {
  return nexusFetch<AgentWorkflowResult>(
    `agent-workflows/${encodeURIComponent(workflowExecutionId)}/result`,
  );
}

/** Page complete workflow nodes in durable node-sequence order. */
export function listAgentWorkflowNodes(
  workflowExecutionId: string,
  cursor?: string | null,
): Promise<CursorPage<AgentWorkflowNodeResult>> {
  const cursorQuery = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
  return nexusFetch<CursorPage<AgentWorkflowNodeResult>>(
    `agent-workflows/${encodeURIComponent(workflowExecutionId)}/nodes?limit=50${cursorQuery}`,
  );
}

/** Read one complete node only within its owning Agent Workflow. */
export function getAgentWorkflowNode(
  workflowExecutionId: string,
  nodeExecutionId: string,
): Promise<AgentWorkflowNodeResult> {
  return nexusFetch<AgentWorkflowNodeResult>(
    `agent-workflows/${encodeURIComponent(workflowExecutionId)}/nodes/${encodeURIComponent(nodeExecutionId)}`,
  );
}

/** Replay only already-committed Agent Workflow events after one acknowledged sequence. */
export async function replayAgentWorkflowEvents(
  workflowExecutionId: string,
  afterSequence: number,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(
    `/api/nexus/agent-workflows/${encodeURIComponent(workflowExecutionId)}/events?after_sequence=${afterSequence}`,
    { headers: { Accept: "text/event-stream" }, signal, cache: "no-store" },
  );
  return requireSuccessfulStreamResponse(response);
}
