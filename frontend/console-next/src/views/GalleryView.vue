<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { Code2, Eye, Plus, RefreshCw, Save, Search, Settings2, Trash2, UserRound } from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElDialog,
  ElDrawer,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElInput,
  ElMessage,
  ElOption,
  ElSelect,
  ElSkeleton,
  ElStep,
  ElSteps,
} from "element-plus";

import { apiRequest, jsonBody } from "../api/client";
import type { GalleryListResponse, PersonSummary } from "../api/contracts";
import DangerConfirm from "../components/DangerConfirm.vue";
import EmptyState from "../components/EmptyState.vue";
import RawDataDrawer from "../components/RawDataDrawer.vue";
import { useCapabilitiesStore } from "../stores/capabilities";
import { usePrefsStore } from "../stores/prefs";
import { formatTimestamp } from "../utils/format";
import { errorBannerMessage } from "../utils/errors";

const route = useRoute();
const router = useRouter();
const capabilities = useCapabilitiesStore();
const prefs = usePrefsStore();
const people = ref<PersonSummary[]>([]);
const loading = ref(true);
const errorMessage = ref("");
const query = ref("");
const nextCursor = ref<string | null>(null);
const detail = ref<Record<string, unknown> | null>(null);
const detailName = ref("");
const detailMetadata = ref<Array<{ key: string; value: string }>>([]);
const detailSaving = ref(false);
const rawOpen = ref(false);
const enrollOpen = ref(false);
const enrollLoading = ref(false);
const enrollFiles = ref<File[]>([]);
const enrollId = ref("");
const enrollName = ref("");
const enrollModality = ref("body");
const enrollStep = ref(0);
const enrollResult = ref<Record<string, unknown> | null>(null);
const deleteOpen = ref(false);
const deleteLoading = ref(false);
const reindexOpen = ref(false);
const reindexConfirmOpen = ref(false);
const reindexLoading = ref(false);
const reindexPreview = ref<Record<string, unknown> | null>(null);
const detailPersonId = computed(() =>
  typeof detail.value?.person_id === "string" ? detail.value.person_id : "",
);
const detailFeatures = computed(() =>
  Array.isArray(detail.value?.features) ? (detail.value.features as Record<string, unknown>[]) : [],
);
const enrollResultPerson = computed(() =>
  enrollResult.value?.person && typeof enrollResult.value.person === "object"
    ? (enrollResult.value.person as Record<string, unknown>)
    : null,
);
const enrollResultPersonId = computed(() =>
  typeof enrollResultPerson.value?.person_id === "string" ? enrollResultPerson.value.person_id : "",
);
const enrollSkippedCount = computed(() => {
  for (const key of ["skipped", "skipped_count", "duplicate_count", "duplicates"]) {
    const value = enrollResult.value?.[key];
    if (typeof value === "number") return value;
    if (Array.isArray(value)) return value.length;
  }
  return 0;
});

async function loadPeople(append = false): Promise<void> {
  loading.value = !append;
  errorMessage.value = "";
  try {
    const params = new URLSearchParams({ limit: "30" });
    if (query.value.trim()) params.set("query", query.value.trim());
    if (append && nextCursor.value) params.set("cursor", nextCursor.value);
    const payload = await apiRequest<GalleryListResponse>(`/v1/gallery?${params}`);
    people.value = append ? [...people.value, ...payload.items] : payload.items;
    nextCursor.value = payload.next_cursor;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "人员列表加载失败");
  } finally {
    loading.value = false;
  }
}

async function openDetail(personId: string): Promise<void> {
  try {
    const payload = await apiRequest<{ person: Record<string, unknown> }>(
      `/v1/gallery/${encodeURIComponent(personId)}`,
    );
    detail.value = payload.person;
    detailName.value = String(payload.person.display_name ?? "");
    const metadata =
      payload.person.metadata && typeof payload.person.metadata === "object"
        ? (payload.person.metadata as Record<string, unknown>)
        : {};
    detailMetadata.value = Object.entries(metadata).map(([key, value]) => ({
      key,
      value: typeof value === "string" ? value : String(value ?? ""),
    }));
    await router.replace(`/gallery/${encodeURIComponent(personId)}`);
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "人员详情加载失败");
  }
}
function closeDetail(): void {
  detail.value = null;
  void router.replace("/gallery");
}
function openEnroll(): void {
  enrollStep.value = 0;
  enrollResult.value = null;
  enrollOpen.value = true;
}

