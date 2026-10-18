export type NexusWorkspaceRoute =
  | { kind: "home" }
  | { kind: "conversation"; sessionId: string }
  | { kind: "run"; runId: string };

type RouteParams = Record<string, string | string[] | undefined>;

/** Resolve native history navigation from its URL, including browser back and forward. */
export function resolveWorkspacePath(pathname: string): NexusWorkspaceRoute {
  const match = /^\/(c|runs)\/([^/]+)\/?$/.exec(pathname);
  if (!match) return { kind: "home" };
  try {
    const id = decodeURIComponent(match[2]);
    return match[1] === "c" ? { kind: "conversation", sessionId: id } : { kind: "run", runId: id };
  } catch {
    return { kind: "home" };
  }
}

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

/**
 * Return a new conversation route only after its first submission can be
 * reconstructed from durable facts, avoiding an App Router remount mid-stream.
 */
export function terminalConversationNavigationTarget(
  currentRoute: NexusWorkspaceRoute,
  sessionId: string,
  hasRestorableTerminalFacts: boolean,
): string | null {
  if (!hasRestorableTerminalFacts) return null;
  if (currentRoute.kind === "conversation" && currentRoute.sessionId === sessionId) {
    return null;
  }
  return conversationPath(sessionId);
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
