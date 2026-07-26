<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { BadgeCheck, Boxes, FlaskConical, GitCompareArrows, Plus, RefreshCw, Rocket } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElCheckbox,
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

interface ModelVersion {
  model_version_id: string;
  model_id?: string;
  version?: string;
  status?: string;
  model_target?: string;
  sha256?: string;
  license?: string;
  source?: string;
  supports_cpu?: boolean;
  supports_batching?: boolean;
  max_batch_size?: number;
  evaluations?: { evaluation_id?: string; passed?: boolean; metrics?: Record<string, number>; created_at?: number }[];
  approvals?: { approval_id?: string; approver?: string; decision?: string; policy?: string; created_at?: number }[];
  created_at?: number;
}
interface RegisteredModel {
  model_id: string;
  name?: string;
  capability?: string;
  owner?: string;
  description?: string;
  version_count?: number;
  active_version?: ModelVersion | null;
  latest_version?: ModelVersion | null;
}
interface ReleaseEvent { release_event_id?: string; model_version_id?: string; action?: string; alias?: string; previous_target?: string | null; target?: string; risk_level?: string; reason?: string; outcome?: string; request_id?: string; created_at?: number; created_by?: string }
interface ShadowResult { source_model_id?: string; shadow_model_id?: string; sampled?: boolean; latency_delta_ms?: number; agreement?: boolean; created_at?: number }
interface ReleasePreflight { ok: boolean; blockers?: string[]; warnings?: string[]; required_approvals?: number; approvers?: string[]; current_target?: string | null; target?: string; artifact?: { configured?: boolean; sha256_matches?: boolean; actual_size?: number | null; expected_size?: number | null; error?: string | null } }

const capabilities = useCapabilitiesStore();
const tab = useRouteTab("registry");
const loading = ref(true);
const actionLoading = ref(false);
const errorMessage = ref("");
const models = ref<RegisteredModel[]>([]);
const versionsByModel = ref<Record<string, ModelVersion[]>>({});
const releaseEvents = ref<ReleaseEvent[]>([]);
const shadowResults = ref<ShadowResult[]>([]);
const capabilityFilter = ref("");
const statusFilter = ref("");
const registerOpen = ref(false);
const evidenceOpen = ref(false);
const approvalOpen = ref(false);
const releaseOpen = ref(false);
const releaseConfirmOpen = ref(false);
const selectedVersion = ref<ModelVersion | null>(null);
const preflight = ref<ReleasePreflight | null>(null);

const registerForm = reactive({ name: "", capability: "person_detection", version: "1.0.0", description: "", owner: "", framework: "onnx", runtime: "onnxruntime", model_target: "portrait_hub/", sha256: "", artifact_size: 0, artifact_uri: "", license: "", source: "", redistribution_allowed: false, model_card_ref: "", governance_ref: "", supports_cpu: false, supports_batching: true, max_batch_size: 1, quality_gates: "{}" });
const evidenceForm = reactive({ dataset_id: "", dataset_manifest_sha256: "", definition_version: "1.0", metrics: "{}", quality_gates: "{}", report_ref: "" });
const approvalForm = reactive({ decision: "approve", policy: "model_release", comment: "" });
const releaseForm = reactive({ model_version_id: "", alias: "", action: "canary", risk_level: "high", traffic_percentage: 5, expected_current_target: "", reason: "" });

