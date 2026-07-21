<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ChevronLeft, Code2, LogOut, Menu } from "@lucide/vue";
import { ElButton, ElMenu, ElMenuItem, ElSwitch, ElTooltip } from "element-plus";

import { apiRequest } from "../api/client";
import brandMarkUrl from "../assets/portrait-hub-mark.svg";
import { clearSession, sessionState } from "../auth/session";
import { useCapabilitiesStore } from "../stores/capabilities";
import { usePrefsStore } from "../stores/prefs";
import { formatTimestamp } from "../utils/format";

const route = useRoute();
const router = useRouter();
const capabilities = useCapabilitiesStore();
const prefs = usePrefsStore();
const logoutPending = ref(false);
const activeMenuPath = computed(() => {
  if (route.path.startsWith("/analysis/video/")) return "/analysis/video";
  if (route.path.startsWith("/analysis/stream/")) return "/analysis/stream";
  return route.path;
});
const sessionExpiryLabel = computed(() => formatTimestamp(sessionState.expiresAt));
const PAGE_DESCRIPTIONS: Record<string, string> = {
  overview:
    "\u5f53\u524d\u79df\u6237\u7684\u670d\u52a1\u72b6\u6001\u3001SLO\u3001\u8c03\u7528\u60c5\u51b5\u4e0e\u5f85\u5904\u7406\u8d44\u6e90\u3002",
  "analysis-image":
    "\u4e0a\u4f20\u56fe\u7247\u5e76\u9009\u62e9\u5206\u6790\u80fd\u529b\uff0c\u7ed3\u679c\u4ee5\u4e1a\u52a1\u6458\u8981\u548c\u56fe\u50cf\u8bc1\u636e\u5448\u73b0\u3002",
  "analysis-video":
    "\u521b\u5efa\u89e3\u6790\u4efb\u52a1\u5e76\u81ea\u52a8\u8ddf\u8e2a\u8fdb\u5ea6\uff0c\u65e0\u9700\u8bb0\u5f55\u6216\u7c98\u8d34\u4efb\u52a1 ID\u3002",
  "analysis-video-detail":
    "\u67e5\u770b\u89c6\u9891\u4efb\u52a1\u7684\u5904\u7406\u8fdb\u5ea6\u3001\u7ed3\u679c\u4e0e\u9519\u8bef\u4fe1\u606f\u3002",
  "analysis-stream":
    "\u96c6\u4e2d\u7ba1\u7406\u6d41\u5730\u5740\u3001\u8fd0\u884c\u72b6\u6001\u548c\u6700\u8fd1\u4e8b\u4ef6\u3002",
  "analysis-stream-detail":
    "\u67e5\u770b\u89c6\u9891\u6d41\u8fd0\u884c\u72b6\u6001\u3001\u5206\u6790\u7ed3\u679c\u4e0e\u6700\u8fd1\u4e8b\u4ef6\u3002",
  "analysis-results":
    "\u7edf\u4e00\u67e5\u770b\u5f53\u524d\u79df\u6237\u7684\u56fe\u7247\u3001\u89c6\u9891\u548c\u89c6\u9891\u6d41\u89e3\u6790\u6863\u6848\u3002",
  compare:
    "\u5e76\u6392\u6838\u9a8c\u8bc1\u636e\uff0c\u4ee5\u76f8\u4f3c\u5ea6\u3001\u9608\u503c\u3001\u8d28\u91cf\u548c\u98ce\u9669\u7ed9\u51fa\u7ed3\u8bba\u3002",
  search:
    "\u4e0a\u4f20\u67e5\u8be2\u56fe\uff0c\u6309\u6a21\u677f\u76f8\u4f3c\u5ea6\u3001\u8d28\u91cf\u4e0e\u98ce\u9669\u8fd4\u56de\u5019\u9009\u4eba\u5458\u3002",
  gallery:
    "\u6309\u4eba\u5458\u7ba1\u7406\u7279\u5f81\u56fe\u50cf\u3001\u6a21\u6001\u3001\u8d28\u91cf\u548c\u4e1a\u52a1\u5143\u6570\u636e\u3002",
  "gallery-detail":
    "\u67e5\u770b\u4eba\u5458\u8d44\u6599\u3001\u7279\u5f81\u56fe\u50cf\u548c\u4e1a\u52a1\u5143\u6570\u636e\u3002",
  "dev-access":
    "\u7ba1\u7406\u5e94\u7528\u8bbf\u95ee\u8303\u56f4\u3001\u8c03\u7528\u72b6\u6001\u4e0e\u4e8b\u4ef6\u56de\u8c03\u3002",
  "dev-playground":
    "\u6309\u771f\u5b9e\u63a5\u53e3\u5951\u7ea6\u6784\u9020\u53ea\u8bfb\u3001\u6279\u91cf\u4e0e\u89c6\u9891\u6d41\u8bf7\u6c42\uff0c\u5e76\u67e5\u770b\u8131\u654f\u54cd\u5e94\u3002",
  "dev-logs":
    "\u6309\u8bf7\u6c42\u3001\u72b6\u6001\u4e0e\u5e94\u7528\u6392\u67e5 \u63a5\u53e3\u8c03\u7528\u3002",
  "admin-identity": "管理企业身份来源、当前用户、租户角色与权限映射。",
  "admin-models":
    "\u7ba1\u7406\u6a21\u578b\u8fd0\u884c\u72b6\u6001\u3001\u522b\u540d\u53d1\u5e03\u4e0e\u8bc4\u4f30\u57fa\u7ebf\u3002",
  "admin-calibration":
    "\u7ef4\u62a4\u5404\u6a21\u6001\u9608\u503c\uff0c\u8bb0\u5f55\u4eba\u5de5\u590d\u6838\uff0c\u5e76\u5c06\u590d\u6838\u6c60\u8f6c\u5316\u4e3a\u8bc4\u4f30\u6570\u636e\u548c\u9608\u503c\u5efa\u8bae\u3002",
  "admin-ops":
    "\u67e5\u770b\u8fd0\u884c\u540e\u7aef\u3001\u5ba1\u8ba1\u94fe\u548c\u5907\u4efd\uff0c\u5e76\u6267\u884c\u53d7\u63a7\u4fdd\u7559\u7b56\u7565\u3002",
  forbidden: "\u5f53\u524d\u51ed\u8bc1\u4e0d\u5177\u5907\u6b64\u9875\u9762\u6240\u9700\u6743\u9650\u3002",
};
const pageDescription = computed(() => PAGE_DESCRIPTIONS[String(route.name)] ?? "");

