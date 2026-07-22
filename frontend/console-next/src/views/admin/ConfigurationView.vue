<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { Pencil, RefreshCw, RotateCcw, Save } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElDialog,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElSkeleton,
  ElSwitch,
  ElTabPane,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTag,
  ElTooltip,
} from "element-plus";

import { ApiError, apiRequest, jsonBody } from "../../api/client";
import { useRouteTab } from "../../utils/routeState";

interface ConfigurationItem {
  key: string;
  description: string;
  category: string;
  value_type: "boolean" | "integer" | "number" | "list" | "path" | "string";
  sensitive: boolean;
  editable: boolean;
  managed_by: "configuration" | "network_policy";
  apply_mode: "service_restart" | "compose_recreate";
  value: string | null;
  desired_value: string | null;
  configured: boolean;
  override_configured: boolean;
  overridden: boolean;
  pending: boolean;
  source: "default" | "environment" | "override";
}

interface ConfigurationPayload {
  revision: number;
  updated_at?: number | null;
  categories: string[];
  items: ConfigurationItem[];
  changed_keys?: string[];
  summary: {
    total: number;
    overridden: number;
    pending: number;
    sensitive: number;
  };
}

interface EndpointNetworkPolicy {
  allow_private_hosts: boolean;
  allowed_hosts: string[];
  allowed_cidrs: string[];
}

interface NetworkAccessPolicy {
  revision: number;
  updated_at?: number | null;
  stream: EndpointNetworkPolicy;
  webhook: EndpointNetworkPolicy;
}

const tab = useRouteTab("network");
const loading = ref(true);
const saving = ref(false);
const errorMessage = ref("");
const configuration = ref<ConfigurationPayload | null>(null);
const networkPolicy = ref<NetworkAccessPolicy | null>(null);
const keyword = ref("");
const category = ref("");
const applyMode = ref("");
const editOpen = ref(false);
const editingItem = ref<ConfigurationItem | null>(null);
const editValue = ref("");
const confirmEmptySensitive = ref(false);
const networkForm = reactive({
  streamAllowPrivate: false,
  streamHosts: "",
  streamCidrs: "",
  webhookAllowPrivate: false,
  webhookHosts: "",
  webhookCidrs: "",
});

const filteredItems = computed(() => {
  const query = keyword.value.trim().toLowerCase();
  return (configuration.value?.items ?? []).filter((item) => {
    const matchesKeyword =
      !query || item.key.toLowerCase().includes(query) || item.description.toLowerCase().includes(query);
    const matchesCategory = !category.value || item.category === category.value;
    const matchesApplyMode = !applyMode.value || item.apply_mode === applyMode.value;
    return matchesKeyword && matchesCategory && matchesApplyMode;
  });
});

const editSaveDisabled = computed(() => {
  const item = editingItem.value;
  if (!item) return true;
  return item.sensitive && !editValue.value && !confirmEmptySensitive.value;
});

const unsafePrivatePolicy = computed(() => {
  const streamEmpty = !splitRules(networkForm.streamHosts).length && !splitRules(networkForm.streamCidrs).length;
  const webhookEmpty = !splitRules(networkForm.webhookHosts).length && !splitRules(networkForm.webhookCidrs).length;
  return (networkForm.streamAllowPrivate && streamEmpty) || (networkForm.webhookAllowPrivate && webhookEmpty);
});

function splitRules(value: string): string[] {
  return [...new Set(value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean))];
}

function applyNetworkPolicy(policy: NetworkAccessPolicy): void {
  networkPolicy.value = policy;
  networkForm.streamAllowPrivate = policy.stream.allow_private_hosts;
  networkForm.streamHosts = policy.stream.allowed_hosts.join("\n");
  networkForm.streamCidrs = policy.stream.allowed_cidrs.join("\n");
  networkForm.webhookAllowPrivate = policy.webhook.allow_private_hosts;
  networkForm.webhookHosts = policy.webhook.allowed_hosts.join("\n");
  networkForm.webhookCidrs = policy.webhook.allowed_cidrs.join("\n");
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [configurationPayload, policyPayload] = await Promise.all([
      apiRequest<ConfigurationPayload>("/v1/admin/configuration"),
      apiRequest<NetworkAccessPolicy>("/v1/admin/network-access-policy"),
    ]);
    configuration.value = configurationPayload;
    applyNetworkPolicy(policyPayload);
  } catch (error) {
    errorMessage.value = errorText(error, "配置中心加载失败");
  } finally {
    loading.value = false;
  }
}

