<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import { Code2, ImageUp, Play, RotateCcw } from "@lucide/vue";
import { ElAlert, ElButton, ElCheckbox, ElOption, ElSelect } from "element-plus";

import { ApiError, apiRequest } from "../../api/client";
import AnalysisNavigation from "../../components/AnalysisNavigation.vue";
import RawDataDrawer from "../../components/RawDataDrawer.vue";
import { usePrefsStore } from "../../stores/prefs";

const prefs = usePrefsStore();
const endpoint = ref("/v1/infer/persons");
const includeEmbeddings = ref(false);
const file = ref<File | null>(null);
const previewUrl = ref("");
const loading = ref(false);
const errorMessage = ref("");
const result = ref<Record<string, unknown> | null>(null);
const showRaw = ref(false);

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
    result.value = await apiRequest<Record<string, unknown>>(
      endpoint.value,
      { method: "POST", body },
      60_000,
    );
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "图片分析失败";
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

onBeforeUnmount(() => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
});
</script>

<template>
  <div>
    <AnalysisNavigation />
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
          <p v-if="summary.length === 0">分析已完成，可在开发者模式查看脱敏详情。</p>
        </div>
        <div v-else class="result-empty"><ImageUp :size="34" /><span>结果将在这里显示</span></div>
      </section>
    </div>
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
@media (max-width: 900px) {
  .analysis-grid {
    grid-template-columns: 1fr;
  }
}
</style>
