import { expect, test, type Page, type Route } from "@playwright/test";

import { WEB_PERFORMANCE_BUDGETS } from "../lib/performance-budgets";
import { conversationSubmissionLockName } from "../lib/conversation-submission";

const MODEL = {
  model_id: "model-1",
  provider: "deepseek",
  model_identifier: "deepseek-v4-flash",
  display_name: "DeepSeek V4 Flash",
  description: "Deterministic browser test model",
  status: "enabled",
  sort_order: 10,
  metadata_json: {},
  registry_available: true,
  selectable: true,
  unavailable_reason: null,
  catalog_version_id: "catalog-1",
  catalog_version: "2026-08-30",
  context_window: 65_536,
  supports_tools: false,
  supports_structured_output: true,
  supports_vision: false,
  supports_caching: true,
  supports_streaming: true,
  tokenizer_name: "deepseek",
  tokenizer_version: "v1",
  reasoning_capabilities: {
    presentation: "raw",
    supports_streaming_presentation: true,
    continuation: "raw-reasoning-replay",
    supports_reasoning_tokens: true,
  },
  reasoning_supported_efforts: ["minimal", "medium", "xhigh"],
  reasoning_supports_disable: true,
  reasoning_default_enabled: true,
  reasoning_default_effort: "medium",
  reasoning_profile_version: "reasoning-policy.v1",
};

/** Fulfill one browser-facing API request with deterministic public facts. */
async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** Replace the BFF with deterministic conversation and model facts for UI tests. */
async function installApiMocks(
  page: Page,
  options: {
    messages?: unknown[];
  } = {},
) {
  let sessionTitle = "Original title";
  await page.route("**/api/nexus/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/nexus/", "");
    if (path === "auth/me") {
      return fulfillJson(route, {
        user_id: "nexuspilot-web",
        display_name: "Test user",
        authentication_method: "browser_session",
        login_session_id: "login-session-1",
        scopes: ["model:invoke", "sessions:read", "sessions:write"],
      });
    }
    if (path === "models") return fulfillJson(route, [MODEL]);
    if (path === "sessions") {
      return fulfillJson(route, {
        items: [{
          session_id: "session-1",
          user_id: "nexuspilot-web",
          title: sessionTitle,
          status: "active",
          created_at: "2026-08-30T00:00:00Z",
          updated_at: "2026-08-30T00:00:00Z",
        }],
        next_cursor: null,
        has_more: false,
        limit: 50,
      });
    }
    if (path === "sessions/session-1" && request.method() === "PATCH") {
      sessionTitle = (request.postDataJSON() as { title: string }).title;
      return fulfillJson(route, {
        session_id: "session-1",
        user_id: "nexuspilot-web",
        title: sessionTitle,
        status: "active",
        created_at: "2026-08-30T00:00:00Z",
        updated_at: "2026-08-30T01:00:00Z",
      });
    }
    if (path === "sessions/session-1/messages/latest") {
      return fulfillJson(route, {
        items: options.messages ?? [
          {
            message_id: "message-user",
            session_id: "session-1",
            run_id: "run-1",
            source_model_attempt_id: null,
            parent_message_id: null,
            role: "user",
            content_type: "text",
            content_text: "original question",
            content_uri: null,
            sequence: 1,
            token_count: 2,
            metadata_json: {},
            created_at: "2026-08-30T00:00:00Z",
          },
          {
            message_id: "message-assistant",
            session_id: "session-1",
            run_id: "run-1",
            source_model_attempt_id: "attempt-1",
            parent_message_id: null,
            role: "assistant",
            content_type: "text",
            content_text: "persisted answer",
            content_uri: null,
            sequence: 2,
            token_count: 2,
            metadata_json: {},
            reasoning_blocks: [],
            created_at: "2026-08-30T00:00:01Z",
          },
        ],
        next_cursor: null,
        has_more: false,
        limit: 100,
      });
    }
    if (path === "sessions/session-1/latest-run") {
      return fulfillJson(route, { detail: "not found" }, 404);
    }
    return fulfillJson(route, { detail: `Unhandled deterministic route: ${path}` }, 404);
  });
}

test.beforeEach(async ({ page }) => installApiMocks(page));

