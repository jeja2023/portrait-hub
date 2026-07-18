<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Archive, RefreshCw, Search, ShieldCheck, Trash2 } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDatePicker,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElSkeleton,
  ElTabPane,
  ElTabs,
} from "element-plus";

import { ApiError, apiRequest, jsonBody } from "../../api/client";
import DangerConfirm from "../../components/DangerConfirm.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { formatTimestamp } from "../../utils/format";

interface AuditEvent {
  event?: string;
  request_id?: string;
  outcome?: string;
  category?: string;
  created_at?: number;
  audit_hash?: string;
}
interface AuditPayload {
  records: AuditEvent[];
  count: number;
  matched_count: number;
  scanned_count: number;
  malformed_count: number;
  summary: {
    category_counts?: Record<string, number>;
    outcome_counts?: Record<string, number>;
  };
}
interface BackupSnapshot {
  snapshot_id?: string;
  request_id?: string;
  created_at?: number;
  outcome?: string;
  object_backend?: string;
  bytes?: number;
  updated_since?: number | null;
}
interface BackupPayload {
  snapshots: BackupSnapshot[];
  count: number;
  scanned_count: number;
  malformed_count: number;
}
interface AuditChain {
  ok: boolean;
  path_hash?: string;
  record_count: number;
  error_count: number;
  head_hash?: string | null;
  errors?: Record<string, unknown>[];
}

const capabilities = useCapabilitiesStore();
const tab = ref("status");
const loading = ref(true);
const actionLoading = ref(false);
const auditLoading = ref(false);
const backupLoading = ref(false);
const errorMessage = ref("");
const status = ref<Record<string, unknown>>({});
const auditPayload = ref<AuditPayload>({
  records: [],
  count: 0,
  matched_count: 0,
  scanned_count: 0,
  malformed_count: 0,
  summary: {},
});
const backupPayload = ref<BackupPayload>({ snapshots: [], count: 0, scanned_count: 0, malformed_count: 0 });
const auditChain = ref<AuditChain | null>(null);
const backupOpen = ref(false);
const cleanupOpen = ref(false);
const retentionDays = ref(90);
const auditEventFilter = ref("");
const auditRequestIdFilter = ref("");
const auditOutcomeFilter = ref("");
const auditCategoryFilter = ref("");
const auditCreatedRange = ref<[Date, Date] | null>(null);

const audit = computed(() => auditPayload.value.records);
const backups = computed(() => backupPayload.value.snapshots);
const backends = computed(() =>
  status.value.configured_backends && typeof status.value.configured_backends === "object"
    ? Object.entries(status.value.configured_backends as Record<string, unknown>)
    : [],
);
const categorySummary = computed(() =>
  Object.entries(auditPayload.value.summary.category_counts ?? {}).filter(([, count]) => count > 0),
);

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function shortHash(value: unknown): string {
  const text = String(value ?? "");
  return text.length > 18 ? text.slice(0, 10) + "…" + text.slice(-6) : text || "--";
}

function formatBytes(value: unknown): string {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "--";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KiB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MiB";
}

async function loadAudit(): Promise<void> {
  auditLoading.value = true;
  try {
    const params = new URLSearchParams({ limit: "100" });
    if (auditEventFilter.value.trim()) params.set("event", auditEventFilter.value.trim());
    if (auditRequestIdFilter.value.trim()) params.set("request_id", auditRequestIdFilter.value.trim());
    if (auditOutcomeFilter.value) params.set("outcome", auditOutcomeFilter.value);
    if (auditCategoryFilter.value) params.set("category", auditCategoryFilter.value);
    if (auditCreatedRange.value) {
      params.set("created_since", String(auditCreatedRange.value[0].getTime() / 1000));
      params.set("created_until", String(auditCreatedRange.value[1].getTime() / 1000));
    }
    auditPayload.value = await apiRequest<AuditPayload>("/v1/admin/audit/events?" + params.toString());
  } catch (error) {
    errorMessage.value = errorText(error, "审计事件加载失败");
  } finally {
    auditLoading.value = false;
  }
}

async function loadBackups(): Promise<void> {
  if (!capabilities.hasPermission("admin:export")) return;
  backupLoading.value = true;
  try {
    backupPayload.value = await apiRequest<BackupPayload>("/v1/admin/backups?limit=20");
  } catch (error) {
    errorMessage.value = errorText(error, "备份快照加载失败");
  } finally {
    backupLoading.value = false;
  }
}

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  const [statusResult] = await Promise.allSettled([
    apiRequest<Record<string, unknown>>("/v1/admin/status"),
    loadAudit(),
    loadBackups(),
  ]);
  if (statusResult.status === "fulfilled") status.value = statusResult.value;
  else errorMessage.value = errorText(statusResult.reason, "运维状态加载失败");
  loading.value = false;
}

