<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Code2, ImageUp, Play, RotateCcw } from "@lucide/vue";
import { ElAlert, ElButton, ElCheckbox, ElInputNumber, ElOption, ElSelect } from "element-plus";

import { apiRequest } from "../../api/client";
import EmptyState from "../../components/EmptyState.vue";
import FrameGrid from "../../components/FrameGrid.vue";
import RawDataDrawer from "../../components/RawDataDrawer.vue";
import { usePrefsStore } from "../../stores/prefs";
import { errorBannerMessage } from "../../utils/errors";
import { modalityLabel } from "../../utils/format";

const prefs = usePrefsStore();
const endpoint = ref("/v1/infer/persons");
const includeEmbeddings = ref(false);
const confidence = ref(0.35);
const iou = ref(0.45);
const maxDetections = ref(32);
const file = ref<File | null>(null);
const previewUrl = ref("");
const loading = ref(false);
const errorMessage = ref("");
const result = ref<Record<string, unknown> | null>(null);
const showRaw = ref(false);
const imageArchives = ref<AnalysisArchive[]>([]);
const archiveNextCursor = ref<string | null>(null);
const archiveLoading = ref(false);

interface AnalysisPreview {
  artifact_id: string;
  label: string;
  src: string;
  content_url?: string;
}

interface AnalysisArchive {
  archive_id: string;
  request_id: string;
  source_type: string;
  mode: string;
  payload: Record<string, unknown>;
  previews: AnalysisPreview[];
  created_at: number;
  next_cursor?: string | null;
}

interface AnalysisArchiveResponse {
  results: AnalysisArchive[];
  next_cursor: string | null;
}

const endpointOptions = [
  { value: "/v1/infer/persons", label: "人体解析" },
  { value: "/v1/infer/faces", label: "人脸检测" },
  { value: "/v1/infer/pose", label: "姿态估计" },
  { value: "/v1/infer/appearance", label: "衣着外观" },
  { value: "/v1/infer/gait", label: "步态特征" },
];

const summary = computed(() => {
  const data = result.value;
  if (!data) return [];
  return [
    ["图片", data.frame_count],
    ["人员", data.person_count],
    ["人脸", data.face_count],
    ["姿态", data.pose_count],
  ].filter((item) => typeof item[1] === "number") as Array<[string, number]>;
});

function selectFile(event: Event): void {
  const selected = (event.target as HTMLInputElement).files?.[0] ?? null;
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  file.value = selected;
  previewUrl.value = selected ? URL.createObjectURL(selected) : "";
  result.value = null;
  errorMessage.value = "";
}

async function analyze(): Promise<void> {
  if (!file.value) {
    errorMessage.value = "请先选择图片";
    return;
  }
  loading.value = true;
  errorMessage.value = "";
  try {
    const body = new FormData();
    body.append("files", file.value);
    body.append("include_embeddings", String(includeEmbeddings.value));
    body.append("confidence", String(confidence.value));
    body.append("iou", String(iou.value));
    body.append("max_detections", String(maxDetections.value));
    result.value = await apiRequest<Record<string, unknown>>(
      endpoint.value,
      { method: "POST", body },
      60_000,
    );
    void loadImageArchives();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "图片分析失败");
  } finally {
    loading.value = false;
  }
}

function reset(): void {
  file.value = null;
  result.value = null;
  errorMessage.value = "";
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value = "";
}

async function loadImageArchives(append = false): Promise<void> {
  archiveLoading.value = true;
  try {
    const params = new URLSearchParams({ source_type: "image", limit: "6" });
    if (append && archiveNextCursor.value) params.set("cursor", archiveNextCursor.value);
    const payload = await apiRequest<AnalysisArchiveResponse>(`/v1/analysis/results?${params}`);
    imageArchives.value = append ? [...imageArchives.value, ...payload.results] : payload.results;
    archiveNextCursor.value = payload.next_cursor;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "图片归档加载失败");
    archiveNextCursor.value = null;
  } finally {
    archiveLoading.value = false;
  }
}