const allVersions = computed(() => Object.values(versionsByModel.value).flat());
const filteredModels = computed(() => models.value.filter((item) => (!capabilityFilter.value || item.capability === capabilityFilter.value) && (!statusFilter.value || allVersions.value.some((version) => version.model_id === item.model_id && version.status === statusFilter.value))));
const modelsPager = useTablePagination(filteredModels);
const releasePager = useTablePagination(releaseEvents);
const candidateCount = computed(() => allVersions.value.filter((item) => ["candidate", "shadow", "canary"].includes(String(item.status))).length);
const activeCount = computed(() => allVersions.value.filter((item) => item.status === "active").length);
const passingEvaluations = computed(() => allVersions.value.flatMap((item) => item.evaluations ?? []).filter((item) => item.passed).length);
const capabilityOptions = computed(() => [...new Set(models.value.map((item) => String(item.capability)).filter(Boolean))]);
const canWrite = computed(() => capabilities.hasPermission("models:write"));
const canApprove = computed(() => capabilities.hasPermission("models:approve"));
const metricsPlaceholder = '{"map50": 0.91}';
const qualityGatePlaceholder = '{"map50": {"min": 0.90}}';

function parseObject(value: string, label: string): Record<string, unknown> {
  try { const parsed = JSON.parse(value); if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(); return parsed as Record<string, unknown>; }
  catch { throw new Error(`${label} 必须是 JSON 对象`); }
}
function modelName(version: ModelVersion | null): string { return models.value.find((item) => item.model_id === version?.model_id)?.name ?? version?.model_id ?? "--"; }
function actionLabel(value: unknown): string { return ({ shadow: "影子", canary: "灰度", activate: "激活", rollback: "回滚", pause: "暂停", deprecate: "弃用" } as Record<string, string>)[String(value)] ?? String(value || "--"); }
function statusLabel(value: unknown): string { return ({ draft: "草稿", candidate: "候选", shadow: "影子", canary: "灰度", active: "已激活", blocked: "已阻断", deprecated: "已弃用" } as Record<string, string>)[String(value)] ?? String(value || "--"); }

async function load(): Promise<void> {
  loading.value = true; errorMessage.value = "";
  try {
    const [registry, audit, shadows] = await Promise.all([
      apiRequest<{ models: RegisteredModel[] }>("/v1/admin/models/registry"),
      apiRequest<{ release_events: ReleaseEvent[] }>("/v1/admin/models/releases/audit?limit=100"),
      apiRequest<{ shadow_results: ShadowResult[] }>("/v1/admin/models/releases/shadow-results?limit=100"),
    ]);
    models.value = registry.models; releaseEvents.value = audit.release_events; shadowResults.value = shadows.shadow_results;
    const versionResults = await Promise.all(registry.models.map(async (model) => [model.model_id, (await apiRequest<{ versions: ModelVersion[] }>(`/v1/admin/models/registry/${encodeURIComponent(model.model_id)}/versions`)).versions] as const));
    versionsByModel.value = Object.fromEntries(versionResults);
  } catch (error) { errorMessage.value = errorBannerMessage(error, "模型注册表加载失败"); }
  finally { loading.value = false; }
}

async function registerModel(): Promise<void> {
  if (registerForm.sha256.length !== 64) { ElMessage.warning("SHA-256 必须是 64 位十六进制摘要"); return; }
  actionLoading.value = true;
  try {
    await apiRequest("/v1/admin/models/registry", { method: "POST", body: jsonBody({ ...registerForm, quality_gates: parseObject(registerForm.quality_gates, "质量门禁"), input_contract: {}, output_contract: {}, thresholds: {}, dataset_lineage: [] }) });
    registerOpen.value = false; ElMessage.success("模型版本已登记为草稿"); await load();
  } catch (error) { errorMessage.value = error instanceof Error && error.message.includes("JSON") ? error.message : errorBannerMessage(error, "模型登记失败"); }
  finally { actionLoading.value = false; }
}

function openEvidence(version: ModelVersion): void { selectedVersion.value = version; Object.assign(evidenceForm, { dataset_id: "", dataset_manifest_sha256: "", definition_version: "1.0", metrics: "{}", quality_gates: "{}", report_ref: "" }); evidenceOpen.value = true; }
async function createEvaluation(): Promise<void> {
  if (!selectedVersion.value || evidenceForm.dataset_manifest_sha256.length !== 64) { ElMessage.warning("请填写数据集和 64 位清单摘要"); return; }
  actionLoading.value = true;
  try {
    await apiRequest(`/v1/admin/models/registry/versions/${selectedVersion.value.model_version_id}/evaluations`, { method: "POST", body: jsonBody({ dataset_id: evidenceForm.dataset_id, dataset_manifest_sha256: evidenceForm.dataset_manifest_sha256, definition_version: evidenceForm.definition_version, environment: {}, thresholds: {}, metrics: parseObject(evidenceForm.metrics, "评估指标"), quality_gates: parseObject(evidenceForm.quality_gates, "质量门禁"), report_ref: evidenceForm.report_ref }) });
    evidenceOpen.value = false; ElMessage.success("评估证据已登记"); await load();
  } catch (error) { errorMessage.value = error instanceof Error && error.message.includes("JSON") ? error.message : errorBannerMessage(error, "评估证据登记失败"); }
  finally { actionLoading.value = false; }
}

function openApproval(version: ModelVersion): void { selectedVersion.value = version; Object.assign(approvalForm, { decision: "approve", policy: "model_release", comment: "" }); approvalOpen.value = true; }
async function createApproval(): Promise<void> {
  if (!selectedVersion.value || !approvalForm.comment.trim()) { ElMessage.warning("请填写审批意见"); return; }
  actionLoading.value = true;
  try { await apiRequest(`/v1/admin/models/registry/versions/${selectedVersion.value.model_version_id}/approvals`, { method: "POST", body: jsonBody(approvalForm) }); approvalOpen.value = false; ElMessage.success("审批决定已记录"); await load(); }
  catch (error) { errorMessage.value = errorBannerMessage(error, "审批登记失败"); }
  finally { actionLoading.value = false; }
}

function openRelease(version: ModelVersion): void { selectedVersion.value = version; Object.assign(releaseForm, { model_version_id: version.model_version_id, alias: "", action: version.status === "active" ? "pause" : "canary", risk_level: "high", traffic_percentage: 5, expected_current_target: "", reason: "" }); preflight.value = null; releaseOpen.value = true; }
async function dryRunRelease(): Promise<void> {
  if (!releaseForm.alias.trim() || !releaseForm.reason.trim()) { ElMessage.warning("请填写别名和发布原因"); return; }
  actionLoading.value = true;
  try { const payload = await apiRequest<{ release_preflight: ReleasePreflight }>("/v1/admin/models/releases/dry-run", { method: "POST", body: jsonBody({ ...releaseForm, expected_current_target: releaseForm.expected_current_target || null }) }); preflight.value = payload.release_preflight; ElMessage[payload.release_preflight.ok ? "success" : "warning"](payload.release_preflight.ok ? "发布预演通过" : "发布预演存在阻断项"); }
  catch (error) { errorMessage.value = errorBannerMessage(error, "发布预演失败"); }
  finally { actionLoading.value = false; }
}
function requestRelease(): void { if (!preflight.value?.ok) { ElMessage.warning("发布预演未通过，不能执行"); return; } releaseOpen.value = false; releaseConfirmOpen.value = true; }
async function applyRelease(): Promise<void> {
  actionLoading.value = true;
  try { const endpoint = releaseForm.action === "rollback" ? "/v1/admin/models/releases/rollback" : "/v1/admin/models/releases/apply"; await apiRequest(endpoint, { method: "POST", body: jsonBody({ ...releaseForm, expected_current_target: releaseForm.expected_current_target || null }) }); releaseConfirmOpen.value = false; ElMessage.success(`${actionLabel(releaseForm.action)}已执行`); await load(); tab.value = "audit"; }
  catch (error) { errorMessage.value = errorBannerMessage(error, "模型发布执行失败"); }
  finally { actionLoading.value = false; }
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header"><div><h1>模型注册与发布</h1><p>从来源、许可和制品摘要开始，串联评估、独立审批、预演、灰度和回滚证据。</p></div><div class="page-actions"><ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton><ElButton v-if="canWrite" type="primary" :icon="Plus" @click="registerOpen = true">登记模型版本</ElButton></div></header>
    <ElAlert v-if="errorMessage" class="error-banner" role="alert" :title="errorMessage" type="error" show-icon :closable="false" />
    <ElSkeleton :loading="loading" :rows="6" animated>
      <div class="stat-grid registry-stats"><StatCard label="注册模型" :value="String(models.length)" :icon="Boxes" :detail="`${allVersions.length} 个版本`" /><StatCard label="候选与灰度" :value="String(candidateCount)" :tone="candidateCount ? 'warning' : 'neutral'" :icon="GitCompareArrows" detail="候选、影子与灰度状态" /><StatCard label="活跃版本" :value="String(activeCount)" :tone="activeCount ? 'success' : 'warning'" :icon="Rocket" detail="已路由生产别名" /><StatCard label="通过评估" :value="String(passingEvaluations)" :tone="passingEvaluations ? 'success' : 'warning'" :icon="FlaskConical" detail="带数据集清单摘要" /></div>
      <section class="tool-surface"><ElTabs v-model="tab" class="page-tabs">
        <ElTabPane label="模型注册表" name="registry">
          <div class="filter-bar"><ElSelect v-model="capabilityFilter" aria-label="按模型能力筛选" clearable placeholder="全部能力"><ElOption v-for="item in capabilityOptions" :key="item" :label="item" :value="item" /></ElSelect><ElSelect v-model="statusFilter" aria-label="按模型状态筛选" clearable placeholder="全部状态"><ElOption v-for="item in ['draft','candidate','shadow','canary','active','blocked','deprecated']" :key="item" :label="statusLabel(item)" :value="item" /></ElSelect></div>
          <EmptyState v-if="!filteredModels.length" title="没有匹配的模型" description="模型必须先登记来源、许可、摘要和治理文件，才能进入评估流程。" :action-label="canWrite ? '登记模型版本' : ''" @action="registerOpen = true" />
          <template v-else><div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>模型</th><th>能力</th><th>版本</th><th>最新状态</th><th>制品目标</th><th>评估/审批</th><th>动作</th></tr></thead><tbody><template v-for="(model, modelIndex) in modelsPager.items" :key="model.model_id"><tr v-for="(version, versionIndex) in versionsByModel[model.model_id] ?? []" :key="version.model_version_id"><td class="sequence-column">{{ modelsPager.startIndex + modelIndex + 1 }}.{{ versionIndex + 1 }}</td><td><strong>{{ model.name }}</strong><br /><span>{{ model.owner || '--' }}</span></td><td>{{ model.capability }}</td><td>{{ version.version }}</td><td><span class="status-pill" :data-status="version.status">{{ statusLabel(version.status) }}</span></td><td><code>{{ version.model_target }}</code><br /><span>SHA {{ version.sha256?.slice(0, 12) }}…</span></td><td>{{ version.evaluations?.filter(item => item.passed).length ?? 0 }} / {{ version.approvals?.filter(item => item.decision === 'approve').length ?? 0 }}</td><td><div class="inline-actions"><ElButton v-if="canWrite" size="small" @click="openEvidence(version)">评估</ElButton><ElButton v-if="canApprove" size="small" :icon="BadgeCheck" @click="openApproval(version)">审批</ElButton><ElButton v-if="canWrite" size="small" type="primary" @click="openRelease(version)">发布</ElButton></div></td></tr></template></tbody></table></div><DataTablePagination v-model:page="modelsPager.page" v-model:page-size="modelsPager.pageSize" :total="modelsPager.total" /></template>
        </ElTabPane>
        <ElTabPane :label="`发布审计 (${releaseEvents.length})`" name="audit"><EmptyState v-if="!releaseEvents.length" title="尚无发布事件" description="成功执行影子、灰度、激活、暂停、弃用或回滚后会留下事件。" /><template v-else><div class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>动作</th><th>模型版本</th><th>别名</th><th>目标变化</th><th>风险</th><th>执行人</th><th>请求/时间</th></tr></thead><tbody><tr v-for="(item, index) in releasePager.items" :key="item.release_event_id"><td class="sequence-column">{{ releasePager.startIndex + index + 1 }}</td><td><span class="status-pill" :data-status="item.outcome === 'success' ? 'completed' : 'failed'">{{ actionLabel(item.action) }}</span></td><td><code>{{ item.model_version_id }}</code></td><td>{{ item.alias }}</td><td><code>{{ item.previous_target || '空' }}</code><br />→ <code>{{ item.target }}</code></td><td>{{ item.risk_level }}</td><td>{{ item.created_by }}</td><td><code>{{ item.request_id?.slice(0, 12) }}</code><br />{{ formatTimestamp(item.created_at) }}</td></tr></tbody></table></div><DataTablePagination v-model:page="releasePager.page" v-model:page-size="releasePager.pageSize" :total="releasePager.total" /></template></ElTabPane>
        <ElTabPane :label="`影子结果 (${shadowResults.length})`" name="shadow"><EmptyState v-if="!shadowResults.length" title="尚无影子对比结果" description="影子发布采样后会记录主模型与候选模型的一致性和延迟差异。" /><div v-else class="table-wrap"><table class="data-table"><thead><tr><th class="sequence-column">序号</th><th>主模型</th><th>影子模型</th><th>采样</th><th>一致</th><th>延迟差异</th><th>时间</th></tr></thead><tbody><tr v-for="(item,index) in shadowResults" :key="index"><td class="sequence-column">{{ index + 1 }}</td><td><code>{{ item.source_model_id }}</code></td><td><code>{{ item.shadow_model_id }}</code></td><td>{{ item.sampled ? '是' : '否' }}</td><td>{{ item.agreement == null ? '--' : item.agreement ? '一致' : '不一致' }}</td><td>{{ item.latency_delta_ms ?? '--' }} ms</td><td>{{ formatTimestamp(item.created_at) }}</td></tr></tbody></table></div></ElTabPane>
      </ElTabs></section>
    </ElSkeleton>

    <ElDialog v-model="registerOpen" title="登记模型版本" width="min(820px, 96vw)" :close-on-click-modal="false"><ElAlert title="登记只创建草稿。制品必须已存在于受控模型目录，摘要会在发布预演时重新计算。" type="info" :closable="false" show-icon /><ElForm label-position="top" class="form-grid dialog-form"><ElFormItem label="模型名称"><ElInput v-model="registerForm.name" /></ElFormItem><ElFormItem label="能力"><ElInput v-model="registerForm.capability" /></ElFormItem><ElFormItem label="版本"><ElInput v-model="registerForm.version" /></ElFormItem><ElFormItem label="负责人"><ElInput v-model="registerForm.owner" /></ElFormItem><ElFormItem label="模型目标"><ElInput v-model="registerForm.model_target" placeholder="project_name/model_name" /></ElFormItem><ElFormItem label="制品大小（字节）"><ElInputNumber v-model="registerForm.artifact_size" :min="0" /></ElFormItem><ElFormItem label="SHA-256" class="span-2"><ElInput v-model="registerForm.sha256" maxlength="64" /></ElFormItem><ElFormItem label="许可"><ElInput v-model="registerForm.license" /></ElFormItem><ElFormItem label="来源"><ElInput v-model="registerForm.source" /></ElFormItem><ElFormItem label="模型卡引用"><ElInput v-model="registerForm.model_card_ref" /></ElFormItem><ElFormItem label="治理文件引用"><ElInput v-model="registerForm.governance_ref" /></ElFormItem><ElFormItem label="制品 URI" class="span-2"><ElInput v-model="registerForm.artifact_uri" /></ElFormItem><ElFormItem label="质量门禁（JSON）" class="span-2"><ElInput v-model="registerForm.quality_gates" type="textarea" :rows="3" /></ElFormItem><ElFormItem label="运行能力" class="span-2"><div class="checkbox-row"><ElCheckbox v-model="registerForm.supports_cpu">支持 CPU</ElCheckbox><ElCheckbox v-model="registerForm.supports_batching">支持动态批处理</ElCheckbox><ElCheckbox v-model="registerForm.redistribution_allowed">允许制品再分发</ElCheckbox><ElInputNumber v-model="registerForm.max_batch_size" :min="1" :max="4096" /></div></ElFormItem></ElForm><template #footer><ElButton @click="registerOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="registerModel">登记草稿</ElButton></template></ElDialog>
    <ElDialog v-model="evidenceOpen" :title="`登记评估 · ${modelName(selectedVersion)} ${selectedVersion?.version ?? ''}`" width="min(720px, 95vw)" :close-on-click-modal="false"><ElForm label-position="top" class="form-grid"><ElFormItem label="数据集 ID"><ElInput v-model="evidenceForm.dataset_id" /></ElFormItem><ElFormItem label="定义版本"><ElInput v-model="evidenceForm.definition_version" /></ElFormItem><ElFormItem label="数据集清单 SHA-256" class="span-2"><ElInput v-model="evidenceForm.dataset_manifest_sha256" maxlength="64" /></ElFormItem><ElFormItem label="评估指标（JSON）" class="span-2"><ElInput v-model="evidenceForm.metrics" type="textarea" :rows="4" :placeholder="metricsPlaceholder" /></ElFormItem><ElFormItem label="质量门禁（JSON）" class="span-2"><ElInput v-model="evidenceForm.quality_gates" type="textarea" :rows="4" :placeholder="qualityGatePlaceholder" /></ElFormItem><ElFormItem label="报告引用" class="span-2"><ElInput v-model="evidenceForm.report_ref" /></ElFormItem></ElForm><template #footer><ElButton @click="evidenceOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createEvaluation">登记评估</ElButton></template></ElDialog>
    <ElDialog v-model="approvalOpen" :title="`模型发布审批 · ${selectedVersion?.version ?? ''}`" width="min(560px, 94vw)" :close-on-click-modal="false"><ElAlert title="高风险或关键发布要求至少两名不同于发布执行人的独立批准人。" type="warning" :closable="false" show-icon /><ElForm label-position="top" class="dialog-form"><ElFormItem label="决定"><ElSelect v-model="approvalForm.decision"><ElOption label="批准" value="approve" /><ElOption label="拒绝" value="reject" /></ElSelect></ElFormItem><ElFormItem label="审批策略"><ElInput v-model="approvalForm.policy" /></ElFormItem><ElFormItem label="审批意见"><ElInput v-model="approvalForm.comment" type="textarea" :rows="3" /></ElFormItem></ElForm><template #footer><ElButton @click="approvalOpen = false">取消</ElButton><ElButton type="primary" :loading="actionLoading" @click="createApproval">记录决定</ElButton></template></ElDialog>
    <ElDialog v-model="releaseOpen" :title="`发布预演 · ${selectedVersion?.version ?? ''}`" width="min(720px, 95vw)" :close-on-click-modal="false"><ElForm label-position="top" class="form-grid"><ElFormItem label="动作"><ElSelect v-model="releaseForm.action" @change="preflight = null"><ElOption label="影子" value="shadow" /><ElOption label="灰度" value="canary" /><ElOption label="激活" value="activate" /><ElOption label="回滚" value="rollback" /><ElOption label="暂停" value="pause" /><ElOption label="弃用" value="deprecate" /></ElSelect></ElFormItem><ElFormItem label="模型别名"><ElInput v-model="releaseForm.alias" @input="preflight = null" /></ElFormItem><ElFormItem label="风险等级"><ElSelect v-model="releaseForm.risk_level" @change="preflight = null"><ElOption label="低" value="low" /><ElOption label="中" value="medium" /><ElOption label="高" value="high" /><ElOption label="关键" value="critical" /></ElSelect></ElFormItem><ElFormItem label="灰度流量（%）"><ElInputNumber v-model="releaseForm.traffic_percentage" :min="1" :max="99" @change="preflight = null" /></ElFormItem><ElFormItem label="预期当前目标" class="span-2"><ElInput v-model="releaseForm.expected_current_target" placeholder="用于乐观并发校验，首次发布可留空" @input="preflight = null" /></ElFormItem><ElFormItem label="发布原因" class="span-2"><ElInput v-model="releaseForm.reason" type="textarea" :rows="3" @input="preflight = null" /></ElFormItem></ElForm><section v-if="preflight" class="preflight" :data-ok="preflight.ok"><div><strong>{{ preflight.ok ? '预演通过' : '预演阻断' }}</strong><span>{{ preflight.current_target || '空目标' }} → {{ preflight.target }}</span></div><ul v-if="preflight.blockers?.length"><li v-for="item in preflight.blockers" :key="item">{{ item }}</li></ul><ul v-if="preflight.warnings?.length"><li v-for="item in preflight.warnings" :key="item">警告：{{ item }}</li></ul><p>审批 {{ preflight.approvers?.length ?? 0 }} / {{ preflight.required_approvals ?? 0 }} · 制品配置 {{ preflight.artifact?.configured ? '是' : '否' }} · 摘要匹配 {{ preflight.artifact?.sha256_matches ? '是' : '否' }}</p></section><template #footer><ElButton @click="releaseOpen = false">取消</ElButton><ElButton :loading="actionLoading" @click="dryRunRelease">执行预演</ElButton><ElButton type="primary" :disabled="!preflight?.ok" @click="requestRelease">继续执行</ElButton></template></ElDialog>
    <DangerConfirm v-model="releaseConfirmOpen" :title="`${actionLabel(releaseForm.action)}模型版本`" :description="`将别名 ${releaseForm.alias} 从 ${preflight?.current_target || '空目标'} 变更为 ${preflight?.target || selectedVersion?.model_target}。风险等级 ${releaseForm.risk_level}，该操作会写入发布审计并在失败时恢复配置。`" high-risk :confirmation-text="releaseForm.action === 'rollback' ? '确认模型回滚' : '确认模型发布'" :loading="actionLoading" @confirm="applyRelease" />
  </div>
</template>

<style scoped>
.registry-stats { margin-bottom: 16px; }
.page-tabs { padding: 0 14px 16px; }
.filter-bar { display: flex; gap: 8px; padding: 8px 0 14px; }
.filter-bar :deep(.el-select) { width: 200px; }
.data-table td > span { color: var(--muted); font-size: 12px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.form-grid .span-2 { grid-column: 1 / -1; }
.form-grid :deep(.el-select), .form-grid :deep(.el-input-number) { width: 100%; }
.dialog-form { margin-top: 16px; }
.checkbox-row { display: flex; align-items: center; flex-wrap: wrap; gap: 14px; }
.preflight { margin-top: 8px; padding: 14px; color: #8b2530; background: #fff7f7; border: 1px solid #efb6bc; border-radius: 5px; }
.preflight[data-ok="true"] { color: #17643b; background: #f1faf5; border-color: #a9d8be; }
.preflight > div { display: flex; justify-content: space-between; gap: 12px; }
.preflight span, .preflight p { font-size: 12px; }
.preflight ul { margin: 10px 0; padding-left: 20px; }
.preflight p { margin: 8px 0 0; }
@media (max-width: 700px) { .filter-bar, .form-grid { display: grid; grid-template-columns: 1fr; } .filter-bar :deep(.el-select) { width: 100%; } .form-grid .span-2 { grid-column: auto; } .preflight > div { flex-direction: column; } }
</style>
