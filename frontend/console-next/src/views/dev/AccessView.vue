<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { Copy, Plus, RefreshCw, RotateCw, Send } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDialog,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElSkeleton,
  ElTabPane,
  ElTabs,
} from "element-plus";

import { apiRequest, jsonBody } from "../../api/client";
import DangerConfirm from "../../components/DangerConfirm.vue";
import EmptyState from "../../components/EmptyState.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { errorBannerMessage } from "../../utils/errors";
import { formatTimestamp, statusLabels } from "../../utils/format";

interface ApplicationRow {
  app_id: string;
  name: string;
  owner: string;
  scopes: string[];
  status: string;
  last_called_at?: number;
}
interface WebhookRow {
  webhook_id: string;
  name: string;
  application_id?: string;
  url?: string;
  events: string[];
  status: string;
}

const capabilities = useCapabilitiesStore();
const tab = ref("applications");
const loading = ref(true);
const actionLoading = ref(false);
const errorMessage = ref("");
const applications = ref<ApplicationRow[]>([]);
const webhooks = ref<WebhookRow[]>([]);
const appDialogOpen = ref(false);
const webhookDialogOpen = ref(false);
const rotateConfirmOpen = ref(false);
const rotateType = ref<"application" | "webhook">("application");
const rotateId = ref("");
const oneTimeSecret = ref("");
const secretDialogOpen = ref(false);
const appForm = reactive({
  name: "",
  owner: "",
  scopes: "infer,compare,gallery:read",
});
const webhookForm = reactive({
  name: "",
  application_id: "",
  url: "",
  events: "job.completed",
});

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [apps, hooks] = await Promise.all([
      apiRequest<{ applications: ApplicationRow[] }>("/v1/access/applications"),
      apiRequest<{ webhooks: WebhookRow[] }>("/v1/access/webhooks"),
    ]);
    applications.value = apps.applications;
    webhooks.value = hooks.webhooks;
    webhookForm.application_id ||= applications.value[0]?.app_id ?? "";
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "接入配置加载失败");
  } finally {
    loading.value = false;
  }
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function createApplication(): Promise<void> {
  if (!appForm.name.trim()) return;
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ one_time_secret: string }>("/v1/access/applications", {
      method: "POST",
      body: jsonBody({
        name: appForm.name.trim(),
        owner: appForm.owner.trim() || "platform",
        scopes: splitList(appForm.scopes),
        status: "active",
      }),
    });
    appDialogOpen.value = false;
    appForm.name = "";
    revealSecret(payload.one_time_secret);
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "应用创建失败");
  } finally {
    actionLoading.value = false;
  }
}

async function toggleApplication(app: ApplicationRow): Promise<void> {
  actionLoading.value = true;
  try {
    await apiRequest("/v1/access/applications/" + encodeURIComponent(app.app_id), {
      method: "PATCH",
      body: jsonBody({ status: app.status === "active" ? "disabled" : "active" }),
    });
    ElMessage.success(app.status === "active" ? "应用已停用" : "应用已启用");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "应用状态更新失败");
  } finally {
    actionLoading.value = false;
  }
}

async function createWebhook(): Promise<void> {
  if (!webhookForm.name.trim() || !webhookForm.application_id) return;
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ one_time_secret: string }>("/v1/access/webhooks", {
      method: "POST",
      body: jsonBody({
        name: webhookForm.name.trim(),
        application_id: webhookForm.application_id,
        url: webhookForm.url.trim() || null,
        events: splitList(webhookForm.events),
        status: webhookForm.url.trim() ? "active" : "disabled",
      }),
    });
    webhookDialogOpen.value = false;
    webhookForm.name = "";
    webhookForm.url = "";
    revealSecret(payload.one_time_secret);
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "事件回调创建失败");
  } finally {
    actionLoading.value = false;
  }
}

function requestRotation(type: "application" | "webhook", id: string): void {
  rotateType.value = type;
  rotateId.value = id;
  rotateConfirmOpen.value = true;
}

