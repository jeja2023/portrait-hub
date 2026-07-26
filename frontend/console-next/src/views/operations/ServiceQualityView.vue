<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { Activity, AlertTriangle, CheckCircle2, Clock3, Plus, RefreshCw } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDatePicker,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElSkeleton,
  ElTabPane,
  ElTabs,
} from "element-plus";

import { apiRequest, jsonBody } from "../../api/client";
import DangerConfirm from "../../components/DangerConfirm.vue";
import DataTablePagination from "../../components/DataTablePagination.vue";
import EmptyState from "../../components/EmptyState.vue";
import StatCard from "../../components/StatCard.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { errorBannerMessage } from "../../utils/errors";
import { formatTimestamp } from "../../utils/format";
import { useRouteTab } from "../../utils/routeState";
import { useTablePagination } from "../../utils/tablePagination";

interface SlaDefinition {
  sla_definition_id?: string;
  definition_version?: string;
  availability_target?: number;
  p95_latency_ms?: number;
  p99_latency_ms?: number;
  window_seconds?: number;
  timezone?: string;
  effective_at?: number;
}
interface SlaReport {
  sla_report_id?: string;
  definition_version?: string;
  availability?: number;
  availability_target?: number;
  p95_latency_ms?: number | null;
  error_budget_remaining?: number;
  request_count?: number;
  met?: boolean;
  created_since?: number;
  created_until?: number;
  created_at?: number;
}
interface Incident {
  incident_id: string;
  incident_number?: string;
  title?: string;
  severity?: string;
  status?: string;
  impact_scope?: string;
  customer_visible_summary?: string;
  owner?: string;
  root_cause?: string | null;
  timeline?: { event_id?: string; at?: number; message?: string; actor?: string }[];
  started_at?: number;
  updated_at?: number;
  version?: number;
}
interface HealthEvent {
  at?: number;
  source_id?: string;
  severity?: string;
  status?: string;
  message?: string;
}

const capabilities = useCapabilitiesStore();
const tab = useRouteTab("incidents");
const loading = ref(true);
const actionLoading = ref(false);
const errorMessage = ref("");
const slaDefinitions = ref<SlaDefinition[]>([]);
const slaReports = ref<SlaReport[]>([]);
const incidents = ref<Incident[]>([]);
const healthEvents = ref<HealthEvent[]>([]);
const incidentFilter = ref("");
const severityFilter = ref("");
const incidentOpen = ref(false);
const slaOpen = ref(false);
const reportOpen = ref(false);
const updateOpen = ref(false);
const updateConfirmOpen = ref(false);
const selectedIncident = ref<Incident | null>(null);

const incidentForm = reactive({ title: "", severity: "sev3", impact_scope: "", customer_visible_summary: "", internal_summary: "", owner: "" });
const slaForm = reactive({ definition_version: "1.0", availability_target: 0.995, p95_latency_ms: 2000, p99_latency_ms: 5000, window_seconds: 2592000, timezone: "Asia/Shanghai" });
const reportRange = ref<[Date, Date] | null>(null);
const updateForm = reactive({ status: "investigating", severity: "sev3", owner: "", impact_scope: "", root_cause: "", timeline_message: "" });

const filteredIncidents = computed(() => incidents.value.filter((item) => (!incidentFilter.value || item.status === incidentFilter.value) && (!severityFilter.value || item.severity === severityFilter.value)));
const incidentsPager = useTablePagination(filteredIncidents);
const reportsPager = useTablePagination(slaReports);
const openIncidents = computed(() => incidents.value.filter((item) => !["resolved", "closed"].includes(String(item.status))).length);
const latestDefinition = computed(() => [...slaDefinitions.value].sort((a, b) => Number(b.effective_at ?? 0) - Number(a.effective_at ?? 0))[0]);
const latestReport = computed(() => slaReports.value[0]);
const canWrite = computed(() => capabilities.hasPermission("operations:write"));

