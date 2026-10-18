import { NextRequest } from "next/server";

import { isAllowedProxyRoute } from "../../../../lib/proxy-policy";

const API_BASE_URL = process.env.NEXUSPILOT_API_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.NEXUSPILOT_API_KEY;
const MAX_REQUEST_BODY_BYTES = 2_000_000;

type RouteContext = { params: Promise<{ path: string[] }> };
type JsonObject = Record<string, unknown>;
type WorkspacePrincipal = {
  user_id: string;
  authentication_method: "browser_session" | "local_deployment";
};


/**
 * Compare the serialized browser Origin with the request's actual Host authority.
 * NextURL rewrites loopback IPs to localhost, so its hostname loses this distinction.
 * The protocol remains the framework's request protocol; forwarded headers are not
 * additional trusted origin sources here. Host-less callers retain the URL check.
 */
function hasSameRequestOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    const originUrl = new URL(origin);
    if (!["http:", "https:"].includes(originUrl.protocol) || origin !== originUrl.origin) {
      return false;
    }
    const host = request.headers.get("host");
    if (host === null) return origin === request.nextUrl.origin;
    // Host is only an authority, never credentials, a path, or a forwarded-host list.
    if (!host || /[\s\\/?#@%,]/u.test(host)) return false;
    const requestOrigin = new URL(`${request.nextUrl.protocol}//${host}`).origin;
    return origin === requestOrigin;
  } catch {
    return false;
  }
}


/** Build the minimum authenticated header set for an upstream request. */
function buildApiHeaders(request: NextRequest): Headers {
  const headers = new Headers({
    Accept: request.headers.get("accept") ?? "application/json",
    "X-API-Key": API_KEY ?? "",
  });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const cookie = request.headers.get("cookie");
  if (cookie) headers.set("cookie", cookie);
  const origin = request.headers.get("origin");
  if (origin) headers.set("origin", origin);
  const authorization = request.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);
  const csrfToken = request.headers.get("x-csrf-token");
  if (csrfToken) headers.set("x-csrf-token", csrfToken);
  return headers;
}

/** Copy the response headers that are safe and necessary across the server proxy. */
function copySafeResponseHeaders(upstream: Response): Headers {
  const responseHeaders = new Headers({ "Cache-Control": "no-store" });
  for (const headerName of ["content-type", "x-accel-buffering", "content-disposition", "x-content-type-options"]) {
    const headerValue = upstream.headers.get(headerName);
    if (headerValue) responseHeaders.set(headerName, headerValue);
  }
  const getSetCookie = (upstream.headers as Headers & { getSetCookie?: () => string[] })
    .getSetCookie;
  const setCookies = getSetCookie
    ? getSetCookie.call(upstream.headers)
    : upstream.headers.get("set-cookie")
      ? [upstream.headers.get("set-cookie") as string]
      : [];
  for (const setCookie of setCookies) responseHeaders.append("set-cookie", setCookie);
  return responseHeaders;
}

/** Copy a non-streaming upstream error while keeping only safe response headers. */
function copyUpstreamResponse(upstream: Response): Promise<Response> {
  const responseHeaders = copySafeResponseHeaders(upstream);
  return upstream.arrayBuffer().then(
    (body) => new Response(body, { status: upstream.status, headers: responseHeaders }),
  );
}

