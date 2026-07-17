<script setup lang="ts">
import { computed } from "vue";
import { Archive, Image, Radio, Video } from "@lucide/vue";

import { useCapabilitiesStore } from "../stores/capabilities";

const capabilities = useCapabilitiesStore();
const items = computed(() =>
  [
    { label: "图片分析", path: "/analysis/image", permission: "infer", icon: Image },
    { label: "视频解析", path: "/analysis/video", permission: "jobs:read", icon: Video },
    { label: "视频流解析", path: "/analysis/stream", permission: "streams:read", icon: Radio },
    { label: "解析结果库", path: "/analysis/results", permission: "infer", icon: Archive },
  ].filter((item) => capabilities.hasPermission(item.permission)),
);
</script>

<template>
  <nav class="analysis-navigation" aria-label="智能分析视图">
    <RouterLink v-for="item in items" :key="item.path" :to="item.path">
      <component :is="item.icon" :size="17" aria-hidden="true" />
      <span>{{ item.label }}</span>
    </RouterLink>
  </nav>
</template>

<style scoped>
.analysis-navigation {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-bottom: 20px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.analysis-navigation a {
  min-width: 0;
  min-height: 46px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 9px 12px;
  color: #43514e;
  border-right: 1px solid #d8e0de;
  text-decoration: none;
}
.analysis-navigation a:last-child {
  border-right: 0;
}
.analysis-navigation a:hover {
  color: #075f69;
  background: #f3f8f7;
}
.analysis-navigation a.router-link-active {
  color: #fff;
  background: #087682;
}
@media (max-width: 700px) {
  .analysis-navigation {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .analysis-navigation a:nth-child(2) {
    border-right: 0;
  }
  .analysis-navigation a:nth-child(-n + 2) {
    border-bottom: 1px solid #d8e0de;
  }
}
</style>
