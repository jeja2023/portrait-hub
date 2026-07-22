<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { Code2, ImagePlus, Play } from "@lucide/vue";
import { ElAlert, ElButton, ElCheckbox, ElCheckboxGroup, ElOption, ElSelect } from "element-plus";

import { apiRequest } from "../api/client";
import DataTablePagination from "../components/DataTablePagination.vue";
import RawDataDrawer from "../components/RawDataDrawer.vue";
import { usePrefsStore } from "../stores/prefs";
import {
  comparisonReasonLabel,
  formatPercent,
  modalityLabel,
  riskLabel,
  thresholdProfileLabel,
} from "../utils/format";
import { errorBannerMessage } from "../utils/errors";
import { useTablePagination } from "../utils/tablePagination";

type CompareMode = "face" | "body" | "gait" | "fusion" | "batch";

const prefs = usePrefsStore();
const mode = ref<CompareMode>("body");
const batchModality = ref("body");
const thresholdProfile = ref("normal");
const includeVectors = ref(false);
const asyncMode = ref(false);
const fusionModalities = ref(["face", "body", "appearance"]);
const thresholdProfiles = ref(["strict", "normal", "loose"]);
const filesA = ref<File[]>([]);
const filesB = ref<File[]>([]);
const previewA = ref("");
const previewB = ref("");
const loading = ref(false);
const errorMessage = ref("");
const result = ref<Record<string, unknown> | null>(null);
const rawOpen = ref(false);

const requiresMultiple = computed(() => mode.value === "gait" || mode.value === "batch");
const passed = computed(() => result.value?.passed === true);
const similarity = computed(() => {
  for (const key of ["final_score", "quality_adjusted_similarity", "similarity"]) {
    if (typeof result.value?.[key] === "number") return Number(result.value[key]);
  }
  return null;
});
const threshold = computed(() =>
  typeof result.value?.threshold === "number" ? Number(result.value.threshold) : null,
);
const risk = computed(() => {
  const decision = result.value?.decision;
  return decision && typeof decision === "object" && "risk" in decision
    ? String((decision as { risk: unknown }).risk)
    : "clear";
});
const fusionRows = computed(() => {
  const modalities = result.value?.modalities;
  return modalities && typeof modalities === "object"
    ? Object.entries(modalities as Record<string, Record<string, unknown>>)
    : [];
});
const batchRows = computed(() =>
  Array.isArray(result.value?.results) ? (result.value.results as Record<string, unknown>[]) : [],
);
const fusionPager = useTablePagination(fusionRows);
const batchPager = useTablePagination(batchRows);

const canCompare = computed(
  () =>
    (mode.value !== "fusion" || fusionModalities.value.length > 0) &&
    filesA.value.length > 0 &&
    filesB.value.length > 0 &&
    (mode.value !== "batch" || filesA.value.length === filesB.value.length),
);

function clearPreview(side: "a" | "b"): void {
  const current = side === "a" ? previewA : previewB;
  if (current.value) URL.revokeObjectURL(current.value);
  current.value = "";
}

function select(side: "a" | "b", event: Event): void {
  const selected = Array.from((event.target as HTMLInputElement).files ?? []);
  clearPreview(side);
  if (side === "a") {
    filesA.value = selected;
    previewA.value = selected[0] ? URL.createObjectURL(selected[0]) : "";
  } else {
    filesB.value = selected;
    previewB.value = selected[0] ? URL.createObjectURL(selected[0]) : "";
  }
  result.value = null;
  errorMessage.value = "";
}

function batchComparison(row: Record<string, unknown>): Record<string, unknown> {
  return row.comparison && typeof row.comparison === "object"
    ? (row.comparison as Record<string, unknown>)
    : {};
}

function batchScore(row: Record<string, unknown>): number {
  const comparison = batchComparison(row);
  return Number(comparison.quality_adjusted_similarity ?? comparison.similarity ?? 0);
}

function batchRisk(row: Record<string, unknown>): string {
  const decision = batchComparison(row).decision;
  return decision && typeof decision === "object"
    ? String((decision as Record<string, unknown>).risk ?? "clear")
    : "clear";
}
function appendFiles(body: FormData, field: string, files: File[]): void {
  for (const file of files) body.append(field, file);
}

