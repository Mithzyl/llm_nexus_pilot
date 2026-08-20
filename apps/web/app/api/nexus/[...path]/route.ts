import { NextRequest } from "next/server";

import { isAllowedProxyRoute } from "../../../../lib/proxy-policy";

const API_BASE_URL = process.env.NEXUSPILOT_API_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.NEXUSPILOT_API_KEY;
const DEVELOPMENT_USER_ID = "nexuspilot-web";
const MAX_REQUEST_BODY_BYTES = 2_000_000;

type RouteContext = { params: Promise<{ path: string[] }> };
type JsonObject = Record<string, unknown>;

/** Build the minimum authenticated header set for an upstream request. */
function buildApiHeaders(request: NextRequest): Headers {
  const headers = new Headers({
    Accept: request.headers.get("accept") ?? "application/json",
    "X-API-Key": API_KEY ?? "",
  });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  return headers;
}

/** Copy a non-streaming upstream error while keeping only safe response headers. */
function copyUpstreamResponse(upstream: Response): Promise<Response> {
  const responseHeaders = new Headers();
  for (const headerName of ["content-type", "cache-control", "x-accel-buffering"]) {
    const headerValue = upstream.headers.get(headerName);
    if (headerValue) responseHeaders.set(headerName, headerValue);
  }
  return upstream.arrayBuffer().then(
    (body) => new Response(body, { status: upstream.status, headers: responseHeaders }),
  );
}

/** Use one neutral not-found response for resources outside the development user. */
function resourceNotFoundResponse(): Response {
  return Response.json({ detail: "资源不存在。" }, { status: 404 });
}

/** Verify that a session belongs to the fixed first-version development user. */
async function fetchOwnedSession(sessionId: string, request: NextRequest): Promise<Response | null> {
  const upstreamUrl = new URL(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, API_BASE_URL);
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return copyUpstreamResponse(upstream);

  const session = (await upstream.json()) as { user_id?: string };
  return session.user_id === DEVELOPMENT_USER_ID ? null : resourceNotFoundResponse();
}

/** Verify that a run belongs to the fixed first-version development user. */
async function fetchOwnedRun(runId: string, request: NextRequest): Promise<Response | null> {
  const upstreamUrl = new URL(`/api/v1/runs/${encodeURIComponent(runId)}`, API_BASE_URL);
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return copyUpstreamResponse(upstream);

  const run = (await upstream.json()) as { user_id?: string };
  return run.user_id === DEVELOPMENT_USER_ID ? null : resourceNotFoundResponse();
}

/** Resolve a Workflow's Run and enforce the same fixed development-user boundary. */
async function fetchOwnedAgentWorkflow(
  workflowExecutionId: string,
  request: NextRequest,
): Promise<Response | null> {
  const upstreamUrl = new URL(
    `/api/v1/agent-workflows/${encodeURIComponent(workflowExecutionId)}`,
    API_BASE_URL,
  );
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return copyUpstreamResponse(upstream);

  const workflow = (await upstream.json()) as { run_id?: string };
  if (!workflow.run_id) return resourceNotFoundResponse();
  return fetchOwnedRun(workflow.run_id, request);
}

/** Resolve a model invocation's Run and enforce the development-user boundary. */
async function fetchOwnedModelAttempt(
  attemptId: string,
  request: NextRequest,
): Promise<Response | null> {
  const upstreamUrl = new URL(
    `/api/v1/attempts/${encodeURIComponent(attemptId)}`,
    API_BASE_URL,
  );
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return copyUpstreamResponse(upstream);

  const attempt = (await upstream.json()) as { run_id?: string };
  if (!attempt.run_id) return resourceNotFoundResponse();
  return fetchOwnedRun(attempt.run_id, request);
}

/** Resolve a message's session and verify that both objects share the owner. */
async function fetchOwnedMessage(messageId: string, request: NextRequest): Promise<Response | null> {
  const upstreamUrl = new URL(`/api/v1/messages/${encodeURIComponent(messageId)}`, API_BASE_URL);
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return copyUpstreamResponse(upstream);

  const message = (await upstream.json()) as { session_id?: string };
  if (!message.session_id) return resourceNotFoundResponse();
  return fetchOwnedSession(message.session_id, request);
}

/**
 * Parse and bound JSON bodies before applying the development-user ownership
 * policy. Invalid or oversized bodies never reach the upstream service.
 */
async function readJsonBody(
  request: NextRequest,
): Promise<{ body: string; payload: JsonObject } | { error: Response }> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    const parsedLength = Number(declaredLength);
    if (!Number.isFinite(parsedLength) || parsedLength < 0) {
      return { error: Response.json({ detail: "请求体长度无效。" }, { status: 400 }) };
    }
    if (parsedLength > MAX_REQUEST_BODY_BYTES) {
      return { error: Response.json({ detail: "请求体过大。" }, { status: 413 }) };
    }
  }

  if (!request.body) {
    return { error: Response.json({ detail: "请求体为空。" }, { status: 400 }) };
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    totalBytes += value.byteLength;
    if (totalBytes > MAX_REQUEST_BODY_BYTES) {
      await reader.cancel("request body limit exceeded");
      return { error: Response.json({ detail: "请求体过大。" }, { status: 413 }) };
    }
    chunks.push(value);
  }

  const bodyBytes = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bodyBytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    const body = new TextDecoder().decode(bodyBytes);
    const payload = JSON.parse(body) as unknown;
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return { error: Response.json({ detail: "请求体必须是 JSON 对象。" }, { status: 400 }) };
    }
    return { body, payload: payload as JsonObject };
  } catch {
    return { error: Response.json({ detail: "请求体不是有效 JSON。" }, { status: 400 }) };
  }
}

