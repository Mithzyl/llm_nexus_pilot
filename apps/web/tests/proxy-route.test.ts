import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server";

process.env.NEXUSPILOT_API_KEY = "route-test-key";
process.env.NEXUSPILOT_API_URL = "https://nexus.test";

let GET: typeof import("../app/api/nexus/[...path]/route").GET;
let POST: typeof import("../app/api/nexus/[...path]/route").POST;
let PATCH: typeof import("../app/api/nexus/[...path]/route").PATCH;
let DELETE: typeof import("../app/api/nexus/[...path]/route").DELETE;
const originalFetch = globalThis.fetch;

test.before(async () => {
  const route = await import("../app/api/nexus/[...path]/route");
  GET = route.GET;
  POST = route.POST;
  PATCH = route.PATCH;
  DELETE = route.DELETE;
});

/** Build a same-origin request for one proxy path. */
function createRequest(
  path: string,
  init?: ConstructorParameters<typeof NextRequest>[1],
): InstanceType<typeof NextRequest> {
  const configuredFetch = globalThis.fetch as typeof fetch & { authenticationWrapped?: boolean };
  if (!configuredFetch.authenticationWrapped) {
    const authenticatedFetch = (async (input: RequestInfo | URL, fetchInit?: RequestInit) => {
      if (String(input).endsWith("/api/v1/auth/me")) {
        return Response.json({ user_id: "nexuspilot-web", authentication_method: "browser_session" });
      }
      return configuredFetch(input, fetchInit);
    }) as typeof fetch & { authenticationWrapped?: boolean };
    authenticatedFetch.authenticationWrapped = true;
    globalThis.fetch = authenticatedFetch;
  }
  const headers = new Headers(init?.headers);
  if (init?.method && init.method !== "GET") {
    headers.set("origin", "http://localhost");
    if (!headers.has("cookie")) headers.set("cookie", "nexuspilot_csrf=test-csrf");
    headers.set("x-csrf-token", headers.get("cookie")?.includes("csrf-value") ? "csrf-value" : "test-csrf");
  }
  return new NextRequest(`http://localhost/api/nexus/${path}`, { ...init, headers });
}

/** Build the catch-all route context expected by the Next.js handler. */
function createContext(path: string) {
  return { params: Promise.resolve({ path: path.split("/") }) };
}