function percentage(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? (number * 100).toFixed(3) + "%" : "--";
}
function incidentStatusLabel(value: unknown): string {
  return ({ investigating: "调查中", identified: "已定位", monitoring: "监控中", resolved: "已恢复", closed: "已关闭" } as Record<string, string>)[String(value)] ?? String(value || "--");
}
function severityLabel(value: unknown): string {
  return ({ sev1: "SEV1 严重", sev2: "SEV2 高", sev3: "SEV3 中", sev4: "SEV4 低" } as Record<string, string>)[String(value)] ?? String(value || "--");
}

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  const results = await Promise.allSettled([
    apiRequest<{ sla_definitions: SlaDefinition[] }>("/v1/admin/operations/sla"),
    apiRequest<{ sla_reports: SlaReport[] }>("/v1/admin/operations/sla/reports"),
    apiRequest<{ incidents: Incident[] }>("/v1/admin/operations/incidents?limit=100"),
    apiRequest<{ events: HealthEvent[] }>("/v1/admin/operations/health-timeline?limit=100"),
  ]);
  if (results[0].status === "fulfilled") slaDefinitions.value = results[0].value.sla_definitions;
  if (results[1].status === "fulfilled") slaReports.value = results[1].value.sla_reports;
  if (results[2].status === "fulfilled") incidents.value = results[2].value.incidents;
  if (results[3].status === "fulfilled") healthEvents.value = results[3].value.events;
  const rejected = results.find((item) => item.status === "rejected");
  if (rejected?.status === "rejected") errorMessage.value = errorBannerMessage(rejected.reason, "服务质量数据加载失败");
  loading.value = false;
}

async function createIncident(): Promise<void> {
  if (!incidentForm.title.trim() || !incidentForm.impact_scope.trim()) {
    ElMessage.warning("请填写事故标题和影响范围");
    return;
  }
  actionLoading.value = true;
  try {
    await apiRequest("/v1/admin/operations/incidents", { method: "POST", body: jsonBody(incidentForm) });
    incidentOpen.value = false;
    ElMessage.success("事故已创建并进入调查中");
    Object.assign(incidentForm, { title: "", severity: "sev3", impact_scope: "", customer_visible_summary: "", internal_summary: "", owner: "" });
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "事故创建失败");
  } finally { actionLoading.value = false; }
}

async function createSla(): Promise<void> {
  actionLoading.value = true;
  try {
    await apiRequest("/v1/admin/operations/sla", { method: "POST", body: jsonBody(slaForm) });
    slaOpen.value = false;
    ElMessage.success("SLA 定义已创建");
    await load();
    tab.value = "sla";
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "SLA 定义创建失败");
  } finally { actionLoading.value = false; }
}

async function createReport(): Promise<void> {
  if (!reportRange.value) { ElMessage.warning("请选择报告时间范围"); return; }
  actionLoading.value = true;
  try {
    await apiRequest("/v1/admin/operations/sla/reports", { method: "POST", body: jsonBody({ created_since: reportRange.value[0].getTime() / 1000, created_until: reportRange.value[1].getTime() / 1000 }) });
    reportOpen.value = false;
    ElMessage.success("SLA 报告已生成");
    await load();
    tab.value = "reports";
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "SLA 报告生成失败");
  } finally { actionLoading.value = false; }
}

function openUpdate(item: Incident): void {
  selectedIncident.value = item;
  Object.assign(updateForm, { status: item.status ?? "investigating", severity: item.severity ?? "sev3", owner: item.owner ?? "", impact_scope: item.impact_scope ?? "", root_cause: item.root_cause ?? "", timeline_message: "" });
  updateOpen.value = true;
}

function requestUpdate(): void {
  if (!updateForm.timeline_message.trim()) { ElMessage.warning("请填写时间线说明"); return; }
  updateOpen.value = false;
  updateConfirmOpen.value = true;
}

