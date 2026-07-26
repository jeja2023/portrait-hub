<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { CheckCircle2, ClipboardCheck, FileArchive, RefreshCw, ShieldAlert, UserRoundCheck } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDatePicker,
  ElDialog,
  ElForm,
  ElFormItem,
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
import DataTablePagination from "../../components/DataTablePagination.vue";
import EmptyState from "../../components/EmptyState.vue";
import StatCard from "../../components/StatCard.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { errorBannerMessage } from "../../utils/errors";
import { formatTimestamp } from "../../utils/format";
import { useRouteTab } from "../../utils/routeState";
import { useTablePagination } from "../../utils/tablePagination";

interface ComplianceRecord {
  compliance_record_id?: string;
  control_id?: string;
  status?: string;
  applicability?: string;
  legal_basis?: string;
  processing_purpose?: string;
  data_categories?: string[];
  data_subjects?: string[];
  storage_regions?: string[];
  evidence_refs?: string[];
  risk_summary?: string;
  mitigations?: string[];
  control_data?: Record<string, unknown>;
  approved_by?: string | null;
  approved_at?: number | null;
  expires_at?: number | null;
  version?: number;
}
interface ComplianceControl { control_id: string; approved?: boolean; expired?: boolean; record?: ComplianceRecord | null }
interface ComplianceStatus { controls: ComplianceControl[]; blocking_controls: string[]; ready: boolean }
interface RightsRequest { rights_request_id?: string; request_type?: string; status?: string; subject_reference?: string; identity_verification?: string; due_at?: number; created_at?: number; version?: number; execution_evidence?: Array<Record<string, unknown>> }
interface EvidencePackage { evidence_package_id?: string; package_type?: string; status?: string; artifact_ref?: string; sha256?: string; signature?: string; definition_version?: string; created_at?: number; expires_at?: number | null }

const CONTROL_TITLES: Record<string, string> = {
  "COM-001": "数据处理档案", "COM-002": "告知与同意台账", "COM-003": "未成年人保护", "COM-004": "替代方式",
  "COM-005": "个人信息保护影响评估", "COM-006": "数据驻留与传输", "COM-007": "保留与删除", "COM-008": "个人权利请求",
  "COM-009": "公共场所配置", "COM-010": "备案阈值监控", "COM-011": "人工复核与申诉", "COM-012": "隐私事件",
};
const CONTROL_REQUIRED_FIELDS: Record<string, string[]> = {
  "COM-001": ["responsible_contact", "necessity_assessment", "recipient_categories"],
  "COM-002": ["notice_version", "consent_scope", "obtained_at", "source", "proof_ref", "withdrawal_status"],
  "COM-003": ["minor_policy", "guardian_consent_status", "guardian_verification_status"],
  "COM-004": ["alternative_available", "alternative_process"],
  "COM-005": ["assessment_ref", "assessment_version", "review_due_at"],
  "COM-006": ["allowed_regions", "transfer_policy", "export_requires_approval"],
  "COM-007": ["backend_retention", "deletion_workflow", "backup_expiry_policy"],
  "COM-008": ["identity_verification_policy", "due_days", "fulfillment_backends"],
  "COM-009": ["collection_area", "signage_ref", "controller", "prohibited_areas"],
  "COM-010": ["filing_threshold", "current_count", "warning_ratio", "filing_status"],
  "COM-011": ["human_review_enabled", "appeal_process", "decision_use"],
  "COM-012": ["incident_process", "notification_decision_owner", "response_plan_ref"],
};
const CONTROL_FIELD_LABELS: Record<string, string> = {
  responsible_contact: "责任联系人", necessity_assessment: "必要性评估", recipient_categories: "接收方类别",
  notice_version: "告知版本", consent_scope: "同意范围", obtained_at: "取得时间", source: "同意来源", proof_ref: "证明引用", withdrawal_status: "撤回状态",
  minor_policy: "未成年人策略", guardian_consent_status: "监护人同意", guardian_verification_status: "监护人核验",
  alternative_available: "替代方式可用性", alternative_process: "替代流程", assessment_ref: "评估引用", assessment_version: "评估版本", review_due_at: "复评期限",
  allowed_regions: "允许区域", transfer_policy: "传输策略", export_requires_approval: "导出审批策略", backend_retention: "各后端保留策略", deletion_workflow: "删除工作流", backup_expiry_policy: "备份过期策略",
  identity_verification_policy: "身份核验策略", due_days: "处理期限（天）", fulfillment_backends: "执行后端", collection_area: "采集区域", signage_ref: "提示标识引用", controller: "责任主体", prohibited_areas: "禁用区域",
  filing_threshold: "备案阈值", current_count: "当前规模", warning_ratio: "预警比例", filing_status: "备案状态", human_review_enabled: "人工复核", appeal_process: "申诉流程", decision_use: "决策用途",
  incident_process: "事件流程", notification_decision_owner: "通知判断负责人", response_plan_ref: "响应预案引用",
};
const RIGHTS_TRANSITIONS: Record<string, string[]> = { received: ["identity_pending", "rejected"], identity_pending: ["verified", "rejected"], verified: ["in_progress", "rejected"], in_progress: ["completed", "rejected"] };
const DELETION_BACKENDS = ["postgresql", "vector_store", "object_storage", "cache", "exports", "backups"];