test("requires a provisioned account before mounting the conversation workspace", async ({
  page,
}) => {
  await page.unroute("**/api/nexus/**");
  let isAuthenticated = false;
  let logoutAttempts = 0;
  await page.route("**/api/nexus/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/nexus/", "");
    if (path === "auth/me") {
      return isAuthenticated
        ? fulfillJson(route, {
            user_id: "authenticated-user",
            display_name: "Authenticated User",
            authentication_method: "browser_session",
            login_session_id: "login-session-1",
            scopes: ["model:invoke", "sessions:read", "sessions:write"],
          })
        : fulfillJson(route, { detail: "需要登录。" }, 401);
    }
    if (path === "auth/login") {
      if (request.postDataJSON().password === "wrong password") {
        return fulfillJson(route, { detail: "邮箱或密码不正确。" }, 401);
      }
      isAuthenticated = true;
      return fulfillJson(route, {
        user_id: "authenticated-user",
        display_name: "Authenticated User",
        authentication_method: "browser_session",
        login_session_id: "login-session-1",
        scopes: ["model:invoke", "sessions:read", "sessions:write"],
      });
    }
    if (path === "auth/logout") {
      logoutAttempts += 1;
      if (logoutAttempts === 1) {
        return fulfillJson(route, { detail: "退出服务暂不可用。" }, 503);
      }
      isAuthenticated = false;
      return route.fulfill({ status: 204, body: "" });
    }
    if (path === "models") return fulfillJson(route, [MODEL]);
    if (path === "sessions") {
      return fulfillJson(route, { items: [], next_cursor: null, has_more: false, limit: 50 });
    }
    return fulfillJson(route, { detail: `Unhandled authentication route: ${path}` }, 404);
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "消息内容" })).toHaveCount(0);
  await page.getByRole("textbox", { name: "邮箱" }).fill("user@example.com");
  await page.getByLabel("密码").fill("wrong password");
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.locator(".authentication-error[role=alert]")).toContainText("邮箱或密码不正确");
  await page.getByLabel("密码").fill("correct horse battery staple");
  await page.getByRole("button", { name: "登录" }).click();

  await expect(page.getByRole("textbox", { name: "消息内容" })).toBeVisible();
  await expect(page.locator(".authenticated-user")).toContainText("Authenticated User");
  await page.reload();
  await expect(page.getByRole("textbox", { name: "消息内容" })).toBeVisible();
  const mobileMenu = page.getByRole("button", { name: "打开会话列表" });
  if (await mobileMenu.isVisible()) await mobileMenu.click();
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page.getByText("退出服务暂不可用。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "登录", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page.getByRole("heading", { name: "登录", exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "消息内容" })).toHaveCount(0);
});

test("restores an unsent new-conversation draft after reload", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: "消息内容" });
  await composer.fill("draft that must survive reload");
  await expect.poll(() => page.evaluate(() =>
    window.sessionStorage.getItem("nexuspilot:draft:nexuspilot-web:new"),
  )).toBe("draft that must survive reload");
  await page.reload();
  await expect(composer).toHaveValue(
    "draft that must survive reload",
    { timeout: WEB_PERFORMANCE_BUDGETS.draftRestoreVisibleMs },
  );
});

test("renames an active conversation and restores the paired question without sending", async ({ page }) => {
  await page.goto("/c/session-1");
  await expect(page.getByText("persisted answer")).toBeVisible();
  await page.getByRole("button", { name: "再次提问" }).click();
  await expect(page.getByRole("textbox", { name: "消息内容" })).toHaveValue("original question");
  await page.getByRole("button", { name: "重命名" }).click();
  await page.getByRole("textbox", { name: "会话标题" }).fill("Renamed conversation");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(page.getByRole("heading", { name: "Renamed conversation" })).toBeVisible();
});

test("opens the native chooser directly from the file button", async ({
  page,
}, testInfo) => {
  await page.unroute("**/api/nexus/**");
  const requestedPaths: string[] = [];
  page.on("request", (request) => requestedPaths.push(new URL(request.url()).pathname));
  await installApiMocks(page);
  await page.goto("/");

  const fileInput = page.locator('input[type="file"][aria-label="选择文件"]');
  const fileAction = page.locator(".file-picker-button");
  await expect(fileInput).toBeVisible();
  await expect(fileAction).toContainText("文件");
  await expect(page.getByText("上下文", { exact: true })).toHaveCount(0);
  await expect(page.getByText("添加文件", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Prompt/i)).toHaveCount(0);

  const fileChooserPromise = page.waitForEvent("filechooser");
  await fileInput.click();
  const fileChooser = await fileChooserPromise;
  await fileChooser.setFiles([]);
  await fileInput.setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("local pending file"),
  });
  await expect(page.locator(".composer > .pending-file-list")).toContainText("notes.txt");
  await expect(page.locator(".composer > .pending-file-list")).toContainText("待上传");
  await expect(page.getByRole("button", { name: "发送消息" })).toBeDisabled();
  expect(requestedPaths.some((path) => path.endsWith("/prompt-templates"))).toBe(false);

  if (testInfo.project.name === "chromium") {
    await page.screenshot({
      path: "../../output/playwright/composer-file-button-direct.png",
      fullPage: true,
    });
  }
});

