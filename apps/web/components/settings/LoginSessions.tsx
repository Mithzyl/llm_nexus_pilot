"use client";

import { useEffect, useState } from "react";
import { nexusFetch } from "../../lib/api";
import { loginSessionStatus, type LoginSession } from "../../lib/login-sessions";

/** List owned browser sessions and explicitly revoke another session without signing this one out. */
export function LoginSessions() {
  const [sessions, setSessions] = useState<LoginSession[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const controller = new AbortController();
    nexusFetch<LoginSession[]>("auth/sessions", {signal: controller.signal}).then(items => {
      setSessions(items); setError(""); setNow(Date.now());
    }).catch(() => {
      if (!controller.signal.aborted) setError("无法读取登录会话，请重试。");
    });
    return () => controller.abort();
  }, [revision]);

  /** Revoke exactly the confirmed session; an uncertain response never displays success. */
  async function revoke(id: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      await nexusFetch(`auth/sessions/${encodeURIComponent(id)}`, {method: "DELETE"});
      setNotice("已退出该会话。"); setSelected(null);
      setSessions(items => items?.map(item => item.login_session_id === id ? {...item, revoked_at: new Date().toISOString()} : item) ?? null);
    } catch { setError("未能确认退出结果，请刷新会话列表后检查。"); }
    finally { setBusy(false); }
  }

  const labels = {current: "当前会话", active: "可用", expired: "已过期", revoked: "已退出"};
  return <section className="login-sessions" aria-label="登录会话">
    <h4>登录会话</h4>
    <button type="button" disabled={busy} onClick={() => {setSelected(null); setRevision(value => value + 1);}}>刷新会话列表</button>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!sessions && !error && <p role="status">正在读取…</p>}
    {sessions?.length === 0 && <p>没有可显示的登录会话。</p>}
    <ul>{sessions?.map(session => {
      const state = loginSessionStatus(session, now);
      return <li key={session.login_session_id}>
        <strong>{session.device_name || "浏览器"} · {labels[state]}</strong>
        <p>最近使用：{new Date(session.last_used_at).toLocaleString()}</p>
        {state === "active" && (selected === session.login_session_id ? <div>
          <p>确认退出这个会话？该浏览器需要重新登录。</p>
          <button type="button" disabled={busy} onClick={() => void revoke(session.login_session_id)}>确认退出会话</button>
          <button type="button" disabled={busy} onClick={() => setSelected(null)}>保留会话</button>
        </div> : <button type="button" disabled={busy} onClick={() => setSelected(session.login_session_id)}>退出此会话</button>)}
      </li>;
    })}</ul>
    <p>这里只管理浏览器登录，不会撤销个人 API Key 或退出关联 App。</p>
  </section>;
}
