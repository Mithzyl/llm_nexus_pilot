import { test, expect } from "@playwright/test";

/** Exercise the actual project UI with deterministic HTTP fixtures; no model service is called. */
test("creates a project, saves a file and preserves an export error", async ({ page }) => {
  const projects: { project_id: string; project_name: string }[] = [];
  const files: { project_file_id: string; filename: string; status: string; size_bytes: number }[] = [];
  await page.route("**/api/nexus/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/nexus/", "");
    if (path === "projects" && route.request().method() === "POST") {
      projects.push({ project_id: "p1", project_name: route.request().postDataJSON().project_name });
      return route.fulfill({ status: 201, json: projects[0] });
    }
    if (path === "projects") return route.fulfill({ json: { items: projects, next_cursor: null } });
    if (path === "files") return route.fulfill({ json: [{ file_id: "f1", filename: "notes.txt", status: "ready" }] });
    if (path === "artifacts") return route.fulfill({ json: { items: [], next_cursor: null } });
    if (path === "projects/p1/sessions") return route.fulfill({ json: [] });
    if (path.endsWith("/materialize")) {
      expect(route.request().postDataJSON()).toEqual({});
      return route.fulfill({ status: 503, json: { detail: "项目本地目录尚未配置，MinIO中的文件仍可下载。" } });
    }
    if (path === "projects/p1/files") {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        expect(body.file_id).toBe("f1"); expect(body.idempotency_key).toBeTruthy();
        files.push({ project_file_id: "saved1", filename: body.filename, status: "ready", size_bytes: 10 });
        return route.fulfill({ status: 201, json: files[0] });
      }
      return route.fulfill({ json: files });
    }
    return route.fulfill({ status: 404, json: { detail: "Unexpected request" } });
  });
  await page.goto("/projects");
  await page.getByLabel("项目名称", { exact: true }).fill("阅读资料");
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page.getByRole("button", { name: "阅读资料" })).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("选择已有文件或产物").selectOption("file:f1");
  await page.getByRole("button", { name: "保存到项目" }).click();
  await expect(page.getByRole("link", { name: "下载", exact: true })).toHaveAttribute("href", "/api/nexus/projects/p1/files/saved1/content");
  await page.getByRole("button", { name: "生成本地副本" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "尚未配置" })).toBeVisible();
  await expect(page.getByRole("link", { name: "下载", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "阅读资料" }).click();
  await expect(page.getByRole("link", { name: "下载", exact: true })).toBeVisible();
  await page.locator("main").evaluate(element => { element.scrollTop = 0; });
  await expect(page.getByRole("heading", { name: "个人项目", exact: true })).toBeVisible();
  await page.screenshot({ path: `../../output/playwright/project-workspace-${test.info().project.name}.png`, fullPage: true });
});
