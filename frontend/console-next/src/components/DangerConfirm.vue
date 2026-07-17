<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { AlertTriangle } from "@lucide/vue";
import { ElAlert, ElButton, ElDialog, ElInput } from "element-plus";

const props = withDefaults(
  defineProps<{
    modelValue: boolean;
    title: string;
    description: string;
    highRisk?: boolean;
    confirmationText?: string;
    loading?: boolean;
  }>(),
  { highRisk: false, confirmationText: "确认执行", loading: false },
);
const emit = defineEmits<{ "update:modelValue": [value: boolean]; confirm: [] }>();
const typed = ref("");
const canConfirm = computed(() => !props.highRisk || typed.value === props.confirmationText);

watch(
  () => props.modelValue,
  (open) => {
    if (!open) typed.value = "";
  },
);
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="title"
    :close-on-click-modal="false"
    width="min(520px, 92vw)"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="danger-title">
        <AlertTriangle :size="21" /><span>{{ title }}</span>
      </div>
    </template>
    <ElAlert :title="description" type="error" :closable="false" show-icon />
    <label v-if="highRisk" class="confirmation-field">
      <span>输入“{{ confirmationText }}”继续</span>
      <ElInput v-model="typed" autocomplete="off" />
    </label>
    <template #footer>
      <ElButton @click="emit('update:modelValue', false)">取消</ElButton>
      <ElButton type="danger" :disabled="!canConfirm" :loading="loading" @click="emit('confirm')">
        确认
      </ElButton>
    </template>
  </ElDialog>
</template>
