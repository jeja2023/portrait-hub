<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Download, RefreshCw, Rocket, Upload } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElMessage,
  ElOption,
  ElSelect,
  ElSkeleton,
  ElTabPane,
  ElTabs,
} from "element-plus";

import { ApiError, apiRequest, jsonBody } from "../../api/client";
import DangerConfirm from "../../components/DangerConfirm.vue";
import EmptyState from "../../components/EmptyState.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";

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

const capabilities = useCapabilitiesStore();
const models = ref<ModelRow[]>([]);
const aliases = ref<AliasRow[]>([]);
const datasets = ref<Record<string, unknown>[]>([]);
const recommendations = ref<Record<string, unknown>>({});
const loading = ref(true);
const actionId = ref("");
const errorMessage = ref("");
const tab = ref("models");
const releaseAlias = ref("");
const releaseTarget = ref("");
const releaseLoading = ref(false);
const releaseConfirmOpen = ref(false);
const releasePreview = ref<Record<string, unknown> | null>(null);

const recommendationRows = computed(() =>
  Object.entries(recommendations.value).map(([modality, value]) => ({
    modality,
    value: value && typeof value === "object" ? JSON.stringify(value) : String(value ?? "--"),
  })),
);

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
    const [modelPayload, aliasPayload] = await Promise.all([
      apiRequest<{ models: ModelRow[] }>("/v1/models"),
      apiRequest<{ aliases: AliasRow[] }>("/v1/admin/models/rollout/aliases"),
    ]);
    models.value = modelPayload.models;
    aliases.value = aliasPayload.aliases;
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
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "模型中心数据加载失败";
  } finally {
    loading.value = false;
  }
}

async function change(model: ModelRow, action: "load" | "unload"): Promise<void> {
  const id = idOf(model);
  actionId.value = id;
  try {
    await apiRequest("/v1/models/" + modelPath(id) + "/" + action, { method: "POST" });
    ElMessage.success(action === "load" ? "模型已加载" : "模型已卸载");
    await load();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "模型操作失败";
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
    errorMessage.value = error instanceof ApiError ? error.message : "模型发布操作失败";
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
      <ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton>
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
        <ElTabPane label="模型" name="models">
          <ElSkeleton :loading="loading" :rows="7" animated>
            <EmptyState v-if="models.length === 0" title="没有已配置模型" />
            <div v-else class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>别名</th>
                    <th>能力</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="model in models" :key="idOf(model)">
                    <td>
                      <code>{{ idOf(model) }}</code>
                    </td>
                    <td>{{ model.alias || "--" }}</td>
                    <td>{{ model.capability || model.task || model.adapter || "--" }}</td>
                    <td>
                      <span class="status-pill" :data-status="model.loaded ? 'completed' : ''">
                        {{ model.loaded ? "已加载" : model.status || "未加载" }}
                      </span>
                    </td>
                    <td>
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
                      <th>数据集</th>
                      <th>样本</th>
                      <th>更新时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(dataset, index) in datasets" :key="String(dataset.dataset_id ?? index)">
                      <td>{{ dataset.name || dataset.dataset_id || "未命名数据集" }}</td>
                      <td>{{ dataset.sample_count ?? "--" }}</td>
                      <td>{{ dataset.updated_at ?? "--" }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
            <section>
              <h2>阈值建议</h2>
              <div class="recommendation-list">
                <div v-for="item in recommendationRows" :key="item.modality">
                  <strong>{{ item.modality }}</strong
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
.release-form,
.alias-list,
.evaluation-grid > section {
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
@media (max-width: 900px) {
  .release-grid,
  .evaluation-grid {
    grid-template-columns: 1fr;
  }
}
</style>
