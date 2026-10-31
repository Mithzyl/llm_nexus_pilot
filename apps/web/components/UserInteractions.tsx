"use client";

import { useEffect, useState } from "react";
import { nexusFetch } from "../lib/api";

type Interaction = {
  interaction_id: string;
  kind: "approval" | "input";
  question: string;
  status: "pending" | "resolved" | "cancelled" | "expired";
  version: number;
};

/** Restore persisted questions independently of the stream or selected evidence panel. */
export function UserInteractions({ runId, active }: { runId: string; active: boolean }) {
  const [items, setItems] = useState<Interaction[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    /** Poll only the current Run; a failed read is not an empty question list. */
    async function refresh() {
      try {
        const result = await nexusFetch<{items: Interaction[]}>(`runs/${runId}/interactions`, {
          signal: controller.signal,
        });
        if (alive) { setItems(result.items); setError(""); }
      } catch (failure) {
        if (alive) setError(failure instanceof Error ? failure.message : "无法读取待确认事项。");
      } finally {
        if (alive && active) timer = setTimeout(refresh, 1500);
      }
    }
    void refresh();
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [runId, active]);
  const pending = items.filter(item => item.status === "pending");
  if (!pending.length && !error) return null;
  return <section className="user-interactions" aria-label="需要你的回复">
    {error && <p role="alert">{error}</p>}
    {pending.map(item => <InteractionQuestion key={item.interaction_id} runId={runId} item={item}
      onResolved={() => setItems(current => current.filter(value => value.interaction_id !== item.interaction_id))} />)}
  </section>;
}

/** Submit an explicit decision with a stable version key; never infer approval from dismissal. */
function InteractionQuestion({runId, item, onResolved}: {
  runId: string; item: Interaction; onResolved: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  /** A retry or second tab uses the same decision identity; changed answers conflict on the server. */
  async function reply(approved?: boolean) {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await nexusFetch(`runs/${runId}/interactions/${item.interaction_id}/reply`, {
        method: "POST", body: JSON.stringify({expected_version: item.version,
          key: `reply:${item.interaction_id}:${item.version}`,
          ...(item.kind === "approval" ? {approved} : {text})}),
      });
      onResolved();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "回复尚未确认，请重试。");
    } finally { setBusy(false); }
  }
  return <form className="interaction-question" onSubmit={event => {event.preventDefault(); if (item.kind === "input") void reply();}}>
    <p id={`question-${item.interaction_id}`}>{item.question}</p>
    {item.kind === "input" && <textarea aria-labelledby={`question-${item.interaction_id}`}
      value={text} maxLength={4000} disabled={busy} onChange={event => setText(event.target.value)} />}
    {error && <p role="alert">{error}</p>}
    <div className="interaction-actions">
      {item.kind === "approval" ? <>
        <button type="button" disabled={busy} onClick={() => void reply(false)}>不批准</button>
        <button type="button" disabled={busy} onClick={() => void reply(true)}>批准并继续</button>
      </> : <button type="submit" disabled={busy || !text.trim()}>回复并继续</button>}
    </div>
  </form>;
}