test("keeps Composer controls and the file action at legible sizes", async ({
  page,
}, testInfo) => {
  await page.unroute("**/api/nexus/**");
  await installApiMocks(page);
  await page.goto("/");

  const controls = page.locator(
    ".composer-mode-row button, .file-picker-button, .reasoning-settings-trigger",
  );
  const controlFontSizes = await controls.evaluateAll((elements) =>
    elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize)),
  );
  expect(controlFontSizes.every((fontSize) => fontSize >= 12)).toBe(true);

  const fileButton = page.locator(".file-picker-button");
  await expect(fileButton).toBeVisible();
  const fileButtonMetrics = await fileButton.evaluate((element) => ({
    fontSize: Number.parseFloat(getComputedStyle(element).fontSize),
    height: element.getBoundingClientRect().height,
  }));
  expect(fileButtonMetrics.fontSize).toBeGreaterThanOrEqual(12);
  expect(fileButtonMetrics.height).toBeGreaterThanOrEqual(44);

  if (testInfo.project.name === "chromium") {
    await page.screenshot({
      path: "../../output/playwright/composer-file-action-readable.png",
      fullPage: true,
    });
  }
});

test("matches the default reasoning display to the selected model capability", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "思考设置" }).click();

  const displayOptions = page.getByRole("listbox", { name: "推理展示" });
  await expect(displayOptions.getByRole("option", { name: "显示推理" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(displayOptions.getByRole("option", { name: "隐藏" })).toBeVisible();
  await expect(displayOptions.getByRole("option", { name: "显示摘要" })).toHaveCount(0);

  if (testInfo.project.name === "chromium") {
    await page.screenshot({
      path: "../../output/playwright/composer-reasoning-auto-display.png",
      fullPage: true,
    });
  }
});

test("shows the read-only server model directory", async ({ page }) => {
  await page.goto("/settings/models");
  await expect(page.getByRole("heading", { name: "模型与能力" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "DeepSeek V4 Flash" })).toBeVisible();
  await expect(page.getByText("可用于对话")).toBeVisible();
  await expect(page.getByText("输入缓存")).toBeVisible();
  await expect(page.getByText("极简 / 中等 / 很高")).toBeVisible();
  await expect(page.getByText("支持关闭")).toBeVisible();
});

test("renders the application not-found boundary", async ({ page }) => {
  await page.goto("/route-that-does-not-exist");
  await expect(page.getByText("页面不存在或不可用")).toBeVisible();
});

test("rejects a billable same-session submission while another tab owns its lock", async ({
  context,
  page,
}) => {
  const lockOwnerPage = await context.newPage();
  await installApiMocks(lockOwnerPage);
  await lockOwnerPage.goto("/c/session-1");
  const lockName = conversationSubmissionLockName("session-1");
  await lockOwnerPage.evaluate(async (name) => {
    await new Promise<void>((lockAcquired) => {
      void navigator.locks.request(name, async () => {
        lockAcquired();
        await new Promise<void>((releaseLock) => {
          (window as Window & { releaseConversationTestLock?: () => void })
            .releaseConversationTestLock = releaseLock;
        });
      });
    });
  }, lockName);

  await page.goto("/c/session-1");
  await page.getByRole("textbox", { name: "消息内容" }).fill("must not be submitted");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".error-banner")).toContainText("另一个标签页生成回复");
  await lockOwnerPage.evaluate(() => {
    (window as Window & { releaseConversationTestLock?: () => void })
      .releaseConversationTestLock?.();
  });
  await lockOwnerPage.close();
});

test("restores one maximum message page within the Phase 4 visibility budget", async ({ page }) => {
  const messages = Array.from(
    { length: WEB_PERFORMANCE_BUDGETS.restoredMessageCount },
    (_, index) => ({
      message_id: `message-${index + 1}`,
      session_id: "session-1",
      run_id: null,
      source_model_attempt_id: null,
      parent_message_id: null,
      role: index % 2 === 0 ? "user" : "assistant",
      content_type: "text",
      content_text: `bounded history message ${index + 1}`,
      content_uri: null,
      sequence: index + 1,
      token_count: 4,
      metadata_json: {},
      reasoning_blocks: [],
      created_at: "2026-08-30T00:00:00Z",
    }),
  );
  await page.unroute("**/api/nexus/**");
  await installApiMocks(page, { messages });
  await page.goto("/c/session-1");
  await expect(page.locator("article.message")).toHaveCount(
    WEB_PERFORMANCE_BUDGETS.restoredMessageCount,
    { timeout: WEB_PERFORMANCE_BUDGETS.restoredConversationVisibleMs },
  );
});
