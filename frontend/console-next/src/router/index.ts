import { nextTick } from "vue";
import { createMemoryHistory, createRouter, createWebHashHistory } from "vue-router";

import { clearSession, hasSession } from "../auth/session";
import ConsoleLayout from "../layouts/ConsoleLayout.vue";
import { useCapabilitiesStore } from "../stores/capabilities";
import LoginView from "../views/LoginView.vue";

const router = createRouter({
  history: window.location.pathname === "/" ? createMemoryHistory() : createWebHashHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: LoginView,
      meta: { title: "登录", public: true },
    },
    {
      path: "/",
      component: ConsoleLayout,
      children: [
        {
          path: "",
          name: "overview",
          component: () => import("../views/OverviewView.vue"),
          meta: { title: "总览", permission: "admin:status", feature: "console_workbench_v2" },
        },
        { path: "analysis", redirect: "/analysis/image", meta: { title: "智能分析" } },
        {
          path: "analysis/image",
          name: "analysis-image",
          component: () => import("../views/analysis/ImageAnalysisView.vue"),
          meta: { title: "图片分析", permission: "infer", feature: "console_workbench_v2" },
        },
        {
          path: "analysis/video",
          name: "analysis-video",
          component: () => import("../views/analysis/VideoJobsView.vue"),
          meta: { title: "视频任务", permission: "jobs:read", feature: "console_workbench_v2" },
        },
        {
          path: "analysis/video/:jobId",
          name: "analysis-video-detail",
          component: () => import("../views/analysis/VideoJobsView.vue"),
          meta: { title: "视频任务详情", permission: "jobs:read", feature: "console_workbench_v2" },
        },
        {
          path: "analysis/stream",
          name: "analysis-stream",
          component: () => import("../views/analysis/StreamsView.vue"),
          meta: { title: "实时视频流", permission: "streams:read", feature: "console_workbench_v2" },
        },
        {
          path: "analysis/results",
          name: "analysis-results",
          component: () => import("../views/analysis/AnalysisResultsView.vue"),
          meta: { title: "解析结果库", permission: "infer", feature: "console_workbench_v2" },
        },
        {
          path: "compare",
          name: "compare",
          component: () => import("../views/CompareView.vue"),
          meta: { title: "比对", permission: "compare", feature: "console_workbench_v2" },
        },
        {
          path: "search",
          name: "search",
          component: () => import("../views/SearchView.vue"),
          meta: { title: "以图搜人", permission: "gallery:read", feature: "console_workbench_v2" },
        },
        {
          path: "gallery",
          name: "gallery",
          component: () => import("../views/GalleryView.vue"),
          meta: { title: "人员库", permission: "gallery:read", feature: "console_workbench_v2" },
        },
        {
          path: "gallery/:personId",
          name: "gallery-detail",
          component: () => import("../views/GalleryView.vue"),
          meta: { title: "人员详情", permission: "gallery:read", feature: "console_workbench_v2" },
        },
        {
          path: "dev/access",
          name: "dev-access",
          component: () => import("../views/dev/AccessView.vue"),
          meta: { title: "接入配置", permission: "access:read", feature: "console_developer_v2" },
        },
        {
          path: "dev/playground",
          name: "dev-playground",
          component: () => import("../views/dev/PlaygroundView.vue"),
          meta: { title: "调试台", permission: "infer", feature: "console_developer_v2" },
        },
        {
          path: "dev/logs",
          name: "dev-logs",
          component: () => import("../views/dev/LogsView.vue"),
          meta: { title: "调用日志", permission: "access:read", feature: "console_developer_v2" },
        },
        {
          path: "admin/models",
          name: "admin-models",
          component: () => import("../views/admin/ModelsView.vue"),
          meta: { title: "模型中心", permission: "models:read", feature: "console_admin_v2" },
        },
        {
          path: "admin/calibration",
          name: "admin-calibration",
          component: () => import("../views/admin/CalibrationView.vue"),
          meta: { title: "阈值与标注", permission: "models:read", feature: "console_admin_v2" },
        },
        {
          path: "admin/ops",
          name: "admin-ops",
          component: () => import("../views/admin/OpsView.vue"),
          meta: { title: "运维与合规", permission: "admin:status", feature: "console_admin_v2" },
        },
        {
          path: "forbidden",
          name: "forbidden",
          component: () => import("../views/ForbiddenView.vue"),
          meta: { title: "无权访问" },
        },
      ],
    },
    {
      path: "/:pathMatch(.*)*",
      name: "not-found",
      component: () => import("../views/NotFoundView.vue"),
      meta: { title: "页面不存在", public: true },
    },
  ],
});

function openRootLogin(redirect: string): false {
  const target = redirect.startsWith("/") && !redirect.startsWith("//") ? redirect : "/";
  window.location.replace(`/?redirect=${encodeURIComponent(target)}`);
  return false;
}

router.beforeEach(async (to) => {
  document.title = `影鉴 · ${to.meta.title}`;
  const isRootLogin = window.location.pathname === "/";
  if (isRootLogin && to.path === "/" && !hasSession.value) return true;
  if (to.meta.public) {
    if (to.name === "login") {
      if (hasSession.value) return { name: "overview" };
      if (!isRootLogin) {
        const redirect = typeof to.query.redirect === "string" ? to.query.redirect : "/";
        return openRootLogin(redirect);
      }
    }
    return true;
  }
  if (!hasSession.value) return openRootLogin(to.fullPath);
  const capabilities = useCapabilitiesStore();
  try {
    await capabilities.load();
  } catch {
    clearSession();
    capabilities.clear();
    return openRootLogin(to.fullPath);
  }
  if (!capabilities.featureEnabled(to.meta.feature) || !capabilities.hasPermission(to.meta.permission)) {
    return to.name === "forbidden" ? true : { name: "forbidden" };
  }
  return true;
});

router.afterEach(() => {
  void nextTick(() => document.querySelector<HTMLElement>("#main-content")?.focus());
});

export default router;