async function verifyAudit(): Promise<void> {
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ audit_chain: AuditChain }>("/v1/admin/audit/verify");
    auditChain.value = payload.audit_chain;
    ElMessage.success(payload.audit_chain.ok ? "审计链校验通过" : "审计链校验发现异常");
  } catch (error) {
    errorMessage.value = errorText(error, "审计链校验失败");
  } finally {
    actionLoading.value = false;
  }
}

async function createBackup(): Promise<void> {
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest("/v1/admin/backup", {
      method: "POST",
      body: jsonBody({ updated_since: null, confirm: "backup" }),
    });
    backupOpen.value = false;
    ElMessage.success("备份快照已创建");
    await loadBackups();
    tab.value = "backups";
  } catch (error) {
    errorMessage.value = errorText(error, "创建备份失败");
  } finally {
    actionLoading.value = false;
  }
}

async function cleanupRetention(): Promise<void> {
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest("/v1/admin/retention/cleanup", {
      method: "POST",
      body: jsonBody({ retention_days: retentionDays.value, confirm: "cleanup" }),
    });
    cleanupOpen.value = false;
    ElMessage.success("保留策略清理已完成");
    await load();
  } catch (error) {
    errorMessage.value = errorText(error, "数据清理失败");
  } finally {
    actionLoading.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>运维与合规</h1>
        <p>查看运行后端、审计链和备份，并执行受控保留策略。</p>
      </div>
      <div class="page-actions">
        <ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton>
        <ElButton v-if="capabilities.hasPermission('admin:export')" :icon="Archive" @click="backupOpen = true">
          创建备份
        </ElButton>
        <ElButton
          v-if="capabilities.hasPermission('admin:retention')"
          type="danger"
          :icon="Trash2"
          @click="cleanupOpen = true"
        >
          数据清理
        </ElButton>
      </div>
    </header>
    <ElAlert
      v-if="errorMessage"
      class="error-banner"
      :title="errorMessage"
      type="error"
      show-icon
      :closable="false"
    />

    <section class="tool-surface">
      <ElTabs v-model="tab" class="page-tabs">
        <ElTabPane label="平台状态" name="status">
          <ElSkeleton :loading="loading" :rows="5" animated>
            <div class="backend-grid">
              <div v-for="item in backends" :key="item[0]">
                <span>{{ item[0] }}</span><strong>{{ item[1] }}</strong>
              </div>
            </div>
          </ElSkeleton>
        </ElTabPane>

        <ElTabPane label="告警" name="alerts">
          <p class="tab-note">SLO 成功率、错误预算和排队延迟显示在总览；此处保留运维操作入口。</p>
        </ElTabPane>

        <ElTabPane label="备份保留" name="backups">
          <div class="retention-control">
            <label>
              <span>清理保留天数</span>
              <ElInputNumber v-model="retentionDays" :min="0" :max="3650" />
            </label>
            <ElButton
              v-if="capabilities.hasPermission('admin:export')"
              :icon="RefreshCw"
              :loading="backupLoading"
              @click="loadBackups"
            >
              刷新快照
            </ElButton>
          </div>
          <div class="summary-strip">
            <span>快照 {{ backupPayload.count }}</span>
            <span>扫描 {{ backupPayload.scanned_count }}</span>
            <span :data-warning="backupPayload.malformed_count > 0">异常记录 {{ backupPayload.malformed_count }}</span>
          </div>
          <div v-if="backups.length === 0" class="tab-note">没有可见备份快照</div>
          <div v-else class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>快照</th>
                  <th>创建时间</th>
                  <th>后端</th>
                  <th>大小</th>
                  <th>增量起点</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in backups" :key="String(item.snapshot_id ?? index)">
                  <td><code :title="item.snapshot_id">{{ shortHash(item.snapshot_id) }}</code></td>
                  <td>{{ formatTimestamp(Number(item.created_at)) }}</td>
                  <td>{{ item.object_backend || "--" }}</td>
                  <td>{{ formatBytes(item.bytes) }}</td>
                  <td>{{ item.updated_since == null ? "完整" : formatTimestamp(Number(item.updated_since)) }}</td>
                  <td>{{ item.outcome || "success" }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </ElTabPane>

        <ElTabPane label="审计" name="audit">
          <div class="audit-chain-panel">
            <div>
              <strong>审计链完整性</strong>
              <span v-if="!auditChain">尚未执行校验</span>
              <span v-else :data-warning="!auditChain.ok">
                {{ auditChain.ok ? "通过" : "异常" }} · {{ auditChain.record_count }} 条 ·
                {{ auditChain.error_count }} 个错误
              </span>
              <code v-if="auditChain?.path_hash" :title="auditChain.path_hash">
                路径指纹 {{ shortHash(auditChain.path_hash) }}
              </code>
            </div>
            <ElButton :icon="ShieldCheck" :loading="actionLoading" @click="verifyAudit">校验审计链</ElButton>
          </div>

          <div class="audit-filters">
            <ElInput v-model="auditEventFilter" clearable placeholder="事件名称" @keyup.enter="loadAudit" />
            <ElInput v-model="auditRequestIdFilter" clearable placeholder="请求 ID" @keyup.enter="loadAudit" />
            <ElSelect v-model="auditOutcomeFilter" clearable placeholder="全部结果">
              <ElOption label="成功" value="success" />
              <ElOption label="失败" value="failure" />
            </ElSelect>
            <ElSelect v-model="auditCategoryFilter" clearable placeholder="全部类别">
              <ElOption label="删除请求" value="delete_requests" />
              <ElOption label="导出与备份" value="exports" />
              <ElOption label="模型版本" value="model_versions" />
              <ElOption label="保留策略" value="retention" />
              <ElOption label="其他" value="other" />
            </ElSelect>
            <ElDatePicker
              v-model="auditCreatedRange"
              type="datetimerange"
              start-placeholder="起始时间"
              end-placeholder="结束时间"
              range-separator="至"
            />
            <ElButton :icon="Search" :loading="auditLoading" @click="loadAudit">筛选</ElButton>
          </div>

          <div class="summary-strip">
            <span>匹配 {{ auditPayload.matched_count }}</span>
            <span>扫描 {{ auditPayload.scanned_count }}</span>
            <span :data-warning="auditPayload.malformed_count > 0">异常记录 {{ auditPayload.malformed_count }}</span>
            <span v-for="item in categorySummary" :key="item[0]">{{ item[0] }} {{ item[1] }}</span>
          </div>

          <div v-if="audit.length === 0" class="tab-note">没有符合条件的审计事件</div>
          <div v-else class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>事件</th>
                  <th>类别</th>
                  <th>结果</th>
                  <th>请求 ID</th>
                  <th>链哈希</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in audit" :key="String(item.audit_hash ?? index)">
                  <td>{{ formatTimestamp(Number(item.created_at)) }}</td>
                  <td>{{ item.event }}</td>
                  <td>{{ item.category }}</td>
                  <td>{{ item.outcome || "--" }}</td>
                  <td><code>{{ item.request_id }}</code></td>
                  <td><code :title="item.audit_hash">{{ shortHash(item.audit_hash) }}</code></td>
                </tr>
              </tbody>
            </table>
          </div>
        </ElTabPane>
      </ElTabs>
    </section>

    <DangerConfirm
      v-model="backupOpen"
      title="创建完整备份"
      description="将当前租户可导出的人员、任务、视频流和阈值写入新的受控备份对象。"
      :loading="actionLoading"
      @confirm="createBackup"
    />
    <DangerConfirm
      v-model="cleanupOpen"
      title="执行数据清理"
      :description="'将永久清理当前租户中早于 ' + retentionDays + ' 天的任务、事件和人员数据，同时删除关联对象。'"
      high-risk
      confirmation-text="清理数据"
      :loading="actionLoading"
      @confirm="cleanupRetention"
    />
  </div>
</template>

<style scoped>
.page-tabs {
  padding: 0 14px 14px;
}
.backend-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  padding: 12px 0;
}
.backend-grid div {
  display: flex;
  flex-direction: column;
  padding: 16px;
  background: #f5f8f7;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.backend-grid span,
.audit-chain-panel span {
  color: #62706d;
  font-size: 12px;
}
.backend-grid strong {
  margin-top: 5px;
}
.tab-note {
  padding: 40px;
  color: #62706d;
  text-align: center;
}
.retention-control,
.retention-control label,
.audit-chain-panel,
.audit-chain-panel > div,
.summary-strip {
  display: flex;
  align-items: center;
}
.retention-control,
.audit-chain-panel {
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0 16px;
}
.retention-control label {
  gap: 10px;
  color: #62706d;
  font-size: 13px;
}
.audit-chain-panel {
  margin-bottom: 12px;
  border-bottom: 1px solid #d8e0de;
}
.audit-chain-panel > div {
  min-width: 0;
  flex-wrap: wrap;
  gap: 9px;
}
.audit-chain-panel code {
  overflow-wrap: anywhere;
}
.audit-filters {
  display: grid;
  grid-template-columns: minmax(150px, 1fr) minmax(150px, 1fr) 130px 160px minmax(280px, 1.4fr) auto;
  gap: 8px;
  margin-bottom: 12px;
}
.summary-strip {
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 12px;
  padding: 9px 12px;
  color: #52605d;
  background: #f5f8f7;
  border: 1px solid #d8e0de;
  font-size: 12px;
}
[data-warning="true"] {
  color: #b4232f;
}
@media (max-width: 1000px) {
  .audit-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 700px) {
  .backend-grid,
  .audit-filters {
    grid-template-columns: 1fr;
  }
  .retention-control,
  .audit-chain-panel {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>