<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { Code2, ImagePlus, Search, UserRound } from "@lucide/vue";
import { ElAlert, ElButton, ElInputNumber, ElOption, ElSelect } from "element-plus";

import { ApiError, apiRequest } from "../api/client";
import RawDataDrawer from "../components/RawDataDrawer.vue";
import { usePrefsStore } from "../stores/prefs";
import { formatPercent } from "../utils/format";

const prefs = usePrefsStore();
const file = ref<File | null>(null);
const previewUrl = ref("");
const modality = ref("body");
const topK = ref(5);
const loading = ref(false);
const errorMessage = ref("");
const result = ref<Record<string, unknown> | null>(null);
const rawOpen = ref(false);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

const candidates = computed(() =>
  Array.isArray(result.value?.candidates) ? result.value.candidates.map(asRecord) : [],
);
const queryWarning = computed(() => {
  const query = asRecord(result.value?.query);
  const quality = Number(query.combined_quality_score ?? query.quality_score);
  return Number.isFinite(quality) && quality < 0.4 ? "查询图质量较低，候选结果需要人工复核。" : "";
});

function candidateScore(candidate: Record<string, unknown>): number {
  return Number(candidate.template_similarity ?? candidate.similarity ?? 0);
}
function candidateDecision(candidate: Record<string, unknown>): Record<string, unknown> {
  return asRecord(candidate.decision);
}
function candidateRisk(candidate: Record<string, unknown>): string {
  return String(candidateDecision(candidate).risk ?? "clear");
}
function candidateThumbnail(candidate: Record<string, unknown>): string {
  return String(asRecord(candidate.feature).thumbnail ?? "");
}
function selectFile(event: Event): void {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null;
  previewUrl.value = file.value ? URL.createObjectURL(file.value) : "";
  result.value = null;
}
async function search(): Promise<void> {
  if (!file.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const body = new FormData();
    body.append("file", file.value);
    body.append("modality", modality.value);
    body.append("top_k", String(topK.value));
    body.append("threshold_profile", "normal");
    result.value = await apiRequest<Record<string, unknown>>(
      "/v1/gallery/search",
      { method: "POST", body },
      90_000,
    );
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "检索失败";
  } finally {
    loading.value = false;
  }
}
onBeforeUnmount(() => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
});
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>以图搜人</h1>
        <p>上传查询图，按模板相似度、质量与风险返回候选人员。</p>
      </div>
      <div class="page-actions">
        <ElButton v-if="prefs.developerMode && result" :icon="Code2" @click="rawOpen = true"
          >原始数据</ElButton
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
    <section class="search-controls">
      <label class="query-file"
        ><input type="file" accept="image/*" @change="selectFile" /><img
          v-if="previewUrl"
          :src="previewUrl"
          alt="查询图预览"
        /><span v-else><ImagePlus :size="27" />选择查询图</span></label
      >
      <div class="search-options">
        <label
          ><span>模态</span
          ><ElSelect v-model="modality"
            ><ElOption label="人体" value="body" /><ElOption label="人脸" value="face" /><ElOption
              label="衣着"
              value="appearance" /></ElSelect></label
        ><label><span>候选数</span><ElInputNumber v-model="topK" :min="1" :max="100" /></label
        ><ElButton type="primary" :icon="Search" :disabled="!file" :loading="loading" @click="search"
          >开始检索</ElButton
        >
      </div>
    </section>
    <ElAlert
      v-if="queryWarning"
      class="quality-warning"
      :title="queryWarning"
      type="warning"
      show-icon
      :closable="false"
    />
    <section v-if="result" class="candidate-section">
      <h2 class="section-title">检索结果（{{ candidates.length }}）</h2>
      <div v-if="candidates.length" class="candidate-list">
        <article v-for="candidate in candidates" :key="String(candidate.person_id)" class="candidate-row">
          <div class="candidate-avatar">
            <img
              v-if="candidateThumbnail(candidate)"
              :src="candidateThumbnail(candidate)"
              alt="候选人员缩略图"
            /><UserRound v-else :size="28" />
          </div>
          <div class="candidate-main">
            <div class="candidate-heading">
              <strong>{{ candidate.display_name || "未命名人员" }}</strong
              ><code>{{ candidate.person_id }}</code
              ><span
                class="status-pill"
                :data-status="candidateRisk(candidate) === 'clear' ? 'completed' : 'running'"
                >{{ candidateRisk(candidate) === "clear" ? "结果稳定" : "需要复核" }}</span
              >
            </div>
            <div class="score-line">
              <progress :value="candidateScore(candidate)" max="1">{{ candidateScore(candidate) }}</progress
              ><b>{{ formatPercent(candidateScore(candidate)) }}</b>
            </div>
            <div class="candidate-meta">
              置信度 {{ formatPercent(Number(candidateDecision(candidate).confidence ?? 0)) }} · 风险
              {{ candidateRisk(candidate) }}
            </div>
          </div>
          <RouterLink :to="`/gallery/${encodeURIComponent(String(candidate.person_id))}`"
            >查看人员</RouterLink
          >
        </article>
      </div>
      <div v-else class="no-candidates">没有符合条件的候选人员</div>
    </section>
    <RawDataDrawer v-model="rawOpen" :data="result" />
  </div>
</template>

<style scoped>
.search-controls {
  display: grid;
  grid-template-columns: 180px minmax(0, 1fr);
  gap: 20px;
  align-items: stretch;
  margin-bottom: 24px;
  padding: 18px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.query-file {
  min-height: 160px;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: #f5f8f7;
  border: 1px dashed #9bafaa;
  border-radius: 4px;
  cursor: pointer;
}
.query-file input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}
.query-file img {
  width: 100%;
  height: 160px;
  object-fit: contain;
}
.query-file span {
  display: grid;
  place-items: center;
  gap: 8px;
  color: #62706d;
}
.search-options {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}
.search-options label {
  width: 170px;
  display: grid;
  gap: 7px;
  color: #62706d;
  font-size: 13px;
}
.quality-warning {
  margin: 0 0 18px;
}
.candidate-list {
  display: grid;
  gap: 8px;
}
.candidate-row {
  min-height: 108px;
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 14px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.candidate-avatar {
  width: 72px;
  height: 72px;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #71807c;
  background: #edf2f1;
  border-radius: 4px;
}
.candidate-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.candidate-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.candidate-heading code {
  color: #62706d;
}
.score-line {
  display: grid;
  grid-template-columns: minmax(120px, 420px) 52px;
  align-items: center;
  gap: 10px;
  margin: 9px 0;
}
.score-line progress {
  width: 100%;
  height: 10px;
  accent-color: #087682;
}
.candidate-meta {
  color: #62706d;
  font-size: 12px;
}
.no-candidates {
  padding: 50px;
  color: #62706d;
  text-align: center;
  border: 1px dashed #bdc9c6;
}
@media (max-width: 700px) {
  .search-controls {
    grid-template-columns: 1fr;
  }
  .candidate-row {
    grid-template-columns: 58px minmax(0, 1fr);
  }
  .candidate-avatar {
    width: 54px;
    height: 54px;
  }
  .candidate-row > a {
    grid-column: 2;
  }
}
</style>
