<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Download, Eye, Plus, RefreshCw, Rocket, RotateCcw, Trash2, Upload, Wrench } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDrawer,
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
import DataTablePagination from "../../components/DataTablePagination.vue";
import DangerConfirm from "../../components/DangerConfirm.vue";
import EmptyState from "../../components/EmptyState.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { errorBannerMessage } from "../../utils/errors";
import { useRouteTab } from "../../utils/routeState";
import { useTablePagination } from "../../utils/tablePagination";
import {
  actionLabel,
  capabilityLabel,
  confidenceLabel,
  datasetNameLabel,
  formatPercent,
  formatTimestamp,
  modalityLabel,
  statusLabel,
  thresholdProfileLabel,
} from "../../utils/format";

interface ModelRow {
  id?: string;
  model_id?: string;
  alias?: string;
  loaded?: boolean;
  task?: string;
  capability?: string;
  status?: string;
  adapter?: string;
}
interface AliasRow {
  alias: string;
  target?: string;
  previous_target?: string;
}
interface WeightedTarget {
  target_model_id: string;
  weight: number;
  status: "active" | "candidate";
}
interface RolloutRecord {
  time?: number;
  event?: string;
  alias?: string;
  old_target?: string;
  new_target?: string;
  total_weight?: number;
  dry_run?: boolean;
  written?: boolean;
}

const capabilities = useCapabilitiesStore();
const models = ref<ModelRow[]>([]);
const modelsPager = useTablePagination(models);
const aliases = ref<AliasRow[]>([]);
const datasets = ref<Record<string, unknown>[]>([]);
const datasetsPager = useTablePagination(datasets);
const recommendations = ref<Record<string, unknown>>({});
const loading = ref(true);
const actionId = ref("");
const errorMessage = ref("");
const tab = useRouteTab("models");
const releaseAlias = ref("");
const releaseTarget = ref("");
const releaseLoading = ref(false);
const releaseConfirmOpen = ref(false);
const releasePreview = ref<Record<string, unknown> | null>(null);
const modelDetail = ref<Record<string, unknown> | null>(null);
const modelDetailOpen = ref(false);
const maintenanceLoading = ref(false);
const trafficKey = ref("");
const trafficPreview = ref<Record<string, unknown> | null>(null);
const weightedTargets = ref<WeightedTarget[]>([
  { target_model_id: "", weight: 90, status: "active" },
  { target_model_id: "", weight: 10, status: "candidate" },
]);
const weightedPreview = ref<Record<string, unknown> | null>(null);
const weightedConfirmOpen = ref(false);
const rollbackPreview = ref<Record<string, unknown> | null>(null);
const rollbackConfirmOpen = ref(false);
const rolloutAudit = ref<RolloutRecord[]>([]);
const rolloutAuditPager = useTablePagination(rolloutAudit);

const recommendationRows = computed(() => {
  const rows = recommendations.value.recommendations;
  if (!Array.isArray(rows)) return [];
  return rows.map((raw, index) => {
    const item = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    const threshold = Number(item.recommended_threshold);
    const parts = [actionLabel(item.action)];
    if (item.profile) parts.push("方案：" + thresholdProfileLabel(item.profile));
    if (Number.isFinite(threshold)) parts.push("建议阈值：" + formatPercent(threshold));
    if (item.confidence) parts.push("置信度：" + confidenceLabel(item.confidence));
    return {
      modality: String(item.modality ?? index),
      value: parts.join(" · "),
    };
  });
});