async function updateIncident(): Promise<void> {
  if (!selectedIncident.value) return;
  actionLoading.value = true;
  try {
    await apiRequest(`/v1/admin/operations/incidents/${encodeURIComponent(selectedIncident.value.incident_id)}`, { method: "PATCH", body: jsonBody({ ...updateForm, expected_version: selectedIncident.value.version }) });
    updateConfirmOpen.value = false;
    ElMessage.success("事故状态已更新");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "事故更新失败");
  } finally { actionLoading.value = false; }
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header"><div><h1>服务质量与事故</h1><p>维护可审计的 SLA 定义和报告，并用完整时间线管理服务事故。</p></div><div class="page-actions"><ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton><ElButton v-if="canWrite" @click="slaOpen = true">新建 SLA</ElButton><ElButton v-if="canWrite" @click="reportOpen = true">生成报告</ElButton><ElButton v-if="canWrite" type="primary" :icon="Plus" @click="incidentOpen = true">创建事故</ElButton></div></header>
    <ElAlert v-if="errorMessage" class="error-banner" role="alert" :title="errorMessage" type="error" show-icon :closable="false" />
    <ElSkeleton :loading="loading" :rows="5" animated>
      <div class="stat-grid quality-stats">
        <StatCard label="未关闭事故" :value="String(openIncidents)" :tone="openIncidents ? 'danger' : 'success'" :icon="AlertTriangle" detail="调查、定位与监控状态" />
        <StatCard label="可用性目标" :value="percentage(latestDefinition?.availability_target)" :tone="latestDefinition ? 'neutral' : 'warning'" :icon="Activity" :detail="latestDefinition ? `SLA v${latestDefinition.definition_version}` : '尚未定义'" />
        <StatCard label="最近达标结果" :value="latestReport ? (latestReport.met ? '达标' : '未达标') : '无报告'" :tone="latestReport ? (latestReport.met ? 'success' : 'danger') : 'warning'" :icon="CheckCircle2" :detail="latestReport ? percentage(latestReport.availability) : '请生成报告'" />
        <StatCard label="健康时间线" :value="String(healthEvents.length)" :icon="Clock3" detail="最近 100 条事件" />
      </div>
      <section class="tool-surface"><ElTabs v-model="tab" class="page-tabs">
        <ElTabPane :label="`事故 (${incidents.length})`" name="incidents">
          <div class="filter-bar"><ElSelect v-model="incidentFilter" clearable placeholder="全部状态"><ElOption label="调查中" value="investigating" /><ElOption label="已定位" value="identified" /><ElOption label="监控中" value="monitoring" /><ElOption label="已恢复" value="resolved" /><ElOption label="已关闭" value="closed" /></ElSelect><ElSelect v-model="severityFilter" clearable placeholder="全部级别"><ElOption v-for="level in ['sev1','sev2','sev3','sev4']" :key="level" :label="severityLabel(level)" :value="level" /></ElSelect></div>
          <EmptyState v-if="!filteredIncidents.length" title="没有匹配的事故" description="当前过滤条件下没有事故记录。" :action-label="canWrite ? '创建事故' : ''" @action="incidentOpen = true" />
          <template v-else><div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>事故</th><th>级别</th><th>状态</th><th>影响范围</th><th>负责人</th><th>开始时间</th><th>动作</th></tr></thead><tbody><tr v-for="(item, index) in incidentsPager.items" :key="item.incident_id"><td class="sequence-column">{{ incidentsPager.startIndex + index + 1 }}</td><td><strong>{{ item.incident_number }}</strong><br /><span>{{ item.title }}</span></td><td><span class="severity" :data-severity="item.severity">{{ severityLabel(item.severity) }}</span></td><td><span class="status-pill" :data-status="item.status">{{ incidentStatusLabel(item.status) }}</span></td><td class="impact-cell">{{ item.impact_scope }}</td><td>{{ item.owner || '--' }}</td><td>{{ formatTimestamp(item.started_at) }}</td><td><ElButton v-if="canWrite" size="small" @click="openUpdate(item)">处置</ElButton></td></tr></tbody></table></div><DataTablePagination v-model:page="incidentsPager.page" v-model:page-size="incidentsPager.pageSize" :total="incidentsPager.total" /></template>
        </ElTabPane>
        <ElTabPane label="SLA 定义" name="sla">
          <EmptyState v-if="!slaDefinitions.length" title="尚未创建 SLA 定义" description="创建版本化定义后，报告将按该目标计算可用性和错误预算。" :action-label="canWrite ? '新建 SLA' : ''" @action="slaOpen = true" />
          <div v-else class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>版本</th><th>可用性</th><th>P95 / P99</th><th>窗口</th><th>时区</th><th>生效时间</th></tr></thead><tbody><tr v-for="(item, index) in slaDefinitions" :key="item.sla_definition_id"><td class="sequence-column">{{ index + 1 }}</td><td>v{{ item.definition_version }}</td><td>{{ percentage(item.availability_target) }}</td><td>{{ item.p95_latency_ms }} / {{ item.p99_latency_ms }} ms</td><td>{{ Math.round(Number(item.window_seconds) / 86400) }} 天</td><td>{{ item.timezone }}</td><td>{{ formatTimestamp(item.effective_at) }}</td></tr></tbody></table></div>
        </ElTabPane>
        <ElTabPane :label="`SLA 报告 (${slaReports.length})`" name="reports">
          <EmptyState v-if="!slaReports.length" title="尚未生成 SLA 报告" description="报告保存统计窗口、来源完整性、延迟和错误预算证据。" :action-label="canWrite ? '生成报告' : ''" @action="reportOpen = true" />
          <template v-else><div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>结论</th><th>定义</th><th>统计窗口</th><th>可用性</th><th>P95</th><th>请求</th><th>剩余错误预算</th></tr></thead><tbody><tr v-for="(item, index) in reportsPager.items" :key="item.sla_report_id"><td class="sequence-column">{{ reportsPager.startIndex + index + 1 }}</td><td><span class="status-pill" :data-status="item.met ? 'completed' : 'failed'">{{ item.met ? '达标' : '未达标' }}</span></td><td>v{{ item.definition_version }}</td><td>{{ formatTimestamp(item.created_since) }}<br />至 {{ formatTimestamp(item.created_until) }}</td><td>{{ percentage(item.availability) }}</td><td>{{ item.p95_latency_ms ?? '--' }} ms</td><td>{{ item.request_count }}</td><td :data-negative="Number(item.error_budget_remaining) < 0">{{ Number(item.error_budget_remaining ?? 0).toFixed(2) }}</td></tr></tbody></table></div><DataTablePagination v-model:page="reportsPager.page" v-model:page-size="reportsPager.pageSize" :total="reportsPager.total" /></template>
        </ElTabPane>
        <ElTabPane label="健康时间线" name="timeline"><div v-if="!healthEvents.length" class="tab-note">暂无事故时间线事件</div><ol v-else class="timeline"><li v-for="(item,index) in healthEvents" :key="String(item.source_id)+index"><time>{{ formatTimestamp(item.at) }}</time><div><strong>{{ severityLabel(item.severity) }} · {{ incidentStatusLabel(item.status) }}</strong><p>{{ item.message }}</p></div></li></ol></ElTabPane>
      </ElTabs></section>
    </ElSkeleton>

    <ElDialog v-model="incidentOpen" title="创建服务事故" width="min(680px, 94vw)" :close-on-click-modal="false"><ElForm label-position="top" class="form-grid"><ElFormItem label="事故标题" class="span-2"><ElInput v-model="incidentForm.title" /></ElFormItem><ElFormItem label="严重级别"><ElSelect v-model="incidentForm.severity"><ElOption v-for="level in ['sev1','sev2','sev3','sev4']" :key="level" :label="severityLabel(level)" :value="level" /></ElSelect></ElFormItem><ElFormItem label="负责人"><ElInput v-model="incidentForm.owner" /></ElFormItem><ElFormItem label="影响范围" class="span-2"><ElInput v-model="incidentForm.impact_scope" type="textarea" :rows="2" /></ElFormItem><ElFormItem label="客户可见摘要" class="span-2"><ElInput v-model="incidentForm.customer_visible_summary" type="textarea" :rows="2" /></ElFormItem><ElFormItem label="内部摘要" class="span-2"><ElInput v-model="incidentForm.internal_summary" type="textarea" :rows="3" /></ElFormItem></ElForm><template #footer><ElButton @click="incidentOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createIncident">创建事故</ElButton></template></ElDialog>
    <ElDialog v-model="slaOpen" title="新建 SLA 定义" width="min(620px, 94vw)" :close-on-click-modal="false"><ElForm label-position="top" class="form-grid"><ElFormItem label="定义版本"><ElInput v-model="slaForm.definition_version" /></ElFormItem><ElFormItem label="可用性目标"><ElInputNumber v-model="slaForm.availability_target" :min="0.001" :max="1" :step="0.001" :precision="4" /></ElFormItem><ElFormItem label="P95 延迟（ms）"><ElInputNumber v-model="slaForm.p95_latency_ms" :min="1" /></ElFormItem><ElFormItem label="P99 延迟（ms）"><ElInputNumber v-model="slaForm.p99_latency_ms" :min="1" /></ElFormItem><ElFormItem label="统计窗口（秒）"><ElInputNumber v-model="slaForm.window_seconds" :min="60" /></ElFormItem><ElFormItem label="时区"><ElInput v-model="slaForm.timezone" /></ElFormItem></ElForm><template #footer><ElButton @click="slaOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createSla">创建定义</ElButton></template></ElDialog>
    <ElDialog v-model="reportOpen" title="生成 SLA 报告" width="min(520px, 94vw)" :close-on-click-modal="false"><ElForm label-position="top"><ElFormItem label="统计时间范围"><ElDatePicker v-model="reportRange" type="datetimerange" start-placeholder="开始时间" end-placeholder="结束时间" /></ElFormItem></ElForm><template #footer><ElButton @click="reportOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createReport">生成报告</ElButton></template></ElDialog>
    <ElDialog v-model="updateOpen" :title="`处置 ${selectedIncident?.incident_number ?? ''}`" width="min(680px, 94vw)" :close-on-click-modal="false"><ElForm label-position="top" class="form-grid"><ElFormItem label="状态"><ElSelect v-model="updateForm.status"><ElOption label="调查中" value="investigating" /><ElOption label="已定位" value="identified" /><ElOption label="监控中" value="monitoring" /><ElOption label="已恢复" value="resolved" /><ElOption label="已关闭" value="closed" /></ElSelect></ElFormItem><ElFormItem label="严重级别"><ElSelect v-model="updateForm.severity"><ElOption v-for="level in ['sev1','sev2','sev3','sev4']" :key="level" :label="severityLabel(level)" :value="level" /></ElSelect></ElFormItem><ElFormItem label="负责人"><ElInput v-model="updateForm.owner" /></ElFormItem><ElFormItem label="影响范围"><ElInput v-model="updateForm.impact_scope" /></ElFormItem><ElFormItem label="根因" class="span-2"><ElInput v-model="updateForm.root_cause" type="textarea" :rows="3" /></ElFormItem><ElFormItem label="时间线说明" class="span-2"><ElInput v-model="updateForm.timeline_message" type="textarea" :rows="2" /></ElFormItem></ElForm><template #footer><ElButton @click="updateOpen = false">取消</ElButton><ElButton type="primary" @click="requestUpdate">继续</ElButton></template></ElDialog>
    <DangerConfirm v-model="updateConfirmOpen" title="确认事故状态变更" :description="`将 ${selectedIncident?.incident_number ?? ''} 更新为“${incidentStatusLabel(updateForm.status)}”，并写入不可省略的处置时间线。`" :high-risk="['resolved','closed'].includes(updateForm.status)" confirmation-text="确认事故结论" :loading="actionLoading" @confirm="updateIncident" />
  </div>