// 单一导航数据源（方案 §6）：由路由表 meta.nav 派生侧栏，标题/权限/图标只在路由声明一次。
const SECTION_ORDER = ["工作台", "开发者中心", "系统管理"];

const visibleSections = computed(() => {
  const items = router
    .getRoutes()
    .filter((record) => record.meta.nav)
    .map((record) => ({
      label: record.meta.nav!.label ?? record.meta.title ?? record.path,
      path: record.path,
      icon: record.meta.nav!.icon,
      section: record.meta.nav!.section,
      order: record.meta.nav!.order,
      permission: record.meta.permission,
    }))
    .filter((item) => capabilities.hasPermission(item.permission))
    .sort((a, b) => a.order - b.order);
  return SECTION_ORDER.map((label) => ({
    label,
    items: items.filter((item) => item.section === label),
  })).filter((section) => section.items.length > 0);
});

async function logout(): Promise<void> {
  if (logoutPending.value) return;
  logoutPending.value = true;

  const browserSession = sessionState.authMode === "oidc" || sessionState.authMode === "local";
  const serverLogout = browserSession
    ? apiRequest("/v1/auth/logout", { method: "POST" }, 3_000).catch(() => undefined)
    : Promise.resolve();

  clearSession();
  capabilities.clear();
  await serverLogout;
  window.location.replace("/?logged_out=1");
}
</script>

<template>
  <div class="console-shell" :data-collapsed="prefs.sidebarCollapsed">
    <aside class="console-sidebar">
      <div class="brand-block">
        <div class="brand-mark" aria-hidden="true">
          <img :src="brandMarkUrl" alt="" />
        </div>
        <div v-if="!prefs.sidebarCollapsed" class="brand-copy">
          <strong>影鉴</strong><span>业务控制台</span>
        </div>
      </div>
      <nav aria-label="主导航" class="sidebar-nav" tabindex="0">
        <section v-for="section in visibleSections" :key="section.label" class="nav-section">
          <div v-if="!prefs.sidebarCollapsed" class="nav-section__title">{{ section.label }}</div>
          <ElMenu :default-active="activeMenuPath" router :collapse="prefs.sidebarCollapsed">
            <ElMenuItem v-for="item in section.items" :key="item.path" :index="item.path">
              <component :is="item.icon" :size="18" aria-hidden="true" />
              <template #title>{{ item.label }}</template>
            </ElMenuItem>
          </ElMenu>
        </section>
      </nav>
      <div class="sidebar-footer">
        <ElTooltip :content="prefs.sidebarCollapsed ? '展开侧栏' : '收起侧栏'" placement="right">
          <ElButton
            text
            :icon="prefs.sidebarCollapsed ? Menu : ChevronLeft"
            aria-label="切换侧栏"
            @click="prefs.sidebarCollapsed = !prefs.sidebarCollapsed"
          />
        </ElTooltip>
      </div>
    </aside>

    <div class="console-workspace">
      <header class="console-topbar">
        <div class="topbar-context">
          <div class="topbar-page-heading">
            <h1>{{ route.meta.title }}</h1>
            <p v-if="pageDescription">{{ pageDescription }}</p>
          </div>
          <span v-if="sessionState.tenantId" class="tenant-label">{{ sessionState.tenantId }}</span>
          <span v-if="sessionState.expiresAt" class="session-expiry" title="当前凭证到期时间">
            会话至 {{ sessionExpiryLabel }}
          </span>
        </div>
        <div class="topbar-actions">
          <label class="developer-switch">
            <Code2 :size="17" />
            <span>调试信息</span>
            <ElSwitch v-model="prefs.developerMode" aria-label="调试信息" />
          </label>
          <ElTooltip content="退出当前会话" placement="bottom">
            <ElButton :icon="LogOut" circle aria-label="退出" :loading="logoutPending" @click="logout" />
          </ElTooltip>
        </div>
      </header>
      <main id="main-content" class="console-main" tabindex="-1">
        <RouterView />
      </main>
      <!-- 路由切换与页面级动态状态的读屏通告（方案 §11.8） -->
      <div class="visually-hidden" role="status" aria-live="polite">当前页面：{{ route.meta.title }}</div>
    </div>
  </div>
</template>
