<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ChevronLeft, Code2, LogOut, Menu, ScanSearch } from "@lucide/vue";
import { ElButton, ElMenu, ElMenuItem, ElSwitch, ElTooltip } from "element-plus";

import { clearSession, sessionState } from "../auth/session";
import { useCapabilitiesStore } from "../stores/capabilities";
import { usePrefsStore } from "../stores/prefs";

const route = useRoute();
const router = useRouter();
const capabilities = useCapabilitiesStore();
const prefs = usePrefsStore();
const activeMenuPath = computed(() => (route.path.startsWith("/analysis/") ? "/analysis/image" : route.path));

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
            :default-active="activeMenuPath"
            router
            :collapse="prefs.sidebarCollapsed"
          >
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
      <!-- 路由切换与页面级动态状态的读屏通告（方案 §11.8） -->
      <div class="visually-hidden" role="status" aria-live="polite">当前页面：{{ route.meta.title }}</div>
    </div>
  </div>
</template>
