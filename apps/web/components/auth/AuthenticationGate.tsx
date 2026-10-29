"use client";

import { useEffect, useState } from "react";

import { loginWithPassword, nexusFetch } from "../../lib/api";
import { RegistrationForm } from "./RegistrationForm";
import type { WorkspacePrincipal } from "../../lib/types";

type AuthenticationGateProps = {
  isChecking: boolean;
  unavailableMessage?: string | null;
  onAuthenticated: (principal: WorkspacePrincipal) => void;
};

/** Show login only when authentication is required; dependency failures remain explicit. */
export function AuthenticationGate({
  isChecking,
  unavailableMessage,
  onAuthenticated,
}: AuthenticationGateProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [registrationEnabled, setRegistrationEnabled] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [registrationNotice, setRegistrationNotice] = useState("");
  useEffect(() => {
    let active = true;
    void nexusFetch<{enabled: boolean}>("auth/registration").then(result => {
      if (active) setRegistrationEnabled(result.enabled === true);
    }).catch(() => { if (active) setRegistrationEnabled(false); });
    return () => {active = false;};
  }, []);

  /** Submit local credentials and hand the committed browser principal to the workspace. */
  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmitting || !email.trim() || !password) return;
    setIsSubmitting(true);
    setErrorMessage(null);
    try {
      const principal = await loginWithPassword({
        email: email.trim(),
        password,
        device_name: "Web browser",
      });
      setPassword("");
      onAuthenticated(principal);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "登录失败，请稍后重试。");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="authentication-page">
      <section className="authentication-panel" aria-labelledby="authentication-title">
        <div className="authentication-brand">
          <div className="brand-mark" aria-hidden="true"><span /></div>
          <span>NexusPilot</span>
        </div>
        {isChecking ? (
          <div className="authentication-checking" role="status">
            <span />
            正在连接工作台…
          </div>
        ) : unavailableMessage ? (
          <>
            <h1 id="authentication-title">工作台暂不可用</h1>
            <p className="authentication-error" role="alert">{unavailableMessage}</p>
            <button type="button" onClick={() => window.location.reload()}>重试</button>
          </>
        ) : (
          <>
            <p className="eyebrow">受保护的对话工作台</p>
            <h1 id="authentication-title">{registering ? "注册" : "登录"}</h1>
            {registrationNotice && <p role="status">{registrationNotice}</p>}
            {registering ? <RegistrationForm onBack={() => setRegistering(false)} onComplete={() => {
              setRegistering(false); setRegistrationNotice("注册成功，请使用新账号登录。");
            }} /> : <>
            <p className="authentication-intro">使用你的账号继续。</p>
            <form className="authentication-form" onSubmit={handleSubmit}>
              <label>
                <span>邮箱</span>
                <input
                  type="email"
                  name="email"
                  autoComplete="username"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                  maxLength={320}
                />
              </label>
              <label>
                <span>密码</span>
                <input
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  maxLength={1_024}
                />
              </label>
              {errorMessage && <p className="authentication-error" role="alert">{errorMessage}</p>}
              <button type="submit" disabled={isSubmitting || !email.trim() || !password}>
                {isSubmitting ? "正在登录…" : "登录"}
              </button>
            </form>
            {registrationEnabled && <button type="button" disabled={isSubmitting}
              onClick={() => {setPassword(""); setRegistering(true); setRegistrationNotice("");}}>注册账号</button>}
            </>}
          </>
        )}
      </section>
    </main>
  );
}