/**
 * Check every path or body reference before forwarding a browser request.
 */
async function validateOwnership(
  request: NextRequest,
  path: string[],
  payload: JsonObject | undefined,
): Promise<Response | null> {
  if (path[0] === "users" && path.length === 2 && path[1] !== DEVELOPMENT_USER_ID) {
    return resourceNotFoundResponse();
  }

  if (path[0] === "sessions" && path.length >= 2) {
    const sessionOwnershipError = await fetchOwnedSession(path[1], request);
    if (sessionOwnershipError) return sessionOwnershipError;
  }

  if (path[0] === "messages" && path.length === 2) {
    const messageOwnershipError = await fetchOwnedMessage(path[1], request);
    if (messageOwnershipError) return messageOwnershipError;
  }

  if (path[0] === "runs" && path.length >= 2) {
    const runOwnershipError = await fetchOwnedRun(path[1], request);
    if (runOwnershipError) return runOwnershipError;
  }

  if (path[0] === "agent-workflows" && path.length >= 2) {
    const workflowOwnershipError = await fetchOwnedAgentWorkflow(path[1], request);
    if (workflowOwnershipError) return workflowOwnershipError;
  }

  if (path[0] === "attempts" && path.length >= 2) {
    const attemptOwnershipError = await fetchOwnedModelAttempt(path[1], request);
    if (attemptOwnershipError) return attemptOwnershipError;
  }

  const referencedRunId = typeof payload?.run_id === "string" ? payload.run_id : undefined;
  if (referencedRunId) {
    const runOwnershipError = await fetchOwnedRun(referencedRunId, request);
    if (runOwnershipError) return runOwnershipError;
  }

  const referencedSessionId = typeof payload?.session_id === "string" ? payload.session_id : undefined;
  if (referencedSessionId) {
    const sessionOwnershipError = await fetchOwnedSession(referencedSessionId, request);
    if (sessionOwnershipError) return sessionOwnershipError;
  }

  return null;
}

/** Determine whether an allow-listed mutation carries JSON; Workflow cancellation is bodyless. */
function requiresJsonRequestBody(method: string, path: readonly string[]): boolean {
  if (["GET", "HEAD"].includes(method)) return false;
  return !(method === "POST" && path.length === 3 && path[0] === "agent-workflows" && path[2] === "cancel");
}

/**
 * Forward only the UI's explicitly allow-listed resources and force every
 * browser request into the current development user's ownership boundary.
 * Multi-user deployments must replace this identity with authenticated user
 * and resource authorization before exposing the route publicly.
 */
async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  if (!API_KEY) {
    return Response.json(
      { detail: "未配置 NEXUSPILOT_API_KEY，前端服务端转发层不可用。" },
      { status: 500 },
    );
  }

  const { path } = await context.params;
  if (!isAllowedProxyRoute(request.method, path)) {
    return Response.json({ detail: "该资源或请求方法未开放给前端。" }, { status: 404 });
  }

  const isBodyRequest = requiresJsonRequestBody(request.method, path);
  let body: string | undefined;
  let payload: JsonObject | undefined;
  if (isBodyRequest) {
    let parsedBody: Awaited<ReturnType<typeof readJsonBody>>;
    try {
      parsedBody = await readJsonBody(request);
    } catch {
      return Response.json({ detail: "无法读取请求体。" }, { status: 400 });
    }
    if ("error" in parsedBody) return parsedBody.error;
    body = parsedBody.body;
    payload = parsedBody.payload;
  }

  if (path[0] === "users" && request.method === "POST" && payload?.user_id !== DEVELOPMENT_USER_ID) {
    return Response.json({ detail: "只能创建当前开发用户。" }, { status: 403 });
  }

  if (path[0] === "sessions" && path.length === 1 && request.method === "POST") {
    payload = { ...payload, user_id: DEVELOPMENT_USER_ID };
    body = JSON.stringify(payload);
  }

  if (
    path[0] === "runs" &&
    path.length === 1 &&
    request.method === "POST" &&
    payload?.user_id !== DEVELOPMENT_USER_ID
  ) {
    return Response.json({ detail: "运行只能归属于当前开发用户。" }, { status: 403 });
  }

  try {
    const ownershipError = await validateOwnership(request, path, payload);
    if (ownershipError) return ownershipError;

    const upstreamPath = path.map((segment) => encodeURIComponent(segment)).join("/");
    const upstreamUrl = new URL(`/api/v1/${upstreamPath}`, API_BASE_URL);
    upstreamUrl.search = request.nextUrl.search;
    if (path.length === 1 && path[0] === "sessions" && request.method === "GET") {
      upstreamUrl.searchParams.set("user_id", DEVELOPMENT_USER_ID);
    }

    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: buildApiHeaders(request),
      body,
      signal: request.signal,
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    for (const headerName of ["content-type", "cache-control", "x-accel-buffering"]) {
      const headerValue = upstream.headers.get(headerName);
      if (headerValue) responseHeaders.set(headerName, headerValue);
    }

    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    if (request.signal.aborted) return new Response(null, { status: 499 });
    return Response.json(
      { detail: "无法连接 NexusPilot API，请确认 FastAPI 已启动。" },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