/** Resolve server-selected local/login ownership; invalid or failed lookups never bypass login. */
async function fetchWorkspacePrincipal(
  request: NextRequest,
): Promise<{ principal: WorkspacePrincipal } | { error: Response }> {
  const upstream = await fetch(new URL("/api/v1/auth/me", API_BASE_URL), {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return { error: await copyUpstreamResponse(upstream) };
  try {
    const principal = (await upstream.json()) as Partial<WorkspacePrincipal>;
    if (typeof principal.user_id !== "string" || !principal.user_id ||
        !["browser_session", "local_deployment"].includes(principal.authentication_method ?? "")) {
      return { error: Response.json({ detail: "当前用户响应格式无效。" }, { status: 502 }) };
    }
    return { principal: principal as WorkspacePrincipal };
  } catch {
    return { error: Response.json({ detail: "当前用户响应格式无效。" }, { status: 502 }) };
  }
}

/** Remove platform System Prompt evidence before a Context projection reaches the browser. */
async function projectContextEvidenceResponse(upstream: Response): Promise<Response> {
  if (!upstream.ok) return copyUpstreamResponse(upstream);
  let upstreamPayload: unknown;
  try {
    upstreamPayload = await upstream.json();
  } catch {
    return Response.json({ detail: "上下文证据响应格式无效。" }, { status: 502 });
  }
  if (!upstreamPayload || typeof upstreamPayload !== "object" || Array.isArray(upstreamPayload)) {
    return Response.json({ detail: "上下文证据响应格式无效。" }, { status: 502 });
  }
  const evidence = upstreamPayload as JsonObject;
  const sources = Array.isArray(evidence.sources)
    ? evidence.sources.filter((source) => (
        !source ||
        typeof source !== "object" ||
        (source as JsonObject).source_type !== "prompt_release"
      ))
    : [];
  return Response.json(
    { ...evidence, sources },
    {
      status: upstream.status,
      headers: { "Cache-Control": upstream.headers.get("cache-control") ?? "no-store" },
    },
  );
}

/** Use one neutral not-found response for resources outside the current user. */
function resourceNotFoundResponse(): Response {
  return Response.json({ detail: "资源不存在。" }, { status: 404 });
}

/** Verify that a session belongs to the authenticated browser user. */
async function fetchOwnedSession(
  sessionId: string,
  request: NextRequest,
  currentUserId: string,
): Promise<Response | null> {
  const upstreamUrl = new URL(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, API_BASE_URL);
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return copyUpstreamResponse(upstream);

  const session = (await upstream.json()) as { user_id?: string };
  return session.user_id === currentUserId ? null : resourceNotFoundResponse();
}

/** Verify that a run belongs to the authenticated browser user. */
async function fetchOwnedRun(
  runId: string,
  request: NextRequest,
  currentUserId: string,
): Promise<Response | null> {
  const upstreamUrl = new URL(`/api/v1/runs/${encodeURIComponent(runId)}`, API_BASE_URL);
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: buildApiHeaders(request),
    signal: request.signal,
    cache: "no-store",
  });
  if (!upstream.ok) return copyUpstreamResponse(upstream);

  const run = (await upstream.json()) as { user_id?: string };
  return run.user_id === currentUserId ? null : resourceNotFoundResponse();
}

/** Resolve a Workflow's Run and enforce the current authenticated user's boundary. */
async function fetchOwnedAgentWorkflow(
  workflowExecutionId: string,
  request: NextRequest,
  currentUserId: string,
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
  return fetchOwnedRun(workflow.run_id, request, currentUserId);
}

/** Resolve a model invocation's Run and enforce the authenticated user's boundary. */
async function fetchOwnedModelAttempt(
  attemptId: string,
  request: NextRequest,
  currentUserId: string,
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
  return fetchOwnedRun(attempt.run_id, request, currentUserId);
}

/** Resolve a message's session and verify that both objects share the owner. */
async function fetchOwnedMessage(
  messageId: string,
  request: NextRequest,
  currentUserId: string,
): Promise<Response | null> {
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
  return fetchOwnedSession(message.session_id, request, currentUserId);
}

