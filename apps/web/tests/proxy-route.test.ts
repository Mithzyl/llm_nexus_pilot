import assert from "node:assert/strict";
import test from "node:test";
import { NextRequest } from "next/server";

process.env.NEXUSPILOT_API_KEY = "route-test-key";
process.env.NEXUSPILOT_API_URL = "https://nexus.test";

let GET: typeof import("../app/api/nexus/[...path]/route").GET;
let POST: typeof import("../app/api/nexus/[...path]/route").POST;
const originalFetch = globalThis.fetch;

test.before(async () => {
  const route = await import("../app/api/nexus/[...path]/route");
  GET = route.GET;
  POST = route.POST;
});

/** Build a same-origin request for one proxy path. */
function createRequest(
  path: string,
  init?: ConstructorParameters<typeof NextRequest>[1],
): InstanceType<typeof NextRequest> {
  return new NextRequest(`http://localhost/api/nexus/${path}`, init);
}

/** Build the catch-all route context expected by the Next.js handler. */
function createContext(path: string) {
  return { params: Promise.resolve({ path: path.split("/") }) };
}

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