async function rotateSecret(): Promise<void> {
  actionLoading.value = true;
  try {
    const segment = rotateType.value === "application" ? "applications" : "webhooks";
    const payload = await apiRequest<{ one_time_secret: string }>(
      "/v1/access/" + segment + "/" + encodeURIComponent(rotateId.value) + "/rotate",
      { method: "POST" },
    );
    rotateConfirmOpen.value = false;
    revealSecret(payload.one_time_secret);
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "密钥轮换失败");
  } finally {
    actionLoading.value = false;
  }
}

async function sendSample(webhookId: string): Promise<void> {
  actionLoading.value = true;
  try {
    await apiRequest("/v1/access/webhooks/" + encodeURIComponent(webhookId) + "/sample", {
      method: "POST",
    });
    ElMessage.success("示例事件已生成");
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "示例事件生成失败");
  } finally {
    actionLoading.value = false;
  }
}

function revealSecret(secret: string): void {
  oneTimeSecret.value = secret;
  secretDialogOpen.value = true;
}
async function copySecret(): Promise<void> {
  await navigator.clipboard.writeText(oneTimeSecret.value);
  ElMessage.success("一次性密钥已复制");
}
function clearSecret(): void {
  oneTimeSecret.value = "";
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>接入配置</h1>
        <p>管理应用访问范围、调用状态与事件回调。</p>
      </div>
      <div class="page-actions">
        <ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton>
        <ElButton
          v-if="capabilities.hasPermission('access:write')"
          type="primary"
          :icon="Plus"
          @click="tab === 'applications' ? (appDialogOpen = true) : (webhookDialogOpen = true)"
          >{{ tab === "applications" ? "创建应用" : "创建回调" }}</ElButton
        >
      </div>
    </header>
    <ElAlert
      v-if="errorMessage"
      class="error-banner"
      role="alert"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
    />

    <section class="tool-surface">
      <ElTabs v-model="tab" class="page-tabs">
        <ElTabPane label="应用凭证" name="applications">
          <ElSkeleton :loading="loading" :rows="6" animated>
            <EmptyState v-if="applications.length === 0" title="还没有接入应用" />
            <div v-else class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>应用</th>
                    <th>负责人</th>
                    <th>权限范围</th>
                    <th>状态</th>
                    <th>最近调用</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="app in applications" :key="app.app_id">
                    <td>
                      <strong>{{ app.name }}</strong
                      ><br /><code>{{ app.app_id }}</code>
                    </td>
                    <td>{{ app.owner || "--" }}</td>
                    <td>
                      <div class="scope-list">
                        <span v-for="scope in app.scopes" :key="scope">{{ scope }}</span>
                      </div>
                    </td>
                    <td>
                      <span class="status-pill" :data-status="app.status">{{
                        statusLabels[app.status] ?? app.status
                      }}</span>
                    </td>
                    <td>{{ formatTimestamp(app.last_called_at) }}</td>
                    <td>
                      <div v-if="capabilities.hasPermission('access:write')" class="inline-actions">
                        <ElButton text @click="toggleApplication(app)">{{
                          app.status === "active" ? "停用" : "启用"
                        }}</ElButton>
                        <ElButton text :icon="RotateCw" @click="requestRotation('application', app.app_id)"
                          >轮换密钥</ElButton
                        >
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </ElSkeleton>
        </ElTabPane>

        <ElTabPane label="事件回调" name="webhooks">
          <EmptyState v-if="!loading && webhooks.length === 0" title="还没有事件回调" />
          <div v-else class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>回调</th>
                  <th>应用</th>
                  <th>地址</th>
                  <th>事件</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="hook in webhooks" :key="hook.webhook_id">
                  <td>
                    <strong>{{ hook.name }}</strong
                    ><br /><code>{{ hook.webhook_id }}</code>
                  </td>
                  <td>{{ hook.application_id || "--" }}</td>
                  <td class="url-cell">{{ hook.url || "未配置" }}</td>
                  <td>{{ hook.events?.join("、") }}</td>
                  <td>
                    <span class="status-pill" :data-status="hook.status">{{
                      statusLabels[hook.status] ?? hook.status
                    }}</span>
                  </td>
                  <td>
                    <div class="inline-actions">
                      <ElButton text :icon="Send" @click="sendSample(hook.webhook_id)">示例事件</ElButton>
                      <ElButton
                        v-if="capabilities.hasPermission('access:write')"
                        text
                        :icon="RotateCw"
                        @click="requestRotation('webhook', hook.webhook_id)"
                        >轮换签名</ElButton
                      >
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </ElTabPane>
      </ElTabs>
    </section>

    <ElDialog
      v-model="appDialogOpen"
      title="创建接入应用"
      width="min(560px, 92vw)"
      :close-on-click-modal="false"
    >
      <div class="dialog-form">
        <label><span>应用名称</span><ElInput v-model="appForm.name" maxlength="256" /></label>
        <label><span>负责人</span><ElInput v-model="appForm.owner" maxlength="256" /></label>
        <label
          ><span>权限范围（逗号分隔）</span
          ><ElInput v-model="appForm.scopes" type="textarea" maxlength="2000"
        /></label>
      </div>
      <template #footer>
        <ElButton @click="appDialogOpen = false">取消</ElButton>
        <ElButton
          type="primary"
          :loading="actionLoading"
          :disabled="!appForm.name.trim()"
          @click="createApplication"
          >创建</ElButton
        >
      </template>
    </ElDialog>

    <ElDialog
      v-model="webhookDialogOpen"
      title="创建事件回调"
      width="min(560px, 92vw)"
      :close-on-click-modal="false"
    >
      <div class="dialog-form">
        <label><span>回调名称</span><ElInput v-model="webhookForm.name" maxlength="256" /></label>
        <label
          ><span>关联应用</span
          ><ElSelect v-model="webhookForm.application_id"
            ><ElOption
              v-for="app in applications"
              :key="app.app_id"
              :label="app.name"
              :value="app.app_id" /></ElSelect
        ></label>
        <label><span>回调地址</span><ElInput v-model="webhookForm.url" maxlength="2048" /></label>
        <label><span>事件（逗号分隔）</span><ElInput v-model="webhookForm.events" maxlength="1000" /></label>
      </div>
      <template #footer>
        <ElButton @click="webhookDialogOpen = false">取消</ElButton>
        <ElButton
          type="primary"
          :loading="actionLoading"
          :disabled="!webhookForm.name.trim() || !webhookForm.application_id"
          @click="createWebhook"
          >创建</ElButton
        >
      </template>
    </ElDialog>

    <DangerConfirm
      v-model="rotateConfirmOpen"
      title="轮换接入密钥"
      :description="'将立即使 ' + rotateId + ' 的现有密钥失效。新密钥只会展示一次，请先协调调用方更新。'"
      :loading="actionLoading"
      @confirm="rotateSecret"
    />

    <ElDialog
      v-model="secretDialogOpen"
      title="一次性密钥"
      width="min(620px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      @closed="clearSecret"
    >
      <ElAlert
        title="关闭后无法再次查看，请立即保存到批准的密钥管理系统。"
        type="warning"
        show-icon
        :closable="false"
      />
      <div class="secret-row">
        <code>{{ oneTimeSecret }}</code>
        <ElButton :icon="Copy" aria-label="复制一次性密钥" @click="copySecret" />
      </div>
    </ElDialog>
  </div>
</template>

<style scoped>
.page-tabs {
  padding: 0 14px 14px;
}
.scope-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.scope-list span {
  padding: 2px 6px;
  color: #36504b;
  background: #eaf0ef;
  border-radius: 3px;
  font-size: 11px;
}
.url-cell {
  max-width: 280px;
  overflow-wrap: anywhere;
}
.dialog-form {
  display: grid;
  gap: 14px;
}
.dialog-form label {
  display: grid;
  gap: 6px;
  color: #62706d;
  font-size: 13px;
}
.secret-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 16px;
  padding: 12px;
  background: #f3f6f5;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.secret-row code {
  min-width: 0;
  flex: 1;
  overflow-wrap: anywhere;
}
</style>
