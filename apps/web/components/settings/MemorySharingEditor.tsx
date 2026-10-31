"use client";

import { useState } from "react";
import { nexusFetch } from "../../lib/api";
import type { PersonalMemory } from "../../lib/types";

type Grant = {grant_id: string; version: number; target_entry: string; fields: string[];
  automatic: boolean; active: boolean};
const targets: Record<string, string> = {personal_assistant: "个人助手", retail: "零售助手"};
const fieldLabels: Record<string, string> = {label: "名称", address: "地址", latitude: "纬度",
  longitude: "经度", coordinate_system: "坐标系", timezone: "时区", origin: "出发地点",
  destination: "目的地", travel_mode: "出行方式", preferred_departure: "偏好出发时间",
  weekdays: "每周日期", activity: "事项", preferred_time: "偏好时间", occurred_on: "发生日期",
  description: "经历", lesson: "经验"};

/** Approve only displayed fields for an existing linked entry; no automatic full-text sharing. */
export function MemorySharingEditor({item}: {item: PersonalMemory}) {
  const [opened, setOpened] = useState(false);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [target, setTarget] = useState("personal_assistant");
  const [fields, setFields] = useState<string[]>([]);
  const [automatic, setAutomatic] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const choices = [
    ...(item.text ? [{path: "text", label: "完整文字说明", value: item.text}] : []),
    ...Object.entries(item.typed_data ?? {}).filter(([key, value]) => key !== "memory_type" && value !== null)
      .map(([key, value]) => ({path: `typed_data.${key}`, label: fieldLabels[key] ?? key,
        value: Array.isArray(value) ? value.join("、") : String(value)})),
  ];

  /** Reload authoritative choices, preserving errors instead of presenting a false empty list. */
  async function refresh() {
    const result = await nexusFetch<{items: Grant[]}>(`me/memory-shares?entry_id=${encodeURIComponent(item.entry_id)}`);
    setGrants(result.items);
  }

  /** Read decisions only when requested; opening settings itself grants no access. */
  async function open() {
    setOpened(true); setBusy(true); setError("");
    try { await refresh(); }
    catch { setError("无法读取共享设置，请重试。"); }
    finally { setBusy(false); }
  }

  /** Explicit approval binds selected fields to the displayed content version. */
  async function save() {
    setBusy(true); setError(""); setNotice("");
    try {
      await nexusFetch("me/memory-shares", {method: "POST", body: JSON.stringify({
        entry_id: item.entry_id, expected_version: item.version, target_entry: target, fields, automatic,
      })});
      await refresh(); setNotice("共享设置已保存。");
    } catch { setError("未能保存。请确认目标应用已关联，或刷新记忆后重试。"); }
    finally { setBusy(false); }
  }

  /** Revoke the selected persisted grant; historical answers and source memory remain intact. */
  async function revoke(grant: Grant) {
    setBusy(true); setError(""); setNotice("");
    try {
      await nexusFetch("me/memory-shares/revoke", {method: "POST",
        body: JSON.stringify({grant_id: grant.grant_id, expected_version: grant.version})});
      await refresh(); setNotice("已停止共享；原始记忆和历史回答未删除。");
    } catch { setError("未能停止共享，请刷新后重试。"); }
    finally { setBusy(false); }
  }

  return <div className="memory-sharing-editor">
    <button type="button" aria-expanded={opened} disabled={busy} onClick={() => opened ? setOpened(false) : void open()}>
      {opened ? "收起共享设置" : "共享到其他应用"}
    </button>
    {opened && <div className="settings-form">
      <p>仅共享勾选的内容。敏感信息还需在目标应用中为每次对话单独确认。</p>
      <label>目标应用<select value={target} onChange={e => setTarget(e.target.value)} disabled={busy}>
        {Object.entries(targets).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <fieldset disabled={busy}><legend>允许读取的内容</legend>
        {choices.map(choice => <label key={choice.path}>
          <input type="checkbox" checked={fields.includes(choice.path)} onChange={e => setFields(
            e.target.checked ? [...fields, choice.path] : fields.filter(value => value !== choice.path))} />
          <span>{choice.label}：{choice.value}</span>
        </label>)}
      </fieldset>
      <label><input type="checkbox" checked={automatic} disabled={busy} onChange={e => setAutomatic(e.target.checked)} />
        允许目标应用按需自动使用；敏感信息仍需本次对话确认。
      </label>
      <button type="button" disabled={busy || fields.length === 0} onClick={() => void save()}>确认共享所选内容</button>
      {grants.map(grant => <div className="settings-form-footer" key={grant.grant_id}>
        <span>{targets[grant.target_entry] ?? grant.target_entry} · {grant.active ? "已共享" : "已失效或停止"}</span>
        {grant.active && <button type="button" disabled={busy} onClick={() => void revoke(grant)}>停止共享</button>}
      </div>)}
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    </div>}
  </div>;
}