async function saveNetworkPolicy(): Promise<void> {
  if (!networkPolicy.value) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<NetworkAccessPolicy>("/v1/admin/network-access-policy", {
      method: "PUT",
      body: jsonBody({
        expected_revision: networkPolicy.value.revision,
        stream: {
          allow_private_hosts: networkForm.streamAllowPrivate,
          allowed_hosts: splitRules(networkForm.streamHosts),
          allowed_cidrs: splitRules(networkForm.streamCidrs),
        },
        webhook: {
          allow_private_hosts: networkForm.webhookAllowPrivate,
          allowed_hosts: splitRules(networkForm.webhookHosts),
          allowed_cidrs: splitRules(networkForm.webhookCidrs),
        },
      }),
    });
    applyNetworkPolicy(payload);
    ElMessage.success("网络访问策略已生效");
  } catch (error) {
    errorMessage.value = errorText(error, "网络访问策略保存失败");
  } finally {
    saving.value = false;
  }
}

function openEdit(item: ConfigurationItem): void {
  if (!item.editable) return;
  editingItem.value = item;
  editValue.value = item.sensitive ? "" : (item.desired_value ?? item.value ?? "");
  confirmEmptySensitive.value = false;
  editOpen.value = true;
}

async function updateConfiguration(value: string | null): Promise<void> {
  const item = editingItem.value;
  if (!item || !configuration.value) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<ConfigurationPayload>("/v1/admin/configuration", {
      method: "PUT",
      body: jsonBody({
        expected_revision: configuration.value.revision,
        changes: [{ key: item.key, value }],
      }),
    });
    configuration.value = payload;
    editOpen.value = false;
    ElMessage.success(value === null ? "配置覆盖已移除" : "配置覆盖已保存");
  } catch (error) {
    errorMessage.value = errorText(error, "配置保存失败");
  } finally {
    saving.value = false;
  }
}

function displayValue(item: ConfigurationItem): string {
  if (item.sensitive) return item.configured ? "已配置" : "未配置";
  if (item.pending && item.desired_value !== null) return item.desired_value || "（空）";
  return item.value || "（空）";
}

function configurationItem(value: unknown): ConfigurationItem {
  return value as ConfigurationItem;
}

function sourceLabel(item: ConfigurationItem): string {
  if (item.managed_by === "network_policy") return "网络策略";
  return { default: "模板默认", environment: "环境变量", override: "配置覆盖" }[item.source];
}

function applyModeLabel(item: ConfigurationItem): string {
  if (item.managed_by === "network_policy") return "立即生效";
  return item.apply_mode === "compose_recreate" ? "重建容器" : "重启服务";
}

onMounted(load);
</script>

