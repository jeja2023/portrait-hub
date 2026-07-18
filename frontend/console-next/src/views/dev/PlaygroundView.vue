<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Copy, Play } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElSkeleton,
  ElSwitch,
  ElTabPane,
  ElTabs,
  ElTag,
} from "element-plus";

import { ApiError, apiRaw, apiRequest, jsonBody } from "../../api/client";
import { redactForDisplay } from "../../utils/redact";

type EndpointKind = "read" | "batch-search" | "batch-compare" | "stream-create" | "stream-events";
interface EndpointDefinition {
  value: string;
  label: string;
  method: "GET" | "POST";
  kind: EndpointKind;
  controlledUse: string;
}
interface Diagnostics {
  endpoint_template: string;
  endpoint: string;
  method: string;
  http_status: number | null;
  error_code: string | null;
  request_id: string | null;
  controlled_use: string;
}
interface SdkExample {
  id: string;
  title: string;
  language: string;
  code: string;
}

const options: EndpointDefinition[] = [
  { value: "/v1/models", label: "模型列表", method: "GET", kind: "read", controlledUse: "只读" },
  { value: "/v1/thresholds", label: "阈值方案", method: "GET", kind: "read", controlledUse: "只读" },
  { value: "/v1/admin/status", label: "平台状态", method: "GET", kind: "read", controlledUse: "只读" },
  { value: "/v1/access/error-codes", label: "错误码目录", method: "GET", kind: "read", controlledUse: "只读" },
  {
    value: "/v1/gallery/search/batch",
    label: "批量以图搜人",
    method: "POST",
    kind: "batch-search",
    controlledUse: "上传图片并执行检索",
  },
  {
    value: "/v1/compare/batch",
    label: "批量图片比对",
    method: "POST",
    kind: "batch-compare",
    controlledUse: "上传两组图片并执行比对",
  },
  {
    value: "/v1/streams",
    label: "注册视频流",
    method: "POST",
    kind: "stream-create",
    controlledUse: "创建资源",
  },
  { value: "/v1/streams", label: "视频流列表", method: "GET", kind: "read", controlledUse: "只读" },
  {
    value: "/v1/streams/{stream_id}/events",
    label: "视频流事件",
    method: "GET",
    kind: "stream-events",
    controlledUse: "只读",
  },
];

const endpointIndex = ref(0);
const defaultEndpoint = options[0]!;
const selectedEndpoint = computed<EndpointDefinition>(() => options[endpointIndex.value] ?? defaultEndpoint);
const endpointTemplate = computed(() => selectedEndpoint.value.value);
const loading = ref(false);
const errorMessage = ref("");
const response = ref<unknown>(null);
const diagnostics = ref<Diagnostics | null>(null);
const referenceLoading = ref(false);
const errorCodes = ref<Record<string, unknown>[]>([]);
const openapiPaths = ref<Array<{ method: string; path: string; summary: string }>>([]);
const tab = ref("debug");
const filesA = ref<File[]>([]);
const filesB = ref<File[]>([]);
const modality = ref("body");
const thresholdProfile = ref("normal");
const topK = ref(5);
const asyncMode = ref(false);
const streamId = ref("");
const streamUrl = ref("");
const streamName = ref("");

const formatted = computed(() =>
  JSON.stringify(redactForDisplay({ diagnostics: diagnostics.value, response: response.value }), null, 2),
);
const canExecute = computed(() => {
  if (selectedEndpoint.value.kind === "batch-search") return filesA.value.length > 0;
  if (selectedEndpoint.value.kind === "batch-compare") {
    return filesA.value.length > 0 && filesA.value.length === filesB.value.length;
  }
  if (selectedEndpoint.value.kind === "stream-create") return streamUrl.value.trim().length > 0;
  if (selectedEndpoint.value.kind === "stream-events") return streamId.value.trim().length > 0;
  return true;
});

const sdkExamples: SdkExample[] = [
  {
    id: "batch-python",
    title: "异步批量检索",
    language: "Python",
    code: [
      "client = PortraitHubClient(api_key=api_key, tenant_id=tenant_id)",
      "submitted = client.search_batch(images, async_mode=True)",
      "result = client.job_result(submitted['batch_id'])",
    ].join("\n"),
  },
  {
    id: "batch-node",
    title: "异步批量比对",
    language: "Node.js",
    code: [
      "const client = new PortraitHubClient({ apiKey, tenantId });",
      "const submitted = await client.compareBatch(imagesA, imagesB, { asyncMode: true });",
      "const result = await client.jobResult(submitted.batch_id);",
    ].join("\n"),
  },
  {
    id: "video-python",
    title: "离线视频任务",
    language: "Python",
    code: [
      "job = client.create_video_job(video_path, sample_interval_seconds=1, batch_size=16)",
      "result = client.job_result(job['job']['job_id'])",
    ].join("\n"),
  },
  {
    id: "video-node",
    title: "离线视频任务",
    language: "Node.js",
    code: [
      "const job = await client.createVideoJob(videoPath, { sampleIntervalSeconds: 1, batchSize: 16 });",
      "const result = await client.jobResult(job.job.job_id);",
    ].join("\n"),
  },
];

