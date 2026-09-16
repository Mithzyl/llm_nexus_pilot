"use client";

import { useState } from "react";
import { nexusFetch } from "../../lib/api";
import type { UserFile } from "../../lib/user-files";

/** Let users inspect interrupted uploads and explicitly delete their private stored files. */
export function FileLibrary({ disabled }: { disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<UserFile[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  /** Query current backend lifecycle facts whenever the list is opened or refreshed. */
  async function refresh() {
    setBusy(true); setError("");
    try { setFiles(await nexusFetch<UserFile[]>("files")); }
    catch (error) { setError(error instanceof Error ? error.message : "无法读取文件列表。"); }
    finally { setBusy(false); }
  }

  /** Confirm deletion because it revokes future use in messages that reference this file. */
  async function remove(file: UserFile) {
    if (!window.confirm(`删除“${file.filename}”？后续回答将不能再读取此文件。`)) return;
    setBusy(true); setError("");
    try { await nexusFetch(`files/${file.file_id}`, { method: "DELETE" }); await refresh(); }
    catch (error) { setError(error instanceof Error ? error.message : "删除失败，请重试。"); }
    finally { setBusy(false); }
  }

  const labels: Record<string, string> = {
    reserved: "待上传", uploading: "上传或解析中", ready: "已保存", failed: "上传失败", deleting: "待清理",
  };
  return <div className="file-library">
    <button type="button" className="composer-quiet-button" disabled={disabled}
      aria-expanded={open} onClick={() => { setOpen(!open); if (!open) void refresh(); }}>已上传文件</button>
    {open && <section className="file-library-panel" aria-label="已上传文件"
      onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}>
      <header><strong>我的文件</strong><button type="button" disabled={busy} onClick={() => void refresh()}>刷新</button>
        <button type="button" aria-label="关闭文件列表" onClick={() => setOpen(false)}>×</button></header>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">正在处理…</p>}
      {!busy && !error && files.length === 0 && <p>还没有上传文件。</p>}
      <ul>{files.map((file) => <li key={file.file_id}>
        <span><strong>{file.filename}</strong><small>{labels[file.status] ?? "状态未知"}
          {file.parse_summary?.status === "partial" ? " · 部分提取，图片和复杂排版可能遗漏" : ""}</small></span>
        {file.status === "ready" && <a href={`/api/nexus/files/${file.file_id}/content`}>下载</a>}
        <button type="button" disabled={busy} onClick={() => void remove(file)}>删除</button>
      </li>)}</ul>
    </section>}
  </div>;
}
