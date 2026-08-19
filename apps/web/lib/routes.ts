export type NexusWorkspaceRoute =
  | { kind: "home" }
  | { kind: "conversation"; sessionId: string }
  | { kind: "run"; runId: string };

type RouteParams = Record<string, string | string[] | undefined>;

/** Return one non-empty dynamic route parameter and reject catch-all arrays. */
function singleParam(value: string | string[] | undefined): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** Resolve the dynamic App Router parameters without guessing malformed arrays. */
export function resolveWorkspaceRoute(params: RouteParams): NexusWorkspaceRoute {
  const runId = singleParam(params.runId);
  if (runId) return { kind: "run", runId };

  const sessionId = singleParam(params.sessionId);
  if (sessionId) return { kind: "conversation", sessionId };

  return { kind: "home" };
}

/** Build the stable conversation URL for one persisted Session identifier. */
export function conversationPath(sessionId: string): string {
  return `/c/${encodeURIComponent(sessionId)}`;
}

/** Build the stable evidence URL for one persisted Run identifier. */
export function runPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}`;
}

/** Build a deterministic key used to deduplicate route restoration work. */
export function workspaceRouteKey(route: NexusWorkspaceRoute): string {
  if (route.kind === "conversation") return `conversation:${route.sessionId}`;
  if (route.kind === "run") return `run:${route.runId}`;
  return "home";
}
