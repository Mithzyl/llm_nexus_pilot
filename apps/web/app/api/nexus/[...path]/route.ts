import { NextRequest } from "next/server";

const API_BASE_URL = process.env.NEXUSPILOT_API_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.NEXUSPILOT_API_KEY;

type RouteContext = { params: Promise<{ path: string[] }> };

/**
 * Forward approved browser API calls to FastAPI while keeping credentials on the server.
 */
async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  if (!API_KEY) {
    return Response.json(
      { detail: "未配置 NEXUSPILOT_API_KEY，前端服务端转发层不可用。" },
      { status: 500 },
    );
  }

  const { path } = await context.params;
  const upstreamPath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const upstreamUrl = new URL(`/api/v1/${upstreamPath}`, API_BASE_URL);
  upstreamUrl.search = request.nextUrl.search;

  const headers = new Headers({
    Accept: request.headers.get("accept") ?? "application/json",
    "X-API-Key": API_KEY,
  });
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  const body = ["GET", "HEAD"].includes(request.method)
    ? undefined
    : await request.arrayBuffer();

  try {
    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body,
      signal: request.signal,
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    const responseContentType = upstream.headers.get("content-type");
    if (responseContentType) responseHeaders.set("content-type", responseContentType);
    const cacheControl = upstream.headers.get("cache-control");
    if (cacheControl) responseHeaders.set("cache-control", cacheControl);

    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json(
      { detail: "无法连接 NexusPilot API，请确认 FastAPI 已启动。" },
      { status: 503 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