/**
 * Parse and bound JSON bodies before applying the authenticated-user ownership
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
  currentUserId: string,
): Promise<Response | null> {
  if (path[0] === "sessions" && path.length >= 2) {
    const sessionOwnershipError = await fetchOwnedSession(path[1], request, currentUserId);
    if (sessionOwnershipError) return sessionOwnershipError;
  }

  if (path[0] === "messages" && path.length === 2) {
    const messageOwnershipError = await fetchOwnedMessage(path[1], request, currentUserId);
    if (messageOwnershipError) return messageOwnershipError;
  }

  if (path[0] === "runs" && path.length >= 2) {
    const runOwnershipError = await fetchOwnedRun(path[1], request, currentUserId);
    if (runOwnershipError) return runOwnershipError;
  }

  if (path[0] === "agent-workflows" && path.length >= 2) {
    const workflowOwnershipError = await fetchOwnedAgentWorkflow(path[1], request, currentUserId);
    if (workflowOwnershipError) return workflowOwnershipError;
  }

  if (path[0] === "attempts" && path.length >= 2) {
    const attemptOwnershipError = await fetchOwnedModelAttempt(path[1], request, currentUserId);
    if (attemptOwnershipError) return attemptOwnershipError;
  }

  const referencedRunId = typeof payload?.run_id === "string" ? payload.run_id : undefined;
  if (referencedRunId) {
    const runOwnershipError = await fetchOwnedRun(referencedRunId, request, currentUserId);
    if (runOwnershipError) return runOwnershipError;
  }

  const referencedSessionId = typeof payload?.session_id === "string" ? payload.session_id : undefined;
  if (referencedSessionId) {
    const sessionOwnershipError = await fetchOwnedSession(
      referencedSessionId,
      request,
      currentUserId,
    );
    if (sessionOwnershipError) return sessionOwnershipError;
  }

  return null;
}

/** Determine whether an allow-listed mutation carries JSON; Run/Workflow cancellation is bodyless. */
function requiresJsonRequestBody(method: string, path: readonly string[]): boolean {
  if (["GET", "HEAD", "DELETE"].includes(method)) return false;
  if (method === "POST" && path[0] === "auth" && ["logout", "revoke-all"].includes(path.at(-1) ?? "")) {
    return false;
  }
  return !(
    method === "POST"
    && path.length === 3
    && ["agent-workflows", "runs"].includes(path[0])
    && path[2] === "cancel"
  );
}

/** Accept only a bounded title update so the Web proxy cannot expose Session lifecycle changes. */
function normalizeSessionTitlePatch(
  payload: JsonObject,
): { payload: JsonObject; body: string } | { error: Response } {
  const fields = Object.keys(payload);
  const title = typeof payload.title === "string" ? payload.title.trim() : "";
  if (fields.length !== 1 || fields[0] !== "title") {
    return {
      error: Response.json(
        { detail: "当前前端只允许更新会话标题。" },
        { status: 400 },
      ),
    };
  }
  if (!title || title.length > 255) {
    return {
      error: Response.json(
        { detail: "会话标题必须为 1 至 255 个字符。" },
        { status: 400 },
      ),
    };
  }
  const normalizedPayload: JsonObject = { title };
  return { payload: normalizedPayload, body: JSON.stringify(normalizedPayload) };
}

/** Reject browser attempts to select a platform System Prompt through the Responses proxy. */
function browserSystemPromptReferenceError(
  method: string,
  path: readonly string[],
  payload: JsonObject | undefined,
): Response | null {
  if (method !== "POST" || path.length !== 1 || path[0] !== "responses") return null;
  const contextReferences = payload?.context_source_references;
  if (
    !contextReferences ||
    typeof contextReferences !== "object" ||
    Array.isArray(contextReferences) ||
    !("prompt" in contextReferences)
  ) {
    return null;
  }
  return Response.json(
    { detail: "当前前端不能选择或覆盖平台系统指令。" },
    { status: 400 },
  );
}