</template>

<style scoped>
.quality-stats { margin-bottom: 16px; }
.page-tabs { padding: 0 14px 16px; }
.filter-bar { display: flex; gap: 8px; padding: 8px 0 14px; }
.filter-bar :deep(.el-select) { width: 180px; }
.impact-cell { min-width: 220px; max-width: 380px; }
.severity { white-space: nowrap; font-size: 12px; font-weight: 700; }
.severity[data-severity="sev1"], [data-negative="true"] { color: var(--danger); }
.severity[data-severity="sev2"] { color: #a65c00; }
.tab-note { padding: 48px; color: var(--muted); text-align: center; }
.timeline { margin: 10px 0; padding: 0; list-style: none; }
.timeline li { display: grid; grid-template-columns: 170px 1fr; gap: 18px; padding: 13px 4px; border-bottom: 1px solid var(--line); }
.timeline time { color: var(--muted); font-size: 12px; }
.timeline p { margin: 5px 0 0; color: var(--muted); }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.form-grid .span-2 { grid-column: 1 / -1; }
.form-grid :deep(.el-select), .form-grid :deep(.el-input-number) { width: 100%; }
@media (max-width: 700px) { .filter-bar, .form-grid { display: grid; grid-template-columns: 1fr; } .filter-bar :deep(.el-select) { width: 100%; } .form-grid .span-2 { grid-column: auto; } .timeline li { grid-template-columns: 1fr; gap: 5px; } }
</style>
