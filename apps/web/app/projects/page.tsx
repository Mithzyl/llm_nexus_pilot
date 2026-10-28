"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { nexusFetch } from "../../lib/api";
import {
  filterProjectSources,
  formatProjectFileSize,
  mergeProjectItems,
  projectIdFromSearch,
  projectRequestErrorMessage,
  projectSourceFromArtifact,
  projectSourceFromUpload,
  projectSourceIdentity,
  projectWorkspacePath,
  type ProjectArtifact,
  type ProjectArtifactPage,
  type ProjectConversation,
  type ProjectFile,
  type ProjectSummary,
} from "../../lib/project-workspace";
import { uploadUserFile, type UserFile } from "../../lib/user-files";
import styles from "./projects.module.css";

type LoadState = "idle" | "loading" | "ready" | "error";
type ProjectState = "idle" | "loading" | "ready" | "unavailable" | "error";
type ProjectDialog = "create" | "rename" | null;
type SourcePanel = "new" | "uploads" | "artifacts";

/** Format persisted project timestamps without changing the server-provided instant. */
function projectDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** Return a compact extension badge while keeping the complete filename available elsewhere. */
function fileTypeLabel(filename: string): string {
  const extension = filename.split(".").at(-1);
  return extension && extension !== filename ? extension.slice(0, 5).toUpperCase() : "FILE";
}

/** Render an actionable load failure without exposing raw API diagnostics. */
function RegionFailure({
  message,
  onRetry,
  label,
}: {
  message: string;
  onRetry: () => void;
  label: string;
}) {
  return (
    <div className={styles.regionFailure} role="alert">
      <p>{message}</p>
      <button type="button" onClick={onRetry}>{label}</button>
    </div>
  );
}

