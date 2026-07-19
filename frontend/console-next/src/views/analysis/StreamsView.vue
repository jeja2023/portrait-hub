<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Code2, Eye, Pause, Play, Plus, RefreshCw, Trash2 } from "@lucide/vue";
import { ElAlert, ElButton, ElDialog, ElDrawer, ElInput, ElSkeleton } from "element-plus";

import { apiRequest, jsonBody } from "../../api/client";
import { openTicketWebSocket, type LiveConnectionState } from "../../api/ws";
import EmptyState from "../../components/EmptyState.vue";
import FrameGrid from "../../components/FrameGrid.vue";
import RawDataDrawer from "../../components/RawDataDrawer.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { usePrefsStore } from "../../stores/prefs";
import { errorBannerMessage } from "../../utils/errors";
import { formatTimestamp, statusLabel, eventLabel } from "../../utils/format";

interface StreamSummary {
  stream_id: string;
  stream_url: string;
  name: string | null;
  status: string;
  metadata: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  event_count: number;
}

interface StreamEvent {
  event_id: string;
  type: string;
  message: string;
  created_at: number;
  payload: Record<string, unknown>;
}

interface StreamDetail extends StreamSummary {
  events?: StreamEvent[];
}

const route = useRoute();
const router = useRouter();
const capabilities = useCapabilitiesStore();
const prefs = usePrefsStore();
const streams = ref<StreamSummary[]>([]);
const loading = ref(true);
const errorMessage = ref("");
const createOpen = ref(false);
const createLoading = ref(false);
const streamName = ref("");
const streamUrl = ref("");
const metadataRows = ref<{ key: string; value: string }[]>([]);
const detail = ref<StreamDetail | null>(null);
const detailEvents = ref<StreamEvent[]>([]);
const liveState = ref<LiveConnectionState>("closed");
const rawOpen = ref(false);
let stopLive: (() => void) | null = null;

const latestAnalysisEvent = computed(() =>
  [...detailEvents.value].reverse().find((event) => Array.isArray(event.payload.frames)),
);

async function loadStreams(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ streams: StreamSummary[] }>("/v1/streams?limit=50");
    streams.value = payload.streams;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "视频流加载失败");
  } finally {
    loading.value = false;
  }
}

function addMetadataRow(): void {
  metadataRows.value = [...metadataRows.value, { key: "", value: "" }];
}

function removeMetadataRow(index: number): void {
  metadataRows.value = metadataRows.value.filter((_, rowIndex) => rowIndex !== index);
}

function collectedMetadata(): Record<string, string> {
  const metadata: Record<string, string> = {};
  for (const row of metadataRows.value) {
    const key = row.key.trim();
    if (key) metadata[key] = row.value;
  }
  return metadata;
}

async function createStream(): Promise<void> {
  createLoading.value = true;
  try {
    const payload = await apiRequest<{ stream: StreamDetail }>("/v1/streams", {
      method: "POST",
      body: jsonBody({
        stream_url: streamUrl.value,
        name: streamName.value || null,
        settings: {},
        metadata: collectedMetadata(),
      }),
    });
    createOpen.value = false;
    streamName.value = "";
    streamUrl.value = "";
    metadataRows.value = [];
    await loadStreams();
    await openDetail(payload.stream.stream_id);
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "视频流注册失败");
  } finally {
    createLoading.value = false;
  }
}

async function changeState(stream: StreamSummary, action: "start" | "stop"): Promise<void> {
  try {
    const payload = await apiRequest<{ stream: StreamDetail }>(
      `/v1/streams/${encodeURIComponent(stream.stream_id)}/${action}`,
      { method: "POST" },
    );
    await loadStreams();
    if (detail.value?.stream_id === stream.stream_id) applyDetail(payload.stream);
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "视频流状态更新失败");
  }
}

function applyDetail(stream: StreamDetail): void {
  detail.value = stream;
  if (Array.isArray(stream.events)) detailEvents.value = stream.events;
  streams.value = streams.value.map((item) =>
    item.stream_id === stream.stream_id ? { ...item, ...stream, events: undefined } : item,
  );
}

function applyLivePayload(payload: unknown): void {
  if (!payload || typeof payload !== "object" || !("stream" in payload)) return;
  const live = payload as { stream: StreamDetail; events?: StreamEvent[] };
  applyDetail(live.stream);
  if (Array.isArray(live.events)) detailEvents.value = live.events;
}

