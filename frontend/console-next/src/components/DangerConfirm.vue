<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { AlertTriangle, ShieldCheck } from "@lucide/vue";
import { ElAlert, ElButton, ElDialog, ElInput } from "element-plus";

import { apiRequest } from "../api/client";
import type { StepUpStatus } from "../api/contracts";
import { errorBannerMessage } from "../utils/errors";

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
const password = ref("");
const stepUpStatus = ref<StepUpStatus | null>(null);
const stepUpLoading = ref(false);
const stepUpError = ref("");
const requiresStepUp = computed(() => props.highRisk && stepUpStatus.value?.recent !== true);
const canConfirm = computed(
  () =>
    (!props.highRisk || typed.value === props.confirmationText) &&
    (!requiresStepUp.value || (stepUpStatus.value?.auth_kind === "local" && Boolean(password.value))),
);

async function loadStepUpStatus(): Promise<void> {
  if (!props.highRisk) return;
  stepUpLoading.value = true;
  stepUpError.value = "";
  try {
    stepUpStatus.value = await apiRequest<StepUpStatus>("/v1/auth/step-up/status");
  } catch (error) {
    stepUpStatus.value = null;
    stepUpError.value = errorBannerMessage(error, "无法确认近期登录状态");
  } finally {
    stepUpLoading.value = false;
  }
}

async function confirmAction(): Promise<void> {
  if (!canConfirm.value) return;
  if (requiresStepUp.value && stepUpStatus.value?.auth_kind === "local") {
    stepUpLoading.value = true;
    stepUpError.value = "";
    try {
      await apiRequest("/v1/auth/local/step-up", {
        method: "POST",
        body: JSON.stringify({ password: password.value }),
      });
      password.value = "";
      await loadStepUpStatus();
    } catch (error) {
      stepUpError.value = errorBannerMessage(error, "身份验证失败");
      return;
    } finally {
      stepUpLoading.value = false;
    }
  }
  if (stepUpStatus.value?.recent === false) return;
  emit("confirm");
}

function startOidcStepUp(): void {
  const returnTo = window.location.pathname + window.location.search + window.location.hash;
  window.location.assign("/auth/oidc/step-up?return_to=" + encodeURIComponent(returnTo));
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) void loadStepUpStatus();
    else {
      typed.value = "";
      password.value = "";
      stepUpStatus.value = null;
      stepUpError.value = "";
    }
  },
  { immediate: true },
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
    <ElAlert v-if="stepUpError" class="step-up-alert" :title="stepUpError" type="error" :closable="false" show-icon />
    <label v-if="highRisk" class="confirmation-field">
      <span>输入“{{ confirmationText }}”继续</span>
      <ElInput v-model="typed" autocomplete="off" />
    </label>
    <label v-if="requiresStepUp && stepUpStatus?.auth_kind === 'local'" class="confirmation-field">
      <span>登录验证已过期，请重新输入密码</span>
      <ElInput v-model="password" type="password" show-password autocomplete="current-password" />
    </label>
    <div v-else-if="requiresStepUp && stepUpStatus?.auth_kind === 'oidc'" class="step-up-action">
      <ElButton :icon="ShieldCheck" @click="startOidcStepUp">重新验证企业账号</ElButton>
    </div>
    <ElAlert
      v-else-if="requiresStepUp"
      class="step-up-alert"
      title="该操作必须使用近期交互式登录，API Key 或平台令牌不能执行"
      type="warning"
      :closable="false"
      show-icon
    />
    <template #footer>
      <ElButton @click="emit('update:modelValue', false)">取消</ElButton>
      <ElButton
        type="danger"
        :disabled="!canConfirm || stepUpLoading"
        :loading="loading || stepUpLoading"
        @click="confirmAction"
      >
        确认
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.step-up-alert,
.step-up-action {
  margin-top: 14px;
}
</style>
