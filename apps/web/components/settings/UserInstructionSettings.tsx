"use client";

import { useEffect, useState } from "react";
import { nexusFetch } from "../../lib/api";

type Scope = "entry" | "global";
type Profile = {version: number; text: string; enabled: boolean; allowed_entries: string[]};

/** Configure explicit preferences separately from remembered facts and platform rules. */
export function UserInstructionSettings() {
  const [scope, setScope] = useState<Scope>("entry");
  return <section className="settings-section settings-memory-panel" aria-label="个性化提示词">
    <label>适用范围<select value={scope} onChange={event => setScope(event.target.value as Scope)}>
      <option value="entry">仅 Nexus 工作台</option><option value="global">跨应用通用偏好</option>
    </select></label>
    <InstructionEditor key={scope} scope={scope} />
  </section>;
}

/** Keep drafts scoped and report saved only after the versioned API acknowledges the edit. */
function InstructionEditor({scope}: {scope: Scope}) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [text, setText] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [entries, setEntries] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void nexusFetch<Profile>(`me/instructions?scope=${scope}`, {signal:controller.signal})
      .then(value => {setProfile(value); setText(value.text); setEnabled(value.enabled); setEntries(value.allowed_entries);})
      .catch(() => {if (!controller.signal.aborted) setError("无法读取偏好，请关闭后重试。");});
    return () => controller.abort();
  }, [scope]);

  /** Preserve expected version and draft after failure; identical retry is handled by the server. */
  async function save() {
    if (!profile || busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await nexusFetch<{version:number; enabled:boolean}>("me/instructions", {
        method:"POST",body:JSON.stringify({scope,text:text.trim(),enabled,
          expected_version:profile.version,allowed_entries:scope === "global" ? entries : []}),
      });
      setProfile({version:result.version,text:text.trim(),enabled:result.enabled,allowed_entries:entries});
      setNotice("已保存。设置用于之后发起的请求，正在执行的任务不受影响。");
    } catch {setError("未能保存。若其他页面已修改设置，请重新打开；跨应用偏好需要目标应用已关联。");}
    finally {setBusy(false);}
  }

  return <form className="settings-form" onSubmit={event => {event.preventDefault();void save();}}>
    <p>填写称呼、语言或回答风格等偏好。不要填写密码或密钥；这些偏好不能授予工具或数据访问权限。</p>
    <label>回答偏好<textarea aria-label="回答偏好" value={text} maxLength={4000}
      disabled={!profile || busy} onChange={event => setText(event.target.value)} /></label>
    <label><input type="checkbox" checked={enabled} disabled={!profile || busy}
      onChange={event => setEnabled(event.target.checked)} />使用这些偏好</label>
    {scope === "global" && <fieldset disabled={!profile || busy}>
      <legend>允许使用的应用</legend>
      {[["native","Nexus 工作台"],["personal_assistant","个人助手"],["retail","零售助手"]].map(([value,label]) =>
        <label key={value}><input type="checkbox" checked={entries.includes(value)} onChange={event =>
          setEntries(event.target.checked ? [...entries,value] : entries.filter(item => item !== value))} />{label}</label>)}
    </fieldset>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!profile && !error && <p role="status">正在读取偏好…</p>}
    <div className="settings-form-footer"><button type="submit" disabled={!profile || busy || !text.trim()}>保存偏好</button></div>
  </form>;
}
