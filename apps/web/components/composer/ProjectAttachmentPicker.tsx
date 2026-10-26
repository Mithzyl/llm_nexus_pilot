"use client";

import { useEffect, useRef, useState } from "react";
import { nexusFetch } from "../../lib/api";

export type SelectedProjectFile = {
  project_file_id: string; filename: string; status: string; requestKey: string;
};

/** Select metadata only; the send operation prepares the chosen snapshot for file tools. */
export function ProjectAttachmentPicker({ projectId, selected, disabled, onSelect }: {
  projectId: string; selected: SelectedProjectFile[]; disabled: boolean;
  onSelect: (file: SelectedProjectFile) => void;
}) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<SelectedProjectFile[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  /** Refresh the selected project's inventory and cancel stale directory requests. */
  async function refresh() {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try { setFiles(await nexusFetch<SelectedProjectFile[]>(`projects/${projectId}/files`, { signal: controller.signal })); }
    catch (error) { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : "无法读取项目文件"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }

  return <div className="file-library" onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>
    <button type="button" className="composer-quiet-button" disabled={disabled} aria-expanded={open}
      onClick={() => { setOpen(!open); if (!open) void refresh(); }}>项目文件</button>
    {open && <section className="file-library-panel" aria-label="选择项目文件">
      <header><strong>选择项目文件</strong><button type="button" aria-label="关闭项目文件" onClick={() => setOpen(false)}>×</button></header>
      {busy && <p role="status">正在读取…</p>}{error && <p role="alert">{error}</p>}
      {!busy && !error && files.length === 0 && <p>项目中还没有文件。</p>}
      <ul>{files.map(file => <li key={file.project_file_id}><span>{file.filename}</span>
        <button type="button" disabled={disabled || file.status !== "ready" || selected.some(item => item.project_file_id === file.project_file_id)}
          onClick={() => { onSelect({ ...file, requestKey: crypto.randomUUID() }); setOpen(false); }}>附加</button>
      </li>)}</ul>
    </section>}
  </div>;
}