const capabilities = useCapabilitiesStore();
const tab = useRouteTab("controls");
const loading = ref(true);
const actionLoading = ref(false);
const errorMessage = ref("");
const evidenceMessage = ref("");
const compliance = ref<ComplianceStatus>({ controls: [], blocking_controls: [], ready: false });
const rightsRequests = ref<RightsRequest[]>([]);
const evidencePackages = ref<EvidencePackage[]>([]);
const controlFilter = ref("all");
const controlOpen = ref(false);
const controlConfirmOpen = ref(false);
const rightsOpen = ref(false);
const rightsActionOpen = ref(false);
const selectedControl = ref<ComplianceControl | null>(null);
const selectedRights = ref<RightsRequest | null>(null);

const controlForm = reactive({
  status: "draft", applicability: "applicable", legal_basis: "", processing_purpose: "", data_categories: "", data_subjects: "", storage_regions: "", retention_days: "30", evidence_refs: "", risk_summary: "", mitigations: "", approved_by: "", expires_at: null as Date | null, control_data: {} as Record<string, string>,
});
const rightsForm = reactive({ request_type: "access", subject_reference: "", due_at: null as Date | null });
const rightsActionForm = reactive({ status: "", identity_verification: "pending", exception_basis: "", timeline_message: "", evidence_sha256: "" });
const deletionEvidence = reactive<Record<string, string>>(Object.fromEntries(DELETION_BACKENDS.map((backend) => [backend, ""])));

const filteredControls = computed(() => compliance.value.controls.filter((item) => controlFilter.value === "all" || (controlFilter.value === "approved" ? item.approved : !item.approved)));
const rightsPager = useTablePagination(rightsRequests);
const evidencePager = useTablePagination(evidencePackages);
const approvedCount = computed(() => compliance.value.controls.filter((item) => item.approved).length);
const overdueRights = computed(() => rightsRequests.value.filter((item) => item.due_at && item.due_at * 1000 < Date.now() && !["completed", "rejected"].includes(String(item.status))).length);
const canWrite = computed(() => capabilities.hasPermission("compliance:write"));
const selectedControlFields = computed(() => CONTROL_REQUIRED_FIELDS[selectedControl.value?.control_id ?? ""] ?? []);
const rightsNextStatuses = computed(() => RIGHTS_TRANSITIONS[String(selectedRights.value?.status)] ?? []);
const rightsNeedsBackendEvidence = computed(() => ["deletion", "withdrawal", "restriction"].includes(String(selectedRights.value?.request_type)));

function splitList(value: string): string[] { return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean); }
function rightsTypeLabel(value: unknown): string { return ({ access: "访问", correction: "更正", deletion: "删除", withdrawal: "撤回", restriction: "限制处理", export: "导出" } as Record<string, string>)[String(value)] ?? String(value || "--"); }

