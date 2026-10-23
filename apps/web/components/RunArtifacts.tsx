"use client";

import { useEffect, useState } from "react";
import { nexusFetch } from "../lib/api";
import type { CursorPage } from "../lib/types";

type Artifact = { artifact_id: string; filename: string; size_bytes: number };

/** Show committed Python outputs, including files saved before a later workflow failure. */
export function RunArtifacts({ runId }: { runId: string }) {
  const [files, setFiles] = useState<Artifact[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    nexusFetch<CursorPage<Artifact>>(
      `artifacts?run_id=${encodeURIComponent(runId)}&artifact_type=python_output&limit=50`,
      { signal: controller.signal },
    ).then((page) => {
      if (controller.signal.aborted) return;
      setFiles(page.items); setCursor(page.next_cursor); setError(false);
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [runId, revision]);

  /** Load the next server-owned page without replacing previously visible files. */
  async function loadMore() {
    setLoading(true); setError(false);
    try {
      const page = await nexusFetch<CursorPage<Artifact>>(
        `artifacts?run_id=${encodeURIComponent(runId)}&artifact_type=python_output&limit=50&cursor=${encodeURIComponent(cursor!)}`,
      );
      setFiles((previous) => [...previous, ...page.items.filter(
        (item) => !previous.some((old) => old.artifact_id === item.artifact_id),
      )]);
      setCursor(page.next_cursor);
    } catch { setError(true); } finally { setLoading(false); }
  }

  /** Download through the authenticated proxy; never save an error response as a file. */
  async function download(file: Artifact) {
    setDownloading(file.artifact_id); setDownloadError(null);
    try {
      const response = await fetch(`/api/nexus/artifacts/${encodeURIComponent(file.artifact_id)}/content`, {
        credentials: "same-origin", cache: "no-store",
      });
      if (!response.ok) throw new Error("Download failed");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = file.filename;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setDownloadError(file.filename); } finally { setDownloading(null); }
  }

  if (!loading && !error && files.length === 0) return null;
  return <section className="run-artifacts" aria-label="生成的文件">
    {files.length > 0 && <p>生成的文件</p>}
    <ul>{files.map((file) => <li key={file.artifact_id}>
      <span className="run-artifact-name">{file.filename}</span>
      <small>{file.size_bytes < 1024 ? `${file.size_bytes} B` : `${(file.size_bytes / 1024).toFixed(1)} KB`}</small>
      <button type="button" disabled={downloading !== null} onClick={() => void download(file)}
        aria-label={`下载 ${file.filename}`}>
        {downloading === file.artifact_id ? "下载中…" : "下载"}
      </button>
    </li>)}</ul>
    {loading && <small role="status">正在读取生成文件…</small>}
    {error && <p role="alert">文件列表读取失败。<button type="button" onClick={() => {
      if (files.length && cursor) void loadMore(); else { setLoading(true); setRevision(revision + 1); }
    }}>重试</button></p>}
    {cursor && !error && <button type="button" disabled={loading} onClick={() => void loadMore()}>更多文件</button>}
    {downloadError && <p role="alert">无法下载 {downloadError}，请重试；文件可能已不可用。</p>}
  </section>;
}
