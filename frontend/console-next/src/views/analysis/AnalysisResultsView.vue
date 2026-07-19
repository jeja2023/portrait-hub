<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Archive, Code2, Eye, Image as ImageIcon, RefreshCw } from "@lucide/vue";
import { ElAlert, ElButton, ElDrawer, ElInput, ElRadioButton, ElRadioGroup, ElSkeleton } from "element-plus";
import { useRoute, useRouter } from "vue-router";

import { apiRequest } from "../../api/client";
import EmptyState from "../../components/EmptyState.vue";
import RawDataDrawer from "../../components/RawDataDrawer.vue";
import { usePrefsStore } from "../../stores/prefs";
import { errorBannerMessage } from "../../utils/errors";
import { artifactLabel, formatTimestamp, modalityLabel } from "../../utils/format";

interface AnalysisPreview {
  artifact_id: string;
  label: string;
  src: string;
  width: number;
  height: number;
  content_url: string;
}

interface AnalysisArchive {
  archive_id: string;
  request_id: string;
  source_type: "image" | "video" | "stream";
  source_ref: string;
  mode: string;
  endpoint: string;
  payload: Record<string, unknown>;
  previews: AnalysisPreview[];
  artifact_count: number;
  created_at: number;
}

interface AnalysisArchiveResponse {
  results: AnalysisArchive[];
  count: number;
  total: number;
  next_cursor: string | null;
  has_more: boolean;
}

const route = useRoute();
const router = useRouter();
const prefs = usePrefsStore();
const archives = ref<AnalysisArchive[]>([]);
const sourceType = ref(typeof route.query.source_type === "string" ? route.query.source_type : "");
const mode = ref(typeof route.query.mode === "string" ? route.query.mode : "");
const nextCursor = ref<string | null>(null);
const total = ref(0);
const loading = ref(true);
const errorMessage = ref("");
const detail = ref<AnalysisArchive | null>(null);
const rawOpen = ref(false);

const sourceLabels: Record<string, string> = {
  image: "图片",
  video: "视频",
  stream: "视频流",
};
const modeLabels: Record<string, string> = {
  detection: "人体检测",
  persons: "人体解析",
  faces: "人脸检测",
  pose: "姿态估计",
  appearance: "衣着外观",
  gait: "步态特征",
  tracks: "人员轨迹",
  video: "视频解析",
  stream: "视频流解析",
  person_tracks: "人员轨迹解析",
};
const resultCountLabel = computed(() => `共 ${total.value} 条`);

function sourceLabel(value: string): string {
  return sourceLabels[value] ?? modalityLabel(value);
}

function modeLabel(value: string): string {
  return modeLabels[value] ?? modalityLabel(value);
}

function resultSummary(item: AnalysisArchive): string {
  const payload = item.payload;
  const metrics = [
    ["人员", payload.person_count ?? payload.track_count],
    ["人脸", payload.face_count],
    ["帧", payload.frame_count ?? payload.sampled_frame_count],
  ].filter((entry) => typeof entry[1] === "number");
  return metrics.length ? metrics.map(([label, value]) => `${label} ${value}`).join(" · ") : "解析已归档";
}

async function loadResults(append = false): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const params = new URLSearchParams({ limit: "24" });
    if (sourceType.value) params.set("source_type", sourceType.value);
    if (mode.value.trim()) params.set("mode", mode.value.trim());
    if (append && nextCursor.value) params.set("cursor", nextCursor.value);
    const payload = await apiRequest<AnalysisArchiveResponse>(`/v1/analysis/results?${params}`);
    archives.value = append ? [...archives.value, ...payload.results] : payload.results;
    nextCursor.value = payload.next_cursor;
    total.value = payload.total;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "解析结果加载失败");
  } finally {
    loading.value = false;
  }
}

function openDetail(item: AnalysisArchive): void {
  detail.value = item;
  void router.replace({ query: { ...route.query, detail: item.archive_id } });
}

function closeDetail(): void {
  detail.value = null;
  void router.replace({ query: { ...route.query, detail: undefined } });
}

function applyFilters(): void {
  nextCursor.value = null;
  void router.replace({
    query: {
      ...route.query,
      source_type: sourceType.value || undefined,
      mode: mode.value.trim() || undefined,
      detail: undefined,
    },
  });
  void loadResults();
}