async function refreshDetail(streamId: string): Promise<void> {
  const payload = await apiRequest<{ stream: StreamDetail }>(
    `/v1/streams/${encodeURIComponent(streamId)}`,
  );
  applyLivePayload({ stream: payload.stream });
}

async function startLive(stream: StreamSummary): Promise<void> {
  stopLive?.();
  stopLive = null;
  stopLive = await openTicketWebSocket({
    resourceType: "stream",
    resourceId: stream.stream_id,
    onMessage: applyLivePayload,
    onState: (state) => (liveState.value = state),
    poll: () => refreshDetail(stream.stream_id),
  });
}

async function openDetail(streamId: string): Promise<void> {
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ stream: StreamDetail }>(
      `/v1/streams/${encodeURIComponent(streamId)}`,
    );
    applyDetail(payload.stream);
    await router.replace(`/analysis/stream/${encodeURIComponent(streamId)}`);
    await startLive(payload.stream);
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "视频流详情加载失败");
  }
}

function closeDetail(): void {
  stopLive?.();
  stopLive = null;
  detail.value = null;
  detailEvents.value = [];
  liveState.value = "closed";
  void router.replace("/analysis/stream");
}

onMounted(async () => {
  await loadStreams();
  if (typeof route.params.streamId === "string") await openDetail(route.params.streamId);
});

onBeforeUnmount(() => {
  stopLive?.();
});
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>实时视频流</h1>
        <p>集中管理流地址、运行状态和最近事件。</p>
      </div>
      <div class="page-actions">
        <ElButton :icon="RefreshCw" :loading="loading" @click="loadStreams">刷新</ElButton
        ><ElButton
          v-if="capabilities.hasPermission('streams')"
          type="primary"
          :icon="Plus"
          @click="createOpen = true"
          >注册视频流</ElButton
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
      <ElSkeleton :loading="loading" animated :rows="6"
        ><EmptyState
          v-if="streams.length === 0"
          title="还没有视频流"
          action-label="注册视频流"
          @action="createOpen = true"
        />
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>名称</th>
                <th>地址</th>
                <th>状态</th>
                <th>事件</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="stream in streams" :key="stream.stream_id">
                <td>
                  <strong>{{ stream.name || stream.stream_id }}</strong>
                </td>
                <td>{{ stream.stream_url }}</td>
                <td>
                  <span class="status-pill" :data-status="stream.status">{{
                    statusLabel(stream.status)
                  }}</span>
                </td>
                <td>{{ stream.event_count }}</td>
                <td>{{ formatTimestamp(stream.updated_at) }}</td>
                <td>
                  <div class="inline-actions">
                    <ElButton text :icon="Eye" @click="openDetail(stream.stream_id)">详情</ElButton
                    ><ElButton
                      v-if="stream.status !== 'running' && capabilities.hasPermission('streams')"
                      text
                      :icon="Play"
                      @click="changeState(stream, 'start')"
                      >启动</ElButton
                    ><ElButton
                      v-if="stream.status === 'running' && capabilities.hasPermission('streams')"
                      text
                      :icon="Pause"
                      @click="changeState(stream, 'stop')"
                      >停止</ElButton
                    >
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div></ElSkeleton
      >
    </section>
    <ElDialog v-model="createOpen" title="注册视频流" width="min(560px, 92vw)" :close-on-click-modal="false"
      ><div class="stream-form">
        <label><span>名称</span><ElInput v-model="streamName" maxlength="256" /></label
        ><label><span>流地址</span><ElInput v-model="streamUrl" placeholder="rtsp:// 或 https://" /></label>
        <div class="metadata-heading">
          <span>业务元数据</span><ElButton text :icon="Plus" @click="addMetadataRow">添加字段</ElButton>
        </div>
        <div v-if="metadataRows.length" class="metadata-rows">
          <div v-for="(row, index) in metadataRows" :key="index">
            <ElInput v-model="row.key" placeholder="字段名" maxlength="128" />
            <ElInput v-model="row.value" placeholder="字段值" maxlength="512" />
            <ElButton :icon="Trash2" aria-label="删除元数据字段" @click="removeMetadataRow(index)" />
          </div>
        </div>
      </div>
      <template #footer
        ><ElButton @click="createOpen = false">取消</ElButton
        ><ElButton
          type="primary"
          :disabled="streamUrl.length < 3"
          :loading="createLoading"
          @click="createStream"
          >注册</ElButton
        ></template
      ></ElDialog
    >
    <ElDrawer
      :model-value="Boolean(detail)"
      size="min(760px, 94vw)"
      title="视频流详情"
      @update:model-value="!$event && closeDetail()"
    >
      <template v-if="detail"
        ><div class="detail-head">
          <div>
            <span>视频流</span><strong>{{ detail.name || detail.stream_id }}</strong>
          </div>
          <span class="status-pill" :data-status="detail.status">{{
            statusLabel(detail.status)
          }}</span>
        </div>
        <dl class="detail-grid">
          <div>
            <dt>流地址</dt>
            <dd class="stream-url">{{ detail.stream_url }}</dd>
          </div>
          <div>
            <dt>实时连接</dt>
            <dd
              role="status"
              aria-live="polite"
            >
              {{ liveState === "open" ? "已连接" : liveState === "degraded" ? "已降级" : "连接中" }}
            </dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{{ formatTimestamp(detail.created_at) }}</dd>
          </div>
          <div>
            <dt>更新时间</dt>
            <dd>{{ formatTimestamp(detail.updated_at) }}</dd>
          </div>
        </dl>
        <section v-if="Object.keys(detail.metadata ?? {}).length" class="metadata-view">
          <h3>业务元数据</h3>
          <dl>
            <div v-for="(value, key) in detail.metadata" :key="key">
              <dt>{{ key }}</dt>
              <dd>{{ value }}</dd>
            </div>
          </dl>
        </section>
        <FrameGrid v-if="latestAnalysisEvent" :data="latestAnalysisEvent.payload" title="实时解析帧" />
        <section class="event-timeline">
          <h3>事件时间线</h3>
          <ol v-if="detailEvents.length" aria-live="polite">
            <li v-for="event in [...detailEvents].reverse().slice(0, 50)" :key="event.event_id">
              <time>{{ formatTimestamp(event.created_at) }}</time>
              <strong>{{ eventLabel(event.type) }}</strong>
              <code v-if="prefs.developerMode">{{ event.type }}</code>
            </li>
          </ol>
          <p v-else class="event-empty">暂无事件，流启动后会自动追加。</p>
        </section>
        <div class="drawer-actions">
          <ElButton
            v-if="detail.status !== 'running' && capabilities.hasPermission('streams')"
            :icon="Play"
            @click="changeState(detail, 'start')"
            >启动</ElButton
          ><ElButton
            v-if="detail.status === 'running' && capabilities.hasPermission('streams')"
            :icon="Pause"
            @click="changeState(detail, 'stop')"
            >停止</ElButton
          ><ElButton v-if="prefs.developerMode" :icon="Code2" @click="rawOpen = true">原始数据</ElButton>
        </div></template
      >
    </ElDrawer>
    <RawDataDrawer v-model="rawOpen" :data="{ stream: detail, events: detailEvents }" />
  </div>
