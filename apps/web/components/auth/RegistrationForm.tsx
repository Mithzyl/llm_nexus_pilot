"use client";

import { useState, type FormEvent } from "react";
import { nexusFetch } from "../../lib/api";

/** Keep verification and password in component state only; never auto-login or auto-resend. */
export function RegistrationForm({onComplete, onBack}: {onComplete: () => void; onBack: () => void}) {
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  /** Request delivery once per explicit action; unknown delivery is not retried automatically. */
  async function requestCode() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await nexusFetch<{message: string}>("auth/registration/request", {
        method: "POST", body: JSON.stringify({email}),
      });
      setSent(true); setNotice(result.message);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "无法申请验证码。"); }
    finally { setBusy(false); }
  }

  /** Verify the emailed proof before asking the server to create credentials. */
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!sent) { await requestCode(); return; }
    if (busy) return;
    setBusy(true); setError("");
    try {
      await nexusFetch("auth/registration/verify", {method: "POST",
        body: JSON.stringify({email, code: code.trim(), display_name: displayName, password})});
      setPassword(""); setCode(""); onComplete();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "注册失败，请检查验证码。"); }
    finally { setBusy(false); }
  }

  return <form className="authentication-form" onSubmit={submit}>
    <label>邮箱<input type="email" required maxLength={320} value={email} disabled={busy}
      autoComplete="email" onChange={event => {setEmail(event.target.value); setSent(false); setCode(""); setNotice("");}} /></label>
    {sent && <>
      <label>验证码<input required maxLength={128} autoComplete="one-time-code" value={code}
        onChange={event => setCode(event.target.value)} /></label>
      <label>昵称<input required maxLength={128} autoComplete="nickname" value={displayName}
        onChange={event => setDisplayName(event.target.value)} /></label>
      <label>密码（至少12个字符）<input required type="password" minLength={12} maxLength={1024}
        autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} /></label>
    </>}
    {notice && <p role="status">{notice}</p>}
    {error && <p className="authentication-error" role="alert">{error}</p>}
    <button type="submit" disabled={busy || !email.trim()}>{busy ? "正在处理…" : sent ? "验证并注册" : "发送验证邮件"}</button>
    {sent && <button type="button" disabled={busy} onClick={() => void requestCode()}>重新发送验证码</button>}
    <button type="button" disabled={busy} onClick={onBack}>返回登录</button>
  </form>;
}
