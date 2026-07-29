<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { BadgeDollarSign, Ban, Gauge, Layers3, LifeBuoy, RefreshCw, RotateCcw, ShieldCheck, TrendingUp } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDialog,
  ElDatePicker,
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
import { sessionState } from "../../auth/session";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { errorBannerMessage } from "../../utils/errors";
import { formatTimestamp } from "../../utils/format";
import { useRouteTab } from "../../utils/routeState";
import { useTablePagination } from "../../utils/tablePagination";

interface CommercialProfile {
  commercial_status?: string;
  delivery_tier?: string;
  environment?: string;
  timezone?: string;
  budget_limit?: number | null;
  budget_currency?: string;
  current_entitlement_id?: string | null;
  template_id?: string | null;
  version?: number;
  scheduled_transition?: {
    transition_id?: string;
    status?: string;
    to_status?: string;
    effective_at?: number;
    reason?: string;
  } | null;
}
interface Entitlement {
  entitlement_id?: string;
  product_version?: string;
  delivery_tier?: string;
  allowed_capabilities?: string[];
  allowed_models?: string[];
  concurrency_limit?: number;
  stream_limit?: number;
  support_level?: string;
  status?: string;
  version?: number;
  starts_at?: number;
  expires_at?: number | null;
  change_type?: string;
  rollback_target_id?: string | null;
  record_version?: number;
  approved_by?: string;
}
interface UsageSummary {
  request_count?: number;
  success_count?: number;
  error_count?: number;
  success_rate?: number;
  latency_ms?: { p50?: number | null; p95?: number | null; p99?: number | null };
  by_endpoint?: { endpoint?: string; request_count?: number }[];
  by_model?: { model_version?: string; request_count?: number }[];
  by_capability?: { capability?: string; request_count?: number }[];
  by_resource_type?: { resource_type?: string; request_count?: number }[];
  quantities?: {
    image_count?: number;
    video_seconds?: number;
    gpu_seconds?: number;
    storage_byte_seconds?: number;
    network_egress_bytes?: number;
    third_party_units?: number;
  };
  outcomes?: Record<string, number>;
  delivery_kinds?: Record<string, number>;
  cost?: { amount?: number; currency?: string; status?: string; unpriced_event_count?: number };
  budget?: { limit?: number | null; utilization?: number | null; alert_status?: string };
  complete?: boolean;
}
interface QuotaApplication {
  application_id?: string;
  daily_quota?: number | null;
  used?: number;
  remaining?: number | null;
  utilization?: number | null;
  forecast_exhaustion_at?: number | null;
  consumption_rate_per_hour?: number;
  alert_status?: string;
}
interface IndustryTemplate {
  template_id: string;
  name?: string;
  version?: string;
  allowed_capabilities?: string[];
  risk_controls?: string[];
  rollback_supported?: boolean;
}
interface CommercialLicense {
  required?: boolean;
  ok?: boolean;
  runtime_status?: string;
  license_id?: string;
  product_version?: string;
  delivery_profile?: string;
  expires_at?: string;
  grace_until?: string;
  entitlement_count?: number;
  error?: string;
}
interface SupportCase {
  support_case_id: string;
  title?: string;
  severity?: string;
  status?: string;
  environment?: string;
  product_version?: string;
  owner?: string | null;
  response_due_at?: number | null;
  version?: number;
  created_at?: number;
}
interface TemplateApplication {
  template_application_id: string;
  template_id?: string;
  template_version?: string;
  status?: string;
  created_at?: number;
  created_by?: string;
}
interface TemplatePreview {
  template: IndustryTemplate;
  changes: Record<string, { before?: unknown; after?: unknown }>;
  fingerprint: string;
}

const capabilities = useCapabilitiesStore();
const tab = useRouteTab("usage");
const loading = ref(true);
const actionLoading = ref(false);
const errorMessage = ref("");
const profile = ref<CommercialProfile>({});
const entitlements = ref<Entitlement[]>([]);
const usage = ref<UsageSummary>({});
const timeseries = ref<Record<string, unknown>[]>([]);
const quotaApplications = ref<QuotaApplication[]>([]);
const templates = ref<IndustryTemplate[]>([]);
const commercialLicense = ref<CommercialLicense>({});
const supportCases = ref<SupportCase[]>([]);
const templateApplications = ref<TemplateApplication[]>([]);
const profileOpen = ref(false);
const profileConfirmOpen = ref(false);
const entitlementOpen = ref(false);
const templateConfirmOpen = ref(false);
const templatePreview = ref<TemplatePreview | null>(null);
const supportOpen = ref(false);
const rollbackConfirmOpen = ref(false);
const rollbackTarget = ref<TemplateApplication | null>(null);
const entitlementActionConfirmOpen = ref(false);
const entitlementActionFormOpen = ref(false);
const entitlementActionTarget = ref<Entitlement | null>(null);
const entitlementAction = ref<"cancel" | "revoke" | "rollback">("cancel");
const entitlementActionReason = ref("");
const entitlementActionApprover = ref("");

const profileForm = reactive({
  commercial_status: "trial",
  delivery_tier: "platform_api",
  environment: "development",
  timezone: "Asia/Shanghai",
  budget_limit: undefined as number | undefined,
  budget_currency: "CNY",
  effective_at: undefined as Date | undefined,
  cancel_scheduled_transition: false,
  reason: "商业配置更新",
  approved_by: "",
});
const entitlementForm = reactive({
  product_version: "1.0",
  delivery_tier: "platform_api",
  capabilities: "person_detection,tracking",
  models: "",
  concurrency_limit: 4,
  stream_limit: 1,
  support_level: "standard",
  grace_period_seconds: 86400,
  change_type: "renewal",
  starts_at: undefined as Date | undefined,
  expires_at: undefined as Date | undefined,
  reason: "合同权益变更",
  approved_by: "",
});
const supportForm = reactive({
  title: "",
  description: "",
  severity: "sev3",
  environment: "production",
  product_version: "0.18.5",
  request_ids: "",
  task_ids: "",
});

