"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { nexusFetch } from "../../lib/api";
import { uploadUserFile, type UserFile } from "../../lib/user-files";
import styles from "./projects.module.css";

type Project = { project_id: string; project_name: string };
type SavedFile = { project_file_id: string; filename: string; status: string; size_bytes: number };
type Conversation = { session_id: string; title: string | null };
type Artifact = { artifact_id: string; filename: string };
type Page<T> = { items: T[]; next_cursor: string | null };

/** Manage explicitly saved project copies without changing the active chat or enabling Memory. */
export default function ProjectsPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [files, setFiles] = useState<SavedFile[]>([]);
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [uploads, setUploads] = useState<UserFile[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [artifactCursor, setArtifactCursor] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("");
  const [rename, setRename] = useState("");
  const [source, setSource] = useState("");
  const [filename, setFilename] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [sessionOffset, setSessionOffset] = useState(0);
  const [hasMoreSessions, setHasMoreSessions] = useState(false);
  const saveIdentity = useRef<{ input: string; key: string } | null>(null);
  const sessionKey = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      nexusFetch<Page<Project>>("projects?limit=100", { signal: controller.signal }),
      nexusFetch<UserFile[]>("files", { signal: controller.signal }),
      nexusFetch<Page<Artifact>>("artifacts?limit=100", { signal: controller.signal }),
    ]).then(([projectPage, uploadList, artifactPage]) => {
      if (controller.signal.aborted) return;
      setProjects(projectPage.items); setNextCursor(projectPage.next_cursor);
      setUploads(uploadList.filter(file => file.status === "ready"));
      setArtifacts(artifactPage.items); setArtifactCursor(artifactPage.next_cursor);
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason.message ?? reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    Promise.all([
      nexusFetch<SavedFile[]>(`projects/${selected}/files`, { signal: controller.signal }),
      nexusFetch<Conversation[]>(`projects/${selected}/sessions`, { signal: controller.signal }),
    ]).then(([saved, conversations]) => {
      if (controller.signal.aborted) return;
      setFiles(saved); setSessions(conversations); setSessionOffset(conversations.length);
      setHasMoreSessions(conversations.length === 100);
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason.message ?? reason)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selected]);

  /** Switch project details without allowing a previous project's asynchronous result to win. */
  function selectProject(project: Project) {
    if (project.project_id === selected) return;
    setSelected(project.project_id); setRename(project.project_name); setFiles([]); setSessions([]);
    setSource(""); setFilename(""); setNotice(""); setError(""); setDetailLoading(true);
    saveIdentity.current = null; sessionKey.current = null;
  }

  /** Serialize explicit writes and show failure without clearing the user's selected inputs. */
  async function act(operation: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await operation(); } catch (reason) { setError(reason instanceof Error ? reason.message : "操作未完成，请重试。"); }
    finally { setBusy(false); }
  }

  /** Reuse a save identity on response loss and require a new one when the input changes. */
  async function saveFile() {
    const [kind, id] = source.split(":");
    const input = JSON.stringify({ selected, source, filename });
    if (saveIdentity.current?.input !== input) {
      // Persist before sending so a reload can confirm the same project copy after response loss.
      const storageKey = `nexus:project-save:${selected}:${source}:${encodeURIComponent(filename)}`;
      const key = sessionStorage.getItem(storageKey) ?? crypto.randomUUID();
      sessionStorage.setItem(storageKey, key);
      saveIdentity.current = { input, key };
    }
    await nexusFetch(`projects/${selected}/files`, { method: "POST", body: JSON.stringify({
      [kind === "file" ? "file_id" : "artifact_id"]: id, filename,
      idempotency_key: saveIdentity.current.key,
    }) });
    setFiles(await nexusFetch<SavedFile[]>(`projects/${selected}/files`));
    setNotice("已保存项目副本。上传原件和运行产物保持不变。");
  }

  return <main className={styles.page}>
    <header className={styles.header}><Link href="/">← 返回对话</Link><h1>个人项目</h1></header>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {loading ? <p role="status">正在读取项目…</p> : <div className={styles.layout}>
      <aside aria-label="项目列表">
        <form onSubmit={event => { event.preventDefault(); void act(async () => {
          const project = await nexusFetch<Project>("projects", { method: "POST", body: JSON.stringify({ project_name: projectName.trim() }) });
          setProjects(current => [...current, project]); setProjectName(""); selectProject(project);
        }); }} className={styles.form}>
          <label>项目名称<input value={projectName} maxLength={255} onChange={event => setProjectName(event.target.value)} /></label>
          <button disabled={busy || !projectName.trim()}>创建项目</button>
        </form>
        <ul className={styles.list}>{projects.map(project => <li key={project.project_id}>
          <button className={selected === project.project_id ? styles.selected : ""} aria-pressed={selected === project.project_id} disabled={busy} onClick={() => selectProject(project)}>{project.project_name}</button>
        </li>)}</ul>
        {!projects.length && <p>还没有项目。</p>}
        {nextCursor && <button disabled={busy} onClick={() => void act(async () => {
          const page = await nexusFetch<Page<Project>>(`projects?limit=100&cursor=${encodeURIComponent(nextCursor)}`);
          setProjects(current => [...current, ...page.items.filter(item => !current.some(p => p.project_id === item.project_id))]); setNextCursor(page.next_cursor);
        })}>更多项目</button>}
      </aside>
      <section aria-label="项目工作区">
        {!selected ? <p>选择项目，管理文件和项目内对话。</p> : <>
          <form className={styles.row} onSubmit={event => { event.preventDefault(); void act(async () => {
            await nexusFetch(`projects/${selected}`, { method: "PATCH", body: JSON.stringify({ project_name: rename.trim() }) });
            setProjects(current => current.map(p => p.project_id === selected ? { ...p, project_name: rename.trim() } : p));
            setNotice("项目已重命名。");
          }); }}><label>项目名称<input value={rename} maxLength={255} onChange={event => setRename(event.target.value)} /></label><button disabled={busy || !rename.trim()}>重命名</button></form>
          <h2>文件</h2><p>显式保存固定副本；不会自动加入对话或覆盖同名文件。</p>
          <div className={styles.form}>
            <label>选择已有文件或产物<select value={source} disabled={busy} onChange={event => {
              setSource(event.target.value);
              const [kind, id] = event.target.value.split(":");
              setFilename(kind === "file" ? uploads.find(f => f.file_id === id)?.filename ?? "" : artifacts.find(f => f.artifact_id === id)?.filename ?? "");
            }}><option value="">请选择</option><optgroup label="已上传文件">{uploads.map(f => <option key={f.file_id} value={`file:${f.file_id}`}>{f.filename}</option>)}</optgroup><optgroup label="运行产物">{artifacts.map(f => <option key={f.artifact_id} value={`artifact:${f.artifact_id}`}>{f.filename}</option>)}</optgroup></select></label>
            {artifactCursor && <button disabled={busy} onClick={() => void act(async () => {
              const page = await nexusFetch<Page<Artifact>>(`artifacts?limit=100&cursor=${encodeURIComponent(artifactCursor)}`);
              setArtifacts(current => [...current, ...page.items]); setArtifactCursor(page.next_cursor);
            })}>更多产物</button>}
            <label>保存名称<input value={filename} maxLength={255} onChange={event => setFilename(event.target.value)} /></label>
            <div className={styles.row}><button disabled={busy || !source || !filename.trim()} onClick={() => void act(saveFile)}>保存到项目</button>
              <button disabled={busy} onClick={() => fileInput.current?.click()}>上传新文件</button></div>
            <input ref={fileInput} type="file" hidden onChange={event => {
              const file = event.target.files?.[0]; event.target.value = "";
              if (file) void act(async () => {
                const uploaded = await uploadUserFile(file, crypto.randomUUID(), new AbortController().signal);
                setUploads(current => [...current.filter(f => f.file_id !== uploaded.file_id), uploaded]);
                setSource(`file:${uploaded.file_id}`); setFilename(uploaded.filename);
                setNotice("上传完成，请确认名称后保存到项目。");
              });
            }} />
          </div>
          {detailLoading ? <p role="status">正在读取项目内容…</p> : <>
            <ul className={styles.list}>{files.map(file => <li key={file.project_file_id} className={styles.file}>
              <span>{file.filename}<small>{(file.size_bytes / 1024).toFixed(1)} KB · {file.status === "ready" ? "已保存" : "保存未完成"}</small></span>
              {file.status === "ready" && <div className={styles.row}><a href={`/api/nexus/projects/${selected}/files/${file.project_file_id}/content`}>下载</a><button disabled={busy} onClick={() => void act(async () => {
                await nexusFetch(`projects/${selected}/files/${file.project_file_id}/materialize`, { method: "POST" }); setNotice("已生成受控本地副本。");
              })}>生成本地副本</button></div>}
            </li>)}</ul>
            {!files.length && <p>尚未保存项目文件。</p>}
            <h2>项目对话</h2>
            <button disabled={busy} onClick={() => void act(async () => {
              sessionKey.current ??= crypto.randomUUID();
              const session = await nexusFetch<Conversation>(`projects/${selected}/sessions`, { method: "POST", body: JSON.stringify({ idempotency_key: sessionKey.current, title: "项目对话" }) });
              router.push(`/c/${session.session_id}`);
            })}>新建项目对话</button>
            <ul className={styles.list}>{sessions.map(session => <li key={session.session_id}><Link href={`/c/${session.session_id}`}>{session.title ?? "未命名对话"}</Link></li>)}</ul>
            {hasMoreSessions && <button disabled={busy} onClick={() => void act(async () => {
              const page = await nexusFetch<Conversation[]>(`projects/${selected}/sessions?offset=${sessionOffset}`);
              setSessions(current => [...current, ...page.filter(item => !current.some(s => s.session_id === item.session_id))]);
              setSessionOffset(current => current + page.length); setHasMoreSessions(page.length === 100);
            })}>更多对话</button>}
          </>}
        </>}
      </section>
    </div>}
  </main>;
}
