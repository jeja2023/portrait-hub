<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { RefreshCw, Save } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
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
import { useCapabilitiesStore } from "../../stores/capabilities";
import { errorBannerMessage } from "../../utils/errors";
import {
  actionLabel,
  confidenceLabel,
  datasetNameLabel,
  datasetPurposeLabel,
  formatTimestamp,
  modalityLabel,
  recommendationReasonLabel,
  reviewLabel,
  thresholdProfileLabel,
} from "../../utils/format";
import { useRouteTab } from "../../utils/routeState";

interface ReviewSummary {
  total_annotations: number;
  unique_job_count: number;
  unique_track_count: number;
  review_attention_count: number;
  label_counts: Array<{ label: string; count: number }>;
}
interface EvaluationDataset {
  dataset_id?: string;
  name?: string;
  purpose?: string;
  sample_count?: number;
  job_count?: number;
  track_count?: number;
  latest_created_at?: number;
}
interface ThresholdRecommendation {
  modality?: string;
  profile?: string;
  current_threshold?: number;
  recommended_threshold?: number;
  delta?: number;
  action?: string;
  reason?: string;
  confidence?: string;
}
interface RecommendationPayload {
  sample_count?: number;
  attention_count?: number;
  recommendations?: ThresholdRecommendation[];
  method?: string;
  auto_apply?: boolean;
}

const capabilities = useCapabilitiesStore();
const loading = ref(true);
const saving = ref(false);
const errorMessage = ref("");
const thresholds = ref<Record<string, Record<string, number>>>({});
const reviews = ref<Record<string, unknown>[]>([]);
const reviewSummary = ref<ReviewSummary>({
  total_annotations: 0,
  unique_job_count: 0,
  unique_track_count: 0,
  review_attention_count: 0,
  label_counts: [],
});
const datasets = ref<EvaluationDataset[]>([]);
const recommendationPayload = ref<RecommendationPayload>({});
const tab = useRouteTab("thresholds");
const profile = ref("normal");
const saveConfirmOpen = ref(false);
const draft = reactive<Record<string, number>>({});
const reviewForm = reactive<{
  job_id: string;
  track_id: string;
  label: string;
  reviewer: string;
  note: string;
  frame_index: number | null;
  evidence_ref: string;
}>({
  job_id: "",
  track_id: "",
  label: "confirmed",
  reviewer: "",
  note: "",
  frame_index: null,
  evidence_ref: "",
});
const profileOptions = ["strict", "normal", "loose"];
const modalities = computed(() => Object.keys(thresholds.value).sort());
const recommendations = computed(() => recommendationPayload.value.recommendations ?? []);

function syncDraft(): void {
  for (const key of Object.keys(draft)) delete draft[key];
  for (const modality of modalities.value) {
    draft[modality] = Number(thresholds.value[modality]?.[profile.value] ?? 0);
  }
}

async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const thresholdPayload = await apiRequest<{
      thresholds: Record<string, Record<string, number>>;
    }>("/v1/thresholds");
    thresholds.value = thresholdPayload.thresholds;
    const [reviewResult, summaryResult, datasetResult, recommendationResult] = await Promise.allSettled([
      apiRequest<{ annotations: Record<string, unknown>[] }>("/v1/evaluation/track-reviews?limit=50"),
      apiRequest<{ summary: ReviewSummary }>("/v1/evaluation/track-reviews/summary?limit=10"),
      apiRequest<{ datasets: EvaluationDataset[] }>("/v1/evaluation/datasets?limit=20"),
      apiRequest<{ threshold_recommendations: RecommendationPayload }>(
        "/v1/evaluation/threshold-recommendations",
      ),
    ]);
    reviews.value = reviewResult.status === "fulfilled" ? reviewResult.value.annotations : [];
    if (summaryResult.status === "fulfilled") reviewSummary.value = summaryResult.value.summary;
    datasets.value = datasetResult.status === "fulfilled" ? datasetResult.value.datasets : [];
    recommendationPayload.value =
      recommendationResult.status === "fulfilled" ? recommendationResult.value.threshold_recommendations : {};
    syncDraft();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "阈值与标注数据加载失败");
  } finally {
    loading.value = false;
  }
}

async function saveThresholds(): Promise<void> {
  saving.value = true;
  errorMessage.value = "";
  try {
    await apiRequest("/v1/thresholds/" + encodeURIComponent(profile.value), {
      method: "PUT",
      body: jsonBody(draft),
    });
    saveConfirmOpen.value = false;
    ElMessage.success("阈值方案已保存");
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "阈值保存失败");
  } finally {
    saving.value = false;
  }
}

