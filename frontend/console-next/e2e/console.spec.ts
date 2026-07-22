import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

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

async function loginAsDefaultAdmin(page: Page): Promise<void> {
  await page.getByLabel("密码", { exact: true }).fill("123456");
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

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
  await expect(page.getByLabel("用户名", { exact: true })).toHaveValue("admin");
  await expect(page.getByLabel("密码", { exact: true })).toBeVisible();
  await expect(page.getByLabel("接口密钥")).toHaveCount(0);
  expect(new URL(page.url()).pathname).toBe("/");
  expect(new URL(page.url()).hash).toBe("");

  await loginAsDefaultAdmin(page);

  await expect(page).toHaveURL(/\/console#\/$/);
  await expect(page.getByRole("heading", { name: "总览", exact: true })).toBeVisible();

  const browserState = await page.evaluate(() => ({
    localValues: Object.values(window.localStorage),
    sessionValues: Object.values(window.sessionStorage),
    url: window.location.href,
  }));
  expect(browserState.localValues).toEqual([]);
  const storedSession = JSON.parse(browserState.sessionValues[0] ?? "{}") as {
    apiKey?: string;
    bearer?: string;
  };
  expect(storedSession.apiKey).toBe("");
  expect(storedSession.bearer).toBe("");
  expect(browserState.localValues.join(" ")).not.toContain("123456");
  expect(browserState.sessionValues.join(" ")).not.toContain("123456");
  expect(browserState.url).not.toContain("token");

  await page.getByRole("menuitem", { name: "图片分析" }).click();
  await expect(page.getByRole("heading", { name: "图片分析" }).first()).toBeVisible();
  for (const menuName of [
    "总览",
    "图片分析",
    "视频任务",
    "实时视频流",
    "分析结果",
    "人员比对",
    "以图搜人",
    "人员库",
  ]) {
    await expect(page.getByRole("menuitem", { name: menuName })).toBeVisible();
  }
  await expect(page.getByRole("menuitem", { name: "智能分析" })).toHaveCount(0);
  const developerSwitch = page.locator(".developer-switch");
  if (testInfo.project.name.includes("mobile")) {
    await expect(developerSwitch).toHaveCount(1);
  } else {
    await expect(developerSwitch).toBeVisible();
  }
  await expect(page.getByRole("switch", { name: "调试信息" })).toHaveCount(1);
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
  await loginAsDefaultAdmin(page);

  await expect(page).toHaveURL(/#\/analysis\/video$/);
  await expect(page.getByRole("heading", { name: "视频任务" })).toBeVisible();
  expect(await page.evaluate(() => window.__portraitCspViolations)).toEqual([]);
});

test("[E2E-AUTH-02] clears the local session immediately while server logout is pending", async ({ page }) => {
  await page.goto("/");
  await loginAsDefaultAdmin(page);
  await expect(page).toHaveURL(/\/console#\/$/);
  await expect(page.getByRole("heading", { name: "总览", exact: true })).toBeVisible();

  let restoreAttempts = 0;
  await page.route("**/v1/console/me", async (route) => {
    restoreAttempts += 1;
    await route.fulfill({
      json: {
        status: "success",
        data: {
          tenant_id: "default",
          auth_kind: "development_anonymous",
          subject: "anonymous",
          roles: ["admin"],
          permissions: ["*"],
          scopes: [],
          expires_at: null,
          identity: { enabled: false, provider_name: "", issuer: "", identity_admin_url: "" },
        },
      },
    });
  });

  let continueLogout: (() => void) | undefined;
  const logoutCanContinue = new Promise<void>((resolve) => {
    continueLogout = resolve;
  });
  await page.route("**/v1/auth/logout", async (route) => {
    await logoutCanContinue;
    await route.continue();
  });

  try {
    await page.getByRole("button", { name: "退出", exact: true }).click();

    await expect(page.getByRole("button", { name: "退出", exact: true })).toBeDisabled();
    await expect.poll(() => page.evaluate(() => window.sessionStorage.length)).toBe(0);
  } finally {
    continueLogout?.();
  }

  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:\d+\/\?logged_out=1$/);
  await expect(page.getByRole("heading", { name: "登录控制台" })).toBeVisible();
  expect(restoreAttempts).toBe(0);
});

test("[E2E-MODELS-01] shows GPU assignment and clear weighted rollout roles", async ({ page }) => {
  await page.goto("/");
  await loginAsDefaultAdmin(page);
  await page.goto("/console#/admin/models");

  await expect(page.getByRole("heading", { name: "模型中心", exact: true })).toBeVisible();
  await expect(page.getByText("运行时 GPU", { exact: true })).toBeVisible();
  const selectors = page.locator(".gpu-device-select");
  await expect(selectors).toHaveCount(2);
  await selectors.first().click();
  await expect(page.getByRole("option", { name: "自动分配", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: /GPU 0/ })).toBeVisible();

  await page.getByRole("tab", { name: "发布", exact: true }).click();
  await expect(page.getByText("灰度目标 1", { exact: true })).toBeVisible();
  const rolloutRoles = page.locator(".weighted-role-select");
  await expect(rolloutRoles).toHaveCount(2);
  await expect(rolloutRoles.first()).toContainText("当前稳定版本");
  await expect(rolloutRoles.nth(1)).toContainText("候选灰度版本");
});

test("[E2E-CONFIG-01] manages network CIDRs and exposes the complete configuration catalog", async ({
  page,
}, testInfo) => {
  await page.route("**/v1/admin/network-access-policy", async (route) => {
    const request = route.request();
    if (request.method() === "PUT") {
      const body = request.postDataJSON() as {
        stream: { allow_private_hosts: boolean; allowed_hosts: string[]; allowed_cidrs: string[] };
        webhook: { allow_private_hosts: boolean; allowed_hosts: string[]; allowed_cidrs: string[] };
      };
      await route.fulfill({
        json: {
          status: "success",
          request_id: "e2e-config-save",
          data: { revision: 2, updated_at: Date.now() / 1000, stream: body.stream, webhook: body.webhook },
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        status: "success",
        request_id: "e2e-config-load",
        data: {
          revision: 1,
          updated_at: null,
          stream: { allow_private_hosts: false, allowed_hosts: [], allowed_cidrs: [] },
          webhook: { allow_private_hosts: false, allowed_hosts: [], allowed_cidrs: [] },
        },
      },
    });
  });

  await page.goto("/");
  await loginAsDefaultAdmin(page);
  await expect(page).toHaveURL(/\/console#\/$/);
  await page.goto("/console#/admin/configuration");

  await expect(page.getByRole("heading", { name: "配置中心", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "视频流访问", exact: true })).toBeVisible();
  await page.getByLabel("视频流允许的 IP 网段").fill("10.30.0.0/16");
  await page.locator(".policy-panel").first().locator(".el-switch").click();
  await expect(page.getByText("允许私网访问时必须填写主机或 CIDR 网段")).toHaveCount(0);
  await page.getByRole("button", { name: "保存策略" }).click();
  await expect(page.getByText("网络访问策略已生效")).toBeVisible();

  await page.getByRole("tab", { name: "全部配置" }).click();
  await page.getByLabel("搜索配置").fill("GPU_WORKER_0_DEVICE");
  const configurationEntry = testInfo.project.name.includes("mobile")
    ? page.locator(".mobile-config-item").filter({ hasText: "GPU_WORKER_0_DEVICE" })
    : page.getByRole("row").filter({ hasText: "GPU_WORKER_0_DEVICE" });
  await expect(configurationEntry).toBeVisible();
  await expect(configurationEntry).toContainText("重建容器");
  await configurationEntry.getByRole("button", { name: "修改配置" }).click();
  await expect(page.getByRole("dialog", { name: "GPU_WORKER_0_DEVICE" })).toBeVisible();
  await expect(page.getByText("需要在宿主机同步 .env 后重建容器")).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

  await expect(page.locator("body")).not.toHaveCSS("overflow-x", "scroll");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((violation) => ["serious", "critical"].includes(violation.impact ?? "")),
  ).toEqual([]);
  await page.screenshot({
    path: testInfo.outputPath("configuration-" + testInfo.project.name + ".png"),
    fullPage: true,
  });
});

test("[E2E-ROUTES-02] loads every product route and opens guarded dialogs without CSP violations", async ({
  page,
}) => {
  await page.goto("/console/next");
  await loginAsDefaultAdmin(page);
  await expect(page).toHaveURL(/\/console#\/$/);
  await expect(page.getByRole("heading", { name: "总览", exact: true })).toBeVisible();

  const routes = [
    ["/", "总览"],
    ["/analysis/image", "图片分析"],
    ["/analysis/video", "视频任务"],
    ["/analysis/stream", "实时视频流"],
    ["/analysis/results", "分析结果"],
    ["/compare", "人员比对"],
    ["/search", "以图搜人"],
    ["/gallery", "人员库"],
    ["/dev/access", "接入配置"],
    ["/dev/playground", "调试台"],
    ["/dev/logs", "调用日志"],
    ["/admin/identity", "身份与权限"],
    ["/admin/models", "模型中心"],
    ["/admin/calibration", "阈值与标注"],
    ["/admin/configuration", "配置中心"],
    ["/admin/ops", "运维与合规"],
  ] as const;

  for (const [route, heading] of routes) {
    await page.goto("/console#" + route);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expect(page.locator(".error-banner")).toHaveCount(0);
  }

  await page.goto("/console#/analysis");
  await expect(page.getByRole("heading", { name: "页面不存在", exact: true }).first()).toBeVisible();

  await page.goto("/console#/gallery");
  await page.getByRole("button", { name: "高级操作" }).click();
  await page.getByRole("menuitem", { name: "特征重建" }).click();
  await page.getByRole("button", { name: "执行预演" }).click();
  await expect(page.getByRole("button", { name: "执行重建" })).toBeEnabled();
  await expect(page.getByRole("dialog", { name: "特征重建预演" })).toBeVisible();
  await page.getByRole("button", { name: "执行重建" }).click();
  await expect(page.getByRole("dialog", { name: "执行特征重建" })).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByRole("button", { name: "关闭", exact: true }).click();

  await page.goto("/console#/dev/playground");
  await page.getByRole("tab", { name: "错误码" }).click();
  await expect(page.getByRole("cell", { name: "unauthorized", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "接口定义" }).click();
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