</template>

<style scoped>
.stream-form {
  display: grid;
  gap: 16px;
}
.stream-form label {
  display: grid;
  gap: 7px;
  color: #62706d;
  font-size: 13px;
}
.metadata-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #62706d;
  font-size: 13px;
}
.metadata-rows {
  display: grid;
  gap: 9px;
}
.metadata-rows > div {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 9px;
}
.data-table td:nth-child(2) {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.detail-grid .stream-url {
  overflow-wrap: anywhere;
}
.metadata-view {
  margin-bottom: 18px;
}
.metadata-view h3,
.event-timeline h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.metadata-view dl {
  display: grid;
  gap: 1px;
  margin: 0;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.metadata-view dl div {
  display: grid;
  grid-template-columns: minmax(0, 180px) 1fr;
  gap: 12px;
  padding: 9px 13px;
  background: #fff;
}
.metadata-view dt {
  color: #62706d;
  font-size: 12px;
}
.metadata-view dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.event-timeline {
  margin-top: 18px;
}
.event-timeline ol {
  display: grid;
  gap: 9px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.event-timeline li {
  display: grid;
  grid-template-columns: 150px 1fr auto;
  gap: 12px;
  align-items: baseline;
  padding: 9px 13px;
  background: #f8faf9;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.event-timeline time,
.event-timeline code {
  color: #62706d;
  font-size: 12px;
}
.event-empty {
  margin: 0;
  padding: 16px;
  color: #62706d;
  border: 1px dashed #c6d0ce;
  border-radius: 5px;
}
.drawer-actions {
  display: flex;
  gap: 9px;
  justify-content: flex-end;
  margin-top: 18px;
}
@media (max-width: 767px) {
  .event-timeline li {
    grid-template-columns: 1fr;
    gap: 3px;
  }
}
</style>
