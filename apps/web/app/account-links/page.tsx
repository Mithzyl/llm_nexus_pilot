"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthenticationGate } from "../../components/auth/AuthenticationGate";
import { getCurrentWorkspacePrincipal, nexusFetch, NexusApiError } from "../../lib/api";
import type { WorkspacePrincipal } from "../../lib/types";

type ApplicationAccount = {application_id: string; external_user_id: string};
type AccountLink = ApplicationAccount & {link_id: string; status: string; version: number};

/** Display an approved App name without treating an unknown identifier as an HTML fragment. */
function applicationName(id: string) {
  return ({personal_assistant: "个人助手", retail: "零售助手"} as Record<string, string>)[id] ?? id;
}

/** Confirm a fragment-only App ticket and manage associations without sharing business data. */
export default function AccountLinksPage() {
  const [principal, setPrincipal] = useState<WorkspacePrincipal | null>(null);
  const [checking, setChecking] = useState(true);
  const [ticket, setTicket] = useState("");
  const [preview, setPreview] = useState<ApplicationAccount | null>(null);
  const [links, setLinks] = useState<AccountLink[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    const value = new URLSearchParams(window.location.hash.slice(1)).get("ticket") ?? "";
    void getCurrentWorkspacePrincipal().then(user => { if (active) setPrincipal(user); })
      .catch(failure => {
        if (active && !(failure instanceof NexusApiError && failure.status === 401))
          setError("无法确认登录状态，请稍后重试。");
      }).finally(() => {
        if (!active) return;
        if (value && !/^[A-Za-z0-9_-]{32,128}$/.test(value)) setError("绑定请求无效，请从应用重新发起。");
        else setTicket(value);
        setChecking(false);
      });
    /** Accept a newly opened App request even when only this page's URL fragment changes. */
    function updateTicket() {
      const next = new URLSearchParams(window.location.hash.slice(1)).get("ticket") ?? "";
      setPreview(null); setError(""); setLoading(true);
      if (next && !/^[A-Za-z0-9_-]{32,128}$/.test(next)) {
        setError("绑定请求无效，请从应用重新发起。"); setLoading(false); return;
      }
      setTicket(next);
    }
    window.addEventListener("hashchange", updateTicket);
    return () => {active = false; window.removeEventListener("hashchange", updateTicket);};
  }, []);

  useEffect(() => {
    if (principal?.authentication_method !== "browser_session") return;
    const controller = new AbortController();
    void Promise.allSettled([
      nexusFetch<{items: AccountLink[]}>("me/account-links", {signal: controller.signal}),
      ticket ? nexusFetch<ApplicationAccount>("me/account-links/preview", {
        method: "POST", body: JSON.stringify({ticket}), signal: controller.signal,
      }) : Promise.resolve(null),
    ]).then(([saved, pending]) => {
      if (controller.signal.aborted) return;
      if (saved.status === "fulfilled") setLinks(saved.value.items);
      if (pending.status === "fulfilled") setPreview(pending.value);
      if (saved.status === "rejected") setError("无法读取已有账号关联，请刷新重试。");
      else if (pending.status === "rejected") setError("绑定请求已失效或已经使用，请先查看下方已有记录。");
    }).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [principal, ticket]);

  /** Drop only the pending local confirmation, leaving saved associations untouched. */
  function cancelPending() {
    window.history.replaceState(null, "", "/account-links");
    setTicket(""); setPreview(null); setError("");
  }

  /** Consume explicit consent once; a lost response remains an error until saved links reload. */
  async function confirm() {
    if (!preview || !ticket || busy) return;
    setBusy(true); setError("");
    try {
      await nexusFetch("me/account-links/confirm", {method: "POST", body: JSON.stringify({ticket})});
      window.history.replaceState(null, "", "/account-links");
      setTicket(""); setPreview(null); setNotice("账号已关联。记忆共享仍需单独设置。");
    } catch {setError("未能确认绑定结果，请刷新查看已有记录；不要重复创建账号。");}
    finally {setBusy(false);}
  }

  /** Revoke only after explaining that original accounts and historical data remain intact. */
  async function revoke(item: AccountLink) {
    if (busy || !window.confirm(`解除与${applicationName(item.application_id)}账号的关联？相关跨端授权将失效，原账号及数据保留。`)) return;
    setBusy(true); setError("");
    try {
      const result = await nexusFetch<{status: string; version: number}>("me/account-links/revoke", {
        method: "POST", body: JSON.stringify({link_id: item.link_id}),
      });
      setLinks(current => current.map(row => row.link_id === item.link_id ? {...row, ...result} : row));
      setNotice("账号关联已解除。");
    } catch {setError("解除结果尚未确认，请刷新后查看。");}
    finally {setBusy(false);}
  }

  if (checking || !principal && !error) return <AuthenticationGate isChecking={checking} onAuthenticated={setPrincipal} />;
  return <main className="authentication-page"><section className="authentication-panel account-links-panel">
    <h1>应用账号关联</h1>
    {principal && <p>当前 Nexus 账号：{principal.display_name}</p>}
    {principal?.authentication_method !== "browser_session" ? <p role="alert">请使用真实账号登录后管理关联。</p> : <>
      {loading && <p role="status">正在读取关联信息…</p>}
      {preview && <section aria-label="待确认关联">
        <h2>{applicationName(preview.application_id)}</h2><p>应用账号：{preview.external_user_id}</p>
        <p>仅在这是你自己的账号时确认。关联不会合并历史数据，也不会自动共享记忆。</p>
        <div className="settings-form-footer application-login-actions">
          <button disabled={busy} onClick={() => void confirm()}>确认关联这两个账号</button>
          <button disabled={busy} onClick={cancelPending}>取消</button>
        </div>
      </section>}
      {!loading && !links.length && !preview && <p>尚无已关联账号。请从对应应用发起关联。</p>}
      {links.map(item => <section key={item.link_id} className="account-link-item">
        <h2>{applicationName(item.application_id)}</h2><p>应用账号：{item.external_user_id}</p>
        <p>{item.status === "active" ? "已关联" : "已解除"}</p>
        {item.status === "active" && <button disabled={busy} onClick={() => void revoke(item)}>解除关联</button>}
      </section>)}
    </>}
    {notice && <p role="status">{notice}</p>}{error && <p role="alert">{error}</p>}
    <Link href="/">返回工作台</Link>
  </section></main>;
}
