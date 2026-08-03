import type {
  CursorPage,
  MessageSummary,
  Run,
  RunDetail,
  Session,
} from "./types";

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
      ...init?.headers,
    },
    cache: "no-store",
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

  return (await response.json()) as T;
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
export function listProviders(): Promise<{ providers: string[] }> {
  return nexusFetch<{ providers: string[] }>("providers");
}

/**
 * Ensure a development identity exists without exposing credentials to the browser.
 */
export function ensureDevelopmentUser(userId: string): Promise<unknown> {
  return nexusFetch<unknown>("users", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, display_name: "NexusPilot 开发用户" }),
  });
}

/**
 * Create a new active session owned by the configured development identity.
 */
export function createSession(userId: string, title: string): Promise<Session> {
  return nexusFetch<Session>("sessions", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, title }),
  });
}

/**
 * Load immutable message summaries in sequence order for one conversation.
 */
export function listMessages(sessionId: string): Promise<CursorPage<MessageSummary>> {
  return nexusFetch<CursorPage<MessageSummary>>(
    `sessions/${encodeURIComponent(sessionId)}/messages?limit=100`,
  );
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
  user_id: string;
  session_id: string;
  user_request: string;
}): Promise<Run> {
  return nexusFetch<Run>("runs", {
    method: "POST",
    body: JSON.stringify(payload),
  });
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
  },
): Promise<MessageSummary> {
  return nexusFetch<MessageSummary>(`sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ content_type: "text", ...payload }),
  });
}