function selectedEnrollFiles(event: Event): void {
  enrollFiles.value = Array.from((event.target as HTMLInputElement).files ?? []);
}

function resetEnroll(): void {
  enrollFiles.value = [];
  enrollId.value = "";
  enrollName.value = "";
  enrollModality.value = "body";
  enrollResult.value = null;
  enrollStep.value = 0;
}

function closeEnroll(): void {
  enrollOpen.value = false;
  resetEnroll();
}

async function finishEnroll(openPerson: boolean): Promise<void> {
  const personId = enrollResultPersonId.value;
  closeEnroll();
  if (openPerson && personId) await openDetail(personId);
}

async function enroll(): Promise<void> {
  if (!enrollFiles.value.length) return;
  enrollLoading.value = true;
  try {
    const body = new FormData();
    for (const file of enrollFiles.value) body.append("files", file);
    if (enrollId.value.trim()) body.append("person_id", enrollId.value.trim());
    if (enrollName.value.trim()) body.append("display_name", enrollName.value.trim());
    body.append("modality", enrollModality.value);
    enrollResult.value = await apiRequest<Record<string, unknown>>(
      "/v1/gallery/enroll",
      { method: "POST", body },
      120_000,
    );
    await loadPeople();
    enrollStep.value = 2;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "人员注册失败");
  } finally {
    enrollLoading.value = false;
  }
}
function addMetadataRow(): void {
  detailMetadata.value.push({ key: "", value: "" });
}

function removeMetadataRow(index: number): void {
  detailMetadata.value.splice(index, 1);
}
async function saveDetail(): Promise<void> {
  if (!detailPersonId.value) return;
  const metadata: Record<string, string> = {};
  for (const row of detailMetadata.value) {
    const key = row.key.trim();
    if (!key) continue;
    if (key in metadata) {
      errorMessage.value = "元数据键不能重复";
      return;
    }
    metadata[key] = row.value;
  }
  detailSaving.value = true;
  try {
    const payload = await apiRequest<{ person: Record<string, unknown> }>(
      "/v1/gallery/" + encodeURIComponent(detailPersonId.value),
      { method: "PATCH", body: jsonBody({ display_name: detailName.value.trim() || null, metadata }) },
    );
    detail.value = payload.person;
    ElMessage.success("人员信息已保存");
    await loadPeople();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "人员信息保存失败");
  } finally {
    detailSaving.value = false;
  }
}

async function previewReindex(): Promise<void> {
  reindexLoading.value = true;
  errorMessage.value = "";
  try {
    reindexPreview.value = await apiRequest<Record<string, unknown>>("/v1/gallery/reindex?dry_run=true", {
      method: "POST",
    });
    reindexOpen.value = true;
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "特征重建预演失败");
  } finally {
    reindexLoading.value = false;
  }
}

async function executeReindex(): Promise<void> {
  reindexLoading.value = true;
  try {
    await apiRequest("/v1/gallery/reindex?dry_run=false", { method: "POST" }, 120_000);
    reindexConfirmOpen.value = false;
    reindexOpen.value = false;
    ElMessage.success("特征索引重建已完成");
    await loadPeople();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "特征索引重建失败");
  } finally {
    reindexLoading.value = false;
  }
}
async function deletePerson(): Promise<void> {
  if (!detailPersonId.value) return;
  deleteLoading.value = true;
  try {
    await apiRequest(`/v1/gallery/${encodeURIComponent(detailPersonId.value)}`, { method: "DELETE" });
    deleteOpen.value = false;
    closeDetail();
    await loadPeople();
  } catch (error) {
    errorMessage.value = errorBannerMessage(error, "删除人员失败");
  } finally {
    deleteLoading.value = false;
  }
}

