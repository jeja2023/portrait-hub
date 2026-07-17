<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Play } from "@lucide/vue";
import { ElAlert, ElButton, ElOption, ElSelect, ElSkeleton, ElTabPane, ElTabs } from "element-plus";

import { ApiError, apiRequest } from "../../api/client";
import { redactForDisplay } from "../../utils/redact";

const endpoint = ref("/v1/models");
const loading = ref(false);
const errorMessage = ref("");
const response = ref<unknown>(null);
const referenceLoading = ref(false);
const errorCodes = ref<Record<string, unknown>[]>([]);
const openapiPaths = ref<Array<{ method: string; path: string; summary: string }>>([]);
const tab = ref("debug");
const options: Array<{ value: string; label: string }> = [
  { value: "/v1/models", label: "模型列表" },
  { value: "/v1/thresholds", label: "阈值方案" },
  { value: "/v1/admin/status", label: "平台状态" },
  { value: "/v1/access/error-codes", label: "错误码目录" },
];
const formatted = computed(() => JSON.stringify(redactForDisplay(response.value), null, 2));
const sdkExamples = [
  {
    language: "Python",
    code: "from portrait_hub import PortraitHubClient\n\nclient = PortraitHubClient(api_key=api_key, tenant_id=tenant_id)\nresult = client.search(image_path)",
  },
  {
    language: "Node.js",
    code: "const client = new PortraitHubClient({ apiKey, tenantId });\nconst result = await client.search(image);",
  },
  {
    language: "cURL",
    code: 'curl -H "X-API-Key: $API_KEY" -H "X-Tenant-ID: $TENANT_ID" https://host/v1/models',
  },
];
async function loadReference(value: string): Promise<void> {
  if (value === "errors" && errorCodes.value.length === 0) {
    referenceLoading.value = true;
    try {
      const payload = await apiRequest<{ error_codes: Record<string, unknown>[] }>("/v1/access/error-codes");
      errorCodes.value = payload.error_codes;
    } catch (error) {
      errorMessage.value = error instanceof ApiError ? error.message : "错误码目录加载失败";
    } finally {
      referenceLoading.value = false;
    }
  }
  if (value === "openapi" && openapiPaths.value.length === 0) {
    referenceLoading.value = true;
    try {
      const specification = await apiRequest<{ paths?: Record<string, Record<string, unknown>> }>(
        "/openapi.json",
      );
      openapiPaths.value = Object.entries(specification.paths ?? {}).flatMap(([path, methods]) =>
        Object.entries(methods)
          .filter(([method]) => ["get", "post", "put", "patch", "delete"].includes(method))
          .map(([method, operation]) => ({
            method: method.toUpperCase(),
            path,
            summary:
              operation && typeof operation === "object" && "summary" in operation
                ? String((operation as Record<string, unknown>).summary ?? "")
                : "",
          })),
      );
    } catch (error) {
      errorMessage.value = error instanceof ApiError ? error.message : "OpenAPI 定义加载失败";
    } finally {
      referenceLoading.value = false;
    }
  }
}

watch(tab, (value) => void loadReference(value));
async function execute(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    response.value = await apiRequest(endpoint.value);
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError
        ? `${error.message}${error.requestId ? ` · ${error.requestId}` : ""}`
        : "请求失败";
  } finally {
    loading.value = false;
  }
}
</script>
<template>
  <div>
    <header class="page-header">
      <div>
        <h1>调试台</h1>
        <p>按业务能力构造请求，并查看经过脱敏的响应。</p>
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
      <ElTabs v-model="tab" class="page-tabs"
        ><ElTabPane label="调试" name="debug"
          ><div class="playground">
            <div class="request-builder">
              <label
                ><span>接口</span
                ><ElSelect v-model="endpoint"
                  ><ElOption
                    v-for="item in options"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value" /></ElSelect></label
              ><ElButton type="primary" :icon="Play" :loading="loading" @click="execute">发送请求</ElButton>
            </div>
            <pre class="response-code">{{ response === null ? "等待请求" : formatted }}</pre>
          </div></ElTabPane
        ><ElTabPane label="SDK 示例" name="sdk">
          <div class="sdk-grid">
            <article v-for="example in sdkExamples" :key="example.language">
              <h3>{{ example.language }}</h3>
              <pre class="reference-code">{{ example.code }}</pre>
            </article>
          </div></ElTabPane
        ><ElTabPane label="错误码" name="errors">
          <ElSkeleton :loading="referenceLoading" :rows="6" animated>
            <div class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>错误码</th>
                    <th>HTTP</th>
                    <th>可重试</th>
                    <th>说明</th>
                    <th>处理建议</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in errorCodes" :key="String(item.code)">
                    <td>
                      <code>{{ item.code }}</code>
                    </td>
                    <td>{{ item.http_status }}</td>
                    <td>{{ item.retryable ? "是" : "否" }}</td>
                    <td>{{ item.description }}</td>
                    <td>{{ item.operator_action }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </ElSkeleton> </ElTabPane
        ><ElTabPane label="OpenAPI" name="openapi">
          <ElSkeleton :loading="referenceLoading" :rows="6" animated>
            <div class="reference-toolbar">
              <span>共 {{ openapiPaths.length }} 个接口操作</span>
              <a href="/openapi.json" target="_blank" rel="noreferrer">打开完整定义</a>
            </div>
            <div class="table-wrap openapi-table">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>方法</th>
                    <th>路径</th>
                    <th>摘要</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in openapiPaths" :key="item.method + item.path">
                    <td>
                      <code>{{ item.method }}</code>
                    </td>
                    <td>
                      <code>{{ item.path }}</code>
                    </td>
                    <td>{{ item.summary || "--" }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </ElSkeleton>
        </ElTabPane></ElTabs
      >
    </section>
  </div>
</template>
<style scoped>
.page-tabs {
  padding: 0 14px 14px;
}
.playground {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 14px;
}
.request-builder {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  background: #f6f8f7;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.request-builder label {
  display: grid;
  gap: 7px;
  color: #62706d;
  font-size: 13px;
}
.response-code {
  min-height: 360px;
  margin: 0;
  overflow: auto;
  padding: 16px;
  color: #d9ebe7;
  background: #17201f;
  border-radius: 4px;
  font:
    12px/1.65 "Cascadia Code",
    Consolas,
    monospace;
  white-space: pre-wrap;
}
.sdk-grid {
  display: grid;
  gap: 12px;
}
.sdk-grid article {
  min-width: 0;
}
.sdk-grid h3 {
  margin: 0 0 6px;
  font-size: 14px;
}
.reference-code {
  margin: 0;
  overflow: auto;
  padding: 14px;
  color: #d9ebe7;
  background: #17201f;
  border-radius: 4px;
  font:
    12px/1.65 "Cascadia Code",
    Consolas,
    monospace;
  white-space: pre-wrap;
}
.reference-toolbar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0 12px;
  color: #62706d;
  font-size: 13px;
}
.openapi-table {
  max-height: 520px;
}
.tab-note {
  color: #62706d;
}
@media (max-width: 800px) {
  .playground {
    grid-template-columns: 1fr;
  }
}
</style>
