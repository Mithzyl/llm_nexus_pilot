import { expect, test, type Page, type Route } from "@playwright/test";

/** Fulfill one browser-facing request without contacting the real BFF or FastAPI. */
async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/** Install only the workspace facts needed by the personal-settings flow. */
async function installPersonalSettingsMocks(page: Page) {
  let createdKeyBody: Record<string, unknown> | null = null;
  let memoryRequests = 0;

  await page.route("**/api/nexus/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/nexus/", "");
    if (path === "auth/me") {
      return fulfillJson(route, {
        user_id: "nexuspilot-web",
        display_name: "张三",
        authentication_method: "browser_session",
        login_session_id: "login-session-1",
        scopes: ["model:invoke", "sessions:read", "sessions:write"],
      });
    }
    if (path === "models") return fulfillJson(route, []);
    if (path === "sessions") {
      return fulfillJson(route, {
        items: [],
        next_cursor: null,
        has_more: false,
        limit: 50,
      });
    }
    if (path === "files/capabilities") {
      return fulfillJson(route, {
        accepted_extensions: [".txt"],
        executor_status: "available",
        max_file_size_bytes: 1048576,
        unavailable_reason: null,
      });
    }
    if (path === "me/api-keys" && request.method() === "GET") {
      return fulfillJson(route, [{
        api_key_id: "existing-key",
        name: "现有脚本",
        masked_key: "np_existing…1234",
        scopes: ["read"],
        created_at: "2026-10-26T00:00:00Z",
        expires_at: null,
        last_used_at: null,
        revoked_at: null,
      }]);
    }
    if (path === "me/api-keys" && request.method() === "POST") {
      createdKeyBody = request.postDataJSON() as Record<string, unknown>;
      return fulfillJson(route, {
        api_key_id: "created-key",
        name: createdKeyBody.name,
        masked_key: "np_created…5678",
        scopes: createdKeyBody.scopes,
        created_at: "2026-10-27T03:00:00Z",
        expires_at: createdKeyBody.expires_at,
        last_used_at: null,
        revoked_at: null,
        key: "np_created.secret-value",
      }, 201);
    }
    if (path.startsWith("memories")) {
      memoryRequests += 1;
      return fulfillJson(route, { detail: "legacy Memory route must stay unused" }, 500);
    }
    if (path === "me/memory-settings") return fulfillJson(route, {
      enabled: true, entry_enabled: true, mode: "entry", version: 0,
    });
    if (path === "me/user-memories") return fulfillJson(route, { items: [], next_offset: null });
    return fulfillJson(route, { detail: `Unhandled fixture route: ${path}` }, 404);
  });

  return {
    createdKeyBody: () => createdKeyBody,
    memoryRequests: () => memoryRequests,
  };
}

test("opens personal settings, manages API keys and keeps legacy Memory closed", async ({ page }) => {
  const fixture = await installPersonalSettingsMocks(page);
  await page.goto("/");

  const isMobile = (page.viewportSize()?.width ?? 0) < 880;
  if (isMobile) {
    await page.getByRole("button", { name: "打开个人设置", exact: true }).click();
  } else {
    await page.getByRole("button", { name: /张三.*个人设置/ }).click();
  }

  let dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "账户", exact: true })).toBeVisible();
  await expect(dialog).toContainText("nexuspilot-web");
  if (!isMobile) {
    await dialog.getByRole("button", { name: "关闭个人设置", exact: true }).click();
    await page.getByRole("button", { name: "打开个人设置", exact: true }).click();
    dialog = page.getByRole("dialog");
  }

  await dialog.getByRole("button", { name: /^API Key/ }).click();
  await expect(dialog).toContainText("现有脚本");
  await dialog.getByLabel("名称").fill("自动化脚本");
  await dialog.getByRole("button", { name: "创建 API Key", exact: true }).click();
  await expect(dialog).toContainText("np_created.secret-value");
  const browserStorage = await page.evaluate(() => JSON.stringify({
    local: { ...localStorage },
    session: { ...sessionStorage },
  }));
  expect(browserStorage).not.toContain("np_created.secret-value");
  await expect.poll(() => fixture.createdKeyBody()).toMatchObject({
    name: "自动化脚本",
    scopes: ["read", "execute"],
    expires_at: null,
  });
  await page.waitForTimeout(250);
  await page.screenshot({
    path: `../../output/playwright/personal-settings-api-keys-${test.info().project.name}.png`,
    fullPage: true,
  });

  await dialog.getByRole("button", { name: /^记忆/ }).click();
  await expect(dialog).toContainText("个人记忆已开启");
  await expect(dialog.getByRole("button", { name: "保存记忆", exact: true })).toBeVisible();
  await expect(dialog.getByRole("textbox", { name: "记忆内容", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认记住" })).toHaveCount(0);
  expect(fixture.memoryRequests()).toBe(0);
  await page.waitForTimeout(250);
  await page.screenshot({
    path: `../../output/playwright/personal-settings-memory-${test.info().project.name}.png`,
    fullPage: true,
  });

  await dialog.getByRole("button", { name: /^个性化提示词/ }).click();
  await expect(dialog).toContainText("用户级提示词暂不可配置");
  await expect(dialog).toContainText("普通浏览器不得读取、选择或覆盖");
  await expect(dialog).toContainText("账户信息与个人 API Key 可继续管理");

  await page.waitForTimeout(250);
  await page.screenshot({
    path: `../../output/playwright/personal-settings-${test.info().project.name}.png`,
    fullPage: true,
  });

  await dialog.getByRole("button", { name: "关闭个人设置", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(fixture.memoryRequests()).toBe(0);
});
