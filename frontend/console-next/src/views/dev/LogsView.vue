<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RefreshCw, Search } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDatePicker,
  ElDialog,
  ElInput,
  ElOption,
  ElSelect,
  ElSkeleton,
} from "element-plus";
import { ApiError, apiRequest } from "../../api/client";
import EmptyState from "../../components/EmptyState.vue";
import { formatTimestamp } from "../../utils/format";
interface LogRow {
  request_id: string;
  endpoint?: string;
  path?: string;
  method?: string;
  status?: string;
  status_code?: number;
  application_id?: string;
  error_code?: string;
  created_at: number;
  duration_ms?: number;
}
const logs = ref<LogRow[]>([]);
const loading = ref(true);
const errorMessage = ref("");
const requestId = ref("");
const status = ref("");
const endpoint = ref("");
const applicationId = ref("");
const errorCode = ref("");
const createdRange = ref<[Date, Date] | null>(null);
const applications = ref<Array<{ app_id: string; name?: string }>>([]);
const errorCatalog = ref<Record<string, unknown>[]>([]);
const selectedErrorCode = ref("");
const selectedError = computed(() =>
  errorCatalog.value.find((item) => item.code === selectedErrorCode.value),
);
async function load(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const params = new URLSearchParams({ limit: "100" });
    if (requestId.value) params.set("request_id", requestId.value);
    if (endpoint.value) params.set("endpoint", endpoint.value);
    if (status.value) params.set("status", status.value);
    if (applicationId.value) params.set("application_id", applicationId.value);
    if (errorCode.value) params.set("error_code", errorCode.value);
    if (createdRange.value) {
      params.set("created_since", String(createdRange.value[0].getTime() / 1000));
      params.set("created_until", String(createdRange.value[1].getTime() / 1000));
    }
    logs.value = (await apiRequest<{ logs: LogRow[] }>(`/v1/access/call-logs?${params}`)).logs;
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "调用日志加载失败";
  } finally {
    loading.value = false;
  }
}
onMounted(async () => {
  const [, applicationsResult, errorsResult] = await Promise.allSettled([
    load(),
    apiRequest<{ applications: Array<{ app_id: string; name?: string }> }>("/v1/access/applications"),
    apiRequest<{ error_codes: Record<string, unknown>[] }>("/v1/access/error-codes"),
  ]);
  if (applicationsResult.status === "fulfilled") applications.value = applicationsResult.value.applications;
  if (errorsResult.status === "fulfilled") errorCatalog.value = errorsResult.value.error_codes;
});
</script>
<template>
  <div>
    <header class="page-header">
      <div>
        <h1>调用日志</h1>
        <p>按请求、状态与应用排查 API 调用。</p>
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
    <div class="log-filters">
      <ElInput
        v-model="requestId"
        clearable
        placeholder="请求 ID"
        :prefix-icon="Search"
        @keyup.enter="load"
      />
      <ElInput v-model="endpoint" clearable placeholder="接口路径" @keyup.enter="load" />
      <ElSelect v-model="status" clearable placeholder="全部状态">
        <ElOption label="成功" value="success" /><ElOption label="失败" value="error" />
      </ElSelect>
      <ElSelect v-model="applicationId" clearable filterable placeholder="全部应用">
        <ElOption
          v-for="application in applications"
          :key="application.app_id"
          :label="application.name || application.app_id"
          :value="application.app_id"
        />
      </ElSelect>
      <ElSelect v-model="errorCode" clearable filterable placeholder="全部错误码">
        <ElOption
          v-for="item in errorCatalog"
          :key="String(item.code)"
          :label="String(item.code)"
          :value="String(item.code)"
        />
      </ElSelect>
      <ElDatePicker
        v-model="createdRange"
        type="datetimerange"
        start-placeholder="起始时间"
        end-placeholder="结束时间"
        range-separator="至"
      />
      <ElButton @click="load">查询</ElButton>
    </div>
    <section class="tool-surface">
      <ElSkeleton :loading="loading" :rows="7" animated
        ><EmptyState v-if="logs.length === 0" title="没有符合条件的调用记录" />
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>请求 ID</th>
                <th>方法/接口</th>
                <th>状态</th>
                <th>应用</th>
                <th>耗时</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="log in logs" :key="`${log.request_id}-${log.created_at}`">
                <td>{{ formatTimestamp(log.created_at) }}</td>
                <td>
                  <code>{{ log.request_id }}</code>
                </td>
                <td>{{ log.method }} {{ log.endpoint || log.path }}</td>
                <td>
                  {{ log.status_code || log.status
                  }}<ElButton
                    v-if="log.error_code"
                    text
                    type="danger"
                    @click="selectedErrorCode = log.error_code"
                    >{{ log.error_code }}</ElButton
                  >
                </td>
                <td>{{ log.application_id || "--" }}</td>
                <td>{{ log.duration_ms == null ? "--" : `${log.duration_ms} ms` }}</td>
              </tr>
            </tbody>
          </table>
        </div></ElSkeleton
      >
    </section>
    <ElDialog
      :model-value="Boolean(selectedErrorCode)"
      :title="selectedErrorCode || '错误码'"
      width="min(520px, 92vw)"
      @update:model-value="!$event && (selectedErrorCode = '')"
    >
      <template v-if="selectedError">
        <p>{{ selectedError.description }}</p>
        <dl class="error-facts">
          <div>
            <dt>HTTP 状态</dt>
            <dd>{{ selectedError.http_status }}</dd>
          </div>
          <div>
            <dt>是否可重试</dt>
            <dd>{{ selectedError.retryable ? "是" : "否" }}</dd>
          </div>
        </dl>
        <ElAlert
          :title="String(selectedError.operator_action || '请携带 request_id 联系平台运维。')"
          type="info"
          :closable="false"
          show-icon
        />
      </template>
      <p v-else>当前部署的错误码目录中没有该项，请携带 request_id 排查。</p>
    </ElDialog>
  </div>
</template>
<style scoped>
.log-filters {
  display: grid;
  grid-template-columns: minmax(180px, 1.2fr) minmax(160px, 1fr) 140px 180px 180px minmax(300px, 1.4fr) auto;
  gap: 8px;
  margin-bottom: 14px;
}
.error-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  margin: 16px 0;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.error-facts div {
  padding: 12px;
  background: #fff;
}
.error-facts dt {
  color: #62706d;
  font-size: 12px;
}
.error-facts dd {
  margin: 5px 0 0;
}
.data-table small {
  color: #b4232f;
}
@media (max-width: 700px) {
  .log-filters {
    grid-template-columns: 1fr;
  }
}
</style>