async function compare(): Promise<void> {
  if (!canCompare.value) {
    errorMessage.value = mode.value === "batch" ? "批量比对左右文件数量必须相同" : "请先选择两侧比对文件";
    return;
  }
  loading.value = true;
  errorMessage.value = "";
  try {
    const body = new FormData();
    body.append("threshold_profile", thresholdProfile.value);
    if (mode.value !== "fusion") body.append("include_vectors", String(includeVectors.value));
    let endpoint = "/v1/compare/persons";

    if (mode.value === "gait") {
      endpoint = "/v1/compare/gait";
      appendFiles(body, "sequence_a", filesA.value);
      appendFiles(body, "sequence_b", filesB.value);
    } else if (mode.value === "batch") {
      endpoint = "/v1/compare/batch";
      appendFiles(body, "image_a", filesA.value);
      appendFiles(body, "image_b", filesB.value);
      body.append("modality", batchModality.value);
      body.append("async_mode", String(asyncMode.value));
    } else {
      appendFiles(body, "image_a", filesA.value.slice(0, 1));
      appendFiles(body, "image_b", filesB.value.slice(0, 1));
      if (mode.value === "face") endpoint = "/v1/compare/faces";
      if (mode.value === "fusion") {
        endpoint = "/v1/fusion/compare";
        body.append("modalities", fusionModalities.value.join(","));
      }
    }

    result.value = await apiRequest<Record<string, unknown>>(
      endpoint,
      { method: "POST", body },
      mode.value === "gait" || mode.value === "batch" ? 180_000 : 90_000,
    );
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "比对失败");
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  try {
    const payload = await apiRequest<{ thresholds: Record<string, Record<string, number>> }>(
      "/v1/thresholds",
    );
    const profiles = new Set<string>();
    for (const values of Object.values(payload.thresholds)) {
      for (const profile of Object.keys(values)) profiles.add(profile);
    }
    if (profiles.size) thresholdProfiles.value = Array.from(profiles);
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "阈值配置加载失败，当前使用本地标准方案");
  }
});

