"use client";

import {useState, type FormEvent} from "react";
import {nexusFetch} from "../../lib/api";

/** Keep recovery proof and new password in component memory; completion never auto-logs in. */
export function PasswordResetForm({onBack,onComplete}:{onBack:()=>void;onComplete:()=>void}) {
  const [email,setEmail]=useState("");
  const [code,setCode]=useState("");
  const [password,setPassword]=useState("");
  const [sent,setSent]=useState(false);
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState("");
  const [error,setError]=useState("");

  /** Request exactly one email attempt; the public message does not disclose account existence. */
  async function requestCode() {
    if(busy) return;
    setBusy(true);setError("");setNotice("");
    try {
      const result=await nexusFetch<{message:string}>("auth/password-reset/request",{method:"POST",body:JSON.stringify({email})});
      setSent(true);setNotice(result.message);
    } catch(failure) {setError(failure instanceof Error ? failure.message : "暂时无法申请重置邮件。");}
    finally {setBusy(false);}
  }

  /** Change the password only after explicit submission; uncertain outcomes remain visible. */
  async function submit(event:FormEvent) {
    event.preventDefault();
    if(!sent) {await requestCode();return;}
    if(busy) return;
    setBusy(true);setError("");
    try {
      await nexusFetch("auth/password-reset/verify",{method:"POST",body:JSON.stringify({email,code:code.trim(),new_password:password})});
      setPassword("");setCode("");onComplete();
    } catch(failure) {setError(failure instanceof Error ? failure.message : "未确认重置结果，请尝试登录或重新申请。");}
    finally {setBusy(false);}
  }

  return <form className="authentication-form" onSubmit={submit}>
    <p>通过已验证的邮箱找回密码。成功后将退出所有原浏览器会话。</p>
    <label>邮箱<input type="email" autoComplete="email" required maxLength={320} disabled={busy} value={email}
      onChange={event=>{setEmail(event.target.value);setSent(false);setCode("");setPassword("");setNotice("");}} /></label>
    {sent && <>
      <label>重置验证码<input required autoComplete="one-time-code" maxLength={128} disabled={busy} value={code} onChange={event=>setCode(event.target.value)} /></label>
      <label>新密码（至少12个字符）<input type="password" autoComplete="new-password" required minLength={12} maxLength={1024} disabled={busy} value={password} onChange={event=>setPassword(event.target.value)} /></label>
    </>}
    {notice && <p role="status">{notice}</p>}{error && <p role="alert" className="authentication-error">{error}</p>}
    <button disabled={busy||!email.trim()}>{busy?"正在处理…":sent?"重置密码":"申请重置邮件"}</button>
    {sent && <button type="button" disabled={busy} onClick={()=>void requestCode()}>重新申请邮件</button>}
    <button type="button" disabled={busy} onClick={onBack}>返回登录</button>
  </form>;
}