/**
 * Forward only the UI's explicitly allow-listed resources and force every
 * browser request into the authenticated user's ownership boundary.
 * Raw file uploads admit up to 20 MiB; JSON and ownership checks remain separate.
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

  const isAuthenticationRoute = path[0] === "auth";
  if (!["GET", "HEAD"].includes(request.method)) {
    if (!hasSameRequestOrigin(request)) {
      return Response.json({ detail: "请求来源未获授权。" }, { status: 403 });
    }
    if (isAuthenticationRoute && !["login", "logout"].includes(path[1])) {
      const csrfCookie = request.cookies.get("nexuspilot_csrf")?.value;
      if (!csrfCookie || request.headers.get("x-csrf-token") !== csrfCookie) {
        return Response.json({ detail: "跨站请求伪造校验失败。" }, { status: 403 });
      }
    }
  }
  let currentUserId: string | null = null;
  if (!isAuthenticationRoute) {
    try {
      const principalResult = await fetchWorkspacePrincipal(request);
      if ("error" in principalResult) return principalResult.error;
      currentUserId = principalResult.principal.user_id;
      if (!["GET", "HEAD"].includes(request.method) &&
          principalResult.principal.authentication_method === "browser_session") {
        const csrfCookie = request.cookies.get("nexuspilot_csrf")?.value;
        if (!csrfCookie || request.headers.get("x-csrf-token") !== csrfCookie) {
          return Response.json({ detail: "跨站请求伪造校验失败。" }, { status: 403 });
        }
      }
    } catch {
      return Response.json({ detail: "身份服务暂不可用，请稍后重试。" }, { status: 503 });
    }
  }

  const isFileUpload = request.method === "PUT" && path[0] === "files" && path[2] === "content";
  const isBodyRequest = !isFileUpload && requiresJsonRequestBody(request.method, path);
  let body: string | Uint8Array<ArrayBuffer> | undefined;
  let payload: JsonObject | undefined;
  if (isFileUpload) {
    const reader = request.body?.getReader();
    if (!reader) return Response.json({ detail: "文件内容为空。" }, { status: 422 });
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 20 * 1024 * 1024) {
          await reader.cancel();
          return Response.json({ detail: "单文件不能超过 20 MiB。" }, { status: 413 });
        }
        chunks.push(value);
      }
      body = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    } catch {
      return Response.json({ detail: "文件传输中断，请重试。" }, { status: 400 });
    } finally { reader.releaseLock(); }
  }
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

  if (
    path[0] === "sessions" &&
    path.length === 2 &&
    request.method === "PATCH" &&
    payload
  ) {
    const normalizedTitlePatch = normalizeSessionTitlePatch(payload);
    if ("error" in normalizedTitlePatch) return normalizedTitlePatch.error;
    payload = normalizedTitlePatch.payload;
    body = normalizedTitlePatch.body;
  }

  const systemPromptReferenceError = browserSystemPromptReferenceError(
    request.method,
    path,
    payload,
  );
  if (systemPromptReferenceError) return systemPromptReferenceError;

  if (path[0] === "sessions" && path.length === 1 && request.method === "POST") {
    payload = { ...payload, user_id: currentUserId };
    body = JSON.stringify(payload);
  }

  if (
    path[0] === "sessions" &&
    path.length === 3 &&
    path[2] === "turns" &&
    request.method === "POST" &&
    payload?.user_id !== undefined
    && payload.user_id !== currentUserId
  ) {
    return Response.json({ detail: "对话轮次只能归属于当前登录用户。" }, { status: 403 });
  }

  if (
    path[0] === "runs" &&
    path.length === 1 &&
    request.method === "POST" &&
    payload?.user_id !== undefined
    && payload.user_id !== currentUserId
  ) {
    return Response.json({ detail: "运行只能归属于当前登录用户。" }, { status: 403 });
  }

  try {
    if (!isAuthenticationRoute && currentUserId) {
      if (
        path[0] === "sessions"
        && path.length === 3
        && path[2] === "turns"
        && request.method === "POST"
      ) {
        payload = { ...payload, user_id: currentUserId };
        body = JSON.stringify(payload);
      }
      if (path[0] === "runs" && path.length === 1 && request.method === "POST") {
        payload = { ...payload, user_id: currentUserId };
        body = JSON.stringify(payload);
      }
    }
    const ownershipError = currentUserId
      ? await validateOwnership(request, path, payload, currentUserId)
      : null;
    if (ownershipError) return ownershipError;

    const upstreamPath = path.map((segment) => encodeURIComponent(segment)).join("/");
    const upstreamUrl = new URL(`/api/v1/${upstreamPath}`, API_BASE_URL);
    upstreamUrl.search = request.nextUrl.search;
    if (path.length === 1 && path[0] === "sessions" && request.method === "GET") {
      upstreamUrl.searchParams.set("user_id", currentUserId as string);
    }
    if (
      path.length === 3 &&
      path[0] === "context-builds" &&
      path[2] === "evidence" &&
      request.method === "GET"
    ) {
      upstreamUrl.searchParams.set("user_id", currentUserId as string);
    }

    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: buildApiHeaders(request),
      body,
      signal: request.signal,
      cache: "no-store",
    });

    if (
      path.length === 3 &&
      path[0] === "context-builds" &&
      path[2] === "evidence" &&
      request.method === "GET"
    ) {
      return projectContextEvidenceResponse(upstream);
    }

    const responseHeaders = copySafeResponseHeaders(upstream);

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
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