function selectFiles(side: "a" | "b", event: Event): void {
  const selected = Array.from((event.target as HTMLInputElement).files ?? []);
  if (side === "a") filesA.value = selected;
  else filesB.value = selected;
  response.value = null;
  diagnostics.value = null;
}

function appendFiles(form: FormData, field: string, files: File[]): void {
  for (const file of files) form.append(field, file);
}

function resetRequestState(): void {
  filesA.value = [];
  filesB.value = [];
  response.value = null;
  diagnostics.value = null;
  errorMessage.value = "";
}

watch(endpointIndex, resetRequestState);

async function copyExample(example: SdkExample): Promise<void> {
  try {
    await navigator.clipboard.writeText(example.code);
    ElMessage.success("SDK 示例已复制");
  } catch {
    ElMessage.error("复制失败，请检查浏览器剪贴板权限");
  }
}

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
      const specification = await apiRequest<{ paths?: Record<string, Record<string, unknown>> }>("/openapi.json");
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
  if (!canExecute.value) return;
  loading.value = true;
  errorMessage.value = "";
  response.value = null;
  const selection = selectedEndpoint.value;
  let path = selection.value;
  const init: { method: string; body?: FormData | string } = { method: selection.method };

  if (selection.kind === "batch-search" || selection.kind === "batch-compare") {
    const form = new FormData();
    form.append("modality", modality.value);
    form.append("threshold_profile", thresholdProfile.value);
    form.append("async_mode", String(asyncMode.value));
    if (selection.kind === "batch-search") {
      form.append("top_k", String(topK.value));
      appendFiles(form, "files", filesA.value);
    } else {
      appendFiles(form, "image_a", filesA.value);
      appendFiles(form, "image_b", filesB.value);
    }
    init.body = form;
  } else if (selection.kind === "stream-create") {
    init.body = jsonBody({
      stream_url: streamUrl.value.trim(),
      name: streamName.value.trim() || null,
      settings: {},
      metadata: {},
    });
  } else if (selection.kind === "stream-events") {
    path = selection.value.replace("{stream_id}", encodeURIComponent(streamId.value.trim())) + "?limit=50";
  } else if (selection.value === "/v1/streams") {
    path += "?limit=50";
  }

  try {
    const result = await apiRaw<unknown>(path, init, 180_000);
    diagnostics.value = {
      endpoint_template: endpointTemplate.value,
      endpoint: path,
      method: selection.method,
      http_status: result.httpStatus,
      error_code: null,
      request_id: result.requestId,
      controlled_use: selection.controlledUse,
    };
    response.value = result.body;
  } catch (error) {
    const apiError = error instanceof ApiError ? error : null;
    diagnostics.value = {
      endpoint_template: endpointTemplate.value,
      endpoint: path,
      method: selection.method,
      http_status: apiError?.status ?? null,
      error_code: apiError?.code ?? null,
      request_id: apiError?.requestId ?? null,
      controlled_use: selection.controlledUse,
    };
    response.value = apiError?.details ?? null;
    errorMessage.value = apiError
      ? apiError.message + (apiError.requestId ? " · " + apiError.requestId : "")
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
        <p>按真实接口契约构造只读、批量与视频流请求，并查看脱敏响应。</p>
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
      <ElTabs v-model="tab" class="page-tabs">
        <ElTabPane label="调试" name="debug">
          <div class="playground">
            <div class="request-builder">
              <label>
                <span>接口</span>
                <ElSelect v-model="endpointIndex">
                  <ElOption
                    v-for="(item, index) in options"
                    :key="item.method + item.value"
                    :label="item.method + ' · ' + item.label"
                    :value="index"
                  />
                </ElSelect>
              </label>
              <div class="endpoint-contract">
                <ElTag size="small" effect="plain">{{ selectedEndpoint.method }}</ElTag>
                <code>{{ endpointTemplate }}</code>
              </div>

              <template v-if="selectedEndpoint.kind === 'batch-search' || selectedEndpoint.kind === 'batch-compare'">
                <label>
                  <span>{{ selectedEndpoint.kind === "batch-search" ? "检索图片" : "左侧图片" }}</span>
                  <input type="file" multiple accept="image/*" @change="selectFiles('a', $event)" />
                  <small>已选择 {{ filesA.length }} 张</small>
                </label>
                <label v-if="selectedEndpoint.kind === 'batch-compare'">
                  <span>右侧图片</span>
                  <input type="file" multiple accept="image/*" @change="selectFiles('b', $event)" />
                  <small>已选择 {{ filesB.length }} 张，数量需与左侧一致</small>
                </label>
                <label>
                  <span>模态</span>
                  <ElSelect v-model="modality">
                    <ElOption label="人体" value="body" />
                    <ElOption label="人脸" value="face" />
                  </ElSelect>
                </label>
                <label>
                  <span>阈值方案</span>
                  <ElSelect v-model="thresholdProfile">
                    <ElOption label="严格" value="strict" />
                    <ElOption label="标准" value="normal" />
                    <ElOption label="宽松" value="loose" />
                  </ElSelect>
                </label>
                <label v-if="selectedEndpoint.kind === 'batch-search'">
                  <span>返回数量</span>
                  <ElInputNumber v-model="topK" :min="1" :max="100" />
                </label>
                <label class="switch-field">
                  <span>异步任务</span>
                  <ElSwitch v-model="asyncMode" />
                </label>
              </template>

              <template v-if="selectedEndpoint.kind === 'stream-create'">
                <label><span>视频流地址</span><ElInput v-model="streamUrl" placeholder="https://..." /></label>
                <label><span>显示名称</span><ElInput v-model="streamName" maxlength="256" /></label>
              </template>

              <label v-if="selectedEndpoint.kind === 'stream-events'">
                <span>视频流 ID</span>
                <ElInput v-model="streamId" maxlength="128" />
              </label>

              <ElButton
                type="primary"
                :icon="Play"
                :loading="loading"
                :disabled="!canExecute"
                @click="execute"
              >
                发送请求
              </ElButton>
            </div>
            <pre class="response-code">{{ response === null && diagnostics === null ? "等待请求" : formatted }}</pre>
          </div>
        </ElTabPane>

        <ElTabPane label="SDK 示例" name="sdk">
          <div class="sdk-grid">
            <article
              v-for="example in sdkExamples"
              :key="example.id"
              class="sdk-example"
              :data-example="example.id"
            >
              <header>
                <div><strong>{{ example.title }}</strong><span>{{ example.language }}</span></div>
                <ElButton text :icon="Copy" @click="copyExample(example)">复制</ElButton>
              </header>
              <pre class="reference-code">{{ example.code }}</pre>
            </article>
          </div>
        </ElTabPane>

        <ElTabPane label="错误码" name="errors">
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
                    <td><code>{{ item.code }}</code></td>
                    <td>{{ item.http_status }}</td>
                    <td>{{ item.retryable ? "是" : "否" }}</td>
                    <td>{{ item.description }}</td>
                    <td>{{ item.operator_action }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </ElSkeleton>
        </ElTabPane>

        <ElTabPane label="OpenAPI" name="openapi">
          <ElSkeleton :loading="referenceLoading" :rows="6" animated>
            <div class="reference-toolbar">
              <span>共 {{ openapiPaths.length }} 个接口操作</span>
              <a href="/openapi.json" target="_blank" rel="noreferrer">打开完整定义</a>
            </div>
            <div class="table-wrap openapi-table">
              <table class="data-table">
                <thead><tr><th>方法</th><th>路径</th><th>摘要</th></tr></thead>
                <tbody>
                  <tr v-for="item in openapiPaths" :key="item.method + item.path">
                    <td><code>{{ item.method }}</code></td>
                    <td><code>{{ item.path }}</code></td>
                    <td>{{ item.summary || "--" }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </ElSkeleton>
        </ElTabPane>
      </ElTabs>
    </section>
  </div>
</template>

<style scoped>
.page-tabs {
  padding: 0 14px 14px;
}
.playground {
  display: grid;
  grid-template-columns: minmax(300px, 380px) minmax(0, 1fr);
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
.request-builder small {
  color: #62706d;
}
.request-builder input[type="file"] {
  width: 100%;
  min-width: 0;
  padding: 8px;
  color: #43514e;
  background: #fff;
  border: 1px solid #c8d3d0;
  border-radius: 4px;
}
.endpoint-contract,
.sdk-example header,
.sdk-example header div,
.switch-field {
  display: flex;
  align-items: center;
}
.endpoint-contract {
  min-width: 0;
  gap: 8px;
}
.endpoint-contract code {
  overflow-wrap: anywhere;
}
.request-builder .switch-field {
  grid-template-columns: 1fr auto;
}
.response-code {
  min-height: 460px;
  margin: 0;
  overflow: auto;
  padding: 16px;
  color: #d9ebe7;
  background: #17201f;
  border-radius: 4px;
  font: 12px/1.65 "Cascadia Code", Consolas, monospace;
  white-space: pre-wrap;
}
.sdk-grid {
  display: grid;
  gap: 12px;
}
.sdk-example {
  min-width: 0;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.sdk-example header {
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  background: #f6f8f7;
  border-bottom: 1px solid #d8e0de;
}
.sdk-example header div {
  gap: 10px;
}
.sdk-example header span {
  color: #62706d;
  font-size: 12px;
}
.reference-code {
  margin: 0;
  overflow: auto;
  padding: 14px;
  color: #d9ebe7;
  background: #17201f;
  font: 12px/1.65 "Cascadia Code", Consolas, monospace;
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
@media (max-width: 800px) {
  .playground {
    grid-template-columns: 1fr;
  }
  .response-code {
    min-height: 320px;
  }
}
</style>