test("forwards login cookies and does not require a pre-existing user session", async () => {
  const upstreamCalls: Array<{ url: string; cookie: string | null }> = [];
  globalThis.fetch = (async (input, init) => {
    const headers = new Headers(init?.headers);
    upstreamCalls.push({ url: String(input), cookie: headers.get("cookie") });
    return new Response(JSON.stringify({ user_id: "authenticated-user" }), {
      status: 200,
      headers: [
        ["content-type", "application/json"],
        ["set-cookie", "nexuspilot_session=opaque; Path=/; HttpOnly; SameSite=Lax"],
        ["set-cookie", "nexuspilot_csrf=csrf; Path=/; SameSite=Lax"],
      ],
    });
  }) as typeof fetch;

  try {
    const response = await POST(
      createRequest("auth/login", {
        method: "POST",
        headers: { "content-type": "application/json", origin: "http://localhost" },
        body: JSON.stringify({ email: "user@example.com", password: "strong-password" }),
      }),
      createContext("auth/login"),
    );

    assert.equal(response.status, 200);
    assert.deepEqual(upstreamCalls, [
      { url: "https://nexus.test/api/v1/auth/login", cookie: "nexuspilot_csrf=test-csrf" },
    ]);
    const setCookies = (response.headers as Headers & { getSetCookie?: () => string[] })
      .getSetCookie?.() ?? [response.headers.get("set-cookie") ?? ""];
    assert.equal(setCookies.some((value) => value.includes("nexuspilot_session=opaque")), true);
    assert.equal(setCookies.some((value) => value.includes("nexuspilot_csrf=csrf")), true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("stops a business request when the browser session is unauthenticated", async () => {
  const upstreamUrls: string[] = [];
  globalThis.fetch = (async (input) => {
    upstreamUrls.push(String(input));
    return Response.json({ detail: "需要登录。" }, { status: 401 });
  }) as typeof fetch;

  try {
    const request = new NextRequest("http://localhost/api/nexus/sessions");
    const response = await GET(request, createContext("sessions"));

    assert.equal(response.status, 401);
    assert.deepEqual(upstreamUrls, ["https://nexus.test/api/v1/auth/me"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects cross-origin before lookup and missing-CSRF before a business mutation", async () => {
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    return Response.json({ user_id: "nexuspilot-web", authentication_method: "browser_session" });
  }) as typeof fetch;
  try {
    for (const origin of ["https://foreign.example", "http://localhost"]) {
      const response = await POST(new NextRequest("http://localhost/api/nexus/sessions", {
        method: "POST",
        headers: { origin, cookie: "nexuspilot_csrf=secret", "content-type": "application/json" },
        body: JSON.stringify({ title: "Forbidden creation" }),
      }), createContext("sessions"));
      assert.equal(response.status, 403);
    }
    assert.equal(calls, 1);
  } finally { globalThis.fetch = originalFetch; }
});

test("local mode permits same-origin writes without cookies and overrides the owner", async () => {
  const writes: unknown[] = [];
  globalThis.fetch = (async (input, init) => {
    if (String(input).endsWith("/api/v1/auth/me")) {
      return Response.json({ user_id: "nexuspilot-web", authentication_method: "local_deployment" });
    }
    writes.push(JSON.parse(String(init?.body)));
    return Response.json({ session_id: "local-session" }, { status: 201 });
  }) as typeof fetch;
  try {
    const response = await POST(new NextRequest("http://localhost/api/nexus/sessions", {
      method: "POST",
      headers: { origin: "http://localhost", "content-type": "application/json" },
      body: JSON.stringify({ title: "Local", user_id: "another-user" }),
    }), createContext("sessions"));
    assert.equal(response.status, 201);
    assert.deepEqual(writes, [{ title: "Local", user_id: "nexuspilot-web" }]);
  } finally { globalThis.fetch = originalFetch; }
});

test("returns an explicit unavailable response when principal lookup cannot connect", async () => {
  globalThis.fetch = (async () => { throw new Error("connection refused"); }) as typeof fetch;
  try {
    const response = await GET(new NextRequest("http://localhost/api/nexus/sessions"), createContext("sessions"));
    assert.equal(response.status, 503);
  } finally { globalThis.fetch = originalFetch; }
});

test("rejects unknown or missing access modes instead of treating them as local", async () => {
  try {
    for (const authenticationMethod of [undefined, "anonymous"]) {
      let calls = 0;
      globalThis.fetch = (async () => {
        calls += 1;
        return Response.json({ user_id: "nexuspilot-web", authentication_method: authenticationMethod });
      }) as typeof fetch;
      const response = await GET(new NextRequest("http://localhost/api/nexus/sessions"), createContext("sessions"));
      assert.equal(response.status, 502);
      assert.equal(calls, 1);
    }
  } finally { globalThis.fetch = originalFetch; }
});

test("forwards session revocation with the browser CSRF proof", async () => {
  const upstreamCalls: Array<{ url: string; csrf: string | null; cookie: string | null }> = [];
  globalThis.fetch = (async (input, init) => {
    const headers = new Headers(init?.headers);
    upstreamCalls.push({
      url: String(input),
      csrf: headers.get("x-csrf-token"),
      cookie: headers.get("cookie"),
    });
    return new Response(null, { status: 204 });
  }) as typeof fetch;

  try {
    const response = await DELETE(
      createRequest("auth/sessions/session-1", {
        method: "DELETE",
        headers: {
          cookie: "nexuspilot_session=opaque; nexuspilot_csrf=csrf-value",
          origin: "http://localhost",
        },
      }),
      createContext("auth/sessions/session-1"),
    );

    assert.equal(response.status, 204);
    assert.deepEqual(upstreamCalls, [{
      url: "https://nexus.test/api/v1/auth/sessions/session-1",
      csrf: "csrf-value",
      cookie: "nexuspilot_session=opaque; nexuspilot_csrf=csrf-value",
    }]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects cross-user session, message, and run resources at the proxy boundary", async () => {
  const upstreamUrls: string[] = [];
  globalThis.fetch = (async (input) => {
    const url = String(input);
    upstreamUrls.push(url);
    if (url.endsWith("/messages/foreign-message")) {
      return Response.json({ session_id: "foreign-session" });
    }
    if (url.endsWith("/runs/foreign-run")) {
      return Response.json({ user_id: "another-user" });
    }
    return Response.json({ user_id: "another-user" });
  }) as typeof fetch;

  try {
    const sessionResponse = await GET(
      createRequest("sessions/foreign-session"),
      createContext("sessions/foreign-session"),
    );
    const messageResponse = await GET(
      createRequest("messages/foreign-message"),
      createContext("messages/foreign-message"),
    );
    const runResponse = await GET(
      createRequest("runs/foreign-run"),
      createContext("runs/foreign-run"),
    );

    assert.equal(sessionResponse.status, 404);
    assert.equal(messageResponse.status, 404);
    assert.equal(runResponse.status, 404);
    assert.equal(upstreamUrls.length, 4);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects an oversized declared body before reading or forwarding it", async () => {
  let upstreamCalled = false;
  globalThis.fetch = (async () => {
    upstreamCalled = true;
    return Response.json({});
  }) as typeof fetch;

  try {
    const response = await POST(
      createRequest("responses", {
        method: "POST",
        headers: {
          "content-length": "2000001",
          "content-type": "application/json",
        },
        body: "{}",
      }),
      createContext("responses"),
    );
    assert.equal(response.status, 413);
    assert.equal(upstreamCalled, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects browser-supplied System Prompt references before forwarding a response", async () => {
  let upstreamCalled = false;
  globalThis.fetch = (async () => {
    upstreamCalled = true;
    return Response.json({});
  }) as typeof fetch;

  try {
    const response = await POST(
      createRequest("responses", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          run_id: "run-1",
          context_source_references: {
            prompt: { template_name: "internal", version_number: 1, variables: {} },
          },
        }),
      }),
      createContext("responses"),
    );

    assert.equal(response.status, 400);
    assert.equal(upstreamCalled, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("renames an owned session without allowing the browser to change ownership", async () => {
  const upstreamCalls: Array<{ url: string; method: string | undefined; body: BodyInit | null | undefined }> = [];
  globalThis.fetch = (async (input, init) => {
    const url = String(input);
    upstreamCalls.push({ url, method: init?.method, body: init?.body });
    if (url.endsWith("/sessions/session-1") && init?.method === "GET") {
      return Response.json({ session_id: "session-1", user_id: "nexuspilot-web" });
    }
    if (url.endsWith("/sessions/session-1") && init?.method === "PATCH") {
      return Response.json({ session_id: "session-1", user_id: "nexuspilot-web", title: "Renamed" });
    }
    throw new Error(`unexpected upstream request: ${url}`);
  }) as typeof fetch;

  try {
    const response = await PATCH(
      createRequest("sessions/session-1", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: "Renamed" }),
      }),
      createContext("sessions/session-1"),
    );
    assert.equal(response.status, 200);
    assert.equal((await response.json()).title, "Renamed");
    assert.deepEqual(upstreamCalls.map(({ url, method }) => ({ url, method })), [
      { url: "https://nexus.test/api/v1/sessions/session-1", method: "GET" },
      { url: "https://nexus.test/api/v1/sessions/session-1", method: "PATCH" },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects session lifecycle fields while conversation archiving is paused", async () => {
  let upstreamCalled = false;
  globalThis.fetch = (async () => {
    upstreamCalled = true;
    return Response.json({});
  }) as typeof fetch;

  try {
    const response = await PATCH(
      createRequest("sessions/session-1", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: "Renamed", status: "archived" }),
      }),
      createContext("sessions/session-1"),
    );
    assert.equal(response.status, 400);
    assert.equal(upstreamCalled, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("checks model invocation ownership before forwarding reasoning history", async () => {
  const upstreamUrls: string[] = [];
  globalThis.fetch = (async (input, init) => {
    const url = String(input);
    upstreamUrls.push(url);
    if (url.endsWith("/attempts/attempt-1") && init?.method === "GET") {
      return Response.json({ attempt_id: "attempt-1", run_id: "run-1" });
    }
    if (url.endsWith("/runs/run-1") && init?.method === "GET") {
      return Response.json({ run_id: "run-1", user_id: "nexuspilot-web" });
    }
    if (url.endsWith("/attempts/attempt-1/reasoning-blocks") && init?.method === "GET") {
      return Response.json([]);
    }
    throw new Error(`unexpected upstream request: ${url}`);
  }) as typeof fetch;

  try {
    const response = await GET(
      createRequest("attempts/attempt-1/reasoning-blocks"),
      createContext("attempts/attempt-1/reasoning-blocks"),
    );
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), []);
    assert.deepEqual(upstreamUrls, [
      "https://nexus.test/api/v1/attempts/attempt-1",
      "https://nexus.test/api/v1/runs/run-1",
      "https://nexus.test/api/v1/attempts/attempt-1/reasoning-blocks",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("forces Context evidence into the user scope and removes System Prompt sources", async () => {
  const upstreamUrls: string[] = [];
  globalThis.fetch = (async (input) => {
    upstreamUrls.push(String(input));
    return Response.json({
      context_build_id: "context-1",
      sources: [
        { source_type: "prompt_release", source_id: "system-prompt-1" },
        { source_type: "message", source_id: "message-1" },
      ],
    });
  }) as typeof fetch;

  try {
    const response = await GET(
      createRequest("context-builds/context-1/evidence?user_id=another-user"),
      createContext("context-builds/context-1/evidence"),
    );

    assert.equal(response.status, 200);
    assert.deepEqual((await response.json()).sources, [
      { source_type: "message", source_id: "message-1" },
    ]);
    assert.deepEqual(upstreamUrls, [
      "https://nexus.test/api/v1/context-builds/context-1/evidence?user_id=nexuspilot-web",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects malformed upstream Context evidence instead of exposing an ambiguous projection", async () => {
  globalThis.fetch = (async () => new Response("not-json", {
    status: 200,
    headers: { "content-type": "application/json" },
  })) as typeof fetch;

  try {
    const response = await GET(
      createRequest("context-builds/context-1/evidence"),
      createContext("context-builds/context-1/evidence"),
    );

    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { detail: "上下文证据响应格式无效。" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects cross-user workflow creation and nested workflow reads", async () => {
  const upstreamUrls: string[] = [];
  globalThis.fetch = (async (input) => {
    const url = String(input);
    upstreamUrls.push(url);
    if (url.endsWith("/agent-workflows/foreign-workflow")) {
      return Response.json({ run_id: "foreign-run" });
    }
    if (url.endsWith("/runs/foreign-run")) {
      return Response.json({ user_id: "another-user" });
    }
    throw new Error(`unexpected upstream request: ${url}`);
  }) as typeof fetch;

  try {
    const createResponse = await POST(
      createRequest("runs/foreign-run/agent-workflows", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ idempotency_key: "workflow-key" }),
      }),
      createContext("runs/foreign-run/agent-workflows"),
    );
    const nodeResponse = await GET(
      createRequest("agent-workflows/foreign-workflow/nodes/node-1"),
      createContext("agent-workflows/foreign-workflow/nodes/node-1"),
    );

    assert.equal(createResponse.status, 404);
    assert.equal(nodeResponse.status, 404);
    assert.deepEqual(upstreamUrls, [
      "https://nexus.test/api/v1/runs/foreign-run",
      "https://nexus.test/api/v1/agent-workflows/foreign-workflow",
      "https://nexus.test/api/v1/runs/foreign-run",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("forwards an owned workflow POST as SSE after Run ownership succeeds", async () => {
  const upstreamCalls: Array<{ url: string; method: string | undefined }> = [];
  globalThis.fetch = (async (input, init) => {
    const url = String(input);
    upstreamCalls.push({ url, method: init?.method });
    if (url.endsWith("/runs/run-1") && init?.method === "GET") {
      return Response.json({ user_id: "nexuspilot-web" });
    }
    if (url.endsWith("/runs/run-1/agent-workflows") && init?.method === "POST") {
      return new Response("id: 1\nevent: agent.workflow.started\ndata: {}\n\n", {
        status: 201,
        headers: { "content-type": "text/event-stream" },
      });
    }
    throw new Error(`unexpected upstream request: ${url}`);
  }) as typeof fetch;

  try {
    const response = await POST(
      createRequest("runs/run-1/agent-workflows", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ idempotency_key: "workflow-key" }),
      }),
      createContext("runs/run-1/agent-workflows"),
    );

    assert.equal(response.status, 201);
    assert.equal(response.headers.get("content-type"), "text/event-stream");
    assert.match(await response.text(), /agent\.workflow\.started/);
    assert.deepEqual(upstreamCalls, [
      { url: "https://nexus.test/api/v1/runs/run-1", method: "GET" },
      { url: "https://nexus.test/api/v1/runs/run-1/agent-workflows", method: "POST" },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("forwards an empty-body cancel only after Workflow and Run ownership succeed", async () => {
  const upstreamCalls: Array<{ url: string; method: string | undefined; body: BodyInit | null | undefined }> = [];
  globalThis.fetch = (async (input, init) => {
    const url = String(input);
    upstreamCalls.push({ url, method: init?.method, body: init?.body });
    if (url.endsWith("/agent-workflows/workflow-1") && init?.method === "GET") {
      return Response.json({ run_id: "run-1" });
    }
    if (url.endsWith("/runs/run-1") && init?.method === "GET") {
      return Response.json({ user_id: "nexuspilot-web" });
    }
    if (url.endsWith("/agent-workflows/workflow-1/cancel") && init?.method === "POST") {
      return Response.json({ workflow_execution_id: "workflow-1", run_id: "run-1", status: "cancelled" });
    }
    throw new Error(`unexpected upstream request: ${url}`);
  }) as typeof fetch;

  try {
    const response = await POST(
      createRequest("agent-workflows/workflow-1/cancel", { method: "POST" }),
      createContext("agent-workflows/workflow-1/cancel"),
    );

    assert.equal(response.status, 200);
    assert.equal((await response.json()).status, "cancelled");
    assert.deepEqual(upstreamCalls, [
      { url: "https://nexus.test/api/v1/agent-workflows/workflow-1", method: "GET", body: undefined },
      { url: "https://nexus.test/api/v1/runs/run-1", method: "GET", body: undefined },
      { url: "https://nexus.test/api/v1/agent-workflows/workflow-1/cancel", method: "POST", body: undefined },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects cross-user Workflow cancellation before the mutation is forwarded", async () => {
  const upstreamUrls: string[] = [];
  globalThis.fetch = (async (input, init) => {
    const url = String(input);
    upstreamUrls.push(url);
    if (url.endsWith("/agent-workflows/foreign-workflow") && init?.method === "GET") {
      return Response.json({ run_id: "foreign-run" });
    }
    if (url.endsWith("/runs/foreign-run") && init?.method === "GET") {
      return Response.json({ user_id: "another-user" });
    }
    throw new Error(`unexpected upstream request: ${url}`);
  }) as typeof fetch;

  try {
    const response = await POST(
      createRequest("agent-workflows/foreign-workflow/cancel", { method: "POST" }),
      createContext("agent-workflows/foreign-workflow/cancel"),
    );

    assert.equal(response.status, 404);
    assert.deepEqual(upstreamUrls, [
      "https://nexus.test/api/v1/agent-workflows/foreign-workflow",
      "https://nexus.test/api/v1/runs/foreign-run",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