const outcomeRows = computed(() => [
  ...Object.entries(usage.value.outcomes ?? {}).map(([name, count]) => ({ rowKey: `outcome-${name}`, kind: "结果", name, count })),
  ...Object.entries(usage.value.delivery_kinds ?? {}).map(([name, count]) => ({ rowKey: `delivery-${name}`, kind: "投递", name, count })),
]);
const dimensionRows = computed(() => [
  ...(usage.value.by_model ?? []).map((item) => ({ rowKey: `model-${item.model_version}`, kind: "模型", name: item.model_version, request_count: item.request_count })),
  ...(usage.value.by_capability ?? []).map((item) => ({ rowKey: `capability-${item.capability}`, kind: "能力", name: item.capability, request_count: item.request_count })),
]);
const endpointPager = useTablePagination(() => usage.value.by_endpoint ?? []);
const quotaPager = useTablePagination(quotaApplications);
const outcomePager = useTablePagination(outcomeRows);
const dimensionPager = useTablePagination(dimensionRows);
const timeseriesPager = useTablePagination(timeseries);
const entitlementsPager = useTablePagination(entitlements);
const templatesPager = useTablePagination(templates);
const templateApplicationsPager = useTablePagination(templateApplications);
const supportPager = useTablePagination(supportCases);
const quotaTotal = computed(() => quotaApplications.value.reduce((sum, item) => sum + Number(item.daily_quota ?? 0), 0));
const quotaRemaining = computed(() => quotaApplications.value.reduce((sum, item) => sum + Number(item.remaining ?? 0), 0));
const quotaUtilization = computed(() => quotaTotal.value ? 1 - quotaRemaining.value / quotaTotal.value : null);
const statusTone = computed(() => profile.value.commercial_status === "active" ? "success" : profile.value.commercial_status === "suspended" ? "danger" : "warning");
const canWrite = computed(() => capabilities.hasPermission("commercial:write"));
const canSupportWrite = computed(() => capabilities.hasPermission("support:write"));
const licenseTone = computed(() => commercialLicense.value.ok ? "success" : commercialLicense.value.required ? "danger" : "neutral");

function projectPath(path: string): string {
  return path.replace("{project_id}", encodeURIComponent(sessionState.projectId || "default"));
}

function splitList(value: string): string[] {
  return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
}

function percentage(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? (number * 100).toFixed(2) + "%" : "--";
}

