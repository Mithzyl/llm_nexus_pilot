import { browserCsrfHeaders, nexusFetch, NexusApiError } from "./api";

export type UserFile = {
  file_id: string; filename: string; size_bytes: number;
  status: string; error: string | null; run_id: string | null;
  parse_summary?: { status?: string; warnings?: string[]; elapsed_ms?: number };
};

export const FILE_ACCEPT = ".txt,.md,.py,.js,.ts,.css,.json,.csv,.tsv,.yaml,.yml,.xml,.pdf,.docx,.xlsx,.pptx";
export const TEXT_FILE_ACCEPT = FILE_ACCEPT.split(",").filter((suffix) =>
  ![".pdf", ".docx", ".xlsx", ".pptx"].includes(suffix)).join(",");

export type FileUploadCapabilities = {
  accepted_extensions: string[];
  executor_status: "available" | "not_configured" | "unavailable" | "busy";
  max_file_size_bytes: number;
  unavailable_reason: string | null;
};

/** Read authenticated parser availability without exposing executor configuration to the browser. */
export function getFileUploadCapabilities(signal?: AbortSignal): Promise<FileUploadCapabilities> {
  return nexusFetch<FileUploadCapabilities>("files/capabilities", { signal });
}

/** Validate browser size and suffix early; the backend independently validates actual content. */
export function validateUploadFiles(files: readonly File[]): void {
  if (files.length > 10) throw new Error("每条消息最多添加 10 个文件。");
  for (const file of files) {
    const suffix = `.${file.name.split(".").at(-1)?.toLowerCase()}`;
    if (!FILE_ACCEPT.split(",").includes(suffix)) throw new Error(`暂不支持 ${file.name} 的格式。`);
    if (file.size === 0 || file.size > 1024 * 1024) throw new Error(`${file.name} 需为非空文件，且不超过 1 MiB。`);
  }
}

/** Check isolated-parser availability before reservation; the API independently checks it again. */
export async function uploadUserFile(file: File, key: string, signal: AbortSignal): Promise<UserFile> {
  validateUploadFiles([file]);
  const suffix = `.${file.name.split(".").at(-1)?.toLowerCase()}`;
  if (!TEXT_FILE_ACCEPT.split(",").includes(suffix)) {
    const capabilities = await getFileUploadCapabilities(signal);
    if (!capabilities.accepted_extensions.includes(suffix)) {
      throw new Error(capabilities.unavailable_reason ?? "服务器暂不支持此文件格式。");
    }
  }
  const bytes = await file.arrayBuffer();
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)))
    .map((byte) => byte.toString(16).padStart(2, "0")).join("");
  const reserved = await nexusFetch<UserFile>("files", {
    method: "POST", signal,
    body: JSON.stringify({ filename: file.name, size_bytes: file.size, content_hash: hash, idempotency_key: key }),
  });
  if (reserved.status === "ready") return reserved;
  const response = await fetch(`/api/nexus/files/${encodeURIComponent(reserved.file_id)}/content`, {
    method: "PUT", body: bytes, signal, credentials: "same-origin",
    headers: { "Content-Type": "application/octet-stream", ...browserCsrfHeaders() },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new NexusApiError(error?.detail ?? "文件上传失败，请在文件列表查看或删除后重试。", response.status);
  }
  return response.json() as Promise<UserFile>;
}

/** Read persisted attachment labels without trusting arbitrary message metadata as markup. */
export function messageFiles(metadata: Record<string, unknown> | null | undefined): UserFile[] {
  if (!Array.isArray(metadata?.files)) return [];
  return metadata.files.filter((entry): entry is UserFile => Boolean(entry &&
    typeof entry.file_id === "string" && typeof entry.filename === "string"));
}