<template>
  <div class="configuration-page">
    <ElAlert v-if="errorMessage" type="error" :title="errorMessage" show-icon :closable="false" />
    <ElSkeleton v-if="loading" :rows="9" animated />

    <ElTabs v-else v-model="tab">
      <ElTabPane label="网络访问策略" name="network">
        <div class="network-toolbar">
          <div class="policy-revision">策略版本 {{ networkPolicy?.revision ?? 0 }}</div>
          <div class="page-actions">
            <ElTooltip content="重新读取策略" placement="top">
              <ElButton :icon="RefreshCw" circle aria-label="刷新网络访问策略" @click="load" />
            </ElTooltip>
            <ElButton
              type="primary"
              :icon="Save"
              :loading="saving"
              :disabled="unsafePrivatePolicy"
              @click="saveNetworkPolicy"
            >
              保存策略
            </ElButton>
          </div>
        </div>

        <ElAlert
          v-if="unsafePrivatePolicy"
          type="warning"
          title="允许私网访问时必须填写主机或 CIDR 网段"
          show-icon
          :closable="false"
        />

        <div class="policy-grid">
          <section class="tool-surface policy-panel">
            <div class="tool-surface__header">
              <div>
                <h2>视频流访问</h2>
                <span>RTSP、RTMP、HTTP、HTTPS</span>
              </div>
              <ElSwitch
                v-model="networkForm.streamAllowPrivate"
                inline-prompt
                active-text="私网"
                inactive-text="公网"
                aria-label="允许视频流访问私网地址"
              />
            </div>
            <div class="tool-surface__body policy-fields">
              <label>
                <span>允许的 IP 网段</span>
                <ElInput
                  v-model="networkForm.streamCidrs"
                  type="textarea"
                  :rows="7"
                  placeholder="10.30.0.0/16"
                  aria-label="视频流允许的 IP 网段"
                />
              </label>
              <label>
                <span>允许的主机</span>
                <ElInput
                  v-model="networkForm.streamHosts"
                  type="textarea"
                  :rows="7"
                  placeholder="10.30.0.8"
                  aria-label="视频流允许的主机"
                />
              </label>
            </div>
          </section>

          <section class="tool-surface policy-panel">
            <div class="tool-surface__header">
              <div>
                <h2>事件回调访问</h2>
                <span>HTTP、HTTPS</span>
              </div>
              <ElSwitch
                v-model="networkForm.webhookAllowPrivate"
                inline-prompt
                active-text="私网"
                inactive-text="公网"
                aria-label="允许事件回调访问私网地址"
              />
            </div>
            <div class="tool-surface__body policy-fields">
              <label>
                <span>允许的 IP 网段</span>
                <ElInput
                  v-model="networkForm.webhookCidrs"
                  type="textarea"
                  :rows="7"
                  placeholder="10.40.0.0/24"
                  aria-label="事件回调允许的 IP 网段"
                />
              </label>
              <label>
                <span>允许的主机</span>
                <ElInput
                  v-model="networkForm.webhookHosts"
                  type="textarea"
                  :rows="7"
                  placeholder="10.40.0.8"
                  aria-label="事件回调允许的主机"
                />
              </label>
            </div>
          </section>
        </div>
      </ElTabPane>

      <ElTabPane label="全部配置" name="all">
        <ElAlert
          v-if="configuration?.summary.pending"
          type="warning"
          :title="`${configuration.summary.pending} 项配置等待重启或重建后生效`"
          show-icon
          :closable="false"
        />
        <div class="configuration-toolbar">
          <div class="configuration-summary">
            <span>全部 {{ configuration?.summary.total ?? 0 }}</span>
            <span>覆盖 {{ configuration?.summary.overridden ?? 0 }}</span>
            <span>敏感 {{ configuration?.summary.sensitive ?? 0 }}</span>
          </div>
          <div class="configuration-filters">
            <ElInput v-model="keyword" clearable placeholder="搜索配置" aria-label="搜索配置" />
            <ElSelect v-model="category" clearable placeholder="全部分类" aria-label="配置分类">
              <ElOption v-for="item in configuration?.categories" :key="item" :label="item" :value="item" />
            </ElSelect>
            <ElSelect v-model="applyMode" clearable placeholder="全部生效方式" aria-label="生效方式">
              <ElOption label="重启服务" value="service_restart" />
              <ElOption label="重建容器" value="compose_recreate" />
            </ElSelect>
            <ElTooltip content="重新读取配置" placement="top">
              <ElButton :icon="RefreshCw" circle aria-label="刷新全部配置" @click="load" />
            </ElTooltip>
          </div>
        </div>

        <div class="tool-surface configuration-table">
          <ElTable :data="filteredItems" row-key="key" empty-text="没有匹配的配置">
            <ElTableColumn label="配置项" min-width="230" fixed="left">
              <template #default="scope">
                <div class="config-key">
                  <code>{{ scope.row.key }}</code>
                  <ElTag v-if="scope.row.pending" size="small" type="warning">待应用</ElTag>
                </div>
              </template>
            </ElTableColumn>
            <ElTableColumn label="当前值" min-width="190">
              <template #default="scope">
                <span :class="['config-value', { 'is-sensitive': scope.row.sensitive }]">
                  {{ displayValue(configurationItem(scope.row)) }}
                </span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="category" label="分类" width="140" />
            <ElTableColumn label="来源" width="110">
              <template #default="scope">{{ sourceLabel(configurationItem(scope.row)) }}</template>
            </ElTableColumn>
            <ElTableColumn label="生效" width="110">
              <template #default="scope">
                <ElTag size="small" :type="scope.row.apply_mode === 'compose_recreate' ? 'warning' : 'info'">
                  {{ applyModeLabel(configurationItem(scope.row)) }}
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="description" label="说明" min-width="360" show-overflow-tooltip />
            <ElTableColumn label="操作" width="86" fixed="right">
              <template #default="scope">
                <ElTooltip
                  :content="scope.row.managed_by === 'network_policy' ? '请在网络访问策略中修改' : '修改配置覆盖'"
                  placement="left"
                >
                  <ElButton
                    link
                    :icon="Pencil"
                    aria-label="修改配置"
                    :disabled="!scope.row.editable"
                    @click="openEdit(configurationItem(scope.row))"
                  />
                </ElTooltip>
              </template>
            </ElTableColumn>
          </ElTable>
        </div>
        <div class="configuration-mobile-list">
          <article v-for="item in filteredItems" :key="item.key" class="tool-surface mobile-config-item">
            <div class="mobile-config-item__header">
              <code>{{ item.key }}</code>
              <ElButton
                link
                :icon="Pencil"
                aria-label="修改配置"
                :disabled="!item.editable"
                @click="openEdit(item)"
              />
            </div>
            <div class="mobile-config-item__value">{{ displayValue(item) }}</div>
            <div class="mobile-config-item__meta">
              <ElTag size="small" type="info">{{ item.category }}</ElTag>
              <ElTag size="small" :type="item.apply_mode === 'compose_recreate' ? 'warning' : 'info'">
                {{ applyModeLabel(item) }}
              </ElTag>
              <span>{{ sourceLabel(item) }}</span>
            </div>
            <p>{{ item.description }}</p>
          </article>
        </div>
      </ElTabPane>
    </ElTabs>

    <ElDialog v-model="editOpen" :title="editingItem?.key ?? '修改配置'" width="min(560px, 92vw)">
      <div v-if="editingItem" class="config-editor">
        <p>{{ editingItem.description }}</p>
        <ElAlert
          v-if="editingItem.sensitive"
          type="warning"
          title="敏感值不会回显，保存后将替换当前覆盖值"
          show-icon
          :closable="false"
        />
        <ElAlert
          v-if="editingItem.apply_mode === 'compose_recreate'"
          type="info"
          title="该值保存为待应用配置，需要在宿主机同步 .env 后重建容器"
          show-icon
          :closable="false"
        />
        <ElSwitch
          v-if="editingItem.value_type === 'boolean'"
          v-model="editValue"
          active-value="true"
          inactive-value="false"
          active-text="启用"
          inactive-text="停用"
        />
        <ElInput
          v-else
          v-model="editValue"
          :type="editingItem.sensitive ? 'password' : editingItem.value_type === 'string' ? 'textarea' : 'text'"
          :rows="editingItem.value_type === 'string' ? 4 : undefined"
          show-password
          :placeholder="editingItem.sensitive ? '输入新的配置值' : ''"
        />
        <ElCheckbox v-if="editingItem.sensitive && !editValue" v-model="confirmEmptySensitive">
          将该配置覆盖设置为空
        </ElCheckbox>
        <div class="editor-apply-mode">
          <ElTag size="small" :type="editingItem.apply_mode === 'compose_recreate' ? 'warning' : 'info'">
            {{ applyModeLabel(editingItem) }}
          </ElTag>
        </div>
      </div>
      <template #footer>
        <ElButton
          v-if="editingItem?.overridden"
          :icon="RotateCcw"
          :loading="saving"
          @click="updateConfiguration(null)"
        >
          移除覆盖
        </ElButton>
        <ElButton @click="editOpen = false">取消</ElButton>
        <ElButton
          type="primary"
          :icon="Save"
          :loading="saving"
          :disabled="editSaveDisabled"
          @click="updateConfiguration(editValue)"
        >
          保存覆盖
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.configuration-page {
  display: grid;
  min-width: 0;
  gap: 14px;
}

