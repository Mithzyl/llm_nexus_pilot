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