onBeforeUnmount(() => {
  clearPreview("a");
  clearPreview("b");
});
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>人员比对</h1>
        <p>并排核验证据，以相似度、阈值、质量和风险给出结论。</p>
      </div>
      <div class="page-actions">
        <ElButton v-if="prefs.developerMode && result" :icon="Code2" @click="rawOpen = true"
          >原始数据</ElButton
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
    <section class="compare-input">
      <div class="compare-options">
        <label
          ><span>比对类型</span
          ><ElSelect v-model="mode">
            <ElOption label="人体" value="body" />
            <ElOption label="人脸" value="face" />
            <ElOption label="步态序列" value="gait" />
            <ElOption label="多模态融合" value="fusion" />
            <ElOption label="批量成对" value="batch" />
          </ElSelect>
        </label>
        <label
          ><span>阈值方案</span
          ><ElSelect v-model="thresholdProfile">
            <ElOption
              v-for="profile in thresholdProfiles"
              :key="profile"
              :label="thresholdProfileLabel(profile)"
              :value="profile"
            />
          </ElSelect>
        </label>
        <label v-if="mode === 'batch'"
          ><span>批量模态</span
          ><ElSelect v-model="batchModality">
            <ElOption label="人体" value="body" />
            <ElOption label="人脸" value="face" />
            <ElOption label="衣着" value="appearance" />
          </ElSelect>
        </label>
      </div>
      <div class="compare-toggles">
        <ElCheckbox v-if="mode !== 'fusion'" v-model="includeVectors">返回特征向量</ElCheckbox>
        <ElCheckbox v-if="mode === 'batch'" v-model="asyncMode">异步提交批量任务</ElCheckbox>
        <div v-if="mode === 'fusion'" class="fusion-options">
          <span>参与融合的模态</span>
          <ElCheckboxGroup v-model="fusionModalities">
            <ElCheckbox value="face">人脸</ElCheckbox>
            <ElCheckbox value="body">人体</ElCheckbox>
            <ElCheckbox value="appearance">衣着</ElCheckbox>
          </ElCheckboxGroup>
        </div>
      </div>
      <div class="image-pair">
        <label class="compare-file">
          <input type="file" accept="image/*" :multiple="requiresMultiple" @change="select('a', $event)" />
          <img v-if="previewA" :src="previewA" alt="左侧证据预览" />
          <span v-else><ImagePlus :size="28" />选择左侧{{ requiresMultiple ? "文件组" : "图像" }}</span>
          <small v-if="filesA.length">{{ filesA.length }} 个文件</small>
        </label>
        <div class="pair-divider">对比</div>
        <label class="compare-file">
          <input type="file" accept="image/*" :multiple="requiresMultiple" @change="select('b', $event)" />
          <img v-if="previewB" :src="previewB" alt="右侧证据预览" />
          <span v-else><ImagePlus :size="28" />选择右侧{{ requiresMultiple ? "文件组" : "图像" }}</span>
          <small v-if="filesB.length">{{ filesB.length }} 个文件</small>
        </label>
      </div>
      <ElAlert
        v-if="mode === 'batch' && filesA.length !== filesB.length"
        title="批量模式要求左右文件按选择顺序一一对应，数量必须相同。"
        type="warning"
        :closable="false"
        show-icon
      />
      <ElButton type="primary" :icon="Play" :loading="loading" :disabled="!canCompare" @click="compare"
        >开始比对</ElButton
      >
    </section>

    <section
      v-if="result && mode !== 'batch'"
      class="verdict"
      :data-verdict="passed ? 'pass' : risk === 'clear' ? 'reject' : 'review'"
    >
      <div>
        <strong>{{ passed ? "比对通过" : risk === "clear" ? "未通过" : "建议人工复核" }}</strong>
        <span
          >相似度 {{ similarity === null ? "--" : formatPercent(similarity) }} · 阈值
          {{ threshold === null ? "--" : formatPercent(threshold) }}</span
        >
        <small>风险：{{ riskLabel(risk) }}</small>
      </div>
      <div class="similarity-scale">
        <progress :value="similarity ?? 0" max="1">{{ similarity }}</progress>
      </div>
    </section>

    <section v-if="fusionRows.length" class="result-table">
      <h2 class="section-title">模态明细</h2>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th class="sequence-column">序号</th>
              <th>模态</th>
              <th>参与</th>
              <th>分数</th>
              <th>质量</th>
              <th>权重</th>
              <th>原因</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="([name, item], index) in fusionPager.items" :key="name">
              <td class="sequence-column">{{ fusionPager.startIndex + index + 1 }}</td>
              <td>{{ modalityLabel(name) }}</td>
              <td>{{ item.used ? "是" : "否" }}</td>
              <td>{{ typeof item.score === "number" ? formatPercent(Number(item.score)) : "--" }}</td>
              <td>{{ typeof item.quality === "number" ? formatPercent(Number(item.quality)) : "--" }}</td>
              <td>{{ item.weight ?? "--" }}</td>
              <td>{{ item.reason ? comparisonReasonLabel(item.reason) : "--" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <DataTablePagination
        v-model:page="fusionPager.page"
        v-model:page-size="fusionPager.pageSize"
        :total="fusionPager.total"
      />
    </section>

    <section v-if="mode === 'batch' && result" class="result-table">
      <h2 class="section-title">批量结果（{{ batchRows.length }}）</h2>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th class="sequence-column">序号</th>
              <th>模态</th>
              <th>结论</th>
              <th>相似度</th>
              <th>风险</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in batchPager.items" :key="String(row.index)">
              <td class="sequence-column">{{ batchPager.startIndex + index + 1 }}</td>
              <td>{{ modalityLabel(row.modality) }}</td>
              <td>{{ batchComparison(row).passed ? "通过" : "未通过" }}</td>
              <td>{{ formatPercent(batchScore(row)) }}</td>
              <td>{{ riskLabel(batchRisk(row)) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <DataTablePagination
        v-model:page="batchPager.page"
        v-model:page-size="batchPager.pageSize"
        :total="batchPager.total"
      />
    </section>
    <RawDataDrawer v-model="rawOpen" :data="result" />
  </div>
</template>

<style scoped>
.compare-input {
  display: grid;
  gap: 18px;
}
.compare-options {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.compare-options label {
  width: 200px;
  display: grid;
  gap: 7px;
  color: #62706d;
  font-size: 13px;
}
.compare-toggles {
  min-height: 36px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
  align-items: center;
}
.fusion-options {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
}
.fusion-options > span {
  color: #62706d;
  font-size: 13px;
}
.image-pair {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 48px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}
.compare-file {
  position: relative;
  min-height: 300px;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: #fff;
  border: 1px dashed #9bafaa;
  border-radius: 5px;
  cursor: pointer;
}
.compare-file input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}
.compare-file img {
  width: 100%;
  height: 300px;
  object-fit: contain;
  background: #eef2f1;
}
.compare-file span {
  display: grid;
  place-items: center;
  gap: 8px;
  color: #62706d;
}
.compare-file small {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 3px 7px;
  color: #fff;
  background: #263431;
  border-radius: 3px;
}
.pair-divider {
  font-weight: 800;
  text-align: center;
}
.verdict {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, 420px);
  align-items: center;
  gap: 24px;
  margin-top: 24px;
  padding: 20px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-left: 5px solid #62706d;
  border-radius: 5px;
}
.verdict[data-verdict="pass"] {
  border-left-color: #237a4b;
}
.verdict[data-verdict="reject"] {
  border-left-color: #b4232f;
}
.verdict[data-verdict="review"] {
  border-left-color: #a96508;
}
.verdict > div:first-child {
  display: flex;
  flex-direction: column;
}
.verdict strong {
  font-size: 20px;
}
.verdict span,
.verdict small {
  margin-top: 5px;
  color: #62706d;
}
.similarity-scale progress {
  width: 100%;
  height: 12px;
  accent-color: #087682;
}
.result-table {
  margin-top: 22px;
}
@media (max-width: 760px) {
  .image-pair {
    grid-template-columns: 1fr;
  }
  .pair-divider {
    padding: 4px;
  }
  .verdict {
    grid-template-columns: 1fr;
  }
  .compare-file,
  .compare-file img {
    min-height: 210px;
    height: 210px;
  }
}
</style>
