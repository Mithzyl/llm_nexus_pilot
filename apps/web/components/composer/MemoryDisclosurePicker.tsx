"use client";

import { useEffect, useRef, useState } from "react";
import { listProductMemories } from "../../lib/api";
import type { PersonalMemory } from "../../lib/types";
import type { MemoryDisclosureSelection } from "../../lib/memory-disclosure";

/** Require a visible per-message selection; never treat opening the picker as disclosure consent. */
export function MemoryDisclosurePicker({disabled, projectId, onChange}: {
  disabled: boolean; projectId?: string; onChange: (items: MemoryDisclosureSelection[]) => void;
}) {
  const [items, setItems] = useState<Array<PersonalMemory & {scope: "personal" | "project"}>>([]);
  const [selected, setSelected] = useState<MemoryDisclosureSelection[]>([]);
  const [opened, setOpened] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  /** Read authorized management data only on demand, retaining failure as an error. */
  async function load() {
    setOpened(true); setLoading(true); setError("");
    // Reloaded text may have a new version: require a fresh choice, not an old checkbox.
    setSelected([]); onChange([]); setItems([]);
    try {
      const result: typeof items = [];
      for (const scope of ["personal", ...(projectId ? ["project"] : [])] as const) {
        let offset: number | null = 0;
        const seen = new Set<number>();
        do {
          if (seen.has(offset) || seen.size >= 20) throw new Error("Invalid memory pagination");
          seen.add(offset);
          const page = await listProductMemories(scope === "project" ? projectId : undefined, offset);
          if (!mounted.current) return;
          result.push(...page.items.filter(item => item.status === "active" && item.sensitive)
            .map(item => ({...item, scope: scope as "personal" | "project"})));
          offset = page.next_offset;
        } while (offset !== null);
      }
      setItems(result);
    } catch { if (mounted.current) setError("无法读取敏感记忆，请重试；本次不会自动授权。"); }
    finally { if (mounted.current) setLoading(false); }
  }

  /** Preserve the exact displayed version and scope; selection never includes the body. */
  function toggle(item: typeof items[number], checked: boolean) {
    const next = checked ? [...selected, {entry_id: item.entry_id, version: item.version, scope: item.scope}]
      : selected.filter(value => value.entry_id !== item.entry_id);
    setSelected(next); onChange(next);
  }

  return <div className="settings-memory-panel composer-memory-disclosure">
    <button type="button" aria-expanded={opened} disabled={disabled || loading} onClick={() => void load()}>
      本次使用敏感记忆{selected.length ? `（${selected.length}）` : ""}
    </button>
    {opened && <section aria-label="本次敏感记忆授权">
      <button type="button" onClick={() => setOpened(false)}>收起</button>
      <p>勾选并发送即允许将这些条目提供给本次所选模型及审核模型，用于回答本次问题。
        不授权联网或其他外部工具，不自动沿用到下一条消息。</p>
      {loading && <p role="status">正在读取…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && <p>没有可选择的敏感记忆。</p>}
      {items.map(item => <label key={item.entry_id}>
        <input type="checkbox" disabled={disabled || loading}
          checked={selected.some(value => value.entry_id === item.entry_id)}
          onChange={event => toggle(item, event.target.checked)} />
        {item.scope === "project" ? "项目" : "个人"}：{item.text}
      </label>)}
    </section>}
  </div>;
}