function idOf(model: ModelRow): string {
  return String(model.model_id ?? model.id ?? "");
}
function modelPath(id: string): string {
  return id.split("/").map(encodeURIComponent).join("/");
}

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [modelPayload, aliasPayload, auditResult] = await Promise.all([
      apiRequest<{ models: ModelRow[] }>("/v1/models"),
      apiRequest<{ aliases: AliasRow[] }>("/v1/admin/models/rollout/aliases"),
      apiRequest<{ records: RolloutRecord[] }>("/v1/admin/models/rollout/audit?limit=100").catch(() => ({
        records: [],
      })),
    ]);
    models.value = modelPayload.models;
    aliases.value = aliasPayload.aliases;
    rolloutAudit.value = auditResult.records;
    const [datasetResult, recommendationResult] = await Promise.allSettled([
      apiRequest<{ datasets: Record<string, unknown>[] }>("/v1/evaluation/datasets?limit=20"),
      apiRequest<{ threshold_recommendations: Record<string, unknown> }>(
        "/v1/evaluation/threshold-recommendations",
      ),
    ]);
    datasets.value = datasetResult.status === "fulfilled" ? datasetResult.value.datasets : [];
    recommendations.value =
      recommendationResult.status === "fulfilled" ? recommendationResult.value.threshold_recommendations : {};
    releaseAlias.value ||= aliases.value[0]?.alias ?? "";
    releaseTarget.value ||=
      aliases.value.find((item) => item.alias === releaseAlias.value)?.target ?? idOf(models.value[0] ?? {});
    if (!weightedTargets.value[0]?.target_model_id) {
      weightedTargets.value[0]!.target_model_id = releaseTarget.value;
      weightedTargets.value[1]!.target_model_id = idOf(
        models.value.find((item) => idOf(item) !== releaseTarget.value) ?? {},
      );
    }
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "模型中心数据加载失败");
  } finally {
    loading.value = false;
  }
}

function modelRequest(id: string): { project_name: string; model_name: string } {
  const [project_name = "", ...modelParts] = id.split("/");
  return { project_name, model_name: modelParts.join("/") };
}

async function openModelDetail(model: ModelRow): Promise<void> {
  try {
    modelDetail.value = await apiRequest<Record<string, unknown>>("/v1/models/" + modelPath(idOf(model)));
    modelDetailOpen.value = true;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "模型详情加载失败");
  }
}

async function maintainModel(model: ModelRow, action: "warmup" | "reload"): Promise<void> {
  maintenanceLoading.value = true;
  try {
    const request = modelRequest(idOf(model));
    await apiRequest("/v1/admin/models/" + action, {
      method: "POST",
      body: jsonBody(action === "warmup" ? { models: [request] } : request),
    });
    ElMessage.success(action === "warmup" ? "模型预热完成" : "模型重载完成");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, action === "warmup" ? "模型预热失败" : "模型重载失败");
  } finally {
    maintenanceLoading.value = false;
  }
}

async function reloadConfig(): Promise<void> {
  maintenanceLoading.value = true;
  try {
    await apiRequest("/v1/admin/models/reload-config", { method: "POST" });
    ElMessage.success("模型配置已重新加载");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "模型配置重载失败");
  } finally {
    maintenanceLoading.value = false;
  }
}

function addWeightedTarget(): void {
  weightedTargets.value = [...weightedTargets.value, { target_model_id: "", weight: 0, status: "candidate" }];
  weightedPreview.value = null;
}

function removeWeightedTarget(index: number): void {
  weightedTargets.value = weightedTargets.value.filter((_, itemIndex) => itemIndex !== index);
  weightedPreview.value = null;
}

async function change(model: ModelRow, action: "load" | "unload"): Promise<void> {
  const id = idOf(model);
  actionId.value = id;
  try {
    await apiRequest("/v1/models/" + modelPath(id) + "/" + action, { method: "POST" });
    ElMessage.success(action === "load" ? "模型已加载" : "模型已卸载");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "模型操作失败");
  } finally {
    actionId.value = "";
  }
}

async function submitRelease(dryRun: boolean): Promise<void> {
  if (!releaseAlias.value || !releaseTarget.value) return;
  releaseLoading.value = true;
  errorMessage.value = "";
  try {
    releasePreview.value = await apiRequest<Record<string, unknown>>(
      "/v1/admin/models/rollout/aliases/switch",
      {
        method: "POST",
        body: jsonBody({
          alias_name: releaseAlias.value,
          target_model_id: releaseTarget.value,
          expected_current_target:
            aliases.value.find((item) => item.alias === releaseAlias.value)?.target ?? null,
          dry_run: dryRun,
        }),
      },
    );
    releaseConfirmOpen.value = false;
    ElMessage.success(dryRun ? "发布预演已完成" : "模型别名已正式切换");
    if (!dryRun) await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "模型发布操作失败");
  } finally {
    releaseLoading.value = false;
  }
}

