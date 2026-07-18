<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { Activity, CircleGauge, Clock3, Cpu, Flame, RefreshCw, Search, Timer, Users } from "@lucide/vue";
import { ElAlert, ElButton, ElSkeleton } from "element-plus";

import { ApiError, apiRequest, apiText } from "../api/client";
import RawDataDrawer from "../components/RawDataDrawer.vue";
import StatCard from "../components/StatCard.vue";
import { useCapabilitiesStore } from "../stores/capabilities";
import { usePrefsStore } from "../stores/prefs";
import {
  SLO_WINDOW_SECONDS,
  deviceQueueDepths,
  histogramPercentile,
  metricValue,
  summarizeSloCallLogs,
  summarizeSloMetrics,
  type SloCallLog,
} from "../utils/slo";

const router = useRouter();
const prefs = usePrefsStore();
const capabilities = useCapabilitiesStore();
const loading = ref(true);
const errorMessage = ref("");
const ready = ref<Record<string, unknown> | null>(null);
const rawMetrics = ref("");
const callLogs30d = ref<SloCallLog[]>([]);
const callLogsAvailable = ref(false);
const showRaw = ref(false);

const metricAvailability = computed(() => summarizeSloMetrics(rawMetrics.value));
const availability = computed(() =>
  callLogsAvailable.value ? summarizeSloCallLogs(callLogs30d.value) : metricAvailability.value,
);
const successRateSource = computed(() => (callLogsAvailable.value ? "call_logs_30d" : "prometheus_counters"));
const requestCount = computed(() => availability.value.request_count);
const errorCount = computed(() => availability.value.error_count);
const queueDepth = computed(() => metricValue(rawMetrics.value, "gpu_worker_gpu_queue_depth"));
const queueP95 = computed(() => histogramPercentile(rawMetrics.value, "gpu_worker_queue_seconds", 0.95));
const queueP99 = computed(() => histogramPercentile(rawMetrics.value, "gpu_worker_queue_seconds", 0.99));
const inferenceP95 = computed(() => histogramPercentile(rawMetrics.value, "gpu_worker_inference_seconds", 0.95));
const inferenceP99 = computed(() => histogramPercentile(rawMetrics.value, "gpu_worker_inference_seconds", 0.99));
const gpuDeviceQueueDepths = computed(() => deviceQueueDepths(rawMetrics.value));
const sloPanel = computed(() => ({
  success_rate_source: successRateSource.value,
  call_logs_30d: callLogs30d.value.length,
  call_log_window_seconds: SLO_WINDOW_SECONDS,
  ...availability.value,
  inference_p95_seconds: inferenceP95.value,
  inference_p99_seconds: inferenceP99.value,
  queue_p95_seconds: queueP95.value,
  queue_p99_seconds: queueP99.value,
  gpu_queue_depth: queueDepth.value,
  gpu_device_queue_depths: gpuDeviceQueueDepths.value,
}));
const isReady = computed(() => ready.value?.status === "ready" || ready.value?.ready === true);

function duration(value: number): string {
  if (value === Number.POSITIVE_INFINITY) return ">10 s";
  return value.toFixed(3) + " s";
}

function readinessFromError(error: unknown): Record<string, unknown> | null {
  if (!(error instanceof ApiError) || error.status !== 503) return null;
  if (error.details && typeof error.details === "object") return error.details as Record<string, unknown>;
  return { status: "not_ready" };
}

async function refresh(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  const callLogPath =
    "/v1/access/call-logs?limit=500&created_since=" +
    (Math.floor(Date.now() / 1000) - SLO_WINDOW_SECONDS);
  const [readyResult, metricsResult, callLogsResult] = await Promise.allSettled([
    apiRequest<Record<string, unknown>>("/ready/deep"),
    apiText("/metrics"),
    capabilities.hasPermission("access:read")
      ? apiRequest<{ logs: SloCallLog[] }>(callLogPath)
      : Promise.reject(new Error("access:read unavailable")),
  ]);

  if (readyResult.status === "fulfilled") {
    ready.value = readyResult.value;
  } else {
    const degradedReady = readinessFromError(readyResult.reason);
    if (degradedReady) ready.value = degradedReady;
    else errorMessage.value = readyResult.reason instanceof ApiError ? readyResult.reason.message : "平台状态加载失败";
  }

  if (metricsResult.status === "fulfilled") {
    rawMetrics.value = metricsResult.value;
  } else if (!errorMessage.value) {
    errorMessage.value = metricsResult.reason instanceof ApiError ? metricsResult.reason.message : "平台指标加载失败";
  }

  if (callLogsResult.status === "fulfilled" && callLogsResult.value.logs.length > 0) {
    callLogs30d.value = callLogsResult.value.logs;
    callLogsAvailable.value = true;
  } else {
    callLogs30d.value = [];
    callLogsAvailable.value = false;
  }

  loading.value = false;
}

