<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Ban, Code2, Eye, Plus, RefreshCw } from "@lucide/vue";
import { ElAlert, ElButton, ElDialog, ElDrawer, ElInputNumber, ElSkeleton } from "element-plus";

import { apiRequest } from "../../api/client";
import type { JobListResponse, JobSummary } from "../../api/contracts";
import { openTicketWebSocket, type LiveConnectionState } from "../../api/ws";
import AnalysisNavigation from "../../components/AnalysisNavigation.vue";
import DangerConfirm from "../../components/DangerConfirm.vue";
import EmptyState from "../../components/EmptyState.vue";
import FrameGrid from "../../components/FrameGrid.vue";
import RawDataDrawer from "../../components/RawDataDrawer.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { usePrefsStore } from "../../stores/prefs";
import { errorBannerMessage } from "../../utils/errors";
import { formatPercent, formatTimestamp, statusLabels } from "../../utils/format";

const route = useRoute();
const router = useRouter();
const capabilities = useCapabilitiesStore();
const prefs = usePrefsStore();
const loading = ref(true);
const errorMessage = ref("");
const jobs = ref<JobSummary[]>([]);
const nextCursor = ref<string | null>(null);
const createOpen = ref(false);
const createLoading = ref(false);
const createFile = ref<File | null>(null);
const sampleInterval = ref(1);
const batchSize = ref(16);
const detail = ref<{ job: JobSummary; result?: unknown } | null>(null);
const detailOpen = computed(() => detail.value !== null);
const liveState = ref<LiveConnectionState>("closed");
const rawOpen = ref(false);
const cancelTarget = ref<JobSummary | null>(null);
const cancelLoading = ref(false);
const cancelOpen = computed({
  get: () => cancelTarget.value !== null,
  set: (open: boolean) => {
    if (!open) cancelTarget.value = null;
  },
});
let refreshTimer: number | null = null;
let stopLive: (() => void) | null = null;

async function loadJobs(append = false): Promise<void> {
  loading.value = !append;
  errorMessage.value = "";
  try {
    const query = new URLSearchParams({ limit: "30" });
    if (append && nextCursor.value) query.set("cursor", nextCursor.value);
    const payload = await apiRequest<JobListResponse>(`/v1/jobs?${query}`);
    jobs.value = append ? [...jobs.value, ...payload.items] : payload.items;
    nextCursor.value = payload.next_cursor;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "任务列表加载失败");
  } finally {
    loading.value = false;
  }
}

function selectedCreateFile(event: Event): void {
  createFile.value = (event.target as HTMLInputElement).files?.[0] ?? null;
}

async function createJob(): Promise<void> {
  if (!createFile.value) return;
  createLoading.value = true;
  try {
    const body = new FormData();
    body.append("file", createFile.value);
    body.append("sample_interval_seconds", String(sampleInterval.value));
    body.append("batch_size", String(batchSize.value));
    const payload = await apiRequest<{ job: JobSummary }>(
      "/v1/jobs/video",
      { method: "POST", body },
      120_000,
    );
    createOpen.value = false;
    createFile.value = null;
    await loadJobs();
    await openDetail(payload.job.job_id);
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "任务创建失败");
  } finally {
    createLoading.value = false;
  }
}

function applyLivePayload(payload: unknown): void {
  if (!payload || typeof payload !== "object" || !("job" in payload)) return;
  const liveJob = (payload as { job: JobSummary }).job;
  detail.value = detail.value ? { ...detail.value, job: liveJob } : { job: liveJob };
  jobs.value = jobs.value.map((job) => (job.job_id === liveJob.job_id ? liveJob : job));
  if (["completed", "failed", "cancelled"].includes(liveJob.status)) {
    stopLive?.();
    stopLive = null;
    if (liveJob.status === "completed") void loadDetailResult(liveJob.job_id);
  }
}

async function startLive(job: JobSummary): Promise<void> {
  stopLive?.();
  stopLive = null;
  if (!["queued", "running"].includes(job.status)) return;
  stopLive = await openTicketWebSocket({
    resourceType: "job",
    resourceId: job.job_id,
    onMessage: applyLivePayload,
    onState: (state) => (liveState.value = state),
  });
}