/** Manage fixed project copies and project conversations without editing or deleting source data. */
export default function ProjectsPage() {
  const router = useRouter();
  const [isDark, setIsDark] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectsState, setProjectsState] = useState<LoadState>("loading");
  const [projectsError, setProjectsError] = useState("");
  const [projectsCursor, setProjectsCursor] = useState<string | null>(null);
  const [isLoadingMoreProjects, setIsLoadingMoreProjects] = useState(false);
  const [projectId, setProjectId] = useState("");
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [projectState, setProjectState] = useState<ProjectState>("idle");
  const [projectError, setProjectError] = useState("");
  const [projectReload, setProjectReload] = useState(0);
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [filesState, setFilesState] = useState<LoadState>("idle");
  const [filesError, setFilesError] = useState("");
  const [conversations, setConversations] = useState<ProjectConversation[]>([]);
  const [conversationsState, setConversationsState] = useState<LoadState>("idle");
  const [conversationsError, setConversationsError] = useState("");
  const [conversationAppendError, setConversationAppendError] = useState("");
  const [conversationOffset, setConversationOffset] = useState(0);
  const [hasMoreConversations, setHasMoreConversations] = useState(false);
  const [isLoadingMoreConversations, setIsLoadingMoreConversations] = useState(false);
  const [notice, setNotice] = useState("");
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [projectDialog, setProjectDialog] = useState<ProjectDialog>(null);
  const [newProjectName, setNewProjectName] = useState("");
  const [projectDialogError, setProjectDialogError] = useState("");
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [isRenamingProject, setIsRenamingProject] = useState(false);
  const [isCreatingConversation, setIsCreatingConversation] = useState(false);
  const [conversationActionError, setConversationActionError] = useState("");
  const [fileActionId, setFileActionId] = useState<string | null>(null);
  const [fileActionError, setFileActionError] = useState("");
  const [isAddFileOpen, setIsAddFileOpen] = useState(false);
  const [sourceMode, setSourceMode] = useState<SourcePanel>("new");
  const [uploads, setUploads] = useState<UserFile[]>([]);
  const [uploadsState, setUploadsState] = useState<LoadState>("idle");
  const [uploadsError, setUploadsError] = useState("");
  const [artifacts, setArtifacts] = useState<ProjectArtifact[]>([]);
  const [artifactsState, setArtifactsState] = useState<LoadState>("idle");
  const [artifactsError, setArtifactsError] = useState("");
  const [artifactCursor, setArtifactCursor] = useState<string | null>(null);
  const [isLoadingMoreArtifacts, setIsLoadingMoreArtifacts] = useState(false);
  const [sourceQuery, setSourceQuery] = useState("");
  const [selectedSourceKey, setSelectedSourceKey] = useState("");
  const [saveFilename, setSaveFilename] = useState("");
  const [sourceActionError, setSourceActionError] = useState("");
  const [sourceActionNotice, setSourceActionNotice] = useState("");
  const [isSavingProjectFile, setIsSavingProjectFile] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  const projectRequestController = useRef<AbortController | null>(null);
  const projectFilesController = useRef<AbortController | null>(null);
  const projectConversationsController = useRef<AbortController | null>(null);
  const currentProjectIdValue = useRef(projectId);
  const sourceSaveIdentity = useRef<{ input: string; key: string } | null>(null);
  const sessionKey = useRef<string | null>(null);
  const addFileFlowId = useRef(0);
  const uploadRequestId = useRef(0);
  const saveRequestId = useRef(0);
  const renameRequestId = useRef(0);
  const conversationCreateRequestId = useRef(0);
  const fileActionRequestId = useRef(0);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const activeDialog = useRef<HTMLElement | null>(null);
  const dialogReturnFocus = useRef<HTMLElement | null>(null);
  /** Advance request identities without scheduling React state updates. */
  const invalidateAddFileRequests = useCallback(() => {
    addFileFlowId.current += 1;
    uploadRequestId.current += 1;
    saveRequestId.current += 1;
  }, []);
  /** Invalidate add-file writes and uploads that belong to a closed dialog. */
  const invalidateAddFileOperations = useCallback(() => {
    invalidateAddFileRequests();
    setIsUploading(false);
    setIsSavingProjectFile(false);
  }, [invalidateAddFileRequests]);
  /** Close the project-name dialog and restore focus to the action that opened it. */
  const closeProjectDialog = useCallback(() => {
    setProjectDialog(null);
    window.requestAnimationFrame(() => dialogReturnFocus.current?.focus());
  }, []);
  /** Close add-file, invalidate pending work, and restore focus to its opener. */
  const closeAddFileDialog = useCallback(() => {
    invalidateAddFileOperations();
    setIsAddFileOpen(false);
    window.requestAnimationFrame(() => dialogReturnFocus.current?.focus());
  }, [invalidateAddFileOperations]);

  const loadProjects = useCallback(async (cursor?: string, append = false) => {
    if (!append) {
      setProjectsState("loading");
    } else {
      setIsLoadingMoreProjects(true);
    }
    setProjectsError("");
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (cursor) query.set("cursor", cursor);
      const page = await nexusFetch<{
        items: ProjectSummary[];
        next_cursor: string | null;
      }>(`projects?${query.toString()}`);
      setProjects((current) => append
        ? mergeProjectItems(current, page.items, (item) => item.project_id)
        : page.items);
      setProjectsCursor(page.next_cursor);
      setProjectsState("ready");
    } catch (error) {
      setProjectsError(projectRequestErrorMessage(error, "读取项目列表"));
      if (!append) setProjectsState("error");
    } finally {
      if (append) setIsLoadingMoreProjects(false);
    }
  }, []);

  const loadProjectFiles = useCallback(async (selectedProjectId: string) => {
    projectFilesController.current?.abort();
    const controller = new AbortController();
    projectFilesController.current = controller;
    setFilesState("loading");
    setFilesError("");
    try {
      const savedFiles = await nexusFetch<ProjectFile[]>(
        `projects/${encodeURIComponent(selectedProjectId)}/files`,
        { signal: controller.signal },
      );
      if (
        projectFilesController.current !== controller
        || currentProjectIdValue.current !== selectedProjectId
      ) return;
      setFiles(savedFiles);
      setFilesState("ready");
    } catch (error) {
      if (
        controller.signal.aborted
        || projectFilesController.current !== controller
        || currentProjectIdValue.current !== selectedProjectId
      ) return;
      setFilesError(projectRequestErrorMessage(error, "读取项目文件"));
      setFilesState("error");
    }
  }, []);

  const loadProjectConversations = useCallback(async (
    selectedProjectId: string,
    offset = 0,
    append = false,
  ) => {
    projectConversationsController.current?.abort();
    const controller = new AbortController();
    projectConversationsController.current = controller;
    if (!append) {
      setConversationsState("loading");
      setConversationsError("");
      setConversationAppendError("");
      setIsLoadingMoreConversations(false);
    } else {
      setConversationAppendError("");
      setIsLoadingMoreConversations(true);
    }
    try {
      const page = await nexusFetch<ProjectConversation[]>(
        `projects/${encodeURIComponent(selectedProjectId)}/sessions?offset=${offset}`,
        { signal: controller.signal },
      );
      if (
        projectConversationsController.current !== controller
        || currentProjectIdValue.current !== selectedProjectId
      ) return;
      setConversations((current) => append
        ? mergeProjectItems(current, page, (item) => item.session_id)
        : page);
      setConversationOffset(offset + page.length);
      setHasMoreConversations(page.length === 100);
      setConversationsState("ready");
      setConversationsError("");
      setConversationAppendError("");
    } catch (error) {
      if (
        controller.signal.aborted
        || projectConversationsController.current !== controller
        || currentProjectIdValue.current !== selectedProjectId
      ) return;
      const message = projectRequestErrorMessage(
        error,
        append ? "继续读取项目对话" : "读取项目对话",
      );
      if (append) {
        setConversationAppendError(message);
      } else {
        setConversationsError(message);
        setConversationsState("error");
      }
    } finally {
      if (
        projectConversationsController.current === controller
        && currentProjectIdValue.current === selectedProjectId
      ) {
        setIsLoadingMoreConversations(false);
      }
    }
  }, []);

  const loadUploads = useCallback(async () => {
    setUploadsState("loading");
    setUploadsError("");
    try {
      const files = await nexusFetch<UserFile[]>("files");
      setUploads(files.filter((file) => file.status === "ready"));
      setUploadsState("ready");
    } catch (error) {
      setUploadsError(projectRequestErrorMessage(error, "读取已上传文件"));
      setUploadsState("error");
    }
  }, []);

  const loadArtifacts = useCallback(async (cursor?: string, append = false) => {
    if (!append) {
      setArtifactsState("loading");
      setArtifactsError("");
    } else {
      setIsLoadingMoreArtifacts(true);
    }
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (cursor) query.set("cursor", cursor);
      const page = await nexusFetch<ProjectArtifactPage>(`artifacts?${query.toString()}`);
      setArtifacts((current) => append
        ? mergeProjectItems(current, page.items, (item) => item.artifact_id)
        : page.items);
      setArtifactCursor(page.next_cursor);
      setArtifactsState("ready");
    } catch (error) {
      setArtifactsError(projectRequestErrorMessage(error, append ? "继续读取运行产物" : "读取运行产物"));
      if (!append) setArtifactsState("error");
    } finally {
      setIsLoadingMoreArtifacts(false);
    }
  }, []);

  useEffect(() => {
    // Hydration-safe initialization reads browser-only state after the first render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsDark(window.localStorage.getItem("nexuspilot-theme") === "dark");
    void loadProjects();
    const initialProjectId = projectIdFromSearch(window.location.search);
    if (initialProjectId) {
      currentProjectIdValue.current = initialProjectId;
      setProjectId(initialProjectId);
    }
    const restoreFromHistory = () => {
      const restored = projectIdFromSearch(window.location.search) ?? "";
      setNotice("");
      currentProjectIdValue.current = restored;
      setProjectId(restored);
      setIsSidebarOpen(false);
    };
    window.addEventListener("popstate", restoreFromHistory);
    return () => {
      window.removeEventListener("popstate", restoreFromHistory);
      projectRequestController.current?.abort();
      projectFilesController.current?.abort();
      projectConversationsController.current?.abort();
    };
  }, [loadProjects]);

  useEffect(() => {
    projectRequestController.current?.abort();
    projectFilesController.current?.abort();
    projectConversationsController.current?.abort();
    invalidateAddFileRequests();
    renameRequestId.current += 1;
    conversationCreateRequestId.current += 1;
    fileActionRequestId.current += 1;
    if (!projectId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setProject(null);
      setProjectState("idle");
      setProjectError("");
      setFiles([]);
      setConversations([]);
      setFilesState("idle");
      setConversationsState("idle");
      setConversationAppendError("");
      setConversationOffset(0);
      setHasMoreConversations(false);
      setIsLoadingMoreConversations(false);
      return;
    }
    const controller = new AbortController();
    projectRequestController.current = controller;
    setProject(null);
    setProjectState("loading");
    setProjectError("");
    setFiles([]);
    setConversations([]);
    setFilesState("idle");
    setConversationsState("idle");
    setConversationsError("");
    setConversationAppendError("");
    setConversationOffset(0);
    setHasMoreConversations(false);
    setIsLoadingMoreConversations(false);
    setSelectedSourceKey("");
    setSaveFilename("");
    setSourceActionError("");
    setSourceActionNotice("");
    setFileActionError("");
    setFileActionId(null);
    setConversationActionError("");
    setIsCreatingConversation(false);
    setIsRenamingProject(false);
    setIsUploading(false);
    setIsSavingProjectFile(false);
    setProjectDialogError("");
    setRenameDraft("");
    sourceSaveIdentity.current = null;
    sessionKey.current = null;
    setIsAddFileOpen(false);
    setProjectDialog(null);
    void nexusFetch<ProjectSummary>(`projects/${encodeURIComponent(projectId)}`, {
      signal: controller.signal,
    }).then((selectedProject) => {
      if (projectRequestController.current !== controller) return;
      setProject(selectedProject);
      setRenameDraft(selectedProject.project_name);
      setProjectState("ready");
      setProjects((current) => mergeProjectItems(
        current,
        [selectedProject],
        (item) => item.project_id,
      ));
    }).catch((error) => {
      if (controller.signal.aborted || projectRequestController.current !== controller) return;
      const status = typeof error === "object" && error !== null && "status" in error
        ? Number((error as { status?: unknown }).status)
        : null;
      setProjectState(status === 403 || status === 404 ? "unavailable" : "error");
      setProjectError(projectRequestErrorMessage(error, "读取项目"));
    });
    return () => controller.abort();
  }, [projectId, projectReload, invalidateAddFileRequests]);

  useEffect(() => {
    if (projectState !== "ready" || !projectId) return;
    // These callbacks only synchronize external project resources with the selected address.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadProjectFiles(projectId);
    void loadProjectConversations(projectId);
  }, [projectId, projectState, loadProjectFiles, loadProjectConversations]);

  useEffect(() => {
    if (!projectDialog && !isAddFileOpen) return;
    const manageDialogKeyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        closeProjectDialog();
        closeAddFileDialog();
        return;
      }
      if (event.key !== "Tab" || !activeDialog.current) return;
      const focusable = Array.from(activeDialog.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      ));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && (document.activeElement === first || !activeDialog.current.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !activeDialog.current.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", manageDialogKeyboard);
    return () => window.removeEventListener("keydown", manageDialogKeyboard);
  }, [projectDialog, isAddFileOpen, closeProjectDialog, closeAddFileDialog]);

  /** Toggle the same local theme used by the conversation workspace. */
  function toggleTheme() {
    const next = !isDark;
    setIsDark(next);
    window.localStorage.setItem("nexuspilot-theme", next ? "dark" : "light");
  }

  /** Navigate to a selected project and create a browser history entry. */
  function openProject(selectedProject: ProjectSummary) {
    if (!projectId || projectId !== selectedProject.project_id) {
      setNotice("");
      window.history.pushState(null, "", projectWorkspacePath(selectedProject.project_id));
      currentProjectIdValue.current = selectedProject.project_id;
      setProjectId(selectedProject.project_id);
    }
    setIsSidebarOpen(false);
  }

  /** Return to the project index and clear an inaccessible project address. */
  function returnToProjects() {
    setNotice("");
    window.history.pushState(null, "", "/projects");
    currentProjectIdValue.current = "";
    setProjectId("");
    setIsSidebarOpen(false);
  }

  /** Open the create-project form without leaving an expanded form in the sidebar. */
  function openCreateProject() {
    dialogReturnFocus.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setNewProjectName("");
    setProjectDialogError("");
    setProjectDialog("create");
    setIsSidebarOpen(false);
  }

  /** Start renaming from the current server-confirmed project name. */
  function openRenameProject() {
    if (!project) return;
    dialogReturnFocus.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setRenameDraft(project.project_name);
    setProjectDialogError("");
    setProjectDialog("rename");
  }

  /** Create one project and select it only after the server confirms the write. */
  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const projectName = newProjectName.trim();
    if (!projectName || isCreatingProject) return;
    setIsCreatingProject(true);
    setProjectDialogError("");
    try {
      const created = await nexusFetch<ProjectSummary>("projects", {
        method: "POST",
        body: JSON.stringify({ project_name: projectName }),
      });
      setProjects((current) => mergeProjectItems(
        current,
        [created],
        (item) => item.project_id,
      ));
      setProjectDialog(null);
      openProject(created);
      setNotice(`已创建项目“${created.project_name}”。`);
    } catch (error) {
      setProjectDialogError(projectRequestErrorMessage(error, "创建项目"));
    } finally {
      setIsCreatingProject(false);
    }
  }

  /** Rename only after explicit confirmation and keep the draft when the write fails. */
  async function renameProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextName = renameDraft.trim();
    if (!project || !nextName || isRenamingProject) return;
    const targetProjectId = project.project_id;
    const requestId = renameRequestId.current + 1;
    renameRequestId.current = requestId;
    setIsRenamingProject(true);
    setProjectDialogError("");
    try {
      const updated = await nexusFetch<ProjectSummary>(
        `projects/${encodeURIComponent(targetProjectId)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ project_name: nextName }),
        },
      );
      if (
        currentProjectIdValue.current === targetProjectId
        && renameRequestId.current === requestId
      ) {
        setProjects((current) => current.map((item) => (
          item.project_id === updated.project_id ? updated : item
        )));
        setProject(updated);
        setProjectDialog(null);
        setNotice("项目名称已更新。");
      }
    } catch (error) {
      if (
        currentProjectIdValue.current === targetProjectId
        && renameRequestId.current === requestId
      ) {
        setProjectDialogError(projectRequestErrorMessage(error, "重命名项目"));
      }
    } finally {
      if (
        currentProjectIdValue.current === targetProjectId
        && renameRequestId.current === requestId
      ) {
        setIsRenamingProject(false);
      }
    }
  }

  /** Reset add-file selections when the chosen source or project changes. */
  function changeSourceMode(mode: SourcePanel) {
    setSourceMode(mode);
    setSelectedSourceKey("");
    setSaveFilename("");
    setSourceQuery("");
    setSourceActionError("");
    setSourceActionNotice("");
    sourceSaveIdentity.current = null;
    if (mode === "uploads" && uploadsState === "idle") void loadUploads();
    if (mode === "artifacts" && artifactsState === "idle") void loadArtifacts();
  }

  /** Open a clean add-file flow while keeping failed post-save input inside the open dialog. */
  function openAddFile() {
    if (!project) return;
    invalidateAddFileOperations();
    dialogReturnFocus.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setIsAddFileOpen(true);
    setSourceMode("new");
    setSourceQuery("");
    setSelectedSourceKey("");
    setSaveFilename("");
    setSourceActionError("");
    setSourceActionNotice("");
    sourceSaveIdentity.current = null;
  }

  /** Upload a new source and require an explicit second save into the current project. */
  async function uploadProjectSource(file: File) {
    if (!project) return;
    const targetProjectId = project.project_id;
    const flowId = addFileFlowId.current;
    const requestId = uploadRequestId.current + 1;
    uploadRequestId.current = requestId;
    setIsUploading(true);
    setSourceActionError("");
    setSourceActionNotice("");
    try {
      const uploaded = await uploadUserFile(
        file,
        crypto.randomUUID(),
        new AbortController().signal,
      );
      if (
        currentProjectIdValue.current !== targetProjectId
        || addFileFlowId.current !== flowId
        || uploadRequestId.current !== requestId
      ) return;
      setUploads((current) => mergeProjectItems(current, [uploaded], (item) => item.file_id));
      setUploadsState("ready");
      const source = projectSourceFromUpload(uploaded);
      setSelectedSourceKey(projectSourceIdentity(source));
      setSaveFilename(uploaded.filename);
      setSourceMode("uploads");
      setSourceActionNotice("文件已上传并选中。请确认名称，然后保存固定副本到当前项目。");
      sourceSaveIdentity.current = null;
    } catch (error) {
      if (
        currentProjectIdValue.current === targetProjectId
        && addFileFlowId.current === flowId
        && uploadRequestId.current === requestId
      ) {
        setSourceActionError(projectRequestErrorMessage(error, "上传文件"));
      }
    } finally {
      if (
        currentProjectIdValue.current === targetProjectId
        && addFileFlowId.current === flowId
        && uploadRequestId.current === requestId
      ) {
        setIsUploading(false);
      }
    }
  }

  /** Persist one selected source as an independent project copy with retry-stable identity. */
  async function saveProjectFile() {
    const source = availableSources.find(
      (item) => projectSourceIdentity(item) === selectedSourceKey,
    );
    const filename = saveFilename.trim();
    if (!project || !source || !filename || isSavingProjectFile) return;
    const targetProjectId = project.project_id;
    const flowId = addFileFlowId.current;
    const requestId = saveRequestId.current + 1;
    saveRequestId.current = requestId;
    const input = JSON.stringify({ projectId: targetProjectId, selectedSourceKey, filename });
    let identity = sourceSaveIdentity.current?.input === input
      ? sourceSaveIdentity.current.key
      : null;
    if (!identity) {
      const storageKey = `nexus:project-save:${project.project_id}:${source.kind}:${source.id}:${encodeURIComponent(filename)}`;
      try {
        identity = sessionStorage.getItem(storageKey) ?? crypto.randomUUID();
        sessionStorage.setItem(storageKey, identity);
      } catch {
        setSourceActionError("浏览器无法保存重复请求标识，请检查站点存储权限后重试。");
        return;
      }
      sourceSaveIdentity.current = { input, key: identity };
    }

    setIsSavingProjectFile(true);
    setSourceActionError("");
    setSourceActionNotice("");
    try {
      const saved = await nexusFetch<ProjectFile>(
        `projects/${encodeURIComponent(targetProjectId)}/files`,
        {
          method: "POST",
          body: JSON.stringify({
            ...(source.kind === "upload"
              ? { file_id: source.id }
              : { artifact_id: source.id }),
            filename,
            idempotency_key: identity,
          }),
        },
      );
      if (currentProjectIdValue.current === targetProjectId) {
        if (
          addFileFlowId.current === flowId
          && saveRequestId.current === requestId
        ) {
          setFiles((current) => mergeProjectItems(
            current,
            [saved],
            (item) => item.project_file_id,
          ));
          setFilesState("ready");
          setIsAddFileOpen(false);
          setNotice(`“${saved.filename}”已保存到项目。`);
          sourceSaveIdentity.current = null;
        }
        void loadProjectFiles(targetProjectId);
      }
    } catch (error) {
      if (
        currentProjectIdValue.current === targetProjectId
        && addFileFlowId.current === flowId
        && saveRequestId.current === requestId
      ) {
        setSourceActionError(projectRequestErrorMessage(error, "保存项目文件"));
      }
    } finally {
      if (
        currentProjectIdValue.current === targetProjectId
        && addFileFlowId.current === flowId
        && saveRequestId.current === requestId
      ) {
        setIsSavingProjectFile(false);
      }
    }
  }

  /** Create one project conversation with a stable key so response loss does not duplicate it. */
  async function createProjectConversation() {
    if (!project || isCreatingConversation) return;
    const targetProjectId = project.project_id;
    const requestId = conversationCreateRequestId.current + 1;
    conversationCreateRequestId.current = requestId;
    setIsCreatingConversation(true);
    setConversationActionError("");
    sessionKey.current ??= crypto.randomUUID();
    try {
      const session = await nexusFetch<ProjectConversation>(
        `projects/${encodeURIComponent(targetProjectId)}/sessions`,
        {
          method: "POST",
          body: JSON.stringify({
            idempotency_key: sessionKey.current,
            title: "项目对话",
          }),
        },
      );
      if (
        currentProjectIdValue.current !== targetProjectId
        || conversationCreateRequestId.current !== requestId
      ) return;
      setConversations((current) => mergeProjectItems(
        current,
        [session],
        (item) => item.session_id,
      ));
      router.push(`/c/${encodeURIComponent(session.session_id)}`);
    } catch (error) {
      if (
        currentProjectIdValue.current === targetProjectId
        && conversationCreateRequestId.current === requestId
      ) {
        setConversationActionError(projectRequestErrorMessage(error, "新建项目对话"));
      }
    } finally {
      if (
        currentProjectIdValue.current === targetProjectId
        && conversationCreateRequestId.current === requestId
      ) {
        setIsCreatingConversation(false);
      }
    }
  }

  /** Materialize one saved project copy only when the server-side host directory is configured. */
  async function materializeProjectFile(file: ProjectFile) {
    if (!project || fileActionId) return;
    const targetProjectId = project.project_id;
    const requestId = fileActionRequestId.current + 1;
    fileActionRequestId.current = requestId;
    setFileActionId(file.project_file_id);
    setFileActionError("");
    try {
      await nexusFetch(
        `projects/${encodeURIComponent(targetProjectId)}/files/${encodeURIComponent(file.project_file_id)}/materialize`,
        { method: "POST", body: JSON.stringify({}) },
      );
      if (
        currentProjectIdValue.current === targetProjectId
        && fileActionRequestId.current === requestId
      ) {
        setNotice(`已为“${file.filename}”生成本地固定副本。`);
      }
    } catch (error) {
      if (
        currentProjectIdValue.current === targetProjectId
        && fileActionRequestId.current === requestId
      ) {
        setFileActionError(projectRequestErrorMessage(error, "生成本地固定副本"));
      }
    } finally {
      if (
        currentProjectIdValue.current === targetProjectId
        && fileActionRequestId.current === requestId
      ) {
        setFileActionId(null);
      }
    }
  }

  const uploadSources = uploads.map(projectSourceFromUpload);
  const artifactSources = artifacts.map(projectSourceFromArtifact);
  const availableSources = sourceMode === "uploads"
    ? filterProjectSources(uploadSources, sourceQuery)
    : sourceMode === "artifacts"
      ? filterProjectSources(artifactSources, sourceQuery)
      : [];
  const selectedSource = [...uploadSources, ...artifactSources].find(
    (source) => projectSourceIdentity(source) === selectedSourceKey,
  ) ?? null;

  return (
    <main className={`${styles.page} ${isDark ? styles.themeDark : ""}`}>
      <header className={styles.header}>
        <div className={styles.headerIdentity}>
          <Link href="/" className={styles.brandLink} aria-label="返回 NexusPilot 对话工作台">
            <span className={styles.brandMark} aria-hidden="true"><i /></span>
            <span><strong>NexusPilot</strong><small>项目与文件</small></span>
          </Link>
          <button
            type="button"
            className={styles.mobileProjectButton}
            aria-expanded={isSidebarOpen}
            aria-controls="project-navigation"
            onClick={() => setIsSidebarOpen((current) => !current)}
          >
            项目列表
          </button>
        </div>
        <div className={styles.headerActions}>
          <button type="button" className={styles.iconButton} onClick={toggleTheme}
            aria-label={isDark ? "切换到浅色主题" : "切换到深色主题"}>
            {isDark ? "☀" : "◐"}
          </button>
          <button type="button" className={styles.primaryButton} onClick={openCreateProject}>
            ＋ 新建项目
          </button>
        </div>
      </header>

      {notice && <div className={styles.notice} role="status">
        <span>{notice}</span>
        <button type="button" onClick={() => setNotice("")} aria-label="关闭提示">×</button>
      </div>}

      {isSidebarOpen && <button className={styles.scrim} type="button"
        aria-label="关闭项目列表" onClick={() => setIsSidebarOpen(false)} />}

      <div className={styles.shell}>
        <aside id="project-navigation" className={`${styles.sidebar} ${isSidebarOpen ? styles.sidebarOpen : ""}`}
          aria-label="项目列表">
          <div className={styles.sidebarHeading}>
            <div>
              <p className={styles.eyebrow}>工作区</p>
              <strong>项目</strong>
            </div>
            <button type="button" className={styles.sidebarClose}
              aria-label="关闭项目列表" onClick={() => setIsSidebarOpen(false)}>×</button>
          </div>

          {projectsState === "loading" ? (
            <p className={styles.regionLoading} role="status">正在读取项目…</p>
          ) : projectsState === "error" ? (
            <RegionFailure message={projectsError} label="重试读取"
              onRetry={() => void loadProjects()} />
          ) : projects.length === 0 ? (
            <div className={styles.sidebarEmpty}>
              <strong>还没有项目</strong>
              <p>创建项目后，可保存固定文件和项目对话。</p>
              <button type="button" onClick={openCreateProject}>创建第一个项目</button>
            </div>
          ) : (
            <nav className={styles.projectList} aria-label="选择项目">
              {projects.map((item) => (
                <Link
                  key={item.project_id}
                  href={projectWorkspacePath(item.project_id)}
                  className={item.project_id === projectId ? styles.projectSelected : ""}
                  aria-current={item.project_id === projectId ? "page" : undefined}
                  title={item.project_name}
                  onClick={(event) => {
                    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
                      return;
                    }
                    event.preventDefault();
                    openProject(item);
                  }}
                >
                  <span>{item.project_name}</span>
                  <i aria-hidden="true">→</i>
                </Link>
              ))}
            </nav>
          )}

          {projectsCursor && projectsState === "ready" && (
            <button type="button" className={styles.moreButton}
              disabled={isLoadingMoreProjects}
              onClick={() => void loadProjects(projectsCursor, true)}>
              {isLoadingMoreProjects ? "正在加载…" : "继续加载项目"}
            </button>
          )}
          {projectsState === "ready" && projectsError && (
            <RegionFailure message={projectsError} label="重试继续加载"
              onRetry={() => void loadProjects(projectsCursor ?? undefined, true)} />
          )}
          <Link href="/" className={styles.backToChat}>← 返回对话</Link>
        </aside>

        <section className={styles.workspace} aria-label="项目工作区">
          {!projectId ? (
            <div className={styles.emptyWorkspace}>
              <span className={styles.emptyIndex} aria-hidden="true">01</span>
              <p className={styles.eyebrow}>个人项目</p>
              <h1>选择项目，继续整理文件与对话</h1>
              <p>项目保存独立文件副本，并把相关对话归在同一个可恢复的地址中。</p>
              <div className={styles.emptyActions}>
                <button type="button" className={styles.mobileProjectButton}
                  onClick={() => setIsSidebarOpen(true)}>查看项目列表</button>
                <button type="button" className={styles.primaryButton} onClick={openCreateProject}>
                  新建项目
                </button>
              </div>
            </div>
          ) : projectState === "loading" || projectState === "idle" ? (
            <div className={styles.workspaceState} role="status">
              <span className={styles.loadingLine} />
              <p>正在读取项目…</p>
            </div>
          ) : projectState === "unavailable" || projectState === "error" ? (
            <div className={styles.workspaceState} role="alert">
              <p className={styles.eyebrow}>{projectState === "unavailable" ? "无法访问" : "读取失败"}</p>
              <h1>{projectState === "unavailable" ? "项目不存在或无权访问" : "项目暂时无法读取"}</h1>
              <p>{projectError}</p>
              <div className={styles.emptyActions}>
                <button type="button" className={styles.primaryButton} onClick={returnToProjects}>
                  返回项目列表
                </button>
                {projectState === "error" && (
                  <button type="button" className={styles.secondaryButton}
                    onClick={() => setProjectReload((value) => value + 1)}>
                    重试读取
                  </button>
                )}
              </div>
            </div>
          ) : project ? (
            <>
              <header className={styles.projectHeader}>
                <div className={styles.projectTitle}>
                  <p className={styles.eyebrow}>当前项目</p>
                  <h1 title={project.project_name}>{project.project_name}</h1>
                </div>
                <div className={styles.projectActions}>
                  <button type="button" className={styles.primaryButton}
                    disabled={isCreatingConversation}
                    onClick={() => void createProjectConversation()}>
                    {isCreatingConversation ? "正在创建…" : "＋ 新建项目对话"}
                  </button>
                  <button type="button" className={styles.secondaryButton} onClick={openAddFile}>
                    ＋ 添加文件
                  </button>
                  <details className={styles.actionMenu}>
                    <summary aria-label="更多项目操作">•••</summary>
                    <div>
                      <button type="button" onClick={openRenameProject}>重命名项目</button>
                    </div>
                  </details>
                </div>
              </header>

              {conversationActionError && <p className={styles.actionError} role="alert">
                {conversationActionError}
              </p>}

              <div className={styles.projectGrid}>
                <section className={styles.filesSection} aria-labelledby="project-files-heading">
                  <div className={styles.sectionHeading}>
                    <div>
                      <p className={styles.eyebrow}>固定副本</p>
                      <h2 id="project-files-heading">文件</h2>
                    </div>
                    <span>{filesState === "ready" ? files.length : "—"}</span>
                  </div>
                  <p className={styles.sectionIntro}>下载或生成宿主副本不会改变原始上传和运行产物。</p>
                  {fileActionError && <p className={styles.actionError} role="alert">{fileActionError}</p>}
                  {filesState === "loading" ? (
                    <p className={styles.regionLoading} role="status">正在读取项目文件…</p>
                  ) : filesState === "error" ? (
                    <RegionFailure message={filesError} label="重试读取"
                      onRetry={() => void loadProjectFiles(project.project_id)} />
                  ) : files.length === 0 ? (
                    <div className={styles.regionEmpty}>
                      <strong>项目还没有文件</strong>
                      <p>从上传文件或运行产物中显式保存一份固定副本。</p>
                      <button type="button" onClick={openAddFile}>添加第一个文件</button>
                    </div>
                  ) : (
                    <ul className={styles.fileList}>
                      {files.map((file) => (
                        <li key={file.project_file_id}>
                          <span className={styles.fileBadge} aria-hidden="true">
                            {fileTypeLabel(file.filename)}
                          </span>
                          <div className={styles.fileCopy}>
                            <strong title={file.filename}>{file.filename}</strong>
                            <small>
                              {formatProjectFileSize(file.size_bytes)}
                              {" · "}
                              {file.status === "ready" ? "已保存" : "保存未完成"}
                              {" · "}
                              {projectDate(file.created_at)}
                            </small>
                          </div>
                          {file.status === "ready" && (
                            <div className={styles.fileActions}>
                              <a href={`/api/nexus/projects/${encodeURIComponent(project.project_id)}/files/${encodeURIComponent(file.project_file_id)}/content`}>
                                下载
                              </a>
                              <details className={styles.rowMenu}>
                                <summary aria-label={`更多操作：${file.filename}`}>•••</summary>
                                <div>
                                  <button type="button" disabled={fileActionId === file.project_file_id}
                                    onClick={() => void materializeProjectFile(file)}>
                                    {fileActionId === file.project_file_id ? "正在生成…" : "生成本地副本"}
                                  </button>
                                </div>
                              </details>
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section className={styles.conversationsSection} aria-labelledby="project-conversations-heading">
                  <div className={styles.sectionHeading}>
                    <div>
                      <p className={styles.eyebrow}>工作记录</p>
                      <h2 id="project-conversations-heading">项目对话</h2>
                    </div>
                    <span>{conversationsState === "ready" ? conversations.length : "—"}</span>
                  </div>
                  <p className={styles.sectionIntro}>对话会保留项目归属，并可从对话顶栏返回这里。</p>
                  {conversationsState === "loading" ? (
                    <p className={styles.regionLoading} role="status">正在读取项目对话…</p>
                  ) : conversationsState === "error" ? (
                    <RegionFailure message={conversationsError} label="重试读取"
                      onRetry={() => void loadProjectConversations(project.project_id)} />
                  ) : conversations.length === 0 ? (
                    <div className={styles.regionEmpty}>
                      <strong>还没有项目对话</strong>
                      <p>新建对话后，项目文件可按需选择并附加。</p>
                      <button type="button" disabled={isCreatingConversation}
                        onClick={() => void createProjectConversation()}>
                        新建项目对话
                      </button>
                    </div>
                  ) : (
                    <ol className={styles.conversationList}>
                      {conversations.map((conversation) => (
                        <li key={conversation.session_id}>
                          <Link href={`/c/${encodeURIComponent(conversation.session_id)}`}>
                            <span>
                              <strong>{conversation.title ?? "未命名对话"}</strong>
                              <small>更新于 {projectDate(conversation.updated_at)}</small>
                            </span>
                            <i aria-hidden="true">↗</i>
                          </Link>
                        </li>
                      ))}
                    </ol>
                  )}
                  {conversationsState === "ready" && conversationAppendError && (
                    <RegionFailure
                      message={conversationAppendError}
                      label="重试继续加载"
                      onRetry={() => void loadProjectConversations(
                        project.project_id,
                        conversationOffset,
                        true,
                      )}
                    />
                  )}
                  {conversationsState === "ready"
                    && hasMoreConversations
                    && !conversationAppendError && (
                    <button type="button" className={styles.moreButton}
                      disabled={isLoadingMoreConversations}
                      onClick={() => void loadProjectConversations(
                        project.project_id,
                        conversationOffset,
                        true,
                      )}>
                      {isLoadingMoreConversations ? "正在加载…" : "继续加载对话"}
                    </button>
                  )}
                </section>
              </div>
            </>
          ) : null}
        </section>
      </div>

      {projectDialog && (
        <div className={styles.dialogBackdrop} role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeProjectDialog();
        }}>
          <section ref={activeDialog} className={styles.dialog} role="dialog" aria-modal="true"
            aria-labelledby="project-dialog-title">
            <header>
              <div>
                <p className={styles.eyebrow}>{projectDialog === "create" ? "新工作区" : "项目设置"}</p>
                <h2 id="project-dialog-title">
                  {projectDialog === "create" ? "创建项目" : "重命名项目"}
                </h2>
              </div>
              <button type="button" aria-label="关闭对话框" onClick={closeProjectDialog}>×</button>
            </header>
            <form onSubmit={(event) => void (projectDialog === "create"
              ? createProject(event)
              : renameProject(event))}>
              <label>
                <span>项目名称</span>
                <input
                  autoFocus
                  value={projectDialog === "create" ? newProjectName : renameDraft}
                  maxLength={255}
                  onChange={(event) => projectDialog === "create"
                    ? setNewProjectName(event.target.value)
                    : setRenameDraft(event.target.value)}
                  placeholder="例如：季度资料整理"
                />
              </label>
              {projectDialogError && <p className={styles.actionError} role="alert">{projectDialogError}</p>}
              <div className={styles.dialogActions}>
                <button type="button" className={styles.secondaryButton}
                  onClick={closeProjectDialog}>取消</button>
                <button type="submit" className={styles.primaryButton}
                  disabled={projectDialog === "create"
                    ? isCreatingProject || !newProjectName.trim()
                    : isRenamingProject || !renameDraft.trim()}>
                  {projectDialog === "create"
                    ? isCreatingProject ? "正在创建…" : "创建项目"
                    : isRenamingProject ? "正在保存…" : "确认重命名"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      {isAddFileOpen && project && (
        <div className={styles.dialogBackdrop} role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeAddFileDialog();
        }}>
          <section ref={activeDialog} className={`${styles.dialog} ${styles.fileDialog}`} role="dialog"
            aria-modal="true" aria-labelledby="add-file-title">
            <header>
              <div>
                <p className={styles.eyebrow}>保存固定副本到 {project.project_name}</p>
                <h2 id="add-file-title">添加文件</h2>
              </div>
              <button type="button" aria-label="关闭添加文件" onClick={closeAddFileDialog}>×</button>
            </header>

            <div className={styles.sourceTabs} role="tablist" aria-label="文件来源">
              <button type="button" role="tab"
                aria-selected={sourceMode === "new"}
                className={sourceMode === "new" ? styles.sourceTabActive : ""}
                onClick={() => changeSourceMode("new")}>
                上传新文件
              </button>
              <button type="button" role="tab"
                aria-selected={sourceMode === "uploads"}
                className={sourceMode === "uploads" ? styles.sourceTabActive : ""}
                onClick={() => changeSourceMode("uploads")}>
                已上传文件
              </button>
              <button type="button" role="tab"
                aria-selected={sourceMode === "artifacts"}
                className={sourceMode === "artifacts" ? styles.sourceTabActive : ""}
                onClick={() => changeSourceMode("artifacts")}>
                运行产物
              </button>
            </div>

            {sourceMode === "new" && (
              <div className={styles.uploadPanel}>
                <input
                  ref={fileInput}
                  type="file"
                  className={styles.hiddenInput}
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file) void uploadProjectSource(file);
                  }}
                />
                <button type="button" className={styles.uploadTarget}
                  disabled={isUploading}
                  onClick={() => fileInput.current?.click()}>
                  <span aria-hidden="true">＋</span>
                  <strong>{isUploading ? "正在上传并解析…" : "选择文件上传"}</strong>
                  <small>上传完成只代表文件可用，仍需确认名称并保存到项目。</small>
                </button>
              </div>
            )}

            {sourceMode !== "new" && <div className={styles.sourceBrowser}>
              <div className={styles.sourceBrowserHeading}>
                <label>
                  <span className={styles.visuallyHidden}>按文件名查找</span>
                  <input type="search" value={sourceQuery}
                    onChange={(event) => setSourceQuery(event.target.value)}
                    placeholder={sourceMode === "uploads" ? "查找已上传文件" : "查找运行产物"}
                    aria-label="按文件名查找" />
                </label>
                <button type="button" onClick={() => {
                  if (sourceMode === "uploads") void loadUploads();
                  else void loadArtifacts();
                }}>
                  刷新来源
                </button>
              </div>
              {sourceMode === "uploads" && uploadsState === "loading" ? (
                <p className={styles.regionLoading} role="status">正在读取已上传文件…</p>
              ) : sourceMode === "uploads" && uploadsState === "error" ? (
                <RegionFailure message={uploadsError} label="重试读取"
                  onRetry={() => void loadUploads()} />
              ) : sourceMode === "artifacts" && artifactsState === "loading" ? (
                <p className={styles.regionLoading} role="status">正在读取运行产物…</p>
              ) : sourceMode === "artifacts" && artifactsState === "error" ? (
                <RegionFailure message={artifactsError} label="重试读取"
                  onRetry={() => void loadArtifacts()} />
              ) : availableSources.length === 0 ? (
                <div className={styles.sourceEmpty}>
                  <strong>{sourceQuery.trim() ? "没有匹配的文件" : "这个来源还没有文件"}</strong>
                  <p>{sourceQuery.trim()
                    ? "清除搜索条件或切换来源后重试。"
                    : sourceMode === "uploads" ? "上传文件后可在此选择。" : "运行任务生成产物后可在此选择。"}</p>
                </div>
              ) : (
                <ul className={styles.sourceList} aria-label={sourceMode === "uploads" ? "已上传文件" : "运行产物"}>
                  {availableSources.map((source) => {
                    const selected = selectedSourceKey === projectSourceIdentity(source);
                    return <li key={projectSourceIdentity(source)}>
                      <button type="button" aria-pressed={selected}
                        className={selected ? styles.sourceSelected : ""}
                        onClick={() => {
                          setSelectedSourceKey(projectSourceIdentity(source));
                          setSaveFilename(source.filename);
                          setSourceActionError("");
                          setSourceActionNotice("");
                          sourceSaveIdentity.current = null;
                        }}>
                        <span className={styles.sourceKindBadge}>
                          {source.kind === "upload" ? "上传" : "产物"}
                        </span>
                        <span className={styles.sourceCopy}>
                          <strong title={source.filename}>{source.filename}</strong>
                          <small>
                            {[formatProjectFileSize(source.sizeBytes), source.mimeType,
                              source.createdAt ? projectDate(source.createdAt) : null]
                              .filter(Boolean).join(" · ")}
                          </small>
                        </span>
                        <span aria-hidden="true">{selected ? "✓" : "选择"}</span>
                      </button>
                    </li>;
                  })}
                </ul>
              )}
              {sourceMode === "artifacts" && artifactCursor && artifactsState === "ready" && (
                <button type="button" className={styles.moreButton}
                  disabled={isLoadingMoreArtifacts}
                  onClick={() => void loadArtifacts(artifactCursor, true)}>
                  {isLoadingMoreArtifacts ? "正在加载…" : "继续加载运行产物"}
                </button>
              )}
            </div>}

            <div className={styles.savePanel}>
              <div>
                <p className={styles.eyebrow}>保存到</p>
                <strong>{project.project_name}</strong>
                <small>{selectedSource
                  ? `${selectedSource.kind === "upload" ? "已上传文件" : "运行产物"} · ${selectedSource.filename}`
                  : "请先选择一个文件来源"}</small>
              </div>
              <label>
                <span>项目内文件名</span>
                <input value={saveFilename} maxLength={255}
                  onChange={(event) => {
                    setSaveFilename(event.target.value);
                    sourceSaveIdentity.current = null;
                  }}
                  disabled={!selectedSource}
                  placeholder="确认保存名称" />
              </label>
            </div>
            {sourceActionNotice && <p className={styles.sourceNotice} role="status">{sourceActionNotice}</p>}
            {sourceActionError && <p className={styles.actionError} role="alert">{sourceActionError}</p>}
            <div className={styles.dialogActions}>
              <button type="button" className={styles.secondaryButton}
                onClick={closeAddFileDialog}>取消</button>
              <button type="button" className={styles.primaryButton}
                disabled={!selectedSource || !saveFilename.trim() || isSavingProjectFile || isUploading}
                onClick={() => void saveProjectFile()}>
                {isSavingProjectFile ? "正在保存…" : "保存到项目"}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
