<script setup lang="ts">
import { computed, ref } from "vue";
import { Code2, Copy } from "@lucide/vue";
import { ElButton, ElDrawer, ElMessage } from "element-plus";

import { redactForDisplay } from "../utils/redact";

const props = defineProps<{ modelValue: boolean; data: unknown; title?: string }>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();
const copying = ref(false);
const content = computed(() => JSON.stringify(redactForDisplay(props.data), null, 2));

async function copyContent(): Promise<void> {
  copying.value = true;
  try {
    await navigator.clipboard.writeText(content.value);
    ElMessage.success("脱敏数据已复制");
  } finally {
    copying.value = false;
  }
}
</script>

<template>
  <ElDrawer
    :model-value="modelValue"
    :title="title ?? '原始数据（已脱敏）'"
    size="min(720px, 92vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="drawer-title">
        <Code2 :size="19" /><span>{{ title ?? "原始数据（已脱敏）" }}</span>
      </div>
    </template>
    <div class="raw-data-toolbar">
      <ElButton :icon="Copy" :loading="copying" @click="copyContent">复制</ElButton>
    </div>
    <pre class="raw-data-view">{{ content }}</pre>
  </ElDrawer>
</template>