onMounted(() => void refresh());
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>总览</h1>
        <p>当前租户的服务状态、SLO、调用情况与待处理资源。</p>
      </div>
      <div class="page-actions">
        <ElButton :icon="RefreshCw" :loading="loading" @click="refresh">刷新</ElButton>
        <ElButton v-if="prefs.developerMode" @click="showRaw = true">原始数据</ElButton>
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
    <ElSkeleton :loading="loading" animated :rows="5">
      <div class="stat-grid">
        <StatCard
          label="平台状态"
          :value="isReady ? '就绪' : '需检查'"
          :tone="isReady ? 'success' : 'warning'"
          :icon="Activity"
        />
        <StatCard label="统计请求" :value="requestCount.toLocaleString('zh-CN')" :icon="CircleGauge" />
        <StatCard
          label="30 天成功率"
          :value="availability.success_rate.toLocaleString('zh-CN', { style: 'percent', maximumFractionDigits: 3 })"
          :tone="availability.success_rate < 0.995 ? 'danger' : 'success'"
          :icon="Clock3"
          :detail="successRateSource === 'call_logs_30d' ? '调用日志窗口' : 'Prometheus 累计回退'"
        />
        <StatCard
          label="错误预算剩余"
          :value="availability.error_budget_remaining.toLocaleString('zh-CN', { style: 'percent', maximumFractionDigits: 1 })"
          :tone="availability.error_budget_remaining < 0.2 ? 'danger' : 'success'"
          :icon="Timer"
          :detail="errorCount + ' 个失败请求'"
        />
        <StatCard
          label="预算燃尽率"
          :value="availability.error_budget_burn_rate.toFixed(2) + 'x'"
          :tone="availability.error_budget_burn_rate > 1 ? 'danger' : 'success'"
          :icon="Flame"
        />
        <StatCard label="推理 P95" :value="duration(inferenceP95)" :icon="Clock3" />
        <StatCard label="推理 P99" :value="duration(inferenceP99)" :icon="Clock3" />
        <StatCard label="排队 P95" :value="duration(queueP95)" :icon="Timer" />
        <StatCard label="排队 P99" :value="duration(queueP99)" :icon="Timer" />
        <StatCard
          label="GPU 队列"
          :value="queueDepth.toLocaleString('zh-CN')"
          :tone="queueDepth > 10 ? 'warning' : 'neutral'"
          :icon="Cpu"
          :detail="Object.keys(gpuDeviceQueueDepths).length + ' 个设备'"
        />
      </div>
      <section class="quick-actions" aria-labelledby="quick-title">
        <h2 id="quick-title" class="section-title">常用操作</h2>
        <div class="quick-grid">
          <button type="button" @click="router.push('/analysis/image')">
            <Activity :size="21" /><span>图片分析</span>
          </button>
          <button type="button" @click="router.push('/search')">
            <Search :size="21" /><span>以图搜人</span>
          </button>
          <button type="button" @click="router.push('/gallery')">
            <Users :size="21" /><span>人员库</span>
          </button>
        </div>
      </section>
    </ElSkeleton>
    <RawDataDrawer
      v-model="showRaw"
      title="平台状态与 SLO 原始数据（已脱敏）"
      :data="{ ready, slo: sloPanel, metrics: rawMetrics }"
    />
  </div>
</template>

<style scoped>
.quick-actions {
  margin-top: 28px;
}
.quick-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.quick-grid button {
  min-height: 78px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 16px;
  color: #1c2826;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
  cursor: pointer;
  text-align: left;
}
.quick-grid button:hover {
  border-color: #087682;
  color: #075f69;
}
@media (max-width: 700px) {
  .quick-grid {
    grid-template-columns: 1fr;
  }
}
</style>