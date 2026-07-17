import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

declare global {
  interface Window {
    __portraitCspViolations?: Array<{
      blockedURI: string;
      directive: string;
    }>;
  }
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.__portraitCspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__portraitCspViolations?.push({
        blockedURI: event.blockedURI,
        directive: event.effectiveDirective,
      });
    });
  });
});

test("[E2E-SHELL-01] loads the public shell, logs in, and keeps credentials out of durable storage", async ({
  page,
}, testInfo) => {
  const response = await page.goto("/");

  expect(response?.status()).toBe(200);
  const csp = response?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("script-src 'self'");
  expect(csp).not.toContain("'unsafe-inline'");
  expect(csp).not.toContain("'unsafe-eval'");
  await expect(page.getByRole("heading", { name: "登录控制台" })).toBeVisible();
  expect(new URL(page.url()).pathname).toBe("/");
  expect(new URL(page.url()).hash).toBe("");

  const apiKey = "e2e-tab-only-secret";
  await page.getByRole("textbox", { name: "接口密钥", exact: true }).fill(apiKey);
  await page.getByRole("button", { name: "进入控制台" }).click();
  await expect(page.getByRole("heading", { name: "总览" })).toBeVisible();
  await expect(page).toHaveURL(/\/console#\/$/);

  const browserState = await page.evaluate((secret) => {
    const localValues = Object.values(window.localStorage);
    const sessionValues = Object.values(window.sessionStorage);
    return {
      localContainsSecret: localValues.some((value) => value.includes(secret)),
      sessionContainsSecret: sessionValues.some((value) => value.includes(secret)),
      urlContainsSecret: window.location.href.includes(secret),
    };
  }, apiKey);
  expect(browserState).toEqual({
    localContainsSecret: false,
    sessionContainsSecret: true,
    urlContainsSecret: false,
  });

  await page.getByRole("menuitem", { name: "智能分析" }).click();
  await expect(page.getByRole("heading", { name: "图片分析" })).toBeVisible();
  await expect(page.getByRole("link", { name: "图片分析" })).toBeVisible();
  await expect(page.getByRole("link", { name: "视频解析" })).toBeVisible();
  await expect(page.getByRole("link", { name: "视频流解析" })).toBeVisible();
  await expect(page.getByRole("link", { name: "解析结果库" })).toBeVisible();
  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((violation) => ["serious", "critical"].includes(violation.impact ?? "")),
  ).toEqual([]);

  expect(await page.evaluate(() => window.__portraitCspViolations)).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("console-" + testInfo.project.name + ".png"),
    fullPage: true,
  });
});

test("[E2E-ROUTE-01] supports direct deep links after authentication", async ({ page }) => {
  await page.goto("/console/next#/analysis/video");
  await expect(page.getByRole("heading", { name: "登录控制台" })).toBeVisible();
  await page.getByRole("button", { name: "进入控制台" }).click();

  await expect(page).toHaveURL(/#\/analysis\/video$/);
  await expect(page.getByRole("heading", { name: "视频任务" })).toBeVisible();
  expect(await page.evaluate(() => window.__portraitCspViolations)).toEqual([]);
});

test("[E2E-ROUTES-02] loads every product route and opens guarded dialogs without CSP violations", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop");

  await page.goto("/console/next");
  await page.getByRole("button", { name: "进入控制台" }).click();
  await expect(page).toHaveURL(/\/console#\/$/);
  await expect(page.getByRole("heading", { name: "总览", exact: true })).toBeVisible();

  const routes = [
    ["/", "总览"],
    ["/analysis/image", "图片分析"],
    ["/analysis/video", "视频任务"],
    ["/analysis/stream", "实时视频流"],
    ["/analysis/results", "解析结果库"],
    ["/compare", "比对"],
    ["/search", "以图搜人"],
    ["/gallery", "人员库"],
    ["/dev/access", "接入配置"],
    ["/dev/playground", "调试台"],
    ["/dev/logs", "调用日志"],
    ["/admin/models", "模型中心"],
    ["/admin/calibration", "阈值与标注"],
    ["/admin/ops", "运维与合规"],
  ] as const;

  for (const [route, heading] of routes) {
    await page.goto("/console#" + route);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".error-banner")).toHaveCount(0);
  }

  await page.goto("/console#/gallery");
  await page.getByRole("button", { name: "高级操作" }).click();
  await page.getByRole("menuitem", { name: "特征重建" }).click();
  await expect(page.getByRole("dialog", { name: "特征重建预演" })).toBeVisible();
  await page.getByRole("button", { name: "执行重建" }).click();
  await expect(page.getByRole("dialog", { name: "执行特征重建" })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByRole("button", { name: "关闭", exact: true }).click();

  await page.goto("/console#/dev/playground");
  await page.getByRole("tab", { name: "错误码" }).click();
  await expect(page.getByRole("cell", { name: "unauthorized", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "OpenAPI" }).click();
  await expect(page.getByText(/共 \d+ 个接口操作/)).toBeVisible();

  await page.goto("/console#/dev/access");
  await page.getByRole("button", { name: "创建应用" }).click();
  await expect(page.getByRole("dialog", { name: "创建接入应用" })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

  await page.goto("/console#/admin/calibration");
  await page.getByRole("button", { name: "保存方案" }).click();
  await expect(page.getByRole("dialog", { name: "保存阈值方案" })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

  await page.goto("/console#/admin/ops");
  await page.getByRole("button", { name: "数据清理" }).click();
  await expect(page.getByRole("dialog", { name: "执行数据清理" })).toBeVisible();

  expect(await page.evaluate(() => window.__portraitCspViolations)).toEqual([]);
});