.configuration-page :deep(.el-tabs),
.configuration-page :deep(.el-tabs__content),
.configuration-page :deep(.el-tab-pane) {
  max-width: 100%;
  min-width: 0;
}

.network-toolbar,
.configuration-toolbar,
.configuration-summary,
.configuration-filters,
.config-key,
.editor-apply-mode {
  display: flex;
  align-items: center;
}

.network-toolbar,
.configuration-toolbar {
  min-height: 48px;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.policy-revision,
.configuration-summary {
  color: var(--muted);
  font-size: 13px;
}

.policy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.policy-panel h2 {
  margin: 0;
  font-size: 15px;
}

.policy-panel .tool-surface__header span {
  color: var(--muted);
  font-size: 12px;
}

.policy-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.policy-fields label {
  display: grid;
  gap: 7px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 600;
}

.configuration-toolbar {
  margin-top: 14px;
}

.configuration-summary,
.configuration-filters {
  gap: 10px;
}

.configuration-summary span + span {
  padding-left: 10px;
  border-left: 1px solid var(--line);
}

.configuration-filters :deep(.el-input) {
  width: 220px;
}

.configuration-filters :deep(.el-select) {
  width: 150px;
}

.configuration-table {
  max-width: 100%;
  min-width: 0;
  overflow-x: auto;
}

.configuration-mobile-list {
  display: none;
}

.configuration-table :deep(.el-table th.el-table__cell) {
  color: #4f5d5a;
}

.config-key {
  gap: 7px;
}

.config-key code,
.config-value {
  overflow-wrap: anywhere;
}

.config-key code {
  color: #263835;
  font-size: 12px;
}

.config-value {
  color: #384945;
  font-size: 13px;
}

.config-value.is-sensitive {
  color: var(--muted);
}

.config-editor {
  display: grid;
  gap: 14px;
}

.config-editor p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.editor-apply-mode {
  justify-content: flex-end;
}

@media (max-width: 1100px) {
  .policy-grid,
  .policy-fields {
    grid-template-columns: 1fr;
  }

  .configuration-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .configuration-filters {
    width: 100%;
    flex-wrap: wrap;
  }
}

@media (max-width: 700px) {
  .network-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .configuration-filters :deep(.el-input),
  .configuration-filters :deep(.el-select) {
    width: 100%;
  }

  .configuration-table {
    display: none;
  }

  .configuration-mobile-list {
    display: grid;
    gap: 10px;
  }

  .mobile-config-item {
    min-width: 0;
    padding: 12px;
  }

  .mobile-config-item__header,
  .mobile-config-item__meta {
    display: flex;
    align-items: center;
  }

  .mobile-config-item__meta :deep(.el-tag--info) {
    --el-tag-text-color: #4f5d5a;
  }

  .mobile-config-item__meta :deep(.el-tag--warning) {
    --el-tag-text-color: #8a4b00;
  }

  .mobile-config-item__header {
    justify-content: space-between;
    gap: 8px;
  }

  .mobile-config-item__header code {
    min-width: 0;
    overflow-wrap: anywhere;
    color: #263835;
    font-size: 12px;
  }

  .mobile-config-item__value {
    margin-top: 7px;
    overflow-wrap: anywhere;
    color: #384945;
    font-size: 13px;
  }

  .mobile-config-item__meta {
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 9px;
    color: var(--muted);
    font-size: 12px;
  }

  .mobile-config-item p {
    margin: 9px 0 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.55;
  }
}
</style>