onMounted(() => void loadImageArchives());
onBeforeUnmount(() => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
});
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>图片分析</h1>
        <p>上传图片并选择分析能力，结果以业务摘要和图像证据呈现。</p>
      </div>
      <div class="page-actions">
        <ElButton :icon="RotateCcw" @click="reset">重置</ElButton>
        <ElButton v-if="prefs.developerMode && result" :icon="Code2" @click="showRaw = true"
          >原始数据</ElButton
        >
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
    <div class="analysis-grid">
      <section class="tool-surface" aria-labelledby="input-title">
        <div class="tool-surface__header"><h2 id="input-title" class="section-title">分析输入</h2></div>
        <div class="tool-surface__body input-form">
          <label class="file-drop">
            <input type="file" accept="image/*" @change="selectFile" />
            <img v-if="previewUrl" :src="previewUrl" alt="待分析图片预览" />
            <span v-else><ImageUp :size="30" />选择图片</span>
          </label>
          <label class="field"
            ><span>分析能力</span
            ><ElSelect v-model="endpoint"
              ><ElOption
                v-for="item in endpointOptions"
                :key="item.value"
                :label="item.label"
                :value="item.value" /></ElSelect
          ></label>
          <details class="advanced">
            <summary>高级参数</summary>
            <ElCheckbox v-model="includeEmbeddings">返回特征向量</ElCheckbox>
            <div class="advanced-fields">
              <label><span>置信度</span><ElInputNumber v-model="confidence" :min="0" :max="1" :step="0.05" /></label>
              <label><span>IoU</span><ElInputNumber v-model="iou" :min="0" :max="1" :step="0.05" /></label>
              <label><span>最大目标数</span><ElInputNumber v-model="maxDetections" :min="1" :max="256" /></label>
            </div>
          </details>
          <ElButton type="primary" :icon="Play" :loading="loading" :disabled="!file" @click="analyze"
            >开始分析</ElButton
          >
        </div>
      </section>
      <section class="result-pane" aria-labelledby="result-title">
        <h2 id="result-title" class="section-title">分析结果</h2>
        <div v-if="result" class="result-summary">
          <div v-for="item in summary" :key="item[0]">
            <span>{{ item[0] }}</span
            ><strong>{{ item[1] }}</strong>
          </div>
          <p v-if="summary.length === 0">分析已完成，开启调试信息后可查看脱敏详情。</p>
        </div>
        <FrameGrid v-if="result" :data="result" title="标注图与证据" />
        <div v-else class="result-empty"><ImageUp :size="34" /><span>结果将在这里显示</span></div>
      </section>
    </div>
    <section class="archive-panel" aria-labelledby="image-archives-title">
      <div class="archive-panel__header">
        <h2 id="image-archives-title" class="section-title">图片归档结果</h2>
        <ElButton :loading="archiveLoading" @click="loadImageArchives()">刷新归档</ElButton>
      </div>
      <EmptyState
        v-if="imageArchives.length === 0"
        title="暂无图片归档"
        description="完成并归档的图片分析结果会显示在这里。"
      />
      <div v-else class="archive-list">
        <article v-for="archive in imageArchives" :key="archive.archive_id" class="archive-row">
          <img v-if="archive.previews[0]?.src" :src="archive.previews[0].src" alt="图片归档预览" />
          <div>
            <strong>{{ archive.mode ? modalityLabel(archive.mode) : "图片分析" }}</strong>
            <code>{{ archive.request_id }}</code>
          </div>
          <span>{{ archive.previews.length }} 张预览</span>
        </article>
      </div>
      <div v-if="archiveNextCursor" class="load-more">
        <ElButton :loading="archiveLoading" @click="loadImageArchives(true)">加载更多</ElButton>
      </div>
    </section>
    <RawDataDrawer v-model="showRaw" :data="result" />
  </div>
</template>

<style scoped>
.analysis-grid {
  display: grid;
  grid-template-columns: minmax(300px, 420px) minmax(0, 1fr);
  gap: 22px;
}
.input-form {
  display: grid;
  gap: 16px;
}
.file-drop {
  min-height: 230px;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #62706d;
  background: #f8faf9;
  border: 1px dashed #9bafaa;
  border-radius: 5px;
  cursor: pointer;
}
.file-drop input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}
.file-drop img {
  width: 100%;
  height: 230px;
  object-fit: contain;
  background: #eef2f1;
}
.file-drop span {
  display: grid;
  place-items: center;
  gap: 8px;
}
.field {
  display: grid;
  gap: 7px;
  color: #62706d;
  font-size: 13px;
}
.advanced {
  padding: 10px 0;
  border-top: 1px solid #e0e7e5;
  border-bottom: 1px solid #e0e7e5;
}
.advanced summary {
  margin-bottom: 10px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}
.result-pane {
  min-height: 480px;
}
.result-summary {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.result-summary > div {
  min-height: 100px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 18px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.result-summary span {
  color: #62706d;
  font-size: 13px;
}
.result-summary strong {
  margin-top: 5px;
  font-size: 28px;
}
.result-empty {
  min-height: 420px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  color: #62706d;
  border: 1px dashed #c6d0ce;
  border-radius: 5px;
}
.archive-panel {
  margin-top: 22px;
  padding: 18px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.archive-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.archive-panel :deep(.el-empty__description p) {
  color: #52605d;
}
.archive-list {
  display: grid;
  gap: 8px;
}
.archive-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 10px;
  border: 1px solid #e0e7e5;
  border-radius: 4px;
}
.archive-row img {
  width: 88px;
  height: 64px;
  object-fit: cover;
  background: #eef3f2;
  border-radius: 3px;
}
.archive-row div {
  min-width: 0;
  display: grid;
  gap: 4px;
}
.archive-row code,
.archive-row span {
  color: #62706d;
  font-size: 12px;
}
.archive-row code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.load-more {
  display: flex;
  justify-content: center;
  margin-top: 12px;
}
@media (max-width: 900px) {
  .analysis-grid {
    grid-template-columns: 1fr;
  }
}
.advanced-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.advanced-fields label {
  display: grid;
  gap: 5px;
  color: #62706d;
  font-size: 12px;
}
</style>


