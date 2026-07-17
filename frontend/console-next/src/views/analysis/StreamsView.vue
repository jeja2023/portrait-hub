<script setup lang="ts">
import { onMounted, ref } from "vue";
import { Pause, Play, Plus, RefreshCw } from "@lucide/vue";
import { ElAlert, ElButton, ElDialog, ElInput, ElSkeleton } from "element-plus";

import { ApiError, apiRequest, jsonBody } from "../../api/client";
import AnalysisNavigation from "../../components/AnalysisNavigation.vue";
import EmptyState from "../../components/EmptyState.vue";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { formatTimestamp, statusLabels } from "../../utils/format";

interface StreamSummary {
  stream_id: string;
  stream_url: string;
  name: string | null;
  status: string;
  created_at: number;
  updated_at: number;
  event_count: number;
}

const capabilities = useCapabilitiesStore();
const streams = ref<StreamSummary[]>([]);
const loading = ref(true);
const errorMessage = ref("");
const createOpen = ref(false);
const createLoading = ref(false);
const streamName = ref("");
const streamUrl = ref("");

async function loadStreams(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const payload = await apiRequest<{ streams: StreamSummary[] }>("/v1/streams?limit=50");
    streams.value = payload.streams;
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "视频流加载失败";
  } finally {
    loading.value = false;
  }
}

async function createStream(): Promise<void> {
  createLoading.value = true;
  try {
    await apiRequest("/v1/streams", {
      method: "POST",
      body: jsonBody({
        stream_url: streamUrl.value,
        name: streamName.value || null,
        settings: {},
        metadata: {},
      }),
    });
    createOpen.value = false;
    streamName.value = "";
    streamUrl.value = "";
    await loadStreams();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "视频流注册失败";
  } finally {
    createLoading.value = false;
  }
}

async function changeState(stream: StreamSummary, action: "start" | "stop"): Promise<void> {
  try {
    await apiRequest(`/v1/streams/${encodeURIComponent(stream.stream_id)}/${action}`, { method: "POST" });
    await loadStreams();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "视频流状态更新失败";
  }
}

onMounted(() => void loadStreams());
</script>

<template>
  <div>
    <AnalysisNavigation />
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
                    statusLabels[stream.status] ?? stream.status
                  }}</span>
                </td>
                <td>{{ stream.event_count }}</td>
                <td>{{ formatTimestamp(stream.updated_at) }}</td>
                <td>
                  <div class="inline-actions">
                    <ElButton
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
    <ElDialog v-model="createOpen" title="注册视频流" width="min(520px, 92vw)" :close-on-click-modal="false"
      ><div class="stream-form">
        <label><span>名称</span><ElInput v-model="streamName" maxlength="256" /></label
        ><label><span>流地址</span><ElInput v-model="streamUrl" placeholder="rtsp:// 或 https://" /></label>
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
.data-table td:nth-child(2) {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