function formatAmount(value: unknown, currency = "CNY"): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${currency} ${number.toFixed(2)}` : "--";
}

function profileStatusLabel(value: unknown): string {
  return ({ trial: "试用", active: "生效", grace: "宽限期", suspended: "暂停", offboarding: "退租中", closed: "已关闭" } as Record<string, string>)[String(value)] ?? String(value || "--");
}

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  const results = await Promise.allSettled([
    apiRequest<{ commercial_profile: CommercialProfile }>(projectPath("/v1/access/projects/{project_id}/commercial-profile")),
    apiRequest<{ entitlements: Entitlement[] }>("/v1/access/entitlements"),
    apiRequest<{ usage_summary: UsageSummary }>("/v1/access/usage/summary"),
    apiRequest<{ timeseries: Record<string, unknown>[] }>("/v1/access/usage/timeseries"),
    apiRequest<{ quota_forecast: { applications?: QuotaApplication[] } }>("/v1/access/quota/forecast"),
    apiRequest<{ industry_templates: IndustryTemplate[] }>("/v1/admin/industry-templates"),
    apiRequest<{ commercial_license: CommercialLicense }>("/v1/access/license/status"),
    apiRequest<{ support_cases: SupportCase[] }>("/v1/access/support/cases"),
    apiRequest<{ template_applications: TemplateApplication[] }>("/v1/admin/industry-template-applications"),
  ]);
  const [profileResult, entitlementResult, usageResult, seriesResult, quotaResult, templateResult, licenseResult, supportResult, applicationsResult] = results;
  if (profileResult.status === "fulfilled") profile.value = profileResult.value.commercial_profile;
  if (entitlementResult.status === "fulfilled") entitlements.value = entitlementResult.value.entitlements;
  if (usageResult.status === "fulfilled") usage.value = usageResult.value.usage_summary;
  if (seriesResult.status === "fulfilled") timeseries.value = seriesResult.value.timeseries;
  if (quotaResult.status === "fulfilled") quotaApplications.value = quotaResult.value.quota_forecast.applications ?? [];
  if (templateResult.status === "fulfilled") templates.value = templateResult.value.industry_templates;
  if (licenseResult.status === "fulfilled") commercialLicense.value = licenseResult.value.commercial_license;
  if (supportResult.status === "fulfilled") supportCases.value = supportResult.value.support_cases;
  if (applicationsResult.status === "fulfilled") templateApplications.value = applicationsResult.value.template_applications;
  const rejected = results.find((item) => item.status === "rejected");
  if (rejected?.status === "rejected") errorMessage.value = errorBannerMessage(rejected.reason, "商业运营数据加载失败");
  loading.value = false;
}

function openProfile(): void {
  Object.assign(profileForm, {
    commercial_status: profile.value.commercial_status ?? "trial",
    delivery_tier: profile.value.delivery_tier ?? "platform_api",
    environment: profile.value.environment ?? "development",
    timezone: profile.value.timezone ?? "Asia/Shanghai",
    budget_limit: profile.value.budget_limit ?? undefined,
    budget_currency: profile.value.budget_currency ?? "CNY",
    effective_at: undefined,
    cancel_scheduled_transition: false,
    reason: "商业配置更新",
    approved_by: "",
  });
  profileOpen.value = true;
}

function requestCancelProfileTransition(): void {
  openProfile();
  profileForm.cancel_scheduled_transition = true;
  profileForm.reason = "取消预约状态变更";
}

function requestProfileSave(): void {
  if (!profileForm.reason.trim()) {
    ElMessage.warning("请填写变更原因");
    return;
  }
  profileOpen.value = false;
  profileConfirmOpen.value = true;
}

async function saveProfile(): Promise<void> {
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest(projectPath("/v1/access/projects/{project_id}/commercial-profile"), {
      method: "PATCH",
      body: jsonBody({
        ...profileForm,
        effective_at: profileForm.effective_at ? profileForm.effective_at.getTime() / 1000 : undefined,
        expected_version: profile.value.version,
      }),
    });
    profileConfirmOpen.value = false;
    ElMessage.success("商业配置已更新");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "商业配置更新失败");
  } finally {
    actionLoading.value = false;
  }
}

async function createEntitlement(): Promise<void> {
  const allowedCapabilities = splitList(entitlementForm.capabilities);
  if (!allowedCapabilities.length || !entitlementForm.reason.trim() || !entitlementForm.approved_by.trim()) {
    ElMessage.warning("请填写能力范围、变更原因和审批人");
    return;
  }
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest("/v1/access/entitlements", {
      method: "POST",
      body: jsonBody({
        definition_version: "1.0",
        product_version: entitlementForm.product_version,
        delivery_tier: entitlementForm.delivery_tier,
        allowed_capabilities: allowedCapabilities,
        allowed_models: splitList(entitlementForm.models),
        project_limit: 1,
        concurrency_limit: entitlementForm.concurrency_limit,
        stream_limit: entitlementForm.stream_limit,
        support_level: entitlementForm.support_level,
        grace_period_seconds: entitlementForm.grace_period_seconds,
        change_type: entitlements.value.length ? entitlementForm.change_type : "new",
        starts_at: entitlementForm.starts_at ? entitlementForm.starts_at.getTime() / 1000 : undefined,
        expires_at: entitlementForm.expires_at ? entitlementForm.expires_at.getTime() / 1000 : undefined,
        reason: entitlementForm.reason,
        rollback_target_id: profile.value.current_entitlement_id || undefined,
        expected_current_entitlement_id: profile.value.current_entitlement_id || undefined,
        approved_by: entitlementForm.approved_by,
      }),
    });
    entitlementOpen.value = false;
    ElMessage.success(entitlementForm.starts_at && entitlementForm.starts_at.getTime() > Date.now() ? "权益版本已预约" : "新权益版本已生效");
    await load();
    tab.value = "entitlements";
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "权益创建失败");
  } finally {
    actionLoading.value = false;
  }
}

function requestEntitlementAction(item: Entitlement, action: "cancel" | "revoke" | "rollback"): void {
  entitlementActionTarget.value = item;
  entitlementAction.value = action;
  entitlementActionReason.value = action === "rollback" ? "显式恢复上一权益版本" : action === "cancel" ? "取消待生效权益" : "撤销当前权益";
  entitlementActionApprover.value = "";
  entitlementActionFormOpen.value = true;
}

function proceedEntitlementAction(): void {
  if (!entitlementActionReason.value.trim() || !entitlementActionApprover.value.trim()) {
    ElMessage.warning("请填写操作原因和审批人");
    return;
  }
  entitlementActionFormOpen.value = false;
  entitlementActionConfirmOpen.value = true;
}

async function applyEntitlementAction(): Promise<void> {
  const target = entitlementActionTarget.value;
  if (!target?.entitlement_id) return;
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest(`/v1/access/entitlements/${encodeURIComponent(target.entitlement_id)}/actions`, {
      method: "POST",
      body: jsonBody({
        action: entitlementAction.value,
        reason: entitlementActionReason.value,
        approved_by: entitlementActionApprover.value,
        expected_version: target.record_version ?? 1,
        expected_current_entitlement_id: profile.value.current_entitlement_id || undefined,
      }),
    });
    entitlementActionConfirmOpen.value = false;
    entitlementActionTarget.value = null;
    ElMessage.success("权益状态已更新");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "权益状态更新失败");
  } finally {
    actionLoading.value = false;
  }
}

async function previewTemplate(item: IndustryTemplate): Promise<void> {
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ preview: TemplatePreview }>(`/v1/admin/industry-templates/${encodeURIComponent(item.template_id)}/preview`);
    templatePreview.value = payload.preview;
    templateConfirmOpen.value = true;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "模板预览失败");
  } finally {
    actionLoading.value = false;
  }
}

async function applyTemplate(): Promise<void> {
  if (!templatePreview.value) return;
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest(`/v1/admin/industry-templates/${encodeURIComponent(templatePreview.value.template.template_id)}/apply`, {
      method: "POST",
      body: jsonBody({ expected_fingerprint: templatePreview.value.fingerprint, dry_run: false }),
    });
    templateConfirmOpen.value = false;
    ElMessage.success("行业模板已应用");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "行业模板应用失败");
  } finally {
    actionLoading.value = false;
  }
}

async function createSupportCase(): Promise<void> {
  if (!supportForm.title.trim() || !supportForm.description.trim()) {
    ElMessage.warning("请填写问题标题和描述");
    return;
  }
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest("/v1/access/support/cases", {
      method: "POST",
      body: jsonBody({
        ...supportForm,
        request_ids: splitList(supportForm.request_ids),
        task_ids: splitList(supportForm.task_ids),
        redacted_attachments: [],
      }),
    });
    supportOpen.value = false;
    Object.assign(supportForm, { title: "", description: "", request_ids: "", task_ids: "" });
    ElMessage.success("支持工单已提交");
    await load();
    tab.value = "support";
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "支持工单提交失败");
  } finally {
    actionLoading.value = false;
  }
}

async function updateSupportCase(item: SupportCase, nextStatus: string): Promise<void> {
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest(`/v1/access/support/cases/${encodeURIComponent(item.support_case_id)}`, {
      method: "PATCH",
      body: jsonBody({ status: nextStatus, expected_version: item.version }),
    });
    ElMessage.success("工单状态已更新");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "工单状态更新失败");
  } finally {
    actionLoading.value = false;
  }
}

function requestTemplateRollback(item: TemplateApplication): void {
  rollbackTarget.value = item;
  rollbackConfirmOpen.value = true;
}

async function rollbackTemplate(): Promise<void> {
  if (!rollbackTarget.value) return;
  actionLoading.value = true;
  errorMessage.value = "";
  try {
    await apiRequest(`/v1/admin/industry-template-applications/${encodeURIComponent(rollbackTarget.value.template_application_id)}/rollback`, {
      method: "POST",
      body: jsonBody({ reason: "控制台显式回滚" }),
    });
    rollbackConfirmOpen.value = false;
    rollbackTarget.value = null;
    ElMessage.success("行业模板已回滚");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "行业模板回滚失败");
  } finally {
    actionLoading.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header">
      <div><h1>商业运营</h1><p>管理当前项目的商业状态、版本化权益、用量和行业方案。</p></div>
      <div class="page-actions">
        <ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton>
        <ElButton v-if="canSupportWrite" :icon="LifeBuoy" @click="supportOpen = true">提交工单</ElButton>
        <ElButton v-if="canWrite" @click="openProfile">编辑商业配置</ElButton>
        <ElButton v-if="canWrite" type="primary" @click="entitlementOpen = true">创建权益版本</ElButton>
      </div>
    </header>
    <ElAlert v-if="errorMessage" class="error-banner" role="alert" :title="errorMessage" type="error" show-icon :closable="false" />

    <ElSkeleton :loading="loading" :rows="5" animated>
      <div class="stat-grid commercial-stats">
        <StatCard label="归因成本" :value="formatAmount(usage.cost?.amount, usage.cost?.currency)" :tone="usage.budget?.alert_status === 'exceeded' ? 'danger' : usage.budget?.alert_status === 'warning' ? 'warning' : 'neutral'" :icon="BadgeDollarSign" :detail="usage.cost?.status === 'unconfigured' ? '成本模型未配置' : '预算 ' + percentage(usage.budget?.utilization)" />
        <StatCard label="商业状态" :value="profileStatusLabel(profile.commercial_status)" :tone="statusTone" :icon="BadgeDollarSign" :detail="profile.delivery_tier || '--'" />
        <StatCard label="调用量" :value="String(usage.request_count ?? 0)" :icon="TrendingUp" :detail="'成功率 ' + percentage(usage.success_rate)" />
        <StatCard label="今日剩余配额" :value="quotaTotal ? String(quotaRemaining) : '不限额'" :tone="quotaUtilization && quotaUtilization > 0.85 ? 'warning' : 'neutral'" :icon="Gauge" :detail="quotaTotal ? '利用率 ' + percentage(quotaUtilization) : '未配置应用配额'" />
        <StatCard label="当前权益" :value="entitlements[0] ? 'v' + (entitlements[0].version ?? 1) : '未配置'" :tone="entitlements[0] ? 'success' : 'warning'" :icon="Layers3" :detail="entitlements[0]?.support_level || '请创建权益版本'" />
        <StatCard label="离线授权" :value="commercialLicense.runtime_status || '未知'" :tone="licenseTone" :icon="ShieldCheck" :detail="commercialLicense.required ? (commercialLicense.license_id || commercialLicense.error || '授权无效') : '当前交付形态无需离线授权'" />
      </div>

      <section class="tool-surface commercial-surface">
        <ElTabs v-model="tab" class="page-tabs">
          <ElTabPane label="用量与配额" name="usage">
            <div class="split-grid">
              <section>
                <h2>接口用量</h2>
                <div v-if="!usage.by_endpoint?.length" class="tab-note">当前统计窗口暂无调用</div>
                <template v-else>
                  <div class="table-wrap">
                    <table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>接口</th><th>调用次数</th></tr></thead><tbody>
                      <tr v-for="(item, index) in endpointPager.items" :key="item.endpoint"><td class="sequence-column">{{ endpointPager.startIndex + index + 1 }}</td><td><code>{{ item.endpoint }}</code></td><td>{{ item.request_count }}</td></tr>
                    </tbody></table>
                  </div>
                  <DataTablePagination v-model:page="endpointPager.page" v-model:page-size="endpointPager.pageSize" :total="endpointPager.total" />
                </template>
              </section>
              <section>
                <h2>应用配额</h2>
                <div v-if="!quotaApplications.length" class="tab-note">当前项目没有应用配额</div>
                <template v-else>
                  <div class="table-wrap">
                    <table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>应用</th><th>已用 / 总量</th><th>利用率</th></tr></thead><tbody>
                      <tr v-for="(item, index) in quotaPager.items" :key="item.application_id"><td class="sequence-column">{{ quotaPager.startIndex + index + 1 }}</td><td><code>{{ item.application_id }}</code></td><td>{{ item.used ?? 0 }} / {{ item.daily_quota ?? '不限额' }}</td><td>{{ percentage(item.utilization) }}</td></tr>
                    </tbody></table>
                  </div>
                  <DataTablePagination v-model:page="quotaPager.page" v-model:page-size="quotaPager.pageSize" :total="quotaPager.total" />
                </template>
              </section>
            </div>
            <div class="usage-details-grid">
              <section>
                <h2>资源计量</h2>
                <dl class="detail-grid usage-metrics">
                  <div><dt>图片</dt><dd>{{ usage.quantities?.image_count ?? 0 }} 张</dd></div>
                  <div><dt>视频时长</dt><dd>{{ Number(usage.quantities?.video_seconds ?? 0).toFixed(1) }} 秒</dd></div>
                  <div><dt>GPU 时间</dt><dd>{{ Number(usage.quantities?.gpu_seconds ?? 0).toFixed(2) }} 秒</dd></div>
                  <div><dt>网络流量</dt><dd>{{ (Number(usage.quantities?.network_egress_bytes ?? 0) / 1073741824).toFixed(3) }} GiB</dd></div>
                </dl>
              </section>
              <section>
                <h2>结果与投递</h2>
                <div v-if="!outcomeRows.length" class="tab-note">当前窗口暂无结果与投递记录</div>
                <template v-else>
                  <div class="table-wrap">
                    <table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>类型</th><th>分类</th><th>数量</th></tr></thead><tbody>
                      <tr v-for="(item, index) in outcomePager.items" :key="item.rowKey"><td class="sequence-column">{{ outcomePager.startIndex + index + 1 }}</td><td>{{ item.kind }}</td><td>{{ item.name }}</td><td>{{ item.count }}</td></tr>
                    </tbody></table>
                  </div>
                  <DataTablePagination v-model:page="outcomePager.page" v-model:page-size="outcomePager.pageSize" :total="outcomePager.total" />
                </template>
              </section>
              <section>
                <h2>模型与能力</h2>
                <div v-if="!dimensionRows.length" class="tab-note">当前窗口暂无模型与能力统计</div>
                <template v-else>
                  <div class="table-wrap">
                    <table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>维度类型</th><th>维度</th><th>调用</th></tr></thead><tbody>
                      <tr v-for="(item, index) in dimensionPager.items" :key="item.rowKey"><td class="sequence-column">{{ dimensionPager.startIndex + index + 1 }}</td><td>{{ item.kind }}</td><td>{{ item.name }}</td><td>{{ item.request_count }}</td></tr>
                    </tbody></table>
                  </div>
                  <DataTablePagination v-model:page="dimensionPager.page" v-model:page-size="dimensionPager.pageSize" :total="dimensionPager.total" />
                </template>
              </section>
              <section>
                <h2>日趋势</h2>
                <div v-if="!timeseries.length" class="tab-note">当前窗口暂无计量事件</div>
                <template v-else>
                  <div class="table-wrap">
                    <table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>日期</th><th>调用</th><th>成功率</th><th>成本</th></tr></thead><tbody>
                      <tr v-for="(item, index) in timeseriesPager.items" :key="String(item.period || item.date)"><td class="sequence-column">{{ timeseriesPager.startIndex + index + 1 }}</td><td>{{ item.period || item.date }}</td><td>{{ item.request_count }}</td><td>{{ percentage(item.success_rate) }}</td><td>{{ formatAmount(item.cost, usage.cost?.currency) }}</td></tr>
                    </tbody></table>
                  </div>
                  <DataTablePagination v-model:page="timeseriesPager.page" v-model:page-size="timeseriesPager.pageSize" :total="timeseriesPager.total" />
                </template>
              </section>
            </div>
          </ElTabPane>

          <ElTabPane :label="`权益版本 (${entitlements.length})`" name="entitlements">
            <EmptyState v-if="!entitlements.length" title="尚未创建权益版本" description="权益决定项目可调用能力、并发、流数量和支持级别。" :action-label="canWrite ? '创建权益版本' : ''" @action="entitlementOpen = true" />
            <template v-else>
              <div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>版本</th><th>状态</th><th>变更类型</th><th>产品/交付</th><th>能力</th><th>并发/流</th><th>生效区间</th><th>操作</th></tr></thead><tbody>
                <tr v-for="(item, index) in entitlementsPager.items" :key="item.entitlement_id"><td class="sequence-column">{{ entitlementsPager.startIndex + index + 1 }}</td><td>v{{ item.version }}</td><td><span class="status-pill" :data-status="item.status">{{ item.status }}</span></td><td>{{ item.change_type || '--' }}</td><td>{{ item.product_version }} / {{ item.delivery_tier }}</td><td class="capability-cell">{{ item.allowed_capabilities?.join('、') || '--' }}</td><td>{{ item.concurrency_limit }} / {{ item.stream_limit }}</td><td>{{ formatTimestamp(item.starts_at) }}<br /><span class="table-subline">至 {{ item.expires_at ? formatTimestamp(item.expires_at) : '长期' }}</span></td><td><div v-if="canWrite" class="inline-actions"><ElButton v-if="item.status === 'pending'" :icon="Ban" size="small" title="取消待生效权益" aria-label="取消待生效权益" @click="requestEntitlementAction(item, 'cancel')" /><ElButton v-if="item.status === 'active' && item.rollback_target_id" :icon="RotateCcw" size="small" title="回滚当前权益" aria-label="回滚当前权益" @click="requestEntitlementAction(item, 'rollback')" /></div></td></tr>
              </tbody></table></div>
              <DataTablePagination v-model:page="entitlementsPager.page" v-model:page-size="entitlementsPager.pageSize" :total="entitlementsPager.total" />
            </template>
          </ElTabPane>

          <ElTabPane label="商业配置" name="profile">
            <ElAlert
              v-if="profile.scheduled_transition"
              class="scheduled-alert"
              :title="`已预约在 ${formatTimestamp(profile.scheduled_transition.effective_at)} 切换为“${profileStatusLabel(profile.scheduled_transition.to_status)}”`"
              :description="profile.scheduled_transition.reason"
              type="warning"
              :closable="false"
              show-icon
            >
              <template #default><ElButton v-if="canWrite" size="small" @click="requestCancelProfileTransition">取消预约</ElButton></template>
            </ElAlert>
            <dl class="profile-grid">
              <div><dt>项目</dt><dd>{{ sessionState.projectId }}</dd></div><div><dt>状态</dt><dd>{{ profileStatusLabel(profile.commercial_status) }}</dd></div>
              <div><dt>交付层级</dt><dd>{{ profile.delivery_tier || '--' }}</dd></div><div><dt>环境</dt><dd>{{ profile.environment || '--' }}</dd></div>
              <div><dt>时区</dt><dd>{{ profile.timezone || '--' }}</dd></div><div><dt>预算上限</dt><dd>{{ profile.budget_limit == null ? '未设置' : `${profile.budget_currency} ${profile.budget_limit}` }}</dd></div>
              <div><dt>当前模板</dt><dd>{{ profile.template_id || '未应用' }}</dd></div><div><dt>配置版本</dt><dd>v{{ profile.version ?? 1 }}</dd></div>
              <div><dt>授权产品版本</dt><dd>{{ commercialLicense.product_version || '--' }}</dd></div><div><dt>授权到期</dt><dd>{{ commercialLicense.expires_at ? formatTimestamp(commercialLicense.expires_at) : '--' }}</dd></div>
            </dl>
          </ElTabPane>

          <ElTabPane :label="`行业模板 (${templates.length})`" name="templates">
            <div v-if="!templates.length" class="tab-note">当前没有可用行业模板</div>
            <template v-else>
              <div class="table-wrap">
                <table class="data-table template-table"><thead><tr><th class="sequence-column">序号</th><th>模板</th><th>版本</th><th>能力</th><th>风险控制</th><th>回滚</th><th v-if="canWrite">操作</th></tr></thead><tbody>
                  <tr v-for="(item, index) in templatesPager.items" :key="item.template_id"><td class="sequence-column">{{ templatesPager.startIndex + index + 1 }}</td><td class="template-name"><strong>{{ item.name }}</strong><br /><code>{{ item.template_id }}</code></td><td>v{{ item.version }}</td><td class="capability-cell">{{ item.allowed_capabilities?.join('、') || '--' }}</td><td class="risk-control-cell">{{ item.risk_controls?.join('、') || '--' }}</td><td><span class="status-pill" :data-status="item.rollback_supported ? 'completed' : ''">{{ item.rollback_supported ? '支持' : '不支持' }}</span></td><td v-if="canWrite"><ElButton size="small" :loading="actionLoading" @click="previewTemplate(item)">预览并应用</ElButton></td></tr>
                </tbody></table>
              </div>
              <DataTablePagination v-model:page="templatesPager.page" v-model:page-size="templatesPager.pageSize" :total="templatesPager.total" />
            </template>
            <h2 class="section-heading">应用历史</h2>
            <div v-if="!templateApplications.length" class="tab-note">当前项目没有模板应用记录</div>
            <template v-else>
              <div class="table-wrap">
                <table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>模板</th><th>状态</th><th>操作人</th><th>应用时间</th><th v-if="canWrite">操作</th></tr></thead><tbody>
                  <tr v-for="(item, index) in templateApplicationsPager.items" :key="item.template_application_id"><td class="sequence-column">{{ templateApplicationsPager.startIndex + index + 1 }}</td><td>{{ item.template_id }} · v{{ item.template_version }}</td><td><span class="status-pill" :data-status="item.status">{{ item.status }}</span></td><td>{{ item.created_by || '--' }}</td><td>{{ formatTimestamp(item.created_at) }}</td><td v-if="canWrite"><ElButton v-if="item.status === 'applied'" :icon="RotateCcw" size="small" :loading="actionLoading" aria-label="回滚行业模板" @click="requestTemplateRollback(item)" /></td></tr>
                </tbody></table>
              </div>
              <DataTablePagination v-model:page="templateApplicationsPager.page" v-model:page-size="templateApplicationsPager.pageSize" :total="templateApplicationsPager.total" />
            </template>
          </ElTabPane>

          <ElTabPane :label="`支持工单 (${supportCases.length})`" name="support">
            <EmptyState v-if="!supportCases.length" title="暂无支持工单" description="当前项目没有待处理或历史支持记录。" :action-label="canSupportWrite ? '提交工单' : ''" @action="supportOpen = true" />
            <template v-else>
              <div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>级别</th><th>标题</th><th>状态</th><th>环境/版本</th><th>负责人</th><th>创建时间</th><th>操作</th></tr></thead><tbody>
                <tr v-for="(item, index) in supportPager.items" :key="item.support_case_id"><td class="sequence-column">{{ supportPager.startIndex + index + 1 }}</td><td>{{ item.severity }}</td><td>{{ item.title }}</td><td><span class="status-pill" :data-status="item.status">{{ item.status }}</span></td><td>{{ item.environment }} / {{ item.product_version }}</td><td>{{ item.owner || '--' }}</td><td>{{ formatTimestamp(item.created_at) }}</td><td><div v-if="canSupportWrite" class="inline-actions"><ElButton v-if="item.status === 'open'" size="small" :loading="actionLoading" @click="updateSupportCase(item, 'acknowledged')">确认接单</ElButton><ElButton v-if="!['resolved', 'closed'].includes(item.status || '')" size="small" :loading="actionLoading" @click="updateSupportCase(item, 'resolved')">标记解决</ElButton></div></td></tr>
              </tbody></table></div>
              <DataTablePagination v-model:page="supportPager.page" v-model:page-size="supportPager.pageSize" :total="supportPager.total" />
            </template>
          </ElTabPane>
        </ElTabs>
      </section>
    </ElSkeleton>

    <ElDialog v-model="profileOpen" title="编辑商业配置" width="min(620px, 94vw)" :close-on-click-modal="false">
      <ElForm label-position="top" class="form-grid">
        <ElAlert v-if="profileForm.cancel_scheduled_transition" class="span-2" title="将取消当前待生效或被阻断的预约变更，现有商业状态保持不变。" type="warning" :closable="false" show-icon />
        <ElFormItem v-if="!profileForm.cancel_scheduled_transition" label="商业状态"><ElSelect v-model="profileForm.commercial_status"><ElOption label="试用" value="trial" /><ElOption label="生效" value="active" /><ElOption label="宽限期" value="grace" /><ElOption label="暂停" value="suspended" /><ElOption label="退租中" value="offboarding" /><ElOption label="已关闭" value="closed" /></ElSelect></ElFormItem>
        <ElFormItem v-if="!profileForm.cancel_scheduled_transition" label="生效时间"><ElDatePicker v-model="profileForm.effective_at" type="datetime" clearable /></ElFormItem>
        <ElFormItem v-if="!profileForm.cancel_scheduled_transition" label="交付层级"><ElSelect v-model="profileForm.delivery_tier"><ElOption label="平台 API" value="platform_api" /><ElOption label="私有部署" value="private_deployment" /><ElOption label="混合部署" value="hybrid" /></ElSelect></ElFormItem>
        <ElFormItem v-if="!profileForm.cancel_scheduled_transition" label="运行环境"><ElSelect v-model="profileForm.environment"><ElOption label="开发" value="development" /><ElOption label="预生产" value="staging" /><ElOption label="生产" value="production" /></ElSelect></ElFormItem>
        <ElFormItem v-if="!profileForm.cancel_scheduled_transition" label="时区"><ElInput v-model="profileForm.timezone" /></ElFormItem>
        <ElFormItem v-if="!profileForm.cancel_scheduled_transition" label="预算上限"><ElInputNumber v-model="profileForm.budget_limit" :min="0" controls-position="right" /></ElFormItem>
        <ElFormItem v-if="!profileForm.cancel_scheduled_transition" label="币种"><ElInput v-model="profileForm.budget_currency" maxlength="3" /></ElFormItem>
        <ElFormItem label="变更原因" class="span-2"><ElInput v-model="profileForm.reason" type="textarea" :rows="2" /></ElFormItem>
        <ElFormItem label="审批人（状态变更必填）" class="span-2"><ElInput v-model="profileForm.approved_by" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="profileOpen = false">取消</ElButton><ElButton type="primary" @click="requestProfileSave">继续</ElButton></template>
    </ElDialog>

    <ElDialog v-model="entitlementOpen" title="创建权益版本" width="min(680px, 94vw)" :close-on-click-modal="false">
      <ElAlert title="未来生效的版本会保持待生效状态；到达时间后再原子替换当前权益。临时扩容必须设置到期时间，并自动恢复回滚目标。" type="warning" :closable="false" show-icon />
      <ElForm label-position="top" class="form-grid dialog-form">
        <ElFormItem label="产品版本"><ElInput v-model="entitlementForm.product_version" /></ElFormItem>
        <ElFormItem label="交付层级"><ElSelect v-model="entitlementForm.delivery_tier"><ElOption label="平台 API" value="platform_api" /><ElOption label="私有部署" value="private_deployment" /><ElOption label="混合部署" value="hybrid" /></ElSelect></ElFormItem>
        <ElFormItem label="允许能力（逗号分隔）" class="span-2"><ElInput v-model="entitlementForm.capabilities" /></ElFormItem>
        <ElFormItem label="允许模型（逗号分隔）" class="span-2"><ElInput v-model="entitlementForm.models" placeholder="留空表示不按模型限制" /></ElFormItem>
        <ElFormItem label="并发上限"><ElInputNumber v-model="entitlementForm.concurrency_limit" :min="1" controls-position="right" /></ElFormItem>
        <ElFormItem label="视频流上限"><ElInputNumber v-model="entitlementForm.stream_limit" :min="0" controls-position="right" /></ElFormItem>
        <ElFormItem label="支持级别"><ElSelect v-model="entitlementForm.support_level"><ElOption label="标准" value="standard" /><ElOption label="高级" value="premium" /><ElOption label="关键业务" value="mission_critical" /></ElSelect></ElFormItem>
        <ElFormItem label="宽限期（秒）"><ElInputNumber v-model="entitlementForm.grace_period_seconds" :min="0" controls-position="right" /></ElFormItem>
        <ElFormItem label="变更类型"><ElSelect v-model="entitlementForm.change_type"><ElOption label="续期" value="renewal" /><ElOption label="升级" value="upgrade" /><ElOption label="降级" value="downgrade" /><ElOption label="临时扩容" value="temporary_expansion" /><ElOption label="紧急授权" value="emergency" /></ElSelect></ElFormItem>
        <ElFormItem label="生效时间"><ElDatePicker v-model="entitlementForm.starts_at" type="datetime" clearable /></ElFormItem>
        <ElFormItem label="到期时间"><ElDatePicker v-model="entitlementForm.expires_at" type="datetime" clearable /></ElFormItem>
        <ElFormItem label="回滚目标"><ElInput :model-value="profile.current_entitlement_id || '首次开通，无回滚目标'" disabled /></ElFormItem>
        <ElFormItem label="变更原因" class="span-2"><ElInput v-model="entitlementForm.reason" type="textarea" :rows="2" /></ElFormItem>
        <ElFormItem label="审批人" class="span-2"><ElInput v-model="entitlementForm.approved_by" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="entitlementOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createEntitlement">创建权益版本</ElButton></template>
    </ElDialog>

    <ElDialog v-model="entitlementActionFormOpen" title="权益状态变更" width="min(560px, 92vw)" :close-on-click-modal="false">
      <ElForm label-position="top">
        <ElFormItem label="操作原因"><ElInput v-model="entitlementActionReason" type="textarea" :rows="3" /></ElFormItem>
        <ElFormItem label="审批人"><ElInput v-model="entitlementActionApprover" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="entitlementActionFormOpen = false">取消</ElButton><ElButton type="primary" @click="proceedEntitlementAction">继续</ElButton></template>
    </ElDialog>

    <ElDialog v-model="supportOpen" title="提交支持工单" width="min(680px, 94vw)" :close-on-click-modal="false">
      <ElForm label-position="top" class="form-grid">
        <ElFormItem label="标题" class="span-2"><ElInput v-model="supportForm.title" /></ElFormItem>
        <ElFormItem label="严重级别"><ElSelect v-model="supportForm.severity"><ElOption label="SEV1" value="sev1" /><ElOption label="SEV2" value="sev2" /><ElOption label="SEV3" value="sev3" /><ElOption label="SEV4" value="sev4" /></ElSelect></ElFormItem>
        <ElFormItem label="环境"><ElInput v-model="supportForm.environment" /></ElFormItem>
        <ElFormItem label="产品版本"><ElInput v-model="supportForm.product_version" /></ElFormItem>
        <ElFormItem label="Request ID（逗号分隔）"><ElInput v-model="supportForm.request_ids" /></ElFormItem>
        <ElFormItem label="任务 ID（逗号分隔）" class="span-2"><ElInput v-model="supportForm.task_ids" /></ElFormItem>
        <ElFormItem label="问题描述" class="span-2"><ElInput v-model="supportForm.description" type="textarea" :rows="4" /></ElFormItem>
      </ElForm>
      <template #footer><ElButton @click="supportOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createSupportCase">提交工单</ElButton></template>
    </ElDialog>

    <DangerConfirm v-model="profileConfirmOpen" title="确认更新商业配置" :description="`将项目 ${sessionState.projectId} 的商业状态更新为“${profileStatusLabel(profileForm.commercial_status)}”，并以配置版本 v${profile.version ?? 1} 进行并发校验。`" :high-risk="profileForm.commercial_status !== profile.commercial_status" confirmation-text="更新商业配置" :loading="actionLoading" @confirm="saveProfile" />
    <DangerConfirm v-model="templateConfirmOpen" title="应用行业模板" :description="`将应用“${templatePreview?.template.name ?? ''}”，覆盖模板能力范围和默认配置；变更指纹 ${templatePreview?.fingerprint.slice(0, 12) ?? ''}。`" high-risk confirmation-text="应用行业模板" :loading="actionLoading" @confirm="applyTemplate" />
    <DangerConfirm v-model="rollbackConfirmOpen" title="回滚行业模板" :description="`将回滚模板应用 ${rollbackTarget?.template_application_id ?? ''}，恢复应用前的能力建议和项目模板配置。`" high-risk confirmation-text="回滚行业模板" :loading="actionLoading" @confirm="rollbackTemplate" />
    <DangerConfirm v-model="entitlementActionConfirmOpen" title="确认权益状态变更" :description="`将对权益 v${entitlementActionTarget?.version ?? ''} 执行${entitlementAction === 'rollback' ? '回滚' : entitlementAction === 'cancel' ? '取消预约' : '撤销'}，并按当前权益 ID 做并发校验。`" high-risk confirmation-text="变更权益状态" :loading="actionLoading" @confirm="applyEntitlementAction" />
  </div>
</template>

<style scoped>
.commercial-stats { margin-bottom: 16px; }
.commercial-surface { min-height: 420px; }
.scheduled-alert { margin: 8px 0 16px; }
.page-tabs { padding: 0 14px 16px; }
.split-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; padding: 8px 0; }
.split-grid h2 { margin: 0 0 12px; font-size: 15px; }
.usage-details-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; padding: 20px 0 8px; border-top: 1px solid var(--line); }
.usage-details-grid h2 { margin: 0 0 12px; font-size: 15px; }
.usage-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--line); }
.usage-metrics div { min-height: 64px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.usage-metrics div:nth-child(odd) { border-right: 1px solid var(--line); }
.usage-metrics dt { color: var(--muted); font-size: 12px; }
.usage-metrics dd { margin: 6px 0 0; font-weight: 600; }
.tab-note { padding: 44px 16px; color: var(--muted); text-align: center; border: 1px dashed var(--line); }
.capability-cell { min-width: 220px; }
.template-table { min-width: 980px; }
.template-name { min-width: 210px; }
.risk-control-cell { min-width: 320px; }
.table-subline { color: var(--muted); font-size: 12px; }
.profile-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 8px 0 0; border: 1px solid var(--line); }
.profile-grid div { min-height: 72px; padding: 13px 16px; border-bottom: 1px solid var(--line); }
.profile-grid div:nth-child(odd) { border-right: 1px solid var(--line); }
.profile-grid dt { color: var(--muted); font-size: 12px; }
.profile-grid dd { margin: 6px 0 0; overflow-wrap: anywhere; font-weight: 600; }
.section-heading { margin: 24px 0 12px; font-size: 15px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.form-grid .span-2 { grid-column: 1 / -1; }
.form-grid :deep(.el-select), .form-grid :deep(.el-input-number) { width: 100%; }
.dialog-form { margin-top: 16px; }
@media (max-width: 850px) { .split-grid, .usage-details-grid { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .profile-grid, .form-grid { grid-template-columns: 1fr; } .profile-grid div:nth-child(odd) { border-right: 0; } .form-grid .span-2 { grid-column: auto; } }
</style>
