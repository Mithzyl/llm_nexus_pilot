"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { getMemorySettings, listProductMemories, mutateProductMemory } from "../../lib/api";
import { memoryErrorMessage } from "../../lib/personal-settings";
import type { MemoryCommand, MemorySettings, PersonalMemory } from "../../lib/types";
import { MemorySharingEditor } from "./MemorySharingEditor";
import { TypedMemoryFields } from "./TypedMemoryFields";
import { buildTypedMemory, memoryKinds, memoryFields, type MemoryFormKind } from "../../lib/typed-memory-form";

/** Manage personal or current-project memory without creating a conversation or model request. */
export function MemorySettingsPanel({ projectId }: { projectId?: string }) {
  const [scope, setScope] = useState<"personal" | "project">("personal");
  return <section className="settings-section settings-memory-panel" aria-label="记忆管理">
    <label>记忆范围 <select value={scope} onChange={event => setScope(event.target.value as typeof scope)}>
      <option value="personal">个人记忆</option>
      {projectId && <option value="project">当前项目记忆</option>}
    </select></label>
    <MemoryScopeEditor key={`${scope}:${projectId ?? ""}`} projectId={scope === "project" ? projectId : undefined} />
  </section>;
}

/** Keep one scope's fields and retry identity separate from other scopes and closed dialogs. */
function MemoryScopeEditor({ projectId }: { projectId?: string }) {
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [items, setItems] = useState<PersonalMemory[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<PersonalMemory | null>(null);
  const [sensitiveConfirmed, setSensitiveConfirmed] = useState(false);
  const [kind, setKind] = useState<MemoryFormKind>("free_text");
  const [fields, setFields] = useState<Record<string,string>>({});
  const [typeConfirmed, setTypeConfirmed] = useState(false);
  const alive = useRef(true);
  const pending = useRef<{ command: string; key: string } | null>(null);

  /** Read current settings and a page; failures remain visible rather than becoming empty data. */
  async function refresh(offset = 0) {
    try {
      const [state, page] = await Promise.all([getMemorySettings(projectId), listProductMemories(projectId, offset)]);
      if (!alive.current) return;
      setSettings(state);
      setItems(previous => offset === 0 ? page.items : [...previous, ...page.items]);
      setNextOffset(page.next_offset);
      setError("");
    } catch (failure) {
      if (alive.current) setError(memoryErrorMessage(failure));
    } finally {
      if (alive.current) setLoading(false);
    }
  }

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    void Promise.all([getMemorySettings(projectId), listProductMemories(projectId)]).then(([state, page]) => {
      if (cancelled) return;
      setSettings(state); setItems(page.items); setNextOffset(page.next_offset); setLoading(false);
    }).catch(failure => {
      if (!cancelled) { setError(memoryErrorMessage(failure)); setLoading(false); }
    });
    return () => { cancelled = true; alive.current = false; };
  }, [projectId]);

  /** Retain the same request key on failure; an unknown result must not create a second record. */
  async function execute(command: Omit<MemoryCommand, "key">) {
    if (busy) return;
    const serialized = JSON.stringify(command);
    if (pending.current?.command !== serialized) pending.current = { command: serialized, key: crypto.randomUUID() };
    setBusy(true); setError(""); setNotice("");
    try {
      await mutateProductMemory({ ...command, key: pending.current.key }, projectId);
      if (!alive.current) return;
      pending.current = null;
      setText(""); setEditing(null); setSensitiveConfirmed(false);
      setKind("free_text"); setFields({}); setTypeConfirmed(false);
      setNotice(command.action === "forget" ? "已忘记；原始对话和文件未删除。" : "操作已保存。");
      await refresh();
    } catch (failure) {
      if (alive.current) setError(memoryErrorMessage(failure));
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  /** Save an explicit form submission with the displayed target/version and separate sensitive consent. */
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    let typed: Record<string,unknown> | undefined;
    try { typed = buildTypedMemory(kind, fields); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "请检查记忆字段。"); return; }
    if (typed && (!typeConfirmed || !sensitiveConfirmed)) { setError("请确认类型字段和敏感信息保存授权。"); return; }
    void execute({ action: editing ? "correct" : "save", text: text.trim(),
      ...(editing ? { entry_id: editing.entry_id, expected_version: editing.version } : {}),
      ...(typed ? {typed_data:typed, confirmed_type:typeConfirmed} : {}),
      sensitive: editing?.sensitive || sensitiveConfirmed, confirmed_sensitive: sensitiveConfirmed });
  }

  return <>
    <div className="settings-form-footer">
      <p>{settings ? projectId
        ? `项目${settings.mode === "inherit" ? "继承入口设置" : "已关闭"}；当前${settings.enabled ? "开启" : "关闭"}`
        : `个人记忆${settings.enabled ? "已开启" : "已关闭"}` : "正在读取设置…"}</p>
      <button disabled={!settings || loading || busy} onClick={() => settings && void execute({
        action: "settings", enabled: projectId ? settings.mode === "disabled" : !settings.enabled,
        expected_version: settings.version,
      })}>{projectId ? settings?.mode === "disabled" ? "继承入口设置" : "关闭项目记忆"
        : settings?.enabled ? "关闭记忆" : "开启记忆"}</button>
      <button disabled={loading || busy} onClick={() => { setLoading(true); void refresh(); }}>刷新记忆</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!settings?.enabled && settings && <p>关闭后不自动使用或新增记忆，仍可查看、纠正和忘记。</p>}
    <form className="settings-form" onSubmit={submit}>
      <label>记忆类型<select value={kind} disabled={busy} onChange={event => {
        setKind(event.target.value as MemoryFormKind); setFields({}); setTypeConfirmed(false); setSensitiveConfirmed(false);
      }}>{memoryKinds.map(([value,label]) => <option key={value} value={value} disabled={value === "free_text" && Boolean(editing?.typed_data)}>{label}</option>)}</select></label>
      <label>{editing ? "纠正记忆" : "新增记忆"}
        <textarea aria-label="记忆内容" maxLength={4000} value={text} onChange={event => setText(event.target.value)} />
      </label>
      <TypedMemoryFields kind={kind} values={fields} onChange={value => {setFields(value); setTypeConfirmed(false);}} />
      {kind !== "free_text" && <label><input type="checkbox" checked={typeConfirmed} onChange={event => setTypeConfirmed(event.target.checked)} />
        我已核对以上结构化字段；保存记忆不会创建日历事项或规划路线。
      </label>}
      <label><input type="checkbox" checked={sensitiveConfirmed} onChange={event => setSensitiveConfirmed(event.target.checked)} />
        此条含敏感信息；我确认将以上内容保存到{projectId ? "当前项目" : "个人"}记忆，不授权向外部模型或工具发送。
      </label>
      <div className="settings-form-footer">
        <button disabled={busy || loading || !text.trim() || (!settings?.enabled && !editing)} type="submit">
          {editing ? "保存纠正" : "保存记忆"}
        </button>
        {editing && <button type="button" disabled={busy} onClick={() => { setEditing(null); setText(""); setSensitiveConfirmed(false); setKind("free_text"); setFields({}); setTypeConfirmed(false); }}>取消编辑</button>}
      </div>
    </form>
    {!loading && !error && items.length === 0 && <p>暂无记忆。</p>}
    <div className="settings-list">
      {items.map(item => <article className="settings-memory-row" key={item.entry_id}>
        <p>{item.text}</p><span>{item.status === "candidate" ? "等待确认" : "已保存"}{item.sensitive ? " · 敏感" : ""}</span>
        {item.typed_data && <dl>{memoryFields[(item.memory_type ?? "free_text") as MemoryFormKind]?.map(field =>
          item.typed_data?.[field.key] != null && <div key={field.key}><dt>{field.label}</dt><dd>{String(item.typed_data[field.key])}</dd></div>)}</dl>}
        <div className="settings-form-footer">
          <button disabled={busy || loading} onClick={() => { setEditing(item); setText(item.text ?? ""); setSensitiveConfirmed(false);
            setKind((item.memory_type ?? "free_text") as MemoryFormKind);
            setFields(Object.fromEntries(Object.entries(item.typed_data ?? {}).map(([key,value]) => [key, value == null ? "" : String(value)])));
            setTypeConfirmed(false);
          }}>纠正</button>
          {item.status === "candidate" && <button disabled={busy || loading || !settings?.enabled} onClick={() => {
            if (item.sensitive && !window.confirm(`确认将这条敏感候选保存为${projectId ? "当前项目" : "个人"}记忆？\n${item.text}`)) return;
            void execute({ action: "confirm", entry_id: item.entry_id, expected_version: item.version, confirmed_sensitive: item.sensitive });
          }}>确认记住</button>}
          <button disabled={busy || loading} onClick={() => {
            if (window.confirm("忘记这条记忆？这不会删除原始对话或文件。")) void execute({ action: "forget", entry_id: item.entry_id, expected_version: item.version });
          }}>忘记</button>
        </div>
        {!projectId && item.status === "active" && <MemorySharingEditor key={`${item.entry_id}:${item.version}`} item={item} />}
      </article>)}
    </div>
    {nextOffset !== null && <button disabled={loading || busy} onClick={() => { setLoading(true); void refresh(nextOffset); }}>加载更多记忆</button>}
  </>;
}
