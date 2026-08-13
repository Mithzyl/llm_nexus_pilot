type RoutePatternSegment = string | ":id";

type AllowedRoute = {
  method: "GET" | "POST";
  pattern: readonly RoutePatternSegment[];
};

/**
 * Keep the browser-facing proxy limited to the resources used by the first
 * NexusPilot UI. A new upstream endpoint must be added here deliberately so
 * the platform API key cannot become a general-purpose browser proxy.
 */
const ALLOWED_ROUTES: readonly AllowedRoute[] = [
  { method: "GET", pattern: ["providers"] },
  { method: "GET", pattern: ["users", ":id"] },
  { method: "POST", pattern: ["users"] },
  { method: "GET", pattern: ["sessions"] },
  { method: "POST", pattern: ["sessions"] },
  { method: "GET", pattern: ["sessions", ":id"] },
  { method: "GET", pattern: ["sessions", ":id", "messages"] },
  { method: "GET", pattern: ["sessions", ":id", "messages", "full"] },
  { method: "GET", pattern: ["sessions", ":id", "messages", "latest"] },
  { method: "POST", pattern: ["sessions", ":id", "messages"] },
  { method: "GET", pattern: ["sessions", ":id", "latest-run"] },
  { method: "GET", pattern: ["messages", ":id"] },
  { method: "POST", pattern: ["runs"] },
  { method: "GET", pattern: ["runs", ":id"] },
  { method: "POST", pattern: ["runs", ":id", "agent-workflows"] },
  { method: "GET", pattern: ["runs", ":id", "agent-workflow"] },
  { method: "GET", pattern: ["agent-workflows", ":id"] },
  { method: "POST", pattern: ["agent-workflows", ":id", "cancel"] },
  { method: "GET", pattern: ["agent-workflows", ":id", "result"] },
  { method: "GET", pattern: ["agent-workflows", ":id", "nodes"] },
  { method: "GET", pattern: ["agent-workflows", ":id", "nodes", ":id"] },
  { method: "GET", pattern: ["agent-workflows", ":id", "events"] },
  { method: "POST", pattern: ["responses"] },
];

/**
 * Match a decoded catch-all path against a fixed route shape and id slots.
 */
function matchesPattern(pathSegments: readonly string[], pattern: readonly RoutePatternSegment[]): boolean {
  return (
    pathSegments.length === pattern.length &&
    pattern.every((segment, index) => segment === ":id" || segment === pathSegments[index])
  );
}

/**
 * Check both the HTTP method and exact path shape before a request reaches
 * the authenticated upstream API.
 */
export function isAllowedProxyRoute(
  method: string,
  pathSegments: readonly string[],
): boolean {
  return ALLOWED_ROUTES.some(
    (route) => route.method === method && matchesPattern(pathSegments, route.pattern),
  );
}
