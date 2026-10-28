export type SessionDateGroupKey = "today" | "yesterday" | "earlier";

/** Block sends until the session identified by the route is loaded. */
export function isActiveSessionReadyForSubmission(
  activeId: string | null, activeSession: { session_id: string } | null,
): boolean {
  return !activeId || activeSession?.session_id === activeId;
}

/** Resolve the independently fetched route session without relying on sidebar pagination. */
export function resolveActiveSession<T extends { session_id: string }>(
  sessions: readonly T[], activeId: string | null, loaded: T | null,
): T | null {
  if (!activeId) return null;
  return loaded?.session_id === activeId
    ? loaded : sessions.find((session) => session.session_id === activeId) ?? null;
}

export type SessionDateGroup<T> = {
  key: SessionDateGroupKey;
  label: string;
  items: T[];
};

const GROUP_LABELS: Record<SessionDateGroupKey, string> = {
  today: "今天",
  yesterday: "昨天",
  earlier: "更早",
};

/** Use the browser-local calendar day, not UTC, when grouping history. */
function localDateKey(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

/** Search only the persisted title; message content is not part of this lightweight filter. */
export function filterSessionsByTitle<T extends { title: string | null }>(
  sessions: readonly T[],
  query: string,
): T[] {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  if (!normalized) return [...sessions];
  return sessions.filter((session) =>
    (session.title ?? "").toLocaleLowerCase("zh-CN").includes(normalized),
  );
}

/** Preserve server order while separating sessions into local day buckets. */
export function groupSessionsByDate<T extends { updated_at: string }>(
  sessions: readonly T[],
  now = new Date(),
): SessionDateGroup<T>[] {
  const today = localDateKey(now);
  const yesterdayDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const yesterday = localDateKey(yesterdayDate);
  const buckets: Record<SessionDateGroupKey, T[]> = {
    today: [],
    yesterday: [],
    earlier: [],
  };
  for (const session of sessions) {
    const date = new Date(session.updated_at);
    const key = localDateKey(date);
    const bucket = Number.isNaN(date.getTime()) ? "earlier" : key === today ? "today" : key === yesterday ? "yesterday" : "earlier";
    buckets[bucket].push(session);
  }
  return (Object.keys(buckets) as SessionDateGroupKey[])
    .filter((key) => buckets[key].length > 0)
    .map((key) => ({ key, label: GROUP_LABELS[key], items: buckets[key] }));
}
