<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import {
  Activity,
  Boxes,
  Braces,
  ChevronLeft,
  Code2,
  ExternalLink,
  FileClock,
  Gauge,
  Images,
  LogOut,
  Menu,
  ScanSearch,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Waypoints,
} from "@lucide/vue";
import { ElButton, ElMenu, ElMenuItem, ElSwitch, ElTooltip } from "element-plus";

import { clearSession, sessionState } from "../auth/session";
import { useCapabilitiesStore } from "../stores/capabilities";
import { usePrefsStore } from "../stores/prefs";

const route = useRoute();
const capabilities = useCapabilitiesStore();
const prefs = usePrefsStore();
const activeMenuPath = computed(() => (route.path.startsWith("/analysis/") ? "/analysis/image" : route.path));

const sections = [
  {
    label: "工作台",
    feature: "console_workbench_v2" as const,
    items: [
      { label: "总览", path: "/", permission: "admin:status", icon: Gauge },
      { label: "智能分析", path: "/analysis/image", permission: "infer", icon: Activity },
      { label: "比对", path: "/compare", permission: "compare", icon: Waypoints },
      { label: "以图搜人", path: "/search", permission: "gallery:read", icon: ScanSearch },
      { label: "人员库", path: "/gallery", permission: "gallery:read", icon: Images },
    ],
  },
  {
    label: "开发者中心",
    feature: "console_developer_v2" as const,
    items: [
      { label: "接入配置", path: "/dev/access", permission: "access:read", icon: Settings2 },
      { label: "调试台", path: "/dev/playground", permission: "infer", icon: Braces },
      { label: "调用日志", path: "/dev/logs", permission: "access:read", icon: FileClock },
    ],
  },
  {
    label: "系统管理",
    feature: "console_admin_v2" as const,
    items: [
      { label: "模型中心", path: "/admin/models", permission: "models:read", icon: Boxes },
      { label: "阈值与标注", path: "/admin/calibration", permission: "models:read", icon: SlidersHorizontal },
      { label: "运维与合规", path: "/admin/ops", permission: "admin:status", icon: ShieldCheck },
    ],
  },
];

const visibleSections = computed(() =>
  sections.map((section) => ({
    ...section,
    enabled: capabilities.featureEnabled(section.feature),
    items: section.items.filter((item) => capabilities.hasPermission(item.permission)),
  })),
);

function logout(): void {
  clearSession();
  capabilities.clear();
  window.location.assign("/");
}
</script>

<template>
  <div class="console-shell" :data-collapsed="prefs.sidebarCollapsed">
    <aside class="console-sidebar">
      <div class="brand-block">
        <div class="brand-mark" aria-hidden="true"><ScanSearch :size="23" /></div>
        <div v-if="!prefs.sidebarCollapsed" class="brand-copy">
          <strong>影鉴</strong><span>业务控制台</span>
        </div>
      </div>
      <nav aria-label="主导航" class="sidebar-nav" tabindex="0">
        <section v-for="section in visibleSections" :key="section.label" class="nav-section">
          <div v-if="!prefs.sidebarCollapsed" class="nav-section__title">{{ section.label }}</div>
          <ElMenu
            v-if="section.enabled"
            :default-active="activeMenuPath"
            router
            :collapse="prefs.sidebarCollapsed"
          >
            <ElMenuItem v-for="item in section.items" :key="item.path" :index="item.path">
              <component :is="item.icon" :size="18" aria-hidden="true" />
              <template #title>{{ item.label }}</template>
            </ElMenuItem>
          </ElMenu>
          <a v-else-if="!prefs.sidebarCollapsed" class="legacy-link" href="/console/legacy">
            <ExternalLink :size="15" />旧版{{ section.label }}
          </a>
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
          <span class="topbar-title">{{ route.meta.title }}</span>
          <span class="tenant-label">{{ sessionState.tenantId }}</span>
        </div>
        <div class="topbar-actions">
          <label class="developer-switch">
            <Code2 :size="17" />
            <span>开发者模式</span>
            <ElSwitch v-model="prefs.developerMode" aria-label="开发者模式" />
          </label>
          <ElTooltip content="退出当前会话" placement="bottom">
            <ElButton :icon="LogOut" circle aria-label="退出" @click="logout" />
          </ElTooltip>
        </div>
      </header>
      <main id="main-content" class="console-main" tabindex="-1">
        <RouterView />
      </main>
    </div>
  </div>
</template>