async function loadDetailResult(jobId: string): Promise<void> {
  const payload = await apiRequest<{ job: JobSummary; result: unknown }>(
    `/v1/jobs/${encodeURIComponent(jobId)}/result`,
  );
  detail.value = payload;
}

async function openDetail(jobId: string): Promise<void> {
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ job: JobSummary }>(`/v1/jobs/${encodeURIComponent(jobId)}`);
    detail.value = payload;
    await router.replace(`/analysis/video/${encodeURIComponent(jobId)}`);
    if (payload.job.status === "completed") await loadDetailResult(jobId);
    else await startLive(payload.job);
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "任务详情加载失败");
  }
}

function closeDetail(): void {
  stopLive?.();
  stopLive = null;
  detail.value = null;
  liveState.value = "closed";
  void router.replace("/analysis/video");
}

async function cancelJob(): Promise<void> {
  if (!cancelTarget.value) return;
  cancelLoading.value = true;
  try {
    await apiRequest(`/v1/jobs/${encodeURIComponent(cancelTarget.value.job_id)}/cancel`, { method: "POST" });
    cancelTarget.value = null;
    await loadJobs();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "取消任务失败");
  } finally {
    cancelLoading.value = false;
  }
}

onMounted(async () => {
  await loadJobs();
  if (typeof route.params.jobId === "string") await openDetail(route.params.jobId);
  refreshTimer = window.setInterval(() => {
    if (
      document.visibilityState === "visible" &&
      jobs.value.some((job) => ["queued", "running"].includes(job.status))
    ) {
      void loadJobs();
    }
  }, 5000);
});

onBeforeUnmount(() => {
  if (refreshTimer !== null) window.clearInterval(refreshTimer);
  stopLive?.();
});
</script>