onMounted(async () => {
  await loadPeople();
  if (typeof route.params.personId === "string") await openDetail(route.params.personId);
});
watch(
  () => route.params.personId,
  (value) => {
    if (typeof value === "string" && value !== detailPersonId.value) void openDetail(value);
  },
);
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>人员库</h1>
        <p>按人员管理特征图像、模态、质量和业务元数据。</p>
      </div>
      <div class="page-actions">
        <ElButton :icon="RefreshCw" :loading="loading" @click="loadPeople()">刷新</ElButton
        ><ElButton
          v-if="capabilities.hasPermission('gallery:write')"
          type="primary"
          :icon="Plus"
          @click="openEnroll"
          >注册人员</ElButton
        >
        <ElDropdown
          v-if="capabilities.hasPermission('gallery:write')"
          trigger="click"
          @command="previewReindex"
        >
          <ElButton :icon="Settings2" :loading="reindexLoading">高级操作</ElButton>
          <template #dropdown>
            <ElDropdownMenu><ElDropdownItem command="reindex">特征重建</ElDropdownItem></ElDropdownMenu>
          </template>
        </ElDropdown>
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
    <div class="gallery-toolbar">
      <ElInput
        v-model="query"
        clearable
        placeholder="搜索姓名或人员 ID"
        :prefix-icon="Search"
        @keyup.enter="loadPeople()"
      /><ElButton @click="loadPeople()">搜索</ElButton>
    </div>
    <section class="tool-surface">
      <ElSkeleton :loading="loading" animated :rows="7"
        ><EmptyState
          v-if="people.length === 0"
          title="还没有人员"
          action-label="注册第一个人员"
          @action="openEnroll"
        />
        <div v-else class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>人员</th>
                <th>ID</th>
                <th>模态</th>
                <th>特征数</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="person in people" :key="person.person_id">
                <td>
                  <div class="person-cell">
                    <span class="person-thumb"
                      ><img v-if="person.thumbnail" :src="person.thumbnail" alt="人员缩略图" /><UserRound
                        v-else
                        :size="20" /></span
                    ><strong>{{ person.display_name || "未命名人员" }}</strong>
                  </div>
                </td>
                <td>
                  <code>{{ person.person_id }}</code>
                </td>
                <td>{{ person.modalities.join("、") || "--" }}</td>
                <td>{{ person.feature_count }}</td>
                <td>{{ formatTimestamp(person.updated_at) }}</td>
                <td><ElButton text :icon="Eye" @click="openDetail(person.person_id)">详情</ElButton></td>
              </tr>
            </tbody>
          </table>
        </div></ElSkeleton
      >
      <div v-if="nextCursor" class="load-more"><ElButton @click="loadPeople(true)">加载更多</ElButton></div>
    </section>
    <ElDrawer
      :model-value="Boolean(detail)"
      size="min(760px, 94vw)"
      title="人员详情"
      @update:model-value="!$event && closeDetail()"
      ><template v-if="detail"
        ><div class="person-detail-head">
          <div class="person-large"><UserRound :size="28" /></div>
          <div>
            <h2>{{ detail.display_name || "未命名人员" }}</h2>
            <code>{{ detail.person_id }}</code>
          </div>
        </div>
        <dl class="detail-facts">
          <div>
            <dt>特征数</dt>
            <dd>{{ detail.feature_count }}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{{ formatTimestamp(Number(detail.created_at)) }}</dd>
          </div>
          <div>
            <dt>更新时间</dt>
            <dd>{{ formatTimestamp(Number(detail.updated_at)) }}</dd>
          </div>
        </dl>
        <section v-if="capabilities.hasPermission('gallery:write')" class="detail-editor">
          <h3>基本信息</h3>
          <label><span>人员名称</span><ElInput v-model="detailName" maxlength="256" /></label>
          <div class="metadata-heading">
            <span>业务元数据</span><ElButton text :icon="Plus" @click="addMetadataRow">添加字段</ElButton>
          </div>
          <div v-if="detailMetadata.length" class="metadata-rows">
            <div v-for="(row, index) in detailMetadata" :key="index">
              <ElInput v-model="row.key" placeholder="字段名" maxlength="128" />
              <ElInput v-model="row.value" placeholder="字段值" maxlength="512" />
              <ElButton :icon="Trash2" aria-label="删除元数据字段" @click="removeMetadataRow(index)" />
            </div>
          </div>
          <ElButton :icon="Save" :loading="detailSaving" @click="saveDetail">保存人员信息</ElButton>
        </section>
        <section class="feature-section">
          <h3>特征图像</h3>
          <div v-if="detailFeatures.length" class="feature-grid">
            <article v-for="feature in detailFeatures" :key="String(feature.feature_id)">
              <div class="feature-thumb">
                <img v-if="feature.thumbnail" :src="String(feature.thumbnail)" alt="人员特征图" />
                <UserRound v-else :size="24" />
              </div>
              <strong>{{ feature.modality || "unknown" }}</strong>
              <span>质量 {{ Math.round(Number(feature.quality_score || 0) * 100) }}%</span>
            </article>
          </div>
          <p v-else class="feature-empty">暂无可见特征图像</p>
        </section>
        <div class="drawer-actions">
          <ElButton v-if="prefs.developerMode" :icon="Code2" @click="rawOpen = true">原始数据</ElButton
          ><ElButton
            v-if="capabilities.hasPermission('gallery:write')"
            type="danger"
            :icon="Trash2"
            @click="deleteOpen = true"
            >删除人员</ElButton
          >
        </div></template
      ></ElDrawer
    >
    <ElDialog
      v-model="enrollOpen"
      title="注册人员"
      width="min(620px, 94vw)"
      :close-on-click-modal="false"
      @closed="resetEnroll"
    >
      <ElSteps :active="enrollStep" finish-status="success" class="enroll-steps">
        <ElStep title="基本信息" />
        <ElStep title="上传图片" />
        <ElStep title="完成" />
      </ElSteps>
      <div v-if="enrollStep === 0" class="enroll-form">
        <label><span>人员名称</span><ElInput v-model="enrollName" maxlength="256" /></label>
        <label><span>人员 ID（可选）</span><ElInput v-model="enrollId" maxlength="128" /></label>
        <label>
          <span>模态</span>
          <ElSelect v-model="enrollModality">
            <ElOption label="人体" value="body" />
            <ElOption label="人脸" value="face" />
            <ElOption label="衣着" value="appearance" />
          </ElSelect>
        </label>
      </div>
      <div v-else-if="enrollStep === 1" class="enroll-form">
        <label>
          <span>特征图片</span>
          <input type="file" accept="image/*" multiple @change="selectedEnrollFiles" />
          <small>已选择 {{ enrollFiles.length }} 张；服务端会跳过重复或不可用图片。</small>
        </label>
      </div>
      <div v-else class="enroll-complete">
        <UserRound :size="32" />
        <h3>注册完成</h3>
        <p>
          {{ enrollResultPerson?.display_name || enrollName || "未命名人员" }}
          <code v-if="enrollResultPersonId">{{ enrollResultPersonId }}</code>
        </p>
        <p v-if="enrollSkippedCount">已跳过 {{ enrollSkippedCount }} 张重复或不可用图片。</p>
        <p v-else>未发现重复跳过图片。</p>
      </div>
      <template #footer>
        <ElButton v-if="enrollStep < 2" @click="closeEnroll">取消</ElButton>
        <ElButton v-if="enrollStep === 1" @click="enrollStep = 0">上一步</ElButton>
        <ElButton v-if="enrollStep === 0" type="primary" @click="enrollStep = 1">下一步</ElButton>
        <ElButton
          v-else-if="enrollStep === 1"
          type="primary"
          :disabled="enrollFiles.length === 0"
          :loading="enrollLoading"
          @click="enroll"
          >提交注册</ElButton
        >
        <ElButton v-if="enrollStep === 2" @click="finishEnroll(false)">完成</ElButton>
        <ElButton v-if="enrollStep === 2" type="primary" @click="finishEnroll(true)">查看人员详情</ElButton>
      </template>
    </ElDialog>
    <ElDialog
      v-model="reindexOpen"
      title="特征重建预演"
      width="min(560px, 92vw)"
      :close-on-click-modal="false"
    >
      <dl v-if="reindexPreview" class="reindex-summary">
        <div>
          <dt>人员</dt>
          <dd>{{ reindexPreview.person_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>匹配特征</dt>
          <dd>{{ reindexPreview.matched_feature_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>预计重建</dt>
          <dd>{{ reindexPreview.reindexed_feature_count ?? 0 }}</dd>
        </div>
        <div>
          <dt>失败</dt>
          <dd>{{ reindexPreview.failed_feature_count ?? 0 }}</dd>
        </div>
      </dl>
      <template #footer>
        <ElButton @click="reindexOpen = false">关闭</ElButton>
        <ElButton type="danger" @click="reindexConfirmOpen = true">执行重建</ElButton>
      </template>
    </ElDialog>
    <DangerConfirm
      v-model="reindexConfirmOpen"
      title="执行特征重建"
      :description="
        '将为当前租户实际重建 ' + String(reindexPreview?.reindexed_feature_count ?? 0) + ' 个特征索引。'
      "
      high-risk
      confirmation-text="重建特征"
      :loading="reindexLoading"
      @confirm="executeReindex"
    />
    <DangerConfirm
      v-model="deleteOpen"
      title="删除人员"
      :description="`将删除 ${detailPersonId} 的人员记录、特征对象和向量索引。`"
      :loading="deleteLoading"
      @confirm="deletePerson"
    />
    <RawDataDrawer v-model="rawOpen" :data="detail" />
  </div>
</template>

<style scoped>
.gallery-toolbar {
  display: flex;
  gap: 8px;
  max-width: 520px;
  margin-bottom: 14px;
}
.person-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}
.person-thumb {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #6c7b78;
  background: #edf2f1;
  border-radius: 4px;
}
.person-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.load-more {
  display: flex;
  justify-content: center;
  padding: 12px;
  border-top: 1px solid #d8e0de;
}
.person-detail-head {
  display: flex;
  align-items: center;
  gap: 14px;
  padding-bottom: 18px;
  border-bottom: 1px solid #d8e0de;
}
.person-large {
  width: 58px;
  height: 58px;
  display: grid;
  place-items: center;
  color: #087682;
  background: #e6f3f2;
  border-radius: 5px;
}
.person-detail-head h2 {
  margin: 0 0 5px;
}
.detail-facts {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin: 18px 0;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.detail-facts div {
  padding: 13px;
  background: #fff;
}
.detail-facts dt {
  color: #62706d;
  font-size: 12px;
}
.detail-facts dd {
  margin: 5px 0 0;
}
.detail-editor,
.feature-section {
  display: grid;
  gap: 12px;
  margin: 18px 0;
  padding: 16px;
  background: #f8faf9;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.detail-editor h3,
.feature-section h3 {
  margin: 0;
  font-size: 15px;
}
.detail-editor label {
  display: grid;
  gap: 6px;
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
  gap: 8px;
}
.metadata-rows > div {
  display: grid;
  grid-template-columns: minmax(100px, 0.7fr) minmax(140px, 1.3fr) 40px;
  gap: 8px;
}
.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.feature-grid article {
  min-width: 0;
  display: grid;
  gap: 4px;
  padding: 8px;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 4px;
}
.feature-grid span,
.feature-empty {
  color: #62706d;
  font-size: 12px;
}
.feature-thumb {
  aspect-ratio: 4 / 3;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #71807c;
  background: #edf2f1;
}
.feature-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.reindex-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 0;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.reindex-summary div {
  padding: 14px;
  background: #fff;
}
.reindex-summary dt {
  color: #62706d;
  font-size: 12px;
}
.reindex-summary dd {
  margin: 5px 0 0;
  font-size: 22px;
  font-weight: 700;
}
.drawer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.enroll-steps {
  margin-bottom: 22px;
}
.enroll-form {
  display: grid;
  gap: 15px;
}
.enroll-form label {
  display: grid;
  gap: 7px;
  color: #62706d;
  font-size: 13px;
}
.enroll-complete {
  display: grid;
  place-items: center;
  gap: 8px;
  min-height: 190px;
  color: #62706d;
  text-align: center;
}
.enroll-complete h3 {
  margin: 4px 0 0;
  color: #1f2d2a;
}
.enroll-complete p {
  margin: 0;
}
.enroll-complete code {
  margin-left: 6px;
}
.enroll-form small {
  color: #71807c;
}
</style>


