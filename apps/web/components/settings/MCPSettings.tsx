"use client";

import { useEffect, useState } from "react";
import { nexusFetch } from "../../lib/api";

type Connection = {
  connection: string; config_version: string; revision: number; enabled: boolean;
  review_required: boolean; discovery_status: string | null; discovered_at: string | null;
  tools: {name: string; description: string; selected: boolean}[];
};

/** Manage a server-approved connection without displaying credentials or editing endpoints. */
function ConnectionSettings({item, reload}: {item: Connection; reload: () => Promise<void>}) {
  const [enabled, setEnabled] = useState(item.enabled);
  const [selected, setSelected] = useState(item.tools.filter(t => t.selected).map(t => t.name));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dirty = enabled !== item.enabled || item.tools.some(t => selected.includes(t.name) !== t.selected);

  /** Save the user-selected subset; stale revisions must reload instead of overwriting decisions. */
  async function save() {
    setBusy(true); setError(""); setNotice("");
    try {
      await nexusFetch("me/mcp-connections", {method: "POST", body: JSON.stringify({
        connection: item.connection, config_version: item.config_version,
        expected_revision: item.revision, enabled, tools: selected,
      })});
      await reload();
    } catch {setError("未能确认保存结果，请重新读取连接设置后再试。");}
    finally {setBusy(false);}
  }

  /** Contact the approved service only on explicit action; discovery never adds remote tools. */
  async function discover() {
    setBusy(true); setError(""); setNotice("");
    try {
      await nexusFetch("me/mcp-connections/discover", {method: "POST", body: JSON.stringify({
        connection: item.connection, config_version: item.config_version, expected_revision: item.revision,
      })});
      await reload(); setNotice("已核对当前批准的工具。");
    } catch {
      setError("未能核对工具，请稍后重试；如定义有变化，需要管理员重新批准。");
      await reload().catch(() => {});
    } finally {setBusy(false);}
  }

  return <section className="mcp-connection" aria-label={item.connection}>
    <h4>{item.connection}</h4>
    <p>{item.review_required ? "批准定义已变化，请重新选择并保存。" : item.enabled ? "可用" : "已停用"}</p>
    <fieldset disabled={busy}>
      <legend>连接与工具</legend>
      <label><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} />启用此连接</label>
      {item.tools.map(tool => <label key={tool.name}>
        <input type="checkbox" checked={selected.includes(tool.name)} onChange={event => setSelected(current =>
          event.target.checked ? [...current, tool.name] : current.filter(name => name !== tool.name))} />
        <span>{tool.name}<small>{tool.description}</small></span>
      </label>)}
    </fieldset>
    <p>{item.discovery_status === "verified" ? "最近核对成功" : item.discovery_status ? "最近核对未成功" : "尚未主动核对"}
      {item.discovered_at && ` · ${new Date(item.discovered_at).toLocaleString()}`}</p>
    <div className="mcp-actions">
      <button disabled={busy || enabled && selected.length === 0} onClick={() => void save()}>保存设置</button>
      <button disabled={busy || !item.enabled || dirty} onClick={() => void discover()}>刷新工具发现</button>
    </div>
    {dirty && <p>刷新前请先保存修改。</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>;
}

/** Load only safe connection metadata; opening settings never contacts an MCP service. */
export function MCPSettings() {
  const [items, setItems] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void nexusFetch<{items: Connection[]}>("me/mcp-connections", {signal: controller.signal})
      .then(result => {if (!controller.signal.aborted) setItems(result.items);})
      .catch(() => {if (!controller.signal.aborted) setError("无法读取连接设置。");})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, []);

  /** Recover saved revisions after an update or uncertain network response. */
  async function reload() {
    const result = await nexusFetch<{items: Connection[]}>("me/mcp-connections");
    setItems(result.items); setError("");
  }

  return <section className="settings-section">
    <h3>MCP 工具连接</h3>
    <p>仅管理已分配给你的连接。停用会停止本地等待和后续调用，不能保证远端任务已经停止。</p>
    {loading && <p role="status">正在读取连接…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && !items.length && <p>尚无已分配的连接。</p>}
    <button className="mcp-reload" onClick={() => void reload().catch(() => setError("无法重新读取连接设置。"))}>重新读取设置</button>
    {items.map(item => <ConnectionSettings key={`${item.connection}:${item.revision}`} item={item} reload={reload} />)}
  </section>;
}