async function submitWeighted(dryRun: boolean): Promise<void> {
  if (!releaseAlias.value || weightedTargets.value.some((item) => !item.target_model_id)) return;
  releaseLoading.value = true;
  try {
    weightedPreview.value = await apiRequest<Record<string, unknown>>(
      "/v1/admin/models/rollout/aliases/weighted",
      {
        method: "POST",
        body: jsonBody({
          alias_name: releaseAlias.value,
          targets: weightedTargets.value,
          expected_current_target:
            aliases.value.find((item) => item.alias === releaseAlias.value)?.target ?? null,
          dry_run: dryRun,
        }),
      },
    );
    weightedConfirmOpen.value = false;
    ElMessage.success(dryRun ? "灰度发布预演已完成" : "灰度发布已生效");
    if (!dryRun) await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "灰度发布失败");
  } finally {
    releaseLoading.value = false;
  }
}

async function previewTraffic(): Promise<void> {
  if (!releaseAlias.value || !trafficKey.value.trim()) return;
  try {
    const params = new URLSearchParams({
      alias_name: releaseAlias.value,
      traffic_key: trafficKey.value.trim(),
    });
    trafficPreview.value = await apiRequest<Record<string, unknown>>(
      "/v1/admin/models/rollout/aliases/preview?" + params,
    );
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "流量命中预览失败");
  }
}

