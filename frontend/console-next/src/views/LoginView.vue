<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Building2, KeyRound, LockKeyhole, ScanSearch, ShieldCheck, UserRound } from "@lucide/vue";
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
  ElSkeleton,
} from "element-plus";

import { apiRequest } from "../api/client";
import type { AuthPublicConfig } from "../api/contracts";
import { beginSession, clearSession, markSessionAuthenticated, type AuthMode } from "../auth/session";
import { useCapabilitiesStore } from "../stores/capabilities";
import { errorBannerMessage } from "../utils/errors";

const route = useRoute();
const router = useRouter();
const capabilities = useCapabilitiesStore();
const username = ref("admin");
const password = ref("");
const apiKey = ref("");
const bearer = ref("");
const mode = ref<AuthMode>("api-key");
const advanced = ref<string[]>([]);
const loading = ref(false);
const configLoading = ref(true);
const errorMessage = ref("");
const authConfig = ref<AuthPublicConfig>({
  local_enabled: true,
  oidc_enabled: false,
  provider_name: "企业账号",
  credential_login_available: true,
});
const credential = computed(() => (mode.value === "jwt" ? bearer.value : apiKey.value));
const showCredentialForm = computed(() => advanced.value.includes("credentials"));

function safeRedirect(): string {
  const searchRedirect = new URLSearchParams(window.location.search).get("redirect");
  const value = typeof route.query.redirect === "string" ? route.query.redirect : (searchRedirect ?? "/");
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

async function enterConsole(): Promise<void> {
  const target = safeRedirect();
  if (window.location.pathname === "/") {
    window.location.replace("/console#" + target);
    return;
  }
  await router.replace(target);
}

async function loginWithLocalAccount(): Promise<void> {
  errorMessage.value = "";
  loading.value = true;
  try {
    await apiRequest("/v1/auth/local/login", {
      method: "POST",
      body: JSON.stringify({
        username: username.value,
        password: password.value,
      }),
    });
    beginSession({ tenantId: "", authMode: "local" });
    await capabilities.load(true);
    markSessionAuthenticated();
    password.value = "";
    await enterConsole();
  } catch (error) {
    clearSession();
    capabilities.clear();
    errorMessage.value = errorBannerMessage(error, "用户名或密码错误");
  } finally {
    loading.value = false;
  }
}


async function restoreBrowserSession(authMode: "local" | "oidc", silent = false): Promise<void> {
  beginSession({ tenantId: "", authMode });
  try {
    await capabilities.load(true);
    markSessionAuthenticated();
    await enterConsole();
  } catch (error) {
    clearSession();
    capabilities.clear();
    if (!silent) errorMessage.value = errorBannerMessage(error, "登录会话验证失败");
  }
}

function startOidcLogin(): void {
  window.location.assign("/auth/oidc/login?return_to=" + encodeURIComponent(safeRedirect()));
}

async function loginWithCredential(): Promise<void> {
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
    await enterConsole();
  } catch (error) {
    clearSession();
    capabilities.clear();
    errorMessage.value = errorBannerMessage(error, "无法验证当前凭证");
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  const params = new URLSearchParams(window.location.search);
  if (params.get("oidc_error")) errorMessage.value = "企业账号登录失败，请重试或联系管理员。";
  try {
    authConfig.value = await apiRequest<AuthPublicConfig>("/v1/auth/config");
  } catch {
    authConfig.value.oidc_enabled = false;
  } finally {
    configLoading.value = false;
  }
  if (params.get("oidc") === "success") {
    await restoreBrowserSession("oidc");
  } else if ((authConfig.value.local_enabled || authConfig.value.oidc_enabled) && !params.get("oidc_error")) {
    await restoreBrowserSession(authConfig.value.local_enabled ? "local" : "oidc", true);
  }
});
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
        <p>使用管理员账号登录，租户和权限由账号角色确定。</p>
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
      <ElSkeleton :loading="configLoading" animated :rows="4">
        <ElForm
          v-if="authConfig.local_enabled"
          class="local-login"
          label-position="top"
          @submit.prevent="loginWithLocalAccount"
        >
          <ElFormItem label="用户名">
            <ElInput
              v-model="username"
              autocomplete="username"
              :prefix-icon="UserRound"
            />
          </ElFormItem>
          <ElFormItem label="密码">
            <ElInput
              v-model="password"
              type="password"
              show-password
              autocomplete="current-password"
              :prefix-icon="LockKeyhole"
            />
          </ElFormItem>
          <ElButton class="login-submit" type="primary" native-type="submit" :loading="loading">
            登录
          </ElButton>
        </ElForm>
        <ElAlert
          v-else
          title="本地账号登录未配置"
          type="warning"
          show-icon
          :closable="false"
        />

        <div v-if="authConfig.oidc_enabled" class="login-separator"><span>或</span></div>
        <ElButton
          v-if="authConfig.oidc_enabled"
          class="enterprise-login"
          :icon="Building2"
          @click="startOidcLogin"
        >
          使用{{ authConfig.provider_name }}登录
        </ElButton>

        <ElCollapse
          v-if="authConfig.credential_login_available"
          v-model="advanced"
          class="login-advanced"
        >
          <ElCollapseItem title="高级凭证登录" name="credentials">
            <p class="credential-note">仅用于开发、自动化验证或受控应急访问。</p>
          </ElCollapseItem>
        </ElCollapse>

        <ElForm v-if="showCredentialForm" label-position="top" @submit.prevent="loginWithCredential">
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
          <ElFormItem label="认证方式">
            <ElRadioGroup v-model="mode">
              <ElRadioButton value="api-key">接口密钥</ElRadioButton>
              <ElRadioButton value="jwt">JWT</ElRadioButton>
            </ElRadioGroup>
          </ElFormItem>
          <ElButton class="login-submit" type="primary" native-type="submit" :loading="loading">
            凭证登录
          </ElButton>
        </ElForm>
      </ElSkeleton>
      <p class="login-footnote">
        账号凭证仅用于验证身份，不会写入浏览器存储。
      </p>
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
.login-heading p,
.credential-note {
  color: #62706d;
  font-size: 14px;
}
.login-heading p {
  margin: 7px 0 0;
}
.local-login {
  margin-top: 2px;
}
.enterprise-login,
.login-submit {
  width: 100%;
  height: 42px;
}
.login-separator {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 18px 0;
  color: #7a8784;
  font-size: 12px;
}
.login-separator::before,
.login-separator::after {
  height: 1px;
  flex: 1;
  content: "";
  background: #dfe6e4;
}
.login-advanced {
  margin: 16px 0;
}
.credential-note {
  margin: 0 0 14px;
}
.login-footnote {
  margin: 16px 0 0;
  color: #697773;
  font-size: 12px;
  text-align: center;
}
</style>
