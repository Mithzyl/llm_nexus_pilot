import { expect, test, type Page, type Route } from "@playwright/test";

/** Fulfill one browser-facing request without contacting the real BFF or API. */
async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

const LONG_PROJECT_NAME = "季度研究资料与跨团队交付记录整理工作区";

/**
 * Install deterministic project resources so tests exercise real navigation, retries and dialogs
 * without calling a model or writing to the local API.
 */
async function installProjectMocks(page: Page) {
  const projects = [{
    project_id: "p1",
    project_name: LONG_PROJECT_NAME,
  }];
  const filesByProject: Record<string, Array<Record<string, unknown>>> = { p1: [] };
  const savedBodies: Array<Record<string, unknown>> = [];
  let nextProject = 2;

  await page.route("**/api/nexus/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/nexus/", "");
    if (path === "projects" && request.method() === "GET") {
      return fulfillJson(route, { items: projects, next_cursor: null });
    }
    if (path === "projects" && request.method() === "POST") {
      const project = {
        project_id: `p${nextProject++}`,
        project_name: (request.postDataJSON() as { project_name: string }).project_name,
      };
      projects.push(project);
      filesByProject[project.project_id] = [];
      return fulfillJson(route, project, 201);
    }
    const projectMatch = /^projects\/([^/]+)$/.exec(path);
    if (projectMatch) {
      const project = projects.find((item) => item.project_id === projectMatch[1]);
      return project
        ? fulfillJson(route, project)
        : fulfillJson(route, { detail: "project not found" }, 404);
    }
    if (path === "files" && request.method() === "GET") {
      return fulfillJson(route, [
        { file_id: "upload-1", filename: "notes.txt", status: "ready", size_bytes: 1536 },
        { file_id: "upload-2", filename: "季度分析.csv", status: "ready", size_bytes: 2048 },
      ]);
    }
    if (path === "artifacts" && request.method() === "GET") {
      return fulfillJson(route, {
        items: [{
          artifact_id: "artifact-1",
          run_id: "run-1",
          task_id: null,
          artifact_type: "report",
          filename: "年度汇总.md",
          mime_type: "text/markdown",
          size_bytes: 4096,
          created_at: "2026-10-27T08:00:00Z",
        }],
        next_cursor: null,
      });
    }
    const fileMatch = /^projects\/([^/]+)\/files$/.exec(path);
    if (fileMatch) {
      const selectedProjectId = fileMatch[1];
      if (request.method() === "POST") {
        const body = request.postDataJSON() as Record<string, unknown>;
        savedBodies.push(body);
        const saved = {
          project_file_id: "saved-1",
          project_id: selectedProjectId,
          filename: body.filename,
          source_type: body.artifact_id ? "artifact" : "upload",
          source_id: String(body.artifact_id ?? body.file_id),
          size_bytes: 1536,
          mime_type: "text/plain",
          status: "ready",
          created_at: "2026-10-27T09:00:00Z",
        };
        filesByProject[selectedProjectId] = [saved];
        return fulfillJson(route, saved, 201);
      }
      return fulfillJson(route, filesByProject[selectedProjectId] ?? []);
    }
    if (path.endsWith("/materialize")) {
      expect(request.postDataJSON()).toEqual({});
      return fulfillJson(route, {
        detail: "项目本地目录尚未配置，MinIO 中的文件仍可下载。",
      }, 503);
    }
    const sessionsMatch = /^projects\/([^/]+)\/sessions$/.exec(path);
    if (sessionsMatch) {
      if (request.method() === "POST") {
        return fulfillJson(route, {
          session_id: "session-new",
          title: "项目对话",
          status: "active",
          created_at: "2026-10-27T09:30:00Z",
          updated_at: "2026-10-27T09:30:00Z",
        }, 201);
      }
      return fulfillJson(route, [{
        session_id: "session-1",
        title: "已有的项目讨论",
        status: "active",
        created_at: "2026-10-27T07:00:00Z",
        updated_at: "2026-10-27T08:30:00Z",
      }]);
    }
    return fulfillJson(route, { detail: `Unhandled fixture route: ${path}` }, 404);
  });

  return { projects, filesByProject, savedBodies };
}