async function load(): Promise<void> {
  loading.value = true; errorMessage.value = ""; evidenceMessage.value = "";
  const results = await Promise.allSettled([
    apiRequest<{ compliance: ComplianceStatus }>("/v1/admin/compliance/status"),
    apiRequest<{ rights_requests: RightsRequest[] }>("/v1/admin/compliance/rights-requests?limit=100"),
    apiRequest<{ evidence_packages: EvidencePackage[] }>("/v1/admin/evidence?limit=100"),
  ]);
  if (results[0].status === "fulfilled") compliance.value = results[0].value.compliance;
  else errorMessage.value = errorBannerMessage(results[0].reason, "合规控制加载失败");
  if (results[1].status === "fulfilled") rightsRequests.value = results[1].value.rights_requests;
  else errorMessage.value ||= errorBannerMessage(results[1].reason, "主体权利请求加载失败");
  if (results[2].status === "fulfilled") evidencePackages.value = results[2].value.evidence_packages;
  else evidenceMessage.value = errorBannerMessage(results[2].reason, "当前凭证无法读取证据包");
  loading.value = false;
}

function openControl(item: ComplianceControl): void {
  selectedControl.value = item;
  const record = item.record ?? {};
  Object.assign(controlForm, {
    status: record.status ?? "draft", applicability: record.applicability ?? "applicable", legal_basis: record.legal_basis ?? "", processing_purpose: record.processing_purpose ?? "", data_categories: record.data_categories?.join(",") ?? "", data_subjects: record.data_subjects?.join(",") ?? "", storage_regions: record.storage_regions?.join(",") ?? "", retention_days: String((record as { retention?: { days?: unknown } }).retention?.days ?? 30), evidence_refs: record.evidence_refs?.join("\n") ?? "", risk_summary: record.risk_summary ?? "", mitigations: record.mitigations?.join("\n") ?? "", approved_by: record.approved_by ?? "", expires_at: record.expires_at ? new Date(record.expires_at * 1000) : null, control_data: Object.fromEntries((CONTROL_REQUIRED_FIELDS[item.control_id] ?? []).map((key) => [key, String(record.control_data?.[key] ?? "")])),
  });
  controlOpen.value = true;
}

function requestControlSave(): void {
  if (controlForm.status === "approved" && (!controlForm.approved_by.trim() || !controlForm.evidence_refs.trim() || !controlForm.legal_basis.trim() || !controlForm.processing_purpose.trim())) { ElMessage.warning("批准控制项必须填写批准人、合法依据、处理目的和证据引用"); return; }
  if (controlForm.status === "approved" && selectedControlFields.value.some((key) => !controlForm.control_data[key]?.trim())) { ElMessage.warning("请填写当前控制项的全部结构化字段"); return; }
  controlOpen.value = false;
  controlConfirmOpen.value = true;
}

async function saveControl(): Promise<void> {
  if (!selectedControl.value) return;
  actionLoading.value = true;
  try {
    await apiRequest(`/v1/admin/compliance/records/${selectedControl.value.control_id}`, {
      method: "PUT",
      body: jsonBody({ status: controlForm.status, definition_version: "1.0", applicability: controlForm.applicability, legal_basis: controlForm.legal_basis, processing_purpose: controlForm.processing_purpose, data_categories: splitList(controlForm.data_categories), data_subjects: splitList(controlForm.data_subjects), storage_regions: splitList(controlForm.storage_regions), retention: { days: Number(controlForm.retention_days) || 0 }, evidence_refs: splitList(controlForm.evidence_refs), risk_summary: controlForm.risk_summary, mitigations: splitList(controlForm.mitigations), control_data: controlForm.control_data, approved_by: controlForm.approved_by || null, expires_at: controlForm.expires_at ? controlForm.expires_at.getTime() / 1000 : null }),
    });
    controlConfirmOpen.value = false;
    ElMessage.success(`${selectedControl.value.control_id} 已更新`);
    await load();
  } catch (error) { errorMessage.value = errorBannerMessage(error, "合规控制更新失败"); }
  finally { actionLoading.value = false; }
}

