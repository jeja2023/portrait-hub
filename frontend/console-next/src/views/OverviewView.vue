<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { Activity, CircleGauge, Clock3, Cpu, RefreshCw, Search, Users } from "@lucide/vue";
import { ElAlert, ElButton, ElSkeleton } from "element-plus";

import { ApiError, apiRequest, apiText } from "../api/client";
import RawDataDrawer from "../components/RawDataDrawer.vue";
import StatCard from "../components/StatCard.vue";
import { usePrefsStore } from "../stores/prefs";

const router = useRouter();
const prefs = usePrefsStore();
const loading = ref(true);
const errorMessage = ref("");
const ready = ref<Record<string, unknown> | null>(null);
const rawMetrics = ref("");
const showRaw = ref(false);

interface MetricSample {
  labels: string;
  value: number;
}

function metricSamples(name: string): MetricSample[] {
  const prefix = name + " ";
  const labeledPrefix = name + "{";
  return rawMetrics.value.split("\n").flatMap((line) => {
    if (!line.startsWith(prefix) && !line.startsWith(labeledPrefix)) return [];
    const separator = line.lastIndexOf(" ");
    const value = Number(line.slice(separator + 1));
    if (!Number.isFinite(value)) return [];
    const labels = line.startsWith(labeledPrefix) ? line.slice(name.length + 1, line.indexOf("}")) : "";
    return [{ labels, value }];
  });
}

function metricValue(name: string): number {
  return metricSamples(name).find((sample) => sample.labels === "")?.value ?? 0;
}

function histogramPercentile(name: string, percentile: number): number {
  const buckets = metricSamples(name + "_bucket")
    .map((sample) => {
      const match = /(?:^|,)le="([^"]+)"/.exec(sample.labels);
      return {
        boundary: match?.[1] === "+Inf" ? Number.POSITIVE_INFINITY : Number(match?.[1]),
        count: sample.value,
      };
    })
    .filter((bucket) => Number.isFinite(bucket.boundary) || bucket.boundary === Number.POSITIVE_INFINITY)
    .sort((left, right) => left.boundary - right.boundary);
  const total = metricValue(name + "_count");
  if (total <= 0) return 0;
  const target = total * percentile;
  return buckets.find((bucket) => bucket.count >= target)?.boundary ?? 0;
}

const requestCount = computed(() => metricValue("gpu_worker_requests_total"));
const errorCount = computed(() =>
  metricSamples("gpu_worker_requests_total")
    .filter((sample) => sample.labels.includes('status_class="5xx"'))
    .reduce((sum, sample) => sum + sample.value, 0),
);
const queueDepth = computed(() => metricValue("gpu_worker_gpu_queue_depth"));
const inferenceP95 = computed(() => histogramPercentile("gpu_worker_inference_seconds", 0.95));
const inferenceP99 = computed(() => histogramPercentile("gpu_worker_inference_seconds", 0.99));
const errorRate = computed(() => (requestCount.value > 0 ? errorCount.value / requestCount.value : 0));
const isReady = computed(() => ready.value?.status === "ready" || ready.value?.ready === true);

async function refresh(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [readyPayload, metricsPayload] = await Promise.all([
      apiRequest<Record<string, unknown>>("/ready/deep"),
      apiText("/metrics"),
    ]);
    ready.value = readyPayload;
    rawMetrics.value = metricsPayload;
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "平台状态加载失败";
  } finally {
    loading.value = false;
  }
}

onMounted(() => void refresh());
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>总览</h1>
        <p>当前租户的服务状态、调用情况与待处理资源。</p>
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
        <StatCard label="累计请求" :value="requestCount.toLocaleString('zh-CN')" :icon="CircleGauge" />
        <StatCard
          label="错误率"
          :value="errorRate.toLocaleString('zh-CN', { style: 'percent', maximumFractionDigits: 2 })"
          :tone="errorRate > 0.02 ? 'danger' : 'success'"
          :icon="Clock3"
        />
        <StatCard
          label="推理 P95"
          :value="inferenceP95 === Infinity ? '>10 s' : inferenceP95.toFixed(3) + ' s'"
          :icon="Clock3"
        />
        <StatCard
          label="推理 P99"
          :value="inferenceP99 === Infinity ? '>10 s' : inferenceP99.toFixed(3) + ' s'"
          :icon="Clock3"
        />
        <StatCard
          label="GPU 队列"
          :value="queueDepth.toLocaleString('zh-CN')"
          :tone="queueDepth > 10 ? 'warning' : 'neutral'"
          :icon="Cpu"
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
      title="平台状态原始数据（已脱敏）"
      :data="{ ready, metrics: rawMetrics }"
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
