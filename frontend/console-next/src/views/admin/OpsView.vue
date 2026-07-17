<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Archive, RefreshCw, ShieldCheck, Trash2 } from "@lucide/vue";
import { ElAlert, ElButton, ElInputNumber, ElMessage, ElSkeleton, ElTabPane, ElTabs } from "element-plus";

import { ApiError, apiRequest, jsonBody } from "../../api/client";
import DangerConfirm from "../../components/DangerConfirm.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { formatTimestamp } from "../../utils/format";

const capabilities = useCapabilitiesStore();
const tab = ref("status");
const loading = ref(true);
const actionLoading = ref(false);
const errorMessage = ref("");
const status = ref<Record<string, unknown>>({});
const audit = ref<Record<string, unknown>[]>([]);
const backups = ref<Record<string, unknown>[]>([]);
const auditChain = ref<Record<string, unknown> | null>(null);
const backupOpen = ref(false);
const cleanupOpen = ref(false);
const retentionDays = ref(90);
const backends = computed(() =>
  status.value.configured_backends && typeof status.value.configured_backends === "object"
    ? Object.entries(status.value.configured_backends as Record<string, unknown>)
    : [],
);

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    status.value = await apiRequest<Record<string, unknown>>("/v1/admin/status");
    const [auditResult, backupResult] = await Promise.allSettled([
      apiRequest<Record<string, unknown>>("/v1/admin/audit/events?limit=50"),
      apiRequest<Record<string, unknown>>("/v1/admin/backups?limit=20"),
    ]);
    if (auditResult.status === "fulfilled") {
      audit.value = (Array.isArray(auditResult.value.events) ? auditResult.value.events : []) as Record<
        string,
        unknown
      >[];
    }
    if (backupResult.status === "fulfilled") {
      backups.value = (
        Array.isArray(backupResult.value.backups)
          ? backupResult.value.backups
          : Array.isArray(backupResult.value.snapshots)
            ? backupResult.value.snapshots
            : []
      ) as Record<string, unknown>[];
    }
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "运维状态加载失败";
  } finally {
    loading.value = false;
  }
}

async function verifyAudit(): Promise<void> {
  actionLoading.value = true;
  try {
    const payload = await apiRequest<{ audit_chain: Record<string, unknown> }>("/v1/admin/audit/verify");
    auditChain.value = payload.audit_chain;
    ElMessage.success("审计链校验已完成");
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "审计链校验失败";
  } finally {
    actionLoading.value = false;
  }
}

async function createBackup(): Promise<void> {
  actionLoading.value = true;
  try {
    await apiRequest("/v1/admin/backup", {
      method: "POST",
      body: jsonBody({ updated_since: null, confirm: "backup" }),
    });
    backupOpen.value = false;
    ElMessage.success("备份快照已创建");
    await load();
    tab.value = "backups";
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "创建备份失败";
  } finally {
    actionLoading.value = false;
  }
}

async function cleanupRetention(): Promise<void> {
  actionLoading.value = true;
  try {
    await apiRequest("/v1/admin/retention/cleanup", {
      method: "POST",
      body: jsonBody({ retention_days: retentionDays.value, confirm: "cleanup" }),
    });
    cleanupOpen.value = false;
    ElMessage.success("保留策略清理已完成");
    await load();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "数据清理失败";
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
        <ElButton v-if="capabilities.hasPermission('admin:export')" :icon="Archive" @click="backupOpen = true"
          >创建备份</ElButton
        >
        <ElButton
          v-if="capabilities.hasPermission('admin:retention')"
          type="danger"
          :icon="Trash2"
          @click="cleanupOpen = true"
          >数据清理</ElButton
        >
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
          <ElSkeleton :loading="loading" :rows="5" animated
            ><div class="backend-grid">
              <div v-for="item in backends" :key="item[0]">
                <span>{{ item[0] }}</span
                ><strong>{{ item[1] }}</strong>
              </div>
            </div></ElSkeleton
          >
        </ElTabPane>
        <ElTabPane label="告警" name="alerts">
          <p class="tab-note">告警状态与 SLO 燃尽率由平台指标实时计算。</p>
        </ElTabPane>
        <ElTabPane label="备份保留" name="backups">
          <div class="retention-control">
            <label
              ><span>清理保留天数</span><ElInputNumber v-model="retentionDays" :min="0" :max="3650"
            /></label>
          </div>
          <div v-if="backups.length === 0" class="tab-note">没有可见备份快照</div>
          <div v-else class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>快照</th>
                  <th>创建时间</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in backups" :key="String(item.backup_id ?? index)">
                  <td>{{ item.backup_id || item.snapshot_id }}</td>
                  <td>{{ formatTimestamp(Number(item.created_at)) }}</td>
                  <td>{{ item.status || "available" }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </ElTabPane>
        <ElTabPane label="审计" name="audit">
          <div class="audit-toolbar">
            <ElButton :icon="ShieldCheck" :loading="actionLoading" @click="verifyAudit">校验审计链</ElButton>
            <span v-if="auditChain">校验结果：{{ auditChain.valid === false ? "异常" : "通过" }}</span>
          </div>
          <div v-if="audit.length === 0" class="tab-note">没有符合条件的审计事件</div>
          <div v-else class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>事件</th>
                  <th>结果</th>
                  <th>请求 ID</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, index) in audit" :key="String(item.event_id ?? index)">
                  <td>{{ formatTimestamp(Number(item.created_at)) }}</td>
                  <td>{{ item.event }}</td>
                  <td>{{ item.outcome }}</td>
                  <td>
                    <code>{{ item.request_id }}</code>
                  </td>
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
      :description="
        '将永久清理当前租户中早于 ' + retentionDays + ' 天的任务、事件和人员数据，同时删除关联对象。'
      "
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
.audit-toolbar span {
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
.audit-toolbar,
.retention-control label {
  display: flex;
  align-items: center;
}
.retention-control,
.audit-toolbar {
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0 16px;
}
.retention-control label {
  gap: 10px;
  color: #62706d;
  font-size: 13px;
}
@media (max-width: 700px) {
  .backend-grid {
    grid-template-columns: 1fr;
  }
}
</style>