test("restores projects by URL and saves a searchable uploaded source", async ({ page }) => {
  const fixture = await installProjectMocks(page);
  await page.goto("/projects?project=p1");

  await expect(page.getByRole("heading", { name: LONG_PROJECT_NAME, exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/projects\?project=p1$/);
  await page.reload();
  await expect(page.getByRole("heading", { name: LONG_PROJECT_NAME, exact: true })).toBeVisible();

  await page.getByRole("button", { name: /添加文件/ }).first().click();
  const addFileDialog = page.getByRole("dialog", { name: "添加文件" });
  await expect(addFileDialog).toBeVisible();
  await addFileDialog.getByRole("tab", { name: "已上传文件" }).click();
  await addFileDialog.getByLabel("按文件名查找").fill("notes");
  const source = addFileDialog.getByRole("list", { name: "已上传文件" }).locator("li")
    .filter({ hasText: "notes.txt" });
  await expect(source).toBeVisible();
  await source.getByRole("button").click();
  await addFileDialog.getByLabel("项目内文件名").fill("季度笔记.md");
  await addFileDialog.getByRole("button", { name: "保存到项目", exact: true }).click();

  await expect(page.getByText("“季度笔记.md”已保存到项目。", { exact: true })).toBeVisible();
  const download = page.getByRole("link", { name: "下载", exact: true });
  await expect(download).toHaveAttribute(
    "href",
    "/api/nexus/projects/p1/files/saved-1/content",
  );
  expect(fixture.savedBodies[0]).toMatchObject({
    file_id: "upload-1",
    filename: "季度笔记.md",
  });
  expect(fixture.savedBodies[0].idempotency_key).toBeTruthy();

  await page.getByLabel("更多操作：季度笔记.md").click();
  await page.getByRole("button", { name: "生成本地副本", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "生成本地固定副本暂时不可用" })).toBeVisible();
  await expect(download).toBeVisible();

  await page.getByRole("button", { name: /新建项目/ }).first().click();
  const createDialog = page.getByRole("dialog", { name: "创建项目" });
  await createDialog.getByLabel("项目名称").fill("第二个项目");
  await createDialog.getByRole("button", { name: "创建项目", exact: true }).click();
  await expect(page).toHaveURL(/\/projects\?project=p2$/);
  await expect(page.getByRole("heading", { name: "第二个项目", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/projects\?project=p1$/);
  await expect(page.getByRole("heading", { name: LONG_PROJECT_NAME, exact: true })).toBeVisible();

  const workspaceFitsViewport = await page.locator('[aria-label="项目工作区"]').evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return bounds.left >= -1 && bounds.right <= window.innerWidth + 1;
  });
  expect(workspaceFitsViewport).toBe(true);
  await page.screenshot({
    path: `../../output/playwright/project-workspace-${test.info().project.name}.png`,
    fullPage: true,
  });
});

test("keeps a pending project save out of a different selected project", async ({ page }) => {
  let releaseSave!: () => void;
  const saveGate = new Promise<void>((resolve) => {
    releaseSave = resolve;
  });
  let savePosts = 0;
  let savedFile: Record<string, unknown> | null = null;
  const fileReads = { p1: 0, p2: 0 };
  await page.route("**/api/nexus/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/nexus/", "");
    if (path === "projects") {
      return fulfillJson(route, {
        items: [
          { project_id: "p1", project_name: "项目甲" },
          { project_id: "p2", project_name: "项目乙" },
        ],
        next_cursor: null,
      });
    }
    if (path === "projects/p1") {
      return fulfillJson(route, { project_id: "p1", project_name: "项目甲" });
    }
    if (path === "projects/p2") {
      return fulfillJson(route, { project_id: "p2", project_name: "项目乙" });
    }
    if (path === "projects/p1/files" && request.method() === "GET") {
      fileReads.p1 += 1;
      return fulfillJson(route, savedFile ? [savedFile] : []);
    }
    if (path === "projects/p2/files" && request.method() === "GET") {
      fileReads.p2 += 1;
      return fulfillJson(route, [{
        project_file_id: "p2-file",
        project_id: "p2",
        filename: "项目乙专属.txt",
        source_type: "upload",
        source_id: "upload-1",
        size_bytes: 10,
        mime_type: "text/plain",
        status: "ready",
        created_at: "2026-10-27T09:00:00Z",
      }]);
    }
    if (path === "projects/p1/files" && request.method() === "POST") {
      savePosts += 1;
      await saveGate;
      const body = request.postDataJSON() as Record<string, unknown>;
      savedFile = {
        project_file_id: "p1-saved",
        project_id: "p1",
        filename: body.filename,
        source_type: "upload",
        source_id: "upload-1",
        size_bytes: 1536,
        mime_type: "text/plain",
        status: "ready",
        created_at: "2026-10-27T09:00:00Z",
      };
      return fulfillJson(route, savedFile, 201);
    }
    if (path === "files") {
      return fulfillJson(route, [{
        file_id: "upload-1",
        filename: "等待保存.txt",
        status: "ready",
        size_bytes: 1536,
      }]);
    }
    if (path === "projects/p1/sessions" || path === "projects/p2/sessions") {
      return fulfillJson(route, []);
    }
    return fulfillJson(route, { detail: `Unhandled fixture route: ${path}` }, 404);
  });

  await page.goto("/projects?project=p1");
  await page.getByRole("button", { name: /添加文件/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "添加文件" });
  await dialog.getByRole("tab", { name: "已上传文件" }).click();
  await dialog.getByRole("list", { name: "已上传文件" }).getByRole("button").click();
  await dialog.getByRole("button", { name: "保存到项目", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "正在保存…" })).toBeDisabled();
  await dialog.getByRole("button", { name: "关闭添加文件" }).click();
  await expect(dialog).toHaveCount(0);

  if ((page.viewportSize()?.width ?? 0) < 880) {
    await page.getByRole("button", { name: "项目列表", exact: true }).click();
  }
  await page.getByRole("link", { name: "项目乙", exact: true }).click();
  await expect(page.getByRole("heading", { name: "项目乙", exact: true })).toBeVisible();
  await expect(page.getByText("项目乙专属.txt", { exact: true })).toBeVisible();

  releaseSave();
  await expect.poll(() => savePosts).toBe(1);
  await expect(page.getByRole("heading", { name: "项目乙", exact: true })).toBeVisible();
  await expect(page.getByText("项目乙专属.txt", { exact: true })).toBeVisible();
  await expect(page.getByText("等待保存.txt", { exact: true })).toHaveCount(0);

  if ((page.viewportSize()?.width ?? 0) < 880) {
    await page.getByRole("button", { name: "项目列表", exact: true }).click();
  }
  await page.getByRole("link", { name: "项目甲", exact: true }).click();
  await expect(page.getByRole("heading", { name: "项目甲", exact: true })).toBeVisible();
  await expect(page.getByText("等待保存.txt", { exact: true })).toBeVisible();
  expect(savePosts).toBe(1);
  expect(fileReads.p2).toBeGreaterThan(0);
});