async function createReview(): Promise<void> {
  if (!reviewForm.job_id.trim() || !reviewForm.track_id.trim()) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    await apiRequest("/v1/evaluation/track-reviews", {
      method: "POST",
      body: jsonBody({
        job_id: reviewForm.job_id.trim(),
        track_id: reviewForm.track_id.trim(),
        label: reviewForm.label,
        reviewer: reviewForm.reviewer.trim() || null,
        note: reviewForm.note.trim() || null,
        frame_index: reviewForm.frame_index,
        evidence_ref: reviewForm.evidence_ref.trim() || null,
      }),
    });
    ElMessage.success("轨迹标注已提交");
    reviewForm.track_id = "";
    reviewForm.note = "";
    reviewForm.frame_index = null;
    reviewForm.evidence_ref = "";
    await load();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "轨迹标注提交失败");
  } finally {
    saving.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>阈值与标注</h1>
        <p>维护各模态阈值，记录人工复核，并将复核池转化为评估数据和阈值建议。</p>
      </div>
      <ElButton :icon="RefreshCw" :loading="loading" @click="load">刷新</ElButton>
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
        <ElTabPane label="阈值" name="thresholds">
          <div class="threshold-toolbar">
            <label>
              <span>阈值方案</span>
              <ElSelect v-model="profile" @change="syncDraft">
                <ElOption v-for="item in profileOptions" :key="item" :label="thresholdProfileLabel(item)" :value="item" />
              </ElSelect>
            </label>
            <ElButton
              v-if="capabilities.hasPermission('thresholds:write')"
              type="primary"
              :icon="Save"
              :disabled="loading"
              @click="saveConfirmOpen = true"
            >
              保存方案
            </ElButton>
          </div>
          <ElSkeleton :loading="loading" :rows="6" animated>
            <div class="table-wrap">
              <table class="data-table">
                <thead><tr><th>模态</th><th>当前阈值</th><th>允许范围</th></tr></thead>
                <tbody>
                  <tr v-for="modality in modalities" :key="modality">
                    <td>{{ modalityLabel(modality) }}</td>
                    <td>
                      <ElInputNumber
                        v-model="draft[modality]"
                        :min="0"
                        :max="1"
                        :step="0.01"
                        :precision="2"
                        :disabled="!capabilities.hasPermission('thresholds:write')"
                      />
                    </td>
                    <td>0.00 - 1.00</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </ElSkeleton>
        </ElTabPane>

        <ElTabPane label="轨迹审阅" name="reviews">
          <div class="review-summary">
            <div><span>总标注</span><strong>{{ reviewSummary.total_annotations }}</strong></div>
            <div><span>任务</span><strong>{{ reviewSummary.unique_job_count }}</strong></div>
            <div><span>轨迹</span><strong>{{ reviewSummary.unique_track_count }}</strong></div>
            <div>
              <span>待关注</span>
              <strong :data-warning="reviewSummary.review_attention_count > 0">
                {{ reviewSummary.review_attention_count }}
              </strong>
            </div>
          </div>
          <div class="review-grid">
            <form class="review-form" @submit.prevent="createReview">
              <h2>新增标注</h2>
              <label><span>任务 ID</span><ElInput v-model="reviewForm.job_id" maxlength="512" /></label>
              <label><span>轨迹 ID</span><ElInput v-model="reviewForm.track_id" maxlength="512" /></label>
              <label>
                <span>结论</span>
                <ElSelect v-model="reviewForm.label">
                  <ElOption label="已确认" value="confirmed" />
                  <ElOption label="身份不匹配" value="mismatch" />
                  <ElOption label="误检" value="false_positive" />
                  <ElOption label="低质量" value="low_quality" />
                  <ElOption label="不确定" value="uncertain" />
                </ElSelect>
              </label>
              <label>
                <span>证据帧</span>
                <ElInputNumber v-model="reviewForm.frame_index" :min="0" :max="1000000000" />
              </label>
              <label><span>证据引用</span><ElInput v-model="reviewForm.evidence_ref" maxlength="512" /></label>
              <label><span>复核人</span><ElInput v-model="reviewForm.reviewer" maxlength="128" /></label>
              <label>
                <span>备注</span>
                <ElInput v-model="reviewForm.note" type="textarea" maxlength="2000" show-word-limit />
              </label>
              <ElButton
                v-if="capabilities.hasPermission('jobs')"
                native-type="submit"
                type="primary"
                :loading="saving"
                :disabled="!reviewForm.job_id.trim() || !reviewForm.track_id.trim()"
              >
                提交标注
              </ElButton>
            </form>
            <section>
              <h2>最近标注</h2>
              <div v-if="reviews.length === 0" class="tab-note">当前没有轨迹标注</div>
              <div v-else class="table-wrap">
                <table class="data-table">
                  <thead><tr><th>轨迹</th><th>结论</th><th>复核人</th><th>证据</th><th>时间</th></tr></thead>
                  <tbody>
                    <tr v-for="(review, index) in reviews" :key="String(review.annotation_id ?? index)">
                      <td>{{ review.track_id }}<br /><code>{{ review.job_id }}</code></td>
                      <td>{{ reviewLabel(review.label) }}</td>
                      <td>{{ review.reviewer || "--" }}</td>
                      <td>{{ review.frame_index == null ? "--" : "帧 " + review.frame_index }}</td>
                      <td>{{ formatTimestamp(Number(review.created_at)) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </ElTabPane>

        <ElTabPane label="评估池" name="evaluation">
          <div class="evaluation-grid">
            <section>
              <h2>评估数据集</h2>
              <div v-if="datasets.length === 0" class="tab-note">复核标注不足，尚未形成评估数据集</div>
              <div v-else class="table-wrap">
                <table class="data-table">
                  <thead><tr><th>数据集</th><th>用途</th><th>样本</th><th>任务/轨迹</th><th>最新证据</th></tr></thead>
                  <tbody>
                    <tr v-for="(dataset, index) in datasets" :key="String(dataset.dataset_id ?? index)">
                      <td>{{ datasetNameLabel(dataset.name || dataset.dataset_id) }}</td>
                      <td>{{ datasetPurposeLabel(dataset.purpose) }}</td>
                      <td>{{ dataset.sample_count ?? 0 }}</td>
                      <td>{{ dataset.job_count ?? 0 }} / {{ dataset.track_count ?? 0 }}</td>
                      <td>{{ formatTimestamp(Number(dataset.latest_created_at)) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
            <section>
              <div class="recommendation-heading">
                <h2>阈值建议</h2>
                <span>自动应用：{{ recommendationPayload.auto_apply ? "开启" : "关闭" }}</span>
              </div>
              <div v-if="recommendations.length === 0" class="tab-note">当前没有阈值建议</div>
              <div v-else class="table-wrap">
                <table class="data-table">
                  <thead><tr><th>模态/方案</th><th>当前</th><th>建议</th><th>变化</th><th>动作</th><th>置信度</th></tr></thead>
                  <tbody>
                    <tr v-for="(item, index) in recommendations" :key="String(item.modality ?? index)">
                      <td>{{ modalityLabel(item.modality) }} / {{ thresholdProfileLabel(item.profile) }}</td>
                      <td>{{ item.current_threshold }}</td>
                      <td>{{ item.recommended_threshold }}</td>
                      <td>{{ item.delta }}</td>
                      <td :title="recommendationReasonLabel(item.reason)">{{ actionLabel(item.action) }}</td>
                      <td>{{ confidenceLabel(item.confidence) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </ElTabPane>
      </ElTabs>
    </section>

    <DangerConfirm
      v-model="saveConfirmOpen"
      title="保存阈值方案"
      :description="'将更新“' + thresholdProfileLabel(profile) + '”方案的 ' + modalities.length + ' 个模态阈值，后续比对将使用新值。'"
      :loading="saving"
      @confirm="saveThresholds"
    />
  </div>
</template>

<style scoped>
.page-tabs {
  padding: 0 14px 14px;
}
.threshold-toolbar,
.threshold-toolbar label,
.review-summary,
.recommendation-heading {
  display: flex;
  align-items: center;
}
.threshold-toolbar,
.recommendation-heading {
  justify-content: space-between;
  gap: 12px;
}
.threshold-toolbar {
  padding: 8px 0 16px;
}
.threshold-toolbar label {
  gap: 10px;
  color: #62706d;
  font-size: 13px;
}
.review-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 8px 0 16px;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.review-summary div {
  display: flex;
  min-height: 68px;
  flex-direction: column;
  justify-content: center;
  padding: 10px 14px;
  background: #f8faf9;
}
.review-summary span,
.recommendation-heading span {
  color: #62706d;
  font-size: 12px;
}
.review-summary strong {
  margin-top: 4px;
  font-size: 20px;
}
.review-grid {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  gap: 22px;
  padding: 10px 0;
}
.review-form {
  display: grid;
  align-content: start;
  gap: 13px;
  padding: 18px;
  background: #f8faf9;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.review-form h2,
.review-grid section > h2,
.evaluation-grid h2 {
  margin: 0 0 4px;
  font-size: 16px;
}
.review-form label {
  display: grid;
  gap: 6px;
  color: #62706d;
  font-size: 13px;
}
.evaluation-grid {
  display: grid;
  gap: 24px;
  padding: 10px 0;
}
.evaluation-grid > section {
  min-width: 0;
}
.recommendation-heading {
  margin-bottom: 12px;
}
.tab-note {
  padding: 40px;
  color: #62706d;
  text-align: center;
}
[data-warning="true"] {
  color: #b4232f;
}
@media (max-width: 900px) {
  .review-grid {
    grid-template-columns: 1fr;
  }
  .review-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>