<template>
  <div>
    <AnalysisNavigation />
    <header class="page-header">
      <div>
        <h1>视频任务</h1>
        <p>创建解析任务并自动跟踪进度，无需记录或粘贴任务 ID。</p>
      </div>
      <div class="page-actions">
        <ElButton :icon="RefreshCw" :loading="loading" @click="loadJobs()">刷新</ElButton>
        <ElButton
          v-if="capabilities.hasPermission('jobs')"
          type="primary"
          :icon="Plus"
          @click="createOpen = true"
          >新建任务</ElButton
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
    <section class="tool-surface">
      <ElSkeleton :loading="loading" animated :rows="7">
        <EmptyState
          v-if="jobs.length === 0"
          title="还没有视频任务"
          action-label="新建任务"
          @action="createOpen = true"
        />
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>类型</th>
                <th>状态</th>
                <th>进度</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="job in jobs" :key="job.job_id">
                <td>{{ job.job_id }}</td>
                <td>{{ job.kind === "batch" ? "批量" : "视频" }}</td>
                <td>
                  <span class="status-pill" :data-status="job.status">{{
                    statusLabels[job.status] ?? job.status
                  }}</span>
                </td>
                <td>
                  <progress class="job-progress" :value="job.progress" max="1">{{ job.progress }}</progress
                  ><small>{{ formatPercent(job.progress) }}</small>
                </td>
                <td>{{ formatTimestamp(job.created_at) }}</td>
                <td>
                  <div class="inline-actions">
                    <ElButton text :icon="Eye" @click="openDetail(job.job_id)">详情</ElButton
                    ><ElButton
                      v-if="['queued', 'running'].includes(job.status) && capabilities.hasPermission('jobs')"
                      text
                      type="danger"
                      :icon="Ban"
                      @click="cancelTarget = job"
                      >取消</ElButton
                    >
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </ElSkeleton>
      <div v-if="nextCursor" class="load-more"><ElButton @click="loadJobs(true)">加载更多</ElButton></div>
    </section>

    <ElDialog
      v-model="createOpen"
      title="新建视频解析任务"
      width="min(560px, 92vw)"
      :close-on-click-modal="false"
    >
      <div class="create-form">
        <label class="file-field"
          ><span>视频文件</span><input type="file" accept="video/*" @change="selectedCreateFile"
        /></label>
        <details>
          <summary>高级参数</summary>
          <label
            ><span>采样间隔（秒）</span
            ><ElInputNumber v-model="sampleInterval" :min="0.01" :max="60" :step="0.5" /></label
          ><label><span>批次大小</span><ElInputNumber v-model="batchSize" :min="1" :max="256" /></label>
        </details>
      </div>
      <template #footer
        ><ElButton @click="createOpen = false">取消</ElButton
        ><ElButton type="primary" :disabled="!createFile" :loading="createLoading" @click="createJob"
          >创建</ElButton
        ></template
      >
    </ElDialog>

    <ElDrawer
      :model-value="detailOpen"
      size="min(760px, 94vw)"
      title="任务详情"
      @update:model-value="!$event && closeDetail()"
    >
      <template v-if="detail"
        ><div class="detail-head">
          <div>
            <span>任务 ID</span><strong>{{ detail.job.job_id }}</strong>
          </div>
          <span class="status-pill" :data-status="detail.job.status">{{
            statusLabels[detail.job.status]
          }}</span>
        </div>
        <dl class="detail-grid">
          <div>
            <dt>进度</dt>
            <dd>{{ formatPercent(detail.job.progress) }}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{{ formatTimestamp(detail.job.created_at) }}</dd>
          </div>
          <div>
            <dt>更新时间</dt>
            <dd>{{ formatTimestamp(detail.job.updated_at) }}</dd>
          </div>
          <div>
            <dt>实时连接</dt>
            <dd role="status" aria-live="polite">
              {{ liveState === "open" ? "已连接" : liveState === "degraded" ? "已降级" : "连接中" }}
            </dd>
          </div>
        </dl>
        <ElAlert v-if="detail.job.error" :title="detail.job.error" type="error" :closable="false" show-icon />
        <FrameGrid v-if="detail.result" :data="detail.result" title="帧结果" />
        <div v-else-if="detail.job.status === 'completed'" class="result-empty-note">
          任务已完成，但当前结果没有可展示的帧预览。
        </div>
        <div v-if="prefs.developerMode" class="drawer-actions">
          <ElButton :icon="Code2" @click="rawOpen = true">原始数据</ElButton>
        </div></template
      >
    </ElDrawer>
    <RawDataDrawer v-model="rawOpen" :data="detail" />
    <DangerConfirm
      v-model="cancelOpen"
      title="取消视频任务"
      description="任务将尽快停止，已经产生的处理结果可能保留。"
      :loading="cancelLoading"
      @confirm="cancelJob"
    />
  </div>
</template>

<style scoped>
.job-progress {
  width: 130px;
  height: 9px;
  accent-color: #087682;
}
.data-table small {
  color: #62706d;
}
.load-more {
  display: flex;
  justify-content: center;
  padding: 12px;
  border-top: 1px solid #d8e0de;
}
.create-form {
  display: grid;
  gap: 16px;
}
.file-field,
.create-form details label {
  display: grid;
  gap: 7px;
  color: #62706d;
  font-size: 13px;
}
.create-form details {
  padding: 12px;
  background: #f6f8f7;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.create-form details summary {
  margin-bottom: 12px;
  cursor: pointer;
  font-weight: 600;
}
.create-form details label + label {
  margin-top: 12px;
}
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 16px;
  border-bottom: 1px solid #d8e0de;
}
.detail-head div {
  display: flex;
  flex-direction: column;
}
.detail-head span {
  color: #62706d;
  font-size: 12px;
}
.detail-head strong {
  margin-top: 4px;
  font-size: 18px;
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 18px 0;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.detail-grid div {
  padding: 13px;
  background: #fff;
}
.detail-grid dt {
  color: #62706d;
  font-size: 12px;
}
.detail-grid dd {
  margin: 5px 0 0;
}
.result-empty-note {
  margin-top: 18px;
  padding: 16px;
  color: #62706d;
  background: #f8faf9;
  border: 1px dashed #c6d0ce;
  border-radius: 5px;
}
.drawer-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
</style>

