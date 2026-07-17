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

import { ApiError, apiRequest, jsonBody } from "../../api/client";
import DangerConfirm from "../../components/DangerConfirm.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { formatTimestamp } from "../../utils/format";

const capabilities = useCapabilitiesStore();
const loading = ref(true);
const saving = ref(false);
const errorMessage = ref("");
const thresholds = ref<Record<string, Record<string, number>>>({});
const reviews = ref<Record<string, unknown>[]>([]);
const tab = ref("thresholds");
const profile = ref("normal");
const saveConfirmOpen = ref(false);
const draft = reactive<Record<string, number>>({});
const reviewForm = reactive({
  job_id: "",
  track_id: "",
  label: "match",
  reviewer: "",
  note: "",
});
const profileOptions = ["strict", "normal", "loose"];
const modalities = computed(() => Object.keys(thresholds.value).sort());

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
    const [reviewResult] = await Promise.allSettled([
      apiRequest<{ annotations: Record<string, unknown>[] }>("/v1/evaluation/track-reviews?limit=50"),
    ]);
    reviews.value = reviewResult.status === "fulfilled" ? reviewResult.value.annotations : [];
    syncDraft();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "阈值与标注数据加载失败";
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
    errorMessage.value = error instanceof ApiError ? error.message : "阈值保存失败";
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
        ...reviewForm,
        reviewer: reviewForm.reviewer.trim() || null,
        note: reviewForm.note.trim() || null,
      }),
    });
    ElMessage.success("轨迹标注已提交");
    reviewForm.track_id = "";
    reviewForm.note = "";
    await load();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "轨迹标注提交失败";
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
        <p>按方案维护各模态阈值，并记录可审计的人工复核结论。</p>
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
        <ElTabPane label="阈值" name="thresholds">
          <div class="threshold-toolbar">
            <label
              ><span>阈值方案</span
              ><ElSelect v-model="profile" @change="syncDraft"
                ><ElOption v-for="item in profileOptions" :key="item" :label="item" :value="item" /></ElSelect
            ></label>
            <ElButton
              v-if="capabilities.hasPermission('thresholds:write')"
              type="primary"
              :icon="Save"
              :disabled="loading"
              @click="saveConfirmOpen = true"
              >保存方案</ElButton
            >
          </div>
          <ElSkeleton :loading="loading" :rows="6" animated>
            <div class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>模态</th>
                    <th>当前阈值</th>
                    <th>允许范围</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="modality in modalities" :key="modality">
                    <td>{{ modality }}</td>
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
          <div class="review-grid">
            <form class="review-form" @submit.prevent="createReview">
              <h2>新增标注</h2>
              <label><span>任务 ID</span><ElInput v-model="reviewForm.job_id" maxlength="512" /></label>
              <label><span>轨迹 ID</span><ElInput v-model="reviewForm.track_id" maxlength="512" /></label>
              <label
                ><span>结论</span
                ><ElSelect v-model="reviewForm.label"
                  ><ElOption label="匹配" value="match" /><ElOption
                    label="不匹配"
                    value="non_match" /><ElOption label="不确定" value="uncertain" /></ElSelect
              ></label>
              <label><span>复核人</span><ElInput v-model="reviewForm.reviewer" maxlength="128" /></label>
              <label
                ><span>备注</span
                ><ElInput v-model="reviewForm.note" type="textarea" maxlength="2000" show-word-limit
              /></label>
              <ElButton
                v-if="capabilities.hasPermission('jobs')"
                native-type="submit"
                type="primary"
                :loading="saving"
                :disabled="!reviewForm.job_id.trim() || !reviewForm.track_id.trim()"
                >提交标注</ElButton
              >
            </form>
            <section>
              <h2>最近标注</h2>
              <div v-if="reviews.length === 0" class="tab-note">当前没有轨迹标注</div>
              <div v-else class="table-wrap">
                <table class="data-table">
                  <thead>
                    <tr>
                      <th>轨迹</th>
                      <th>结论</th>
                      <th>复核人</th>
                      <th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(review, index) in reviews" :key="String(review.annotation_id ?? index)">
                      <td>
                        {{ review.track_id }}<br /><code>{{ review.job_id }}</code>
                      </td>
                      <td>{{ review.label }}</td>
                      <td>{{ review.reviewer || "--" }}</td>
                      <td>{{ formatTimestamp(Number(review.created_at)) }}</td>
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
      :description="
        '将更新 ' + profile + ' 方案的 ' + modalities.length + ' 个模态阈值，后续比对将使用新值。'
      "
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
.threshold-toolbar label {
  display: flex;
  align-items: center;
}
.threshold-toolbar {
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0 16px;
}
.threshold-toolbar label {
  gap: 10px;
  color: #62706d;
  font-size: 13px;
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
.review-grid section > h2 {
  margin: 0 0 4px;
  font-size: 16px;
}
.review-form label {
  display: grid;
  gap: 6px;
  color: #62706d;
  font-size: 13px;
}
.tab-note {
  padding: 40px;
  color: #62706d;
  text-align: center;
}
@media (max-width: 900px) {
  .review-grid {
    grid-template-columns: 1fr;
  }
}
</style>