async function rollbackAlias(dryRun: boolean): Promise<void> {
  if (!releaseAlias.value) return;
  releaseLoading.value = true;
  try {
    rollbackPreview.value = await apiRequest<Record<string, unknown>>(
      "/v1/admin/models/rollout/aliases/rollback",
      { method: "POST", body: jsonBody({ alias_name: releaseAlias.value, dry_run: dryRun }) },
    );
    rollbackConfirmOpen.value = false;
    ElMessage.success(dryRun ? "回滚预演已完成" : "模型别名已回滚");
    if (!dryRun) await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "模型回滚失败");
  } finally {
    releaseLoading.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>模型中心</h1>
        <p>管理模型运行状态、别名发布与评估基线。</p>
      </div>
      <div class="page-actions">
        <ElButton
          v-if="capabilities.hasPermission('models:write')"
          :icon="Wrench"
          :loading="maintenanceLoading"
          @click="reloadConfig"
          >重载配置</ElButton
        >
        <ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton>
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
        <ElTabPane label="模型" name="models">
          <ElSkeleton :loading="loading" :rows="7" animated>
            <EmptyState v-if="models.length === 0" title="没有已配置模型" />
            <div v-else class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th class="sequence-column">序号</th>
                    <th>模型</th>
                    <th>别名</th>
                    <th>能力</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(model, index) in modelsPager.items" :key="idOf(model)">
                    <td class="sequence-column">{{ modelsPager.startIndex + index + 1 }}</td>
                    <td>
                      <code>{{ idOf(model) }}</code>
                    </td>
                    <td>{{ model.alias || "--" }}</td>
                    <td>{{ capabilityLabel(model.capability || model.task || model.adapter) }}</td>
                    <td>
                      <span class="status-pill" :data-status="model.loaded ? 'completed' : ''">
                        {{ model.loaded ? "已加载" : model.status ? statusLabel(model.status) : "未加载" }}
                      </span>
                    </td>
                    <td>
                      <ElButton text :icon="Eye" @click="openModelDetail(model)">详情</ElButton>
                      <ElButton
                        v-if="capabilities.hasPermission('models:write')"
                        text
                        :loading="maintenanceLoading"
                        @click="maintainModel(model, model.loaded ? 'reload' : 'warmup')"
                        >{{ model.loaded ? "重载" : "预热" }}</ElButton
                      >
                      <ElButton
                        v-if="capabilities.hasPermission('models:write') && !model.loaded"
                        text
                        :icon="Upload"
                        :loading="actionId === idOf(model)"
                        @click="change(model, 'load')"
                        >加载</ElButton
                      >
                      <ElButton
                        v-if="capabilities.hasPermission('models:write') && model.loaded"
                        text
                        :icon="Download"
                        :loading="actionId === idOf(model)"
                        @click="change(model, 'unload')"
                        >卸载</ElButton
                      >
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </ElSkeleton>
          <DataTablePagination
            v-if="models.length"
            v-model:page="modelsPager.page"
            v-model:page-size="modelsPager.pageSize"
            :total="modelsPager.total"
          />
        </ElTabPane>

        <ElTabPane label="发布" name="release">
          <div class="release-grid">
            <section class="release-form" aria-labelledby="release-title">
              <h2 id="release-title">别名切换</h2>
              <label
                ><span>模型别名</span
                ><ElSelect v-model="releaseAlias"
                  ><ElOption
                    v-for="item in aliases"
                    :key="item.alias"
                    :label="item.alias"
                    :value="item.alias" /></ElSelect
              ></label>
              <label
                ><span>目标模型</span
                ><ElSelect v-model="releaseTarget" filterable
                  ><ElOption
                    v-for="model in models"
                    :key="idOf(model)"
                    :label="idOf(model)"
                    :value="idOf(model)" /></ElSelect
              ></label>
              <div class="release-actions">
                <ElButton
                  :icon="Rocket"
                  :loading="releaseLoading"
                  :disabled="!releaseAlias || !releaseTarget"
                  @click="submitRelease(true)"
                  >执行预演</ElButton
                >
                <ElButton
                  v-if="capabilities.hasPermission('models:write')"
                  type="danger"
                  :disabled="!releasePreview"
                  @click="releaseConfirmOpen = true"
                  >正式发布</ElButton
                >
              </div>
              <p class="form-note">正式发布仅在同一配置完成预演后开放，并校验当前别名目标。</p>
            </section>
            <section class="alias-list" aria-labelledby="alias-title">
              <h2 id="alias-title">当前别名</h2>
              <div v-for="item in aliases" :key="item.alias">
                <strong>{{ item.alias }}</strong
                ><code>{{ item.target || "未解析" }}</code>
              </div>
            </section>
          </div>

          <div class="release-grid release-grid--secondary">
            <section class="release-form">
              <div class="section-heading">
                <h2>按权重灰度</h2>
                <ElButton text :icon="Plus" @click="addWeightedTarget">添加目标</ElButton>
              </div>
              <div class="weighted-targets">
                <div v-for="(target, index) in weightedTargets" :key="index" class="weighted-row">
                  <ElSelect v-model="target.target_model_id" filterable placeholder="目标模型">
                    <ElOption
                      v-for="model in models"
                      :key="idOf(model)"
                      :label="idOf(model)"
                      :value="idOf(model)"
                    />
                  </ElSelect>
                  <ElInputNumber v-model="target.weight" :min="0" :max="100000" aria-label="流量权重" />
                  <ElSelect v-model="target.status" aria-label="发布状态">
                    <ElOption label="当前版本" value="active" />
                    <ElOption label="候选版本" value="candidate" />
                  </ElSelect>
                  <ElButton
                    :icon="Trash2"
                    aria-label="删除灰度目标"
                    :disabled="weightedTargets.length <= 1"
                    @click="removeWeightedTarget(index)"
                  />
                </div>
              </div>
              <div class="release-actions">
                <ElButton
                  :icon="Rocket"
                  :loading="releaseLoading"
                  :disabled="weightedTargets.some((item) => !item.target_model_id)"
                  @click="submitWeighted(true)"
                  >灰度预演</ElButton
                >
                <ElButton
                  v-if="capabilities.hasPermission('models:write')"
                  type="danger"
                  :disabled="!weightedPreview"
                  @click="weightedConfirmOpen = true"
                  >应用灰度</ElButton
                >
              </div>
            </section>

            <section class="release-form">
              <h2>命中预览与回滚</h2>
              <label>
                <span>流量标识</span>
                <ElInput v-model="trafficKey" placeholder="客户、设备或请求标识" maxlength="256" />
              </label>
              <div class="release-actions">
                <ElButton :icon="Eye" :disabled="!trafficKey.trim()" @click="previewTraffic"
                  >预览命中模型</ElButton
                >
                <ElButton
                  :icon="RotateCcw"
                  :loading="releaseLoading"
                  :disabled="!aliases.find((item) => item.alias === releaseAlias)?.previous_target"
                  @click="rollbackAlias(true)"
                  >回滚预演</ElButton
                >
                <ElButton
                  v-if="capabilities.hasPermission('models:write')"
                  type="danger"
                  :disabled="!rollbackPreview"
                  @click="rollbackConfirmOpen = true"
                  >确认回滚</ElButton
                >
              </div>
              <p v-if="trafficPreview" class="form-note">
                命中模型：<code>{{ trafficPreview.target || "未解析" }}</code>
              </p>
            </section>
          </div>

          <section class="rollout-audit">
            <h2>发布审计</h2>
            <div v-if="rolloutAudit.length" class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th class="sequence-column">序号</th>
                    <th>时间</th>
                    <th>事件</th>
                    <th>别名</th>
                    <th>变更</th>
                    <th>结果</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(item, index) in rolloutAuditPager.items" :key="String(item.time) + index">
                    <td class="sequence-column">{{ rolloutAuditPager.startIndex + index + 1 }}</td>
                    <td>{{ formatTimestamp(item.time) }}</td>
                    <td>{{ item.event || "--" }}</td>
                    <td>{{ item.alias || "--" }}</td>
                    <td>
                      <code
                        >{{ item.old_target || "--" }} →
                        {{ item.new_target || item.total_weight || "--" }}</code
                      >
                    </td>
                    <td>{{ item.dry_run ? "预演" : item.written === false ? "未写入" : "已生效" }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <EmptyState v-else title="还没有发布审计记录" />
            <DataTablePagination
              v-if="rolloutAudit.length"
              v-model:page="rolloutAuditPager.page"
              v-model:page-size="rolloutAuditPager.pageSize"
              :total="rolloutAuditPager.total"
            />
          </section>
        </ElTabPane>

        <ElTabPane label="评估" name="evaluation">
          <div class="evaluation-grid">
            <section>
              <h2>评估数据集</h2>
              <div v-if="datasets.length === 0" class="tab-note">暂无可用评估数据集</div>
              <div v-else class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th class="sequence-column">序号</th>
                      <th>数据集</th>
                      <th>样本</th>
                      <th>更新时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(dataset, index) in datasetsPager.items"
                      :key="String(dataset.dataset_id ?? datasetsPager.startIndex + index)"
                    >
                      <td class="sequence-column">{{ datasetsPager.startIndex + index + 1 }}</td>
                      <td>{{ datasetNameLabel(dataset.name || dataset.dataset_id) }}</td>
                      <td>{{ dataset.sample_count ?? "--" }}</td>
                      <td>{{ formatTimestamp(dataset.updated_at as string | number | undefined) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <DataTablePagination
                v-if="datasets.length"
                v-model:page="datasetsPager.page"
                v-model:page-size="datasetsPager.pageSize"
                :total="datasetsPager.total"
              />
            </section>
            <section>
              <h2>阈值建议</h2>
              <div class="recommendation-list">
                <div v-for="item in recommendationRows" :key="item.modality">
                  <strong>{{ modalityLabel(item.modality) }}</strong
                  ><span>{{ item.value }}</span>
                </div>
              </div>
            </section>
          </div>
        </ElTabPane>
      </ElTabs>
    </section>

    <DangerConfirm
      v-model="releaseConfirmOpen"
      title="确认正式发布"
      :description="'将别名 ' + releaseAlias + ' 切换到 ' + releaseTarget + '，该操作会影响后续在线请求。'"
      high-risk
      confirmation-text="正式发布"
      :loading="releaseLoading"
      @confirm="submitRelease(false)"
    />
    <DangerConfirm
      v-model="weightedConfirmOpen"
      title="应用灰度发布"
      description="将按预演中的权重配置写入模型别名，后续流量会稳定分配到多个模型。"
      high-risk
      confirmation-text="应用灰度"
      :loading="releaseLoading"
      @confirm="submitWeighted(false)"
    />
    <DangerConfirm
      v-model="rollbackConfirmOpen"
      title="回滚模型别名"
      :description="'将别名 ' + releaseAlias + ' 恢复到上一个模型目标。'"
      high-risk
      confirmation-text="确认回滚"
      :loading="releaseLoading"
      @confirm="rollbackAlias(false)"
    />
    <ElDrawer v-model="modelDetailOpen" title="模型详情" size="min(760px, 94vw)">
      <template v-if="modelDetail">
        <dl class="model-detail">
          <div>
            <dt>模型</dt>
            <dd>
              <code>{{ modelDetail.model_id }}</code>
            </dd>
          </div>
          <div>
            <dt>别名</dt>
            <dd>{{ modelDetail.alias || "--" }}</dd>
          </div>
          <div>
            <dt>加载状态</dt>
            <dd>{{ modelDetail.loaded ? "已加载" : "未加载" }}</dd>
          </div>
        </dl>
        <section class="detail-json">
          <h3>模型包与运行信息</h3>
          <pre>{{
            JSON.stringify(
              { package: modelDetail.package, config: modelDetail.config, runtime: modelDetail.runtime },
              null,
              2,
            )
          }}</pre>
        </section>
      </template>
    </ElDrawer>
  </div>
</template>

<style scoped>
.page-tabs {
  padding: 0 14px 14px;
}
.release-grid,
.evaluation-grid {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) minmax(0, 1.1fr);
  gap: 22px;
  padding: 10px 0;
}
.release-grid--secondary {
  border-top: 1px solid #d8e0de;
}
.release-form,
.alias-list,
.evaluation-grid > section {
  min-width: 0;
  padding: 18px;
  background: #f8faf9;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.release-form h2,
.alias-list h2,
.evaluation-grid h2 {
  margin: 0 0 16px;
  font-size: 16px;
}
.section-heading,
.weighted-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-heading {
  justify-content: space-between;
}
.section-heading h2 {
  margin-bottom: 0;
}
.weighted-targets {
  display: grid;
  gap: 8px;
  margin-bottom: 14px;
}
.weighted-row {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr) 36px;
}
.weighted-row > :first-child {
  grid-column: 1 / -1;
  min-width: 0;
}
.rollout-audit {
  padding-top: 18px;
  border-top: 1px solid #d8e0de;
}
.rollout-audit h2 {
  font-size: 16px;
}
.release-form label {
  display: grid;
  gap: 7px;
  margin-bottom: 14px;
  color: #62706d;
  font-size: 13px;
}
.release-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.form-note,
.tab-note {
  color: #62706d;
  font-size: 13px;
}
.alias-list,
.recommendation-list {
  display: grid;
  gap: 8px;
}
.alias-list div,
.recommendation-list div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 12px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.alias-list code,
.recommendation-list span {
  overflow-wrap: anywhere;
}
.recommendation-list span {
  color: #62706d;
  text-align: right;
}
.model-detail {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.model-detail div {
  min-width: 0;
  padding: 12px;
  background: #f5f8f7;
  border: 1px solid #d8e0de;
}
.model-detail dt {
  color: #62706d;
  font-size: 12px;
}
.model-detail dd {
  margin: 6px 0 0;
  overflow-wrap: anywhere;
}
.detail-json pre {
  max-height: 52vh;
  padding: 14px;
  overflow: auto;
  background: #f5f8f7;
  border: 1px solid #d8e0de;
  white-space: pre-wrap;
}
@media (max-width: 900px) {
  .release-grid,
  .evaluation-grid {
    grid-template-columns: 1fr;
  }
  .weighted-row,
  .model-detail {
    grid-template-columns: 1fr;
  }
}
</style>
