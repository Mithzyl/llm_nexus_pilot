import type { UserFile } from "./user-files";

export type ProjectSummary = {
  project_id: string;
  project_name: string;
};

export type ProjectFile = {
  project_file_id: string;
  project_id: string;
  filename: string;
  source_type: string;
  source_id: string;
  size_bytes: number;
  mime_type: string;
  status: string;
  created_at: string;
};

export type ProjectConversation = {
  session_id: string;
  title: string | null;
  status: string;
  created_at: string;
  updated_at: string;
};

export type ProjectArtifact = {
  artifact_id: string;
  run_id: string;
  task_id: string | null;
  artifact_type: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  created_at: string;
};

export type ProjectArtifactPage = {
  items: ProjectArtifact[];
  next_cursor: string | null;
};

export type ProjectSourceKind = "upload" | "artifact";

export type ProjectSource = {
  kind: ProjectSourceKind;
  id: string;
  filename: string;
  sizeBytes: number | null;
  mimeType: string | null;
  createdAt: string | null;
};

/** Build the shareable project workspace address used by project and conversation navigation. */
export function projectWorkspacePath(projectId: string | null): string {
  return projectId ? `/projects?project=${encodeURIComponent(projectId)}` : "/projects";
}

/** Read only one bounded project identifier from browser search parameters. */
export function projectIdFromSearch(search: string): string | null {
  const identifier = new URLSearchParams(search).get("project")?.trim() ?? "";
  return identifier && identifier.length <= 128 ? identifier : null;
}

/** Convert an uploaded file into a searchable add-file source. */
export function projectSourceFromUpload(file: UserFile): ProjectSource {
  return {
    kind: "upload",
    id: file.file_id,
    filename: file.filename,
    sizeBytes: file.size_bytes,
    mimeType: null,
    createdAt: null,
  };
}

/** Convert a Run Artifact into the same source projection without inventing display metadata. */
export function projectSourceFromArtifact(artifact: ProjectArtifact): ProjectSource {
  return {
    kind: "artifact",
    id: artifact.artifact_id,
    filename: artifact.filename,
    sizeBytes: artifact.size_bytes,
    mimeType: artifact.mime_type,
    createdAt: artifact.created_at,
  };
}

/** Match a filename case-insensitively while retaining the server-provided source order. */
export function filterProjectSources(
  sources: readonly ProjectSource[],
  query: string,
): ProjectSource[] {
  const normalized = query.trim().toLocaleLowerCase();
  return normalized
    ? sources.filter((source) => source.filename.toLocaleLowerCase().includes(normalized))
    : [...sources];
}

/** Merge cursor pages or post-write rows without duplicating stable resource identifiers. */
export function mergeProjectItems<T>(
  current: readonly T[],
  incoming: readonly T[],
  identifier: (item: T) => string,
): T[] {
  const merged = new Map(current.map((item) => [identifier(item), item]));
  for (const item of incoming) merged.set(identifier(item), item);
  return [...merged.values()];
}

/** Format file size for scanning without leaking byte precision into the interface. */
export function formatProjectFileSize(sizeBytes: number | null): string {
  if (sizeBytes === null || !Number.isFinite(sizeBytes) || sizeBytes < 0) return "大小未知";
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Return a stable source identity for selecting and clearing add-file state. */
export function projectSourceIdentity(source: ProjectSource): string {
  return `${source.kind}:${source.id}`;
}

/** Map request failures to an actionable message without exposing raw service errors. */
export function projectRequestErrorMessage(
  error: unknown,
  action: string,
): string {
  const status = typeof error === "object" && error !== null && "status" in error
    ? Number((error as { status?: unknown }).status)
    : null;
  if (status === 401) return "登录已失效，请重新登录后重试。";
  if (status === 403 || status === 404) return "项目不存在或无权访问。";
  if (status === 409) return `${action}时资源已发生变化，请重新读取后重试。`;
  if (status === 413) return "文件超过当前项目允许的大小。";
  if (status && status >= 500) return `${action}暂时不可用，请稍后重试。`;
  return `${action}失败，请重试。`;
}