test("keeps project files, conversations and source pickers independent during failures", async ({
  page,
}) => {
  let projectFileReads = 0;
  let appendAttempts = 0;
  const conversationOffsets: number[] = [];
  let releaseAppendFailure!: () => void;
  const appendFailureGate = new Promise<void>((resolve) => {
    releaseAppendFailure = resolve;
  });
  await page.route("**/api/nexus/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/nexus/", "");
    if (path === "projects") {
      return fulfillJson(route, {
        items: [{ project_id: "p1", project_name: "容错项目" }],
        next_cursor: null,
      });
    }
    if (path === "projects/p1") {
      return fulfillJson(route, { project_id: "p1", project_name: "容错项目" });
    }
    if (path === "projects/p1/files" && request.method() === "GET") {
      projectFileReads += 1;
      return projectFileReads === 1
        ? fulfillJson(route, { detail: "temporary storage failure" }, 503)
        : fulfillJson(route, []);
    }
    if (path === "projects/p1/sessions") {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      conversationOffsets.push(offset);
      if (offset === 0) {
        return fulfillJson(route, Array.from({ length: 100 }, (_, index) => ({
          session_id: `session-${index + 1}`,
          title: index === 0 ? "文件失败时仍应可见的对话" : `已有对话 ${index + 1}`,
          status: "active",
          created_at: "2026-10-27T07:00:00Z",
          updated_at: "2026-10-27T08:30:00Z",
        })));
      }
      appendAttempts += 1;
      if (appendAttempts === 1) {
        await appendFailureGate;
        return fulfillJson(route, { detail: "conversation append unavailable" }, 503);
      }
      return fulfillJson(route, [{
        session_id: "session-101",
        title: "继续加载后的对话",
        status: "active",
        created_at: "2026-10-27T09:00:00Z",
        updated_at: "2026-10-27T09:30:00Z",
      }]);
    }
    if (path === "files") {
      return fulfillJson(route, { detail: "upload listing unavailable" }, 503);
    }
    if (path === "artifacts") {
      return fulfillJson(route, {
        items: [{
          artifact_id: "artifact-1",
          run_id: "run-1",
          task_id: null,
          artifact_type: "report",
          filename: "仍可选择.md",
          mime_type: "text/markdown",
          size_bytes: 4096,
          created_at: "2026-10-27T08:00:00Z",
        }],
        next_cursor: null,
      });
    }
    return fulfillJson(route, { detail: `Unhandled fixture route: ${path}` }, 404);
  });

  await page.goto("/projects?project=p1");
  const filesRegion = page.getByRole("region", { name: "文件", exact: true });
  const conversationsRegion = page.getByRole("region", { name: "项目对话", exact: true });
  await expect(conversationsRegion.getByText("文件失败时仍应可见的对话")).toBeVisible();
  await expect(filesRegion.getByRole("alert")).toContainText("读取项目文件暂时不可用");
  await expect(page.getByRole("link", { name: "容错项目" })).toBeVisible();
  await filesRegion.getByRole("button", { name: "重试读取", exact: true }).click();
  await expect(filesRegion.getByText("项目还没有文件")).toBeVisible();

  await conversationsRegion.getByRole("button", { name: "继续加载对话" }).click();
  await expect(conversationsRegion.getByRole("button", { name: "正在加载…" })).toBeDisabled();
  releaseAppendFailure();
  await expect(conversationsRegion.getByRole("alert")).toContainText("继续读取项目对话暂时不可用");
  await expect(conversationsRegion.getByText("文件失败时仍应可见的对话")).toBeVisible();
  await conversationsRegion.getByRole("button", { name: "重试继续加载" }).click();
  await expect(conversationsRegion.getByText("继续加载后的对话")).toBeVisible();
  expect(conversationOffsets).toEqual([0, 100, 100]);

  await page.getByRole("button", { name: /添加文件/ }).first().click();
  const dialog = page.getByRole("dialog", { name: "添加文件" });
  await dialog.getByRole("tab", { name: "已上传文件" }).click();
  await expect(dialog.getByRole("alert")).toContainText("读取已上传文件暂时不可用");
  await dialog.getByRole("tab", { name: "运行产物" }).click();
  await expect(dialog.getByText("仍可选择.md", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "容错项目", exact: true })).toBeVisible();
});

test("opens the project drawer and keeps dialog focus usable on a narrow screen", async ({
  page,
}) => {
  test.skip((page.viewportSize()?.width ?? 0) >= 880, "Mobile-only drawer behavior");
  await installProjectMocks(page);
  await page.goto("/projects");

  await page.getByRole("button", { name: "项目列表", exact: true }).click();
  await page.getByRole("link", { name: LONG_PROJECT_NAME, exact: true }).click();
  await expect(page).toHaveURL(/\/projects\?project=p1$/);
  await expect(page.getByRole("heading", { name: LONG_PROJECT_NAME, exact: true })).toBeVisible();

  const addFileTrigger = page.getByRole("button", { name: /添加文件/ }).first();
  await addFileTrigger.click();
  const dialog = page.getByRole("dialog", { name: "添加文件" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Tab");
  expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(addFileTrigger).toBeFocused();

  await page.screenshot({
    path: `../../output/playwright/project-workspace-mobile-${test.info().project.name}.png`,
    fullPage: true,
  });
});
