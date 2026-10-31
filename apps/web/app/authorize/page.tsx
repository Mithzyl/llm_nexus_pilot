"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AuthenticationGate } from "../../components/auth/AuthenticationGate";
import { getCurrentWorkspacePrincipal, nexusFetch, NexusApiError } from "../../lib/api";
import type { WorkspacePrincipal } from "../../lib/types";

type LoginRequest = {application_id:string; redirect_uri:string; code_challenge:string; state:string};
type Destination = {name:string; redirect_uri:string};

/** Require an explicit logged-in decision for a server-approved App; never approve on page load. */
export default function ApplicationAuthorizationPage() {
  const [principal, setPrincipal] = useState<WorkspacePrincipal | null>(null);
  const [checking, setChecking] = useState(true);
  const [request, setRequest] = useState<LoginRequest | null>(null);
  const [destination, setDestination] = useState<Destination | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    const params = new URLSearchParams(window.location.search);
    const value = {application_id:params.get("application_id") ?? "",redirect_uri:params.get("redirect_uri") ?? "",
      code_challenge:params.get("code_challenge") ?? "",state:params.get("state") ?? ""};
    const valid = !!value.application_id && !!value.redirect_uri && /^[A-Za-z0-9_-]{43}$/.test(value.code_challenge)
      && value.state.length >= 16 && value.state.length <= 256;
    void getCurrentWorkspacePrincipal().then(user => {if(active) setPrincipal(user);})
      .catch(failure => {if(active && !(failure instanceof NexusApiError && failure.status === 401)) setError("无法验证登录状态，请稍后重试。");})
      .finally(() => {
        if (!active) return;
        if (valid) setRequest(value);
        else setError("登录请求不完整，请从应用重新发起。");
        setChecking(false);
      });
    return () => {active=false;};
  }, []);
  useEffect(() => {
    if (!principal || !request) return;
    const controller = new AbortController();
    void nexusFetch<Destination>(`me/application-login?${new URLSearchParams({application_id:request.application_id,
      redirect_uri:request.redirect_uri})}`, {signal:controller.signal})
      .then(value => {if(!controller.signal.aborted) setDestination(value);})
      .catch(() => {if(!controller.signal.aborted) setError("此应用或返回地址尚未获准登录。");});
    return () => controller.abort();
  }, [principal, request]);

  /** Obtain a one-use code only on consent; use only the callback revalidated by the server. */
  async function authorize() {
    if (!request || !destination || busy) return;
    setBusy(true); setError("");
    try {
      const result = await nexusFetch<{code:string;state:string;redirect_uri:string}>("me/application-login",{
        method:"POST",body:JSON.stringify(request),
      });
      const callback = new URL(result.redirect_uri);
      if (result.redirect_uri !== destination.redirect_uri || result.state !== request.state) throw new Error("Invalid confirmation");
      // Fragment delivery keeps the one-use code out of the receiving App's HTTP access logs.
      // Existing clients retain query delivery unless they explicitly request fragment mode.
      if (new URLSearchParams(window.location.search).get("response_mode") === "fragment") {
        callback.hash = new URLSearchParams({code:result.code,state:result.state}).toString();
      } else {
        callback.searchParams.set("code",result.code); callback.searchParams.set("state",result.state);
      }
      window.location.assign(callback.toString());
    } catch {setError("未能完成授权，请从应用重新发起登录。");setBusy(false);}
  }

  if (checking || !principal && !error) return <AuthenticationGate isChecking={checking} onAuthenticated={setPrincipal} />;
  return <main className="authentication-page"><section className="authentication-panel">
    <h1>登录到{destination?.name ?? "应用"}</h1>
    {principal && <p>当前 Nexus 账号：{principal.display_name}</p>}
    {destination && <p>确认后将返回 {new URL(destination.redirect_uri).host}。应用会建立自己的登录会话。
      此操作不会共享历史对话、文件或记忆。</p>}
    {principal?.authentication_method !== "browser_session" && <p role="alert">需要真实账号登录，开发环境身份不能用于应用登录。</p>}
    {error && <p role="alert">{error}</p>}
    {!destination && !error && <p role="status">正在验证应用…</p>}
    <div className="settings-form-footer application-login-actions">
      <button type="button" disabled={busy || !destination || !!error || principal?.authentication_method !== "browser_session"}
        onClick={() => void authorize()}>确认并继续</button>
      <Link href="/">取消</Link>
    </div>
  </section></main>;
}
