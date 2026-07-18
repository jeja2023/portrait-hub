<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { KeyRound, ScanSearch, ShieldCheck } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElCollapse,
  ElCollapseItem,
  ElForm,
  ElFormItem,
  ElInput,
  ElRadioButton,
  ElRadioGroup,
} from "element-plus";

import { beginSession, clearSession, markSessionAuthenticated, type AuthMode } from "../auth/session";
import { errorBannerMessage } from "../utils/errors";
import { useCapabilitiesStore } from "../stores/capabilities";

const route = useRoute();
const router = useRouter();
const capabilities = useCapabilitiesStore();
const apiKey = ref("");
const bearer = ref("");
const mode = ref<AuthMode>("api-key");
const advanced = ref<string[]>([]);
const loading = ref(false);
const errorMessage = ref("");
const credential = computed(() => (mode.value === "jwt" ? bearer.value : apiKey.value));

function safeRedirect(): string {
  const searchRedirect = new URLSearchParams(window.location.search).get("redirect");
  const value = typeof route.query.redirect === "string" ? route.query.redirect : (searchRedirect ?? "/");
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

async function login(): Promise<void> {
  errorMessage.value = "";
  loading.value = true;
  try {
    const selectedMode: AuthMode = credential.value.trim() ? mode.value : "anonymous";
    beginSession({
      tenantId: "",
      authMode: selectedMode,
      apiKey: apiKey.value,
      bearer: bearer.value,
    });
    await capabilities.load(true);
    markSessionAuthenticated();
    const target = safeRedirect();
    if (window.location.pathname === "/") {
      window.location.replace(`/console#${target}`);
      return;
    }
    await router.replace(target);
  } catch (error) {
    clearSession();
    capabilities.clear();
    errorMessage.value = errorBannerMessage(error, "无法验证当前凭证");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <main class="login-view">
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-brand">
        <span class="login-brand__mark"><ScanSearch :size="28" /></span>
        <div><strong>影鉴</strong><span>业务控制台</span></div>
      </div>
      <div class="login-heading">
        <h1 id="login-title">登录控制台</h1>
        <p>使用部署方提供的访问凭证登录，租户和权限由凭证确定。</p>
      </div>
      <ElAlert
        v-if="errorMessage"
        class="error-banner"
        role="alert"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
      />
      <ElForm label-position="top" @submit.prevent="login">
        <ElFormItem v-if="mode === 'api-key'" label="接口密钥">
          <ElInput
            v-model="apiKey"
            type="password"
            show-password
            autocomplete="current-password"
            :prefix-icon="KeyRound"
          />
        </ElFormItem>
        <ElFormItem v-else label="JWT">
          <ElInput
            v-model="bearer"
            type="password"
            show-password
            autocomplete="current-password"
            :prefix-icon="ShieldCheck"
          />
        </ElFormItem>
        <ElCollapse v-model="advanced" class="login-advanced">
          <ElCollapseItem title="高级选项" name="advanced">
            <ElFormItem label="认证方式">
              <ElRadioGroup v-model="mode">
                <ElRadioButton value="api-key">接口密钥</ElRadioButton>
                <ElRadioButton value="jwt">JWT</ElRadioButton>
              </ElRadioGroup>
            </ElFormItem>
          </ElCollapseItem>
        </ElCollapse>
        <ElButton class="login-submit" type="primary" native-type="submit" :loading="loading"
          >进入控制台</ElButton
        >
      </ElForm>
      <p class="login-footnote">凭证仅保留在当前标签页，关闭后自动失效。</p>
    </section>
  </main>
</template>

<style scoped>
.login-view {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 28px 16px;
  background: #edf3f1;
}
.login-panel {
  width: min(420px, 100%);
  padding: 30px;
  background: #fff;
  border: 1px solid #d5dfdc;
  border-radius: 6px;
  box-shadow: 0 14px 34px rgba(20, 42, 38, 0.1);
}
.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 22px;
  border-bottom: 1px solid #e0e7e5;
}
.login-brand__mark {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  color: #fff;
  background: #087682;
  border-radius: 5px;
}
.login-brand div {
  display: flex;
  flex-direction: column;
}
.login-brand strong {
  font-size: 21px;
}
.login-brand span:not(.login-brand__mark) {
  color: #62706d;
  font-size: 12px;
}
.login-heading {
  margin: 24px 0 18px;
}
.login-heading h1 {
  margin: 0;
  font-size: 24px;
}
.login-heading p {
  margin: 7px 0 0;
  color: #62706d;
  font-size: 14px;
}
.login-advanced {
  margin: 2px 0 18px;
  border-top: 0;
}
.login-submit {
  width: 100%;
  height: 42px;
}
.login-footnote {
  margin: 16px 0 0;
  color: #697773;
  font-size: 12px;
  text-align: center;
}
</style>