async function createRightsRequest(): Promise<void> {
  if (!rightsForm.subject_reference.trim()) { ElMessage.warning("请填写数据主体引用"); return; }
  actionLoading.value = true;
  try {
    await apiRequest("/v1/admin/compliance/rights-requests", { method: "POST", body: jsonBody({ request_type: rightsForm.request_type, subject_reference: rightsForm.subject_reference, due_at: rightsForm.due_at ? rightsForm.due_at.getTime() / 1000 : null }) });
    rightsOpen.value = false;
    Object.assign(rightsForm, { request_type: "access", subject_reference: "", due_at: null });
    ElMessage.success("主体权利请求已登记，主体引用已哈希化保存");
    await load(); tab.value = "rights";
  } catch (error) { errorMessage.value = errorBannerMessage(error, "主体权利请求登记失败"); }
  finally { actionLoading.value = false; }
}

function openRightsAction(item: RightsRequest): void {
  selectedRights.value = item;
  Object.assign(rightsActionForm, { status: RIGHTS_TRANSITIONS[String(item.status)]?.[0] ?? "", identity_verification: item.identity_verification ?? "pending", exception_basis: "", timeline_message: "", evidence_sha256: "" });
  for (const backend of DELETION_BACKENDS) deletionEvidence[backend] = "";
  rightsActionOpen.value = true;
}

