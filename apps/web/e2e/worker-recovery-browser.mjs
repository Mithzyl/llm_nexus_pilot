/** Observe the real local API through the real BFF; used by the opt-in Worker integration test. */
import { chromium, expect } from "@playwright/test";

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.WORKER_TEST_WEB_URL}/c/${process.env.WORKER_TEST_SESSION_ID}`);
  await expect(page.locator("article.assistant-message")).toBeVisible({timeout:30000});
  await page.reload();
  await expect(page.locator("article.assistant-message")).toBeVisible({timeout:30000});
  console.log("observing");
  if (process.env.WORKER_TEST_CANCEL === "1") {
    const stop = page.getByRole("button", {name:"取消工作流",exact:true});
    const processTab = page.getByRole("button", {name:"运行过程",exact:true});
    if (!await processTab.isVisible()) await page.getByRole("button", {name:"运行详情",exact:true}).click();
    await processTab.click();
    await stop.click();
    await expect(page.locator("article.assistant-message")).toContainText("已取消", {timeout:30000});
  } else {
    await expect(page.locator("article.assistant-message")).toContainText("普通回答", {timeout:45000});
    await page.reload();
    await expect(page.locator("article.assistant-message")).toContainText("普通回答", {timeout:30000});
  }
  await page.screenshot({path:process.env.WORKER_TEST_SCREENSHOT,fullPage:true});
  console.log("verified");
} finally {
  await browser.close();
}