onMounted(async () => {
  await loadResults();
  const detailId = typeof route.query.detail === "string" ? route.query.detail : "";
  if (!detailId) return;
  const loaded = archives.value.find((item) => item.archive_id === detailId);
  if (loaded) {
    detail.value = loaded;
    return;
  }
  try {
    const payload = await apiRequest<{ result: AnalysisArchive }>(
      "/v1/analysis/results/" + encodeURIComponent(detailId),
    );
    detail.value = payload.result;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "解析结果详情加载失败");
  }
});
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>分析结果</h1>
        <p>统一查看当前租户的图片、视频和视频流解析档案。</p>
      </div>
      <div class="page-actions">
        <span class="result-count">{{ resultCountLabel }}</span>
        <ElButton :icon="RefreshCw" :loading="loading" @click="loadResults()">刷新</ElButton>
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
    <div class="result-filters">
      <ElRadioGroup v-model="sourceType" aria-label="结果来源" @change="applyFilters">
        <ElRadioButton value="">全部</ElRadioButton>
        <ElRadioButton value="image">图片</ElRadioButton>
        <ElRadioButton value="video">视频</ElRadioButton>
        <ElRadioButton value="stream">视频流</ElRadioButton>
      </ElRadioGroup>
      <ElInput
        v-model="mode"
        clearable
        placeholder="按解析模式筛选"
        @keyup.enter="applyFilters"
        @clear="applyFilters"
      />
      <ElButton @click="applyFilters">筛选</ElButton>
    </div>
    <ElSkeleton :loading="loading && archives.length === 0" animated :rows="8">
      <EmptyState
        v-if="archives.length === 0"
        title="还没有解析结果"
        description="完成一次图片、视频或视频流解析后，归档会显示在这里。"
      />
      <section v-else class="archive-grid" aria-label="解析结果列表">
        <article v-for="item in archives" :key="item.archive_id" class="archive-card">
          <button type="button" class="archive-preview" @click="openDetail(item)">
            <img
              v-if="item.previews[0]"
              :src="item.previews[0].src"
              :alt="`${sourceLabel(item.source_type)}解析预览`"
            />
            <span v-else
              ><ImageIcon v-if="item.source_type === 'image'" :size="30" /><Archive v-else :size="30"
            /></span>
            <span class="source-badge">{{ sourceLabel(item.source_type) }}</span>
            <span v-if="item.previews.length > 1" class="preview-count">{{ item.previews.length }} 张</span>
          </button>
          <div class="archive-body">
            <div class="archive-heading">
              <strong>{{ modeLabel(item.mode) }}</strong>
              <span>{{ formatTimestamp(item.created_at) }}</span>
            </div>
            <p>{{ resultSummary(item) }}</p>
            <code>{{ item.source_ref || item.request_id }}</code>
          </div>
          <ElButton text :icon="Eye" @click="openDetail(item)">查看详情</ElButton>
        </article>
      </section>
    </ElSkeleton>
    <div v-if="nextCursor" class="load-more">
      <ElButton :loading="loading" @click="loadResults(true)">加载更多</ElButton>
    </div>

    <ElDrawer
      :model-value="Boolean(detail)"
      title="解析结果详情"
      size="min(820px, 94vw)"
      @update:model-value="!$event && closeDetail()"
    >
      <template v-if="detail">
        <dl class="detail-facts">
          <div>
            <dt>来源</dt>
            <dd>{{ sourceLabel(detail.source_type) }}</dd>
          </div>
          <div>
            <dt>模式</dt>
            <dd>{{ modeLabel(detail.mode) }}</dd>
          </div>
          <div>
            <dt>来源标识</dt>
            <dd>
              <code>{{ detail.source_ref || "--" }}</code>
            </dd>
          </div>
          <div>
            <dt>归档时间</dt>
            <dd>{{ formatTimestamp(detail.created_at) }}</dd>
          </div>
        </dl>
        <div v-if="detail.previews.length" class="detail-previews">
          <figure v-for="(preview, index) in detail.previews" :key="preview.artifact_id">
            <img :src="preview.src" :alt="artifactLabel(preview.label, index)" />
            <figcaption>{{ artifactLabel(preview.label, index) }}</figcaption>
          </figure>
        </div>
        <EmptyState v-else title="该档案没有可见预览" />
        <div v-if="prefs.developerMode" class="drawer-actions">
          <ElButton :icon="Code2" @click="rawOpen = true">原始数据</ElButton>
        </div>
      </template>
    </ElDrawer>
    <RawDataDrawer v-model="rawOpen" :data="detail" />
  </div>
</template>

<style scoped>
.result-count {
  color: #62706d;
  font-size: 13px;
}
.result-filters {
  display: grid;
  grid-template-columns: auto minmax(180px, 320px) auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 18px;
}
.archive-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.archive-card {
  min-width: 0;
  display: grid;
  grid-template-rows: 190px 1fr auto;
  overflow: hidden;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.archive-preview {
  position: relative;
  width: 100%;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  color: #71807c;
  background: #edf2f1;
  border: 0;
  cursor: pointer;
}
.archive-preview img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.archive-preview > span:not(.source-badge):not(.preview-count) {
  height: 100%;
  display: grid;
  place-items: center;
}
.source-badge,
.preview-count {
  position: absolute;
  top: 10px;
  padding: 3px 7px;
  color: #fff;
  background: #263431;
  border-radius: 3px;
  font-size: 12px;
}
.source-badge {
  left: 10px;
}
.preview-count {
  right: 10px;
}
.archive-body {
  padding: 14px 14px 8px;
}
.archive-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.archive-heading span,
.archive-body p,
.archive-body code {
  color: #62706d;
  font-size: 12px;
}
.archive-body p {
  margin: 9px 0;
}
.archive-body code {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.archive-card > .el-button {
  justify-self: end;
  margin: 0 8px 8px;
}
.load-more {
  display: flex;
  justify-content: center;
  padding: 18px;
}
.detail-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 0 0 18px;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.detail-facts div {
  min-width: 0;
  padding: 13px;
  background: #fff;
}
.detail-facts dt {
  color: #62706d;
  font-size: 12px;
}
.detail-facts dd {
  margin: 5px 0 0;
  overflow-wrap: anywhere;
}
.detail-previews {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.detail-previews figure {
  min-width: 0;
  margin: 0;
}
.detail-previews img {
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: contain;
  background: #edf2f1;
  border: 1px solid #d8e0de;
}
.detail-previews figcaption {
  margin-top: 5px;
  color: #62706d;
  font-size: 12px;
}
.drawer-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
@media (max-width: 1000px) {
  .archive-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 700px) {
  .result-filters {
    grid-template-columns: 1fr;
  }
  .archive-grid {
    grid-template-columns: 1fr;
  }
  .detail-facts,
  .detail-previews {
    grid-template-columns: 1fr;
  }
}
</style>