async function updateRightsRequest(): Promise<void> {
  if (!selectedRights.value?.rights_request_id || !rightsActionForm.status) return;
  let executionEvidence: Array<Record<string, unknown>> | undefined;
  if (rightsActionForm.status === "completed") {
    if (rightsNeedsBackendEvidence.value) {
      if (DELETION_BACKENDS.some((backend) => (deletionEvidence[backend] ?? "").length !== 64)) { ElMessage.warning("完成删除类请求前必须填写六类后端的 SHA-256 证据摘要"); return; }
      executionEvidence = DELETION_BACKENDS.map((backend) => ({ backend, status: "deleted", evidence_sha256: deletionEvidence[backend] }));
    } else {
      if (rightsActionForm.evidence_sha256.length !== 64) { ElMessage.warning("完成请求前必须填写 SHA-256 证据摘要"); return; }
      executionEvidence = [{ backend: "response", status: "completed", evidence_sha256: rightsActionForm.evidence_sha256 }];
    }
  }
  actionLoading.value = true;
  try {
    await apiRequest(`/v1/admin/compliance/rights-requests/${selectedRights.value.rights_request_id}`, { method: "PATCH", body: jsonBody({ status: rightsActionForm.status, identity_verification: rightsActionForm.identity_verification, exception_basis: rightsActionForm.exception_basis || null, timeline_message: rightsActionForm.timeline_message || null, execution_evidence: executionEvidence, expected_version: selectedRights.value.version }) });
    rightsActionOpen.value = false;
    ElMessage.success("主体权利请求已更新");
    await load();
  } catch (error) { errorMessage.value = errorBannerMessage(error, "主体权利请求更新失败"); }
  finally { actionLoading.value = false; }
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header"><div><h1>合规证据</h1><p>以 COM-001 至 COM-012 为发布门禁，集中维护批准记录、主体权利请求和证据包。</p></div><div class="page-actions"><ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton><ElButton v-if="canWrite" type="primary" @click="rightsOpen = true">登记权利请求</ElButton></div></header>
    <ElAlert v-if="errorMessage" class="error-banner" role="alert" :title="errorMessage" type="error" show-icon :closable="false" />
    <ElSkeleton :loading="loading" :rows="5" animated>
      <div class="stat-grid compliance-stats">
        <StatCard label="合规发布门禁" :value="compliance.ready ? '通过' : '阻断'" :tone="compliance.ready ? 'success' : 'danger'" :icon="ClipboardCheck" :detail="compliance.ready ? '全部控制项有效' : `${compliance.blocking_controls.length} 项待处理`" />
        <StatCard label="已批准控制" :value="`${approvedCount} / ${compliance.controls.length || 12}`" :tone="approvedCount === 12 ? 'success' : 'warning'" :icon="CheckCircle2" detail="到期记录自动失效" />
        <StatCard label="权利请求" :value="String(rightsRequests.length)" :tone="overdueRights ? 'danger' : 'neutral'" :icon="UserRoundCheck" :detail="overdueRights ? `${overdueRights} 项已逾期` : '当前无逾期'" />
        <StatCard label="证据包" :value="String(evidencePackages.length)" :tone="evidenceMessage ? 'warning' : 'neutral'" :icon="FileArchive" :detail="evidenceMessage || '可校验摘要与签名'" />
      </div>
      <section class="tool-surface"><ElTabs v-model="tab" class="page-tabs">
        <ElTabPane label="控制项" name="controls">
          <div class="filter-bar"><ElSelect v-model="controlFilter"><ElOption label="全部控制项" value="all" /><ElOption label="已批准" value="approved" /><ElOption label="待处理" value="blocking" /></ElSelect><span>{{ compliance.blocking_controls.length ? `阻断项：${compliance.blocking_controls.join('、')}` : '发布门禁已通过' }}</span></div>
          <div class="control-list"><article v-for="item in filteredControls" :key="item.control_id" class="control-row"><div class="control-id" :data-approved="item.approved"><CheckCircle2 v-if="item.approved" :size="18" /><ShieldAlert v-else :size="18" /><strong>{{ item.control_id }}</strong></div><div><strong>{{ CONTROL_TITLES[item.control_id] }}</strong><span>{{ item.record ? `记录 v${item.record.version} · ${item.record.applicability}` : '尚未创建记录' }}</span></div><div><span class="status-pill" :data-status="item.approved ? 'completed' : 'failed'">{{ item.expired ? '已过期' : item.approved ? '已批准' : '待处理' }}</span><ElButton v-if="canWrite" size="small" @click="openControl(item)">{{ item.record ? '更新' : '配置' }}</ElButton></div></article></div>
        </ElTabPane>
        <ElTabPane :label="`主体权利 (${rightsRequests.length})`" name="rights">
          <EmptyState v-if="!rightsRequests.length" title="尚未登记主体权利请求" description="登记后主体原始引用不会保存，系统仅保留哈希用于追踪。" :action-label="canWrite ? '登记请求' : ''" @action="rightsOpen = true" />
          <template v-else><div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>类型</th><th>状态</th><th>身份核验</th><th>主体引用摘要</th><th>截止时间</th><th>登记时间</th><th v-if="canWrite">操作</th></tr></thead><tbody><tr v-for="(item, index) in rightsPager.items" :key="item.rights_request_id"><td class="sequence-column">{{ rightsPager.startIndex + index + 1 }}</td><td>{{ rightsTypeLabel(item.request_type) }}</td><td><span class="status-pill" :data-status="item.status">{{ item.status }}</span></td><td>{{ item.identity_verification }}</td><td><code>{{ item.subject_reference?.slice(0, 16) }}…</code></td><td :data-overdue="item.due_at && item.due_at * 1000 < Date.now()">{{ formatTimestamp(item.due_at) }}</td><td>{{ formatTimestamp(item.created_at) }}</td><td v-if="canWrite"><ElButton v-if="RIGHTS_TRANSITIONS[String(item.status)]?.length" size="small" @click="openRightsAction(item)">推进</ElButton></td></tr></tbody></table></div><DataTablePagination v-model:page="rightsPager.page" v-model:page-size="rightsPager.pageSize" :total="rightsPager.total" /></template>
        </ElTabPane>
        <ElTabPane :label="`证据包 (${evidencePackages.length})`" name="evidence">
          <ElAlert v-if="evidenceMessage" :title="evidenceMessage" type="warning" :closable="false" show-icon />
          <EmptyState v-else-if="!evidencePackages.length" title="尚未生成证据包" description="备份、删除、模型发布和交付验证流程会登记带摘要的证据包。" />
          <template v-else><div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>证据类型</th><th>状态</th><th>定义版本</th><th>制品</th><th>SHA-256</th><th>创建时间</th></tr></thead><tbody><tr v-for="(item, index) in evidencePager.items" :key="item.evidence_package_id"><td class="sequence-column">{{ evidencePager.startIndex + index + 1 }}</td><td>{{ item.package_type || '--' }}</td><td><span class="status-pill" :data-status="item.status">{{ item.status }}</span></td><td>{{ item.definition_version || '--' }}</td><td><code>{{ item.artifact_ref || '--' }}</code></td><td><code>{{ item.sha256?.slice(0, 16) || '--' }}…</code></td><td>{{ formatTimestamp(item.created_at) }}</td></tr></tbody></table></div><DataTablePagination v-model:page="evidencePager.page" v-model:page-size="evidencePager.pageSize" :total="evidencePager.total" /></template>
        </ElTabPane>
      </ElTabs></section>
    </ElSkeleton>

    <ElDialog v-model="controlOpen" :title="`${selectedControl?.control_id ?? ''} ${CONTROL_TITLES[selectedControl?.control_id ?? ''] ?? ''}`" width="min(760px, 95vw)" :close-on-click-modal="false"><ElForm label-position="top" class="form-grid"><ElFormItem label="记录状态"><ElSelect v-model="controlForm.status"><ElOption label="草稿" value="draft" /><ElOption label="已批准" value="approved" /><ElOption label="已拒绝" value="rejected" /></ElSelect></ElFormItem><ElFormItem label="适用性"><ElSelect v-model="controlForm.applicability"><ElOption label="适用" value="applicable" /><ElOption label="不适用（需证据）" value="not_applicable" /><ElOption label="待确认" value="pending" /></ElSelect></ElFormItem><ElFormItem label="合法依据" class="span-2"><ElInput v-model="controlForm.legal_basis" type="textarea" :rows="2" /></ElFormItem><ElFormItem label="处理目的" class="span-2"><ElInput v-model="controlForm.processing_purpose" type="textarea" :rows="2" /></ElFormItem><ElFormItem v-for="field in selectedControlFields" :key="field" :label="CONTROL_FIELD_LABELS[field] || field"><ElInput v-model="controlForm.control_data[field]" /></ElFormItem><ElFormItem label="数据类别（逗号分隔）"><ElInput v-model="controlForm.data_categories" /></ElFormItem><ElFormItem label="数据主体（逗号分隔）"><ElInput v-model="controlForm.data_subjects" /></ElFormItem><ElFormItem label="存储区域（逗号分隔）"><ElInput v-model="controlForm.storage_regions" /></ElFormItem><ElFormItem label="保留天数"><ElInput v-model="controlForm.retention_days" inputmode="numeric" /></ElFormItem><ElFormItem label="风险摘要" class="span-2"><ElInput v-model="controlForm.risk_summary" type="textarea" :rows="2" /></ElFormItem><ElFormItem label="缓解措施（每行一项）" class="span-2"><ElInput v-model="controlForm.mitigations" type="textarea" :rows="3" /></ElFormItem><ElFormItem label="证据引用（每行一项）" class="span-2"><ElInput v-model="controlForm.evidence_refs" type="textarea" :rows="3" /></ElFormItem><ElFormItem label="批准人"><ElInput v-model="controlForm.approved_by" /></ElFormItem><ElFormItem label="到期时间"><ElDatePicker v-model="controlForm.expires_at" type="datetime" placeholder="长期有效可留空" /></ElFormItem></ElForm><template #footer><ElButton @click="controlOpen = false">取消</ElButton><ElButton type="primary" @click="requestControlSave">继续</ElButton></template></ElDialog>
    <ElDialog v-model="rightsOpen" title="登记主体权利请求" width="min(560px, 94vw)" :close-on-click-modal="false"><ElAlert title="主体原始引用仅用于当前请求计算摘要，不会原文保存。" type="info" :closable="false" show-icon /><ElForm label-position="top" class="rights-form"><ElFormItem label="请求类型"><ElSelect v-model="rightsForm.request_type"><ElOption label="访问" value="access" /><ElOption label="更正" value="correction" /><ElOption label="删除" value="deletion" /><ElOption label="撤回" value="withdrawal" /><ElOption label="限制处理" value="restriction" /><ElOption label="导出" value="export" /></ElSelect></ElFormItem><ElFormItem label="数据主体引用"><ElInput v-model="rightsForm.subject_reference" autocomplete="off" /></ElFormItem><ElFormItem label="截止时间"><ElDatePicker v-model="rightsForm.due_at" type="datetime" placeholder="留空默认 30 天" /></ElFormItem></ElForm><template #footer><ElButton @click="rightsOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createRightsRequest">登记请求</ElButton></template></ElDialog>
    <ElDialog v-model="rightsActionOpen" title="推进主体权利请求" width="min(680px, 95vw)" :close-on-click-modal="false"><ElForm label-position="top" class="form-grid"><ElFormItem label="目标状态"><ElSelect v-model="rightsActionForm.status"><ElOption v-for="status in rightsNextStatuses" :key="status" :label="status" :value="status" /></ElSelect></ElFormItem><ElFormItem label="身份核验"><ElSelect v-model="rightsActionForm.identity_verification"><ElOption label="待核验" value="pending" /><ElOption label="已核验" value="verified" /><ElOption label="核验失败" value="failed" /></ElSelect></ElFormItem><ElFormItem label="例外依据" class="span-2"><ElInput v-model="rightsActionForm.exception_basis" type="textarea" :rows="2" /></ElFormItem><ElFormItem label="时间线记录" class="span-2"><ElInput v-model="rightsActionForm.timeline_message" /></ElFormItem><template v-if="rightsActionForm.status === 'completed' && rightsNeedsBackendEvidence"><ElFormItem v-for="backend in DELETION_BACKENDS" :key="backend" :label="`${backend} 证据 SHA-256`"><ElInput v-model="deletionEvidence[backend]" maxlength="64" /></ElFormItem></template><ElFormItem v-else-if="rightsActionForm.status === 'completed'" label="完成证据 SHA-256" class="span-2"><ElInput v-model="rightsActionForm.evidence_sha256" maxlength="64" /></ElFormItem></ElForm><template #footer><ElButton @click="rightsActionOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="updateRightsRequest">更新请求</ElButton></template></ElDialog>
    <DangerConfirm v-model="controlConfirmOpen" title="确认合规记录变更" :description="`将 ${selectedControl?.control_id ?? ''} 保存为“${controlForm.status === 'approved' ? '已批准' : controlForm.status}”。批准记录会直接影响商业发布门禁。`" :high-risk="controlForm.status === 'approved'" confirmation-text="批准合规控制" :loading="actionLoading" @confirm="saveControl" />
  </div>
</template>

<style scoped>
.compliance-stats { margin-bottom: 16px; }
.page-tabs { padding: 0 14px 16px; }
.filter-bar { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 8px 0 14px; color: var(--muted); font-size: 12px; }
.filter-bar :deep(.el-select) { width: 190px; }
.control-list { display: grid; }
.control-row { display: grid; grid-template-columns: 118px minmax(0, 1fr) auto; align-items: center; gap: 14px; min-height: 72px; padding: 10px 4px; border-bottom: 1px solid var(--line); }
.control-id { display: flex; align-items: center; gap: 8px; color: var(--danger); }
.control-id[data-approved="true"] { color: var(--success); }
.control-row > div:nth-child(2) { display: grid; gap: 4px; }
.control-row > div:nth-child(2) span { color: var(--muted); font-size: 12px; }
.control-row > div:last-child { display: flex; align-items: center; gap: 8px; }
[data-overdue="true"] { color: var(--danger); font-weight: 700; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.form-grid .span-2 { grid-column: 1 / -1; }
.form-grid :deep(.el-select), .form-grid :deep(.el-date-editor), .rights-form :deep(.el-select), .rights-form :deep(.el-date-editor) { width: 100%; }
.rights-form { margin-top: 16px; }
@media (max-width: 700px) { .filter-bar { align-items: stretch; flex-direction: column; } .filter-bar :deep(.el-select) { width: 100%; } .control-row { grid-template-columns: 1fr; gap: 6px; } .form-grid { grid-template-columns: 1fr; } .form-grid .span-2 { grid-column: auto; } }
</style>
