<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import {
  Building2,
  Check,
  Copy,
  ExternalLink,
  KeyRound,
  Pencil,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  UserRound,
  UsersRound,
} from "@lucide/vue";
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElCheckboxGroup,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTabPane,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTag,
  ElTooltip,
} from "element-plus";

import { ApiError, apiRequest, jsonBody } from "../../api/client";
import DataTablePagination from "../../components/DataTablePagination.vue";
import type {
  AccessTenant,
  AccessTenantListPayload,
  IdentityAdminPayload,
  IdentityMember,
  IdentityMemberListPayload,
} from "../../api/contracts";
import { useCapabilitiesStore } from "../../stores/capabilities";
import { formatTimestamp } from "../../utils/format";
import { useTablePagination } from "../../utils/tablePagination";

const capabilities = useCapabilitiesStore();
const profile = computed(() => capabilities.capabilities);
const adminPayload = ref<IdentityAdminPayload | null>(null);
const tenants = ref<AccessTenant[]>([]);
const members = ref<IdentityMember[]>([]);
const activeTab = ref("members");
const loading = ref(false);
const submitting = ref(false);
const errorMessage = ref("");
const memberSearch = ref("");
const memberTenantFilter = ref("");
const memberStatusFilter = ref("");
const busyMemberIds = ref<string[]>([]);
const busyTenantIds = ref<string[]>([]);
const memberDialogOpen = ref(false);
const tenantDialogOpen = ref(false);
const secretDialogOpen = ref(false);
const editingMemberId = ref<string | null>(null);
const editingTenantId = ref<string | null>(null);
const oneTimeSecret = ref("");
const secretTenantName = ref("");
const MEMBER_PHONE_PATTERN = /^\+[1-9][0-9]{6,19}$/;
const TENANT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;

function normalizeMemberPhone(value: string): string {
  let normalized = value.replace(/[\s()-]/g, "");
  if (normalized.startsWith("00")) normalized = `+${normalized.slice(2)}`;
  if (/^1[3-9][0-9]{9}$/.test(normalized)) normalized = `+86${normalized}`;
  return normalized;
}

const memberForm = reactive({
  tenant_id: "",
  phone: "",
  display_name: "",
  subject: "",
  roles: ["viewer"] as string[],
  status: "active" as "active" | "disabled",
});

const tenantForm = reactive({
  tenant_id: "",
  name: "",
  status: "active" as "active" | "disabled",
  create_default_application: true,
  application_name: "",
  owner: "platform",
  scopes: "infer,compare,gallery:read,gallery:write,jobs,jobs:read,streams,streams:read,models:read",
  rate_limit_per_minute: null as number | null,
  rate_limit_burst: null as number | null,
  daily_quota: null as number | null,
});

const identity = computed(() => adminPayload.value?.identity ?? profile.value?.identity);
const activeTenantCount = computed(() => tenants.value.filter((tenant) => tenant.status === "active").length);
const activeMemberCount = computed(() => members.value.filter((member) => member.status === "active").length);
const roleCatalog = computed(() => adminPayload.value?.roles ?? []);
const memberDialogTitle = computed(() => (editingMemberId.value ? "编辑成员授权" : "添加租户成员"));
const tenantDialogTitle = computed(() => (editingTenantId.value ? "编辑租户" : "添加租户"));
const memberFormValid = computed(
  () =>
    Boolean(memberForm.tenant_id && memberForm.display_name.trim()) &&
    MEMBER_PHONE_PATTERN.test(normalizeMemberPhone(memberForm.phone)) &&
    memberForm.roles.length > 0,
);
const tenantFormValid = computed(
  () =>
    Boolean(tenantForm.name.trim()) &&
    (!tenantForm.tenant_id.trim() || TENANT_ID_PATTERN.test(tenantForm.tenant_id.trim())),
);
const hasActiveTenant = computed(() => tenants.value.some((tenant) => tenant.status === "active"));
const grantsPlatformAdmin = computed(() => memberForm.roles.includes("admin"));

const roleLabels: Record<string, string> = {
  admin: "超级管理员",
  operator: "业务管理员",
  algorithm: "算法人员",
  auditor: "审计人员",
  viewer: "只读用户",
};

const roleDescriptions: Record<string, string> = {
  admin: "平台与租户全局管理",
  operator: "日常业务操作与资源维护",
  algorithm: "模型、算法与阈值维护",
  auditor: "审计、导出与只读运维",
  viewer: "业务数据只读访问",
};

const permissionLabels: Record<string, string> = {
  "*": "全部权限",
  infer: "执行分析推理",
  compare: "人员比对",
  "gallery:read": "读取人员库",
  "gallery:write": "维护人员库",
  jobs: "管理视频任务",
  "jobs:read": "读取视频任务",
  streams: "管理实时流",
  "streams:read": "读取实时流",
  "models:read": "读取模型",
  "models:write": "维护模型",
  "thresholds:write": "维护阈值",
  "admin:status": "查看运维状态",
  "admin:export": "导出与备份",
  "admin:retention": "执行数据清理",
  "admin:identity": "管理身份与权限",
  "metrics:read": "读取指标",
  "access:read": "读取接入配置",
  "access:write": "维护接入配置",
  "tenants:read": "读取租户",
  "tenants:write": "维护租户",
};

const authKindLabels: Record<string, string> = {
  oidc: "企业账号",
  local: "本地管理员",
  jwt: "JWT",
  global_api_token: "全局接口令牌",
  application_api_key: "应用接口密钥",
  development_anonymous: "本地开发身份",
};

const filteredMembers = computed(() => {
  const keyword = memberSearch.value.trim().toLowerCase();
  return members.value.filter((member) => {
    const matchesKeyword =
      !keyword ||
      member.display_name.toLowerCase().includes(keyword) ||
      member.phone.includes(keyword) ||
      (member.subject ?? "").toLowerCase().includes(keyword);
    const matchesTenant = !memberTenantFilter.value || member.tenant_id === memberTenantFilter.value;
    const matchesStatus = !memberStatusFilter.value || member.status === memberStatusFilter.value;
    return matchesKeyword && matchesTenant && matchesStatus;
  });
});
const membersPager = useTablePagination(filteredMembers);
const tenantsPager = useTablePagination(tenants);
const rolesPager = useTablePagination(roleCatalog);

function permissionLabel(value: string): string {
  return permissionLabels[value] ?? value;
}

function tenantName(tenantId: string): string {
  return tenants.value.find((tenant) => tenant.tenant_id === tenantId)?.name ?? tenantId;
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function openIdentityAdmin(): void {
  const url = identity.value?.identity_admin_url;
  if (url) window.open(url, "_blank", "noopener,noreferrer");
}

async function loadWorkspace(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    const [identityPayload, tenantPayload, memberPayload] = await Promise.all([
      apiRequest<IdentityAdminPayload>("/v1/admin/identity"),
      apiRequest<AccessTenantListPayload>("/v1/access/tenants"),
      apiRequest<IdentityMemberListPayload>("/v1/admin/members?all_tenants=true"),
    ]);
    adminPayload.value = identityPayload;
    tenants.value = tenantPayload.tenants;
    members.value = memberPayload.members;
  } catch (error) {
    errorMessage.value = errorText(error, "身份与租户信息加载失败");
  } finally {
    loading.value = false;
  }
}

function resetMemberForm(tenantId = ""): void {
  editingMemberId.value = null;
  memberForm.tenant_id =
    tenantId || memberTenantFilter.value || profile.value?.tenant_id || tenants.value[0]?.tenant_id || "";
  memberForm.phone = "";
  memberForm.display_name = "";
  memberForm.subject = "";
  memberForm.roles = ["viewer"];
  memberForm.status = "active";
}

function openCreateMember(tenantId = ""): void {
  if (!hasActiveTenant.value) {
    ElMessage.warning("请先创建并启用租户");
    activeTab.value = "tenants";
    return;
  }
  resetMemberForm(tenantId);
  memberDialogOpen.value = true;
}

function identityMember(value: unknown): IdentityMember {
  return value as IdentityMember;
}

function accessTenant(value: unknown): AccessTenant {
  return value as AccessTenant;
}

function openEditMember(value: unknown): void {
  const member = identityMember(value);
  editingMemberId.value = member.member_id;
  memberForm.tenant_id = member.tenant_id;
  memberForm.phone = member.phone;
  memberForm.display_name = member.display_name;
  memberForm.subject = member.subject ?? "";
  memberForm.roles = [...member.roles];
  memberForm.status = member.status;
  memberDialogOpen.value = true;
}

async function submitMember(): Promise<void> {
  if (!memberFormValid.value) return;
  submitting.value = true;
  try {
    const editing = editingMemberId.value;
    if (editing) {
      await apiRequest(`/v1/admin/members/${encodeURIComponent(editing)}`, {
        method: "PATCH",
        body: jsonBody({
          phone: normalizeMemberPhone(memberForm.phone),
          display_name: memberForm.display_name.trim(),
          subject: memberForm.subject.trim() || null,
          roles: memberForm.roles,
          status: memberForm.status,
        }),
      });
      ElMessage.success("成员授权已更新");
    } else {
      await apiRequest("/v1/admin/members", {
        method: "POST",
        body: jsonBody({
          tenant_id: memberForm.tenant_id,
          phone: normalizeMemberPhone(memberForm.phone),
          display_name: memberForm.display_name.trim(),
          subject: memberForm.subject.trim() || null,
          roles: memberForm.roles,
          status: memberForm.status,
        }),
      });
      ElMessage.success("租户成员已添加");
    }
    memberDialogOpen.value = false;
    await loadWorkspace();
  } catch (error) {
    ElMessage.error(errorText(error, "成员授权保存失败"));
  } finally {
    submitting.value = false;
  }
}

async function setMemberStatus(value: unknown, active: boolean): Promise<void> {
  const member = identityMember(value);
  busyMemberIds.value = [...busyMemberIds.value, member.member_id];
  try {
    await apiRequest(`/v1/admin/members/${encodeURIComponent(member.member_id)}`, {
      method: "PATCH",
      body: jsonBody({ status: active ? "active" : "disabled" }),
    });
    member.status = active ? "active" : "disabled";
    ElMessage.success(active ? "成员已启用" : "成员已停用");
  } catch (error) {
    ElMessage.error(errorText(error, "成员状态更新失败"));
  } finally {
    busyMemberIds.value = busyMemberIds.value.filter((id) => id !== member.member_id);
  }
}

async function removeMember(value: unknown): Promise<void> {
  const member = identityMember(value);
  try {
    const consequence = identity.value?.enabled
      ? "将移除影鉴内的租户授权；企业用户目录中的账号和角色映射不会被删除。"
      : "将移除该成员的预配置租户授权。";
    await ElMessageBox.confirm(
      `${member.display_name} 将从租户“${tenantName(member.tenant_id)}”移除。${consequence}`,
      "移除租户成员",
      { confirmButtonText: "移除", cancelButtonText: "取消", type: "warning" },
    );
    await apiRequest(`/v1/admin/members/${encodeURIComponent(member.member_id)}`, { method: "DELETE" });
    members.value = members.value.filter((item) => item.member_id !== member.member_id);
    ElMessage.success("成员已移除");
  } catch (error) {
    if (error === "cancel" || error === "close") return;
    ElMessage.error(errorText(error, "成员移除失败"));
  }
}

function resetTenantForm(): void {
  editingTenantId.value = null;
  tenantForm.tenant_id = "";
  tenantForm.name = "";
  tenantForm.status = "active";
  tenantForm.create_default_application = true;
  tenantForm.application_name = "";
  tenantForm.owner = "platform";
  tenantForm.scopes =
    "infer,compare,gallery:read,gallery:write,jobs,jobs:read,streams,streams:read,models:read";
  tenantForm.rate_limit_per_minute = null;
  tenantForm.rate_limit_burst = null;
  tenantForm.daily_quota = null;
}

function openCreateTenant(): void {
  resetTenantForm();
  tenantDialogOpen.value = true;
}

function openEditTenant(value: unknown): void {
  const tenant = accessTenant(value);
  editingTenantId.value = tenant.tenant_id;
  tenantForm.tenant_id = tenant.tenant_id;
  tenantForm.name = tenant.name;
  tenantForm.status = tenant.status;
  tenantForm.create_default_application = false;
  tenantForm.application_name = "";
  tenantForm.daily_quota = null;
  tenantDialogOpen.value = true;
}

async function submitTenant(): Promise<void> {
  if (!tenantFormValid.value) return;
  submitting.value = true;
  try {
    if (editingTenantId.value) {
      await apiRequest(`/v1/access/tenants/${encodeURIComponent(editingTenantId.value)}`, {
        method: "PATCH",
        body: jsonBody({ name: tenantForm.name.trim(), status: tenantForm.status }),
      });
      ElMessage.success("租户设置已更新");
    } else {
      const result = await apiRequest<{
        tenant: AccessTenant;
        one_time_secret?: string;
      }>("/v1/access/tenants", {
        method: "POST",
        body: jsonBody({
          tenant_id: tenantForm.tenant_id.trim() || null,
          name: tenantForm.name.trim(),
          status: tenantForm.status,
          create_default_application: tenantForm.create_default_application,
          application_name: tenantForm.application_name.trim() || null,
          owner: tenantForm.owner.trim() || "platform",
          scopes: tenantForm.scopes
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          rate_limit_per_minute: tenantForm.rate_limit_per_minute,
          rate_limit_burst: tenantForm.rate_limit_burst,
          daily_quota: tenantForm.daily_quota,
        }),
      });
      ElMessage.success("租户已创建");
      if (result.one_time_secret) {
        oneTimeSecret.value = result.one_time_secret;
        secretTenantName.value = result.tenant.name;
        secretDialogOpen.value = true;
      }
    }
    tenantDialogOpen.value = false;
    await loadWorkspace();
  } catch (error) {
    ElMessage.error(errorText(error, "租户保存失败"));
  } finally {
    submitting.value = false;
  }
}

async function setTenantStatus(value: unknown, active: boolean): Promise<void> {
  const tenant = accessTenant(value);
  if (!active) {
    const sessionImpact =
      tenant.tenant_id === profile.value?.tenant_id ? "当前会话也会立即失去该租户的访问权限。" : "";
    try {
      await ElMessageBox.confirm(
        `停用后，租户“${tenant.name}”的成员和应用将无法访问业务接口。${sessionImpact}`,
        "停用租户",
        {
          confirmButtonText: "停用",
          cancelButtonText: "取消",
          type: "warning",
        },
      );
    } catch {
      return;
    }
  }
  busyTenantIds.value = [...busyTenantIds.value, tenant.tenant_id];
  try {
    await apiRequest(`/v1/access/tenants/${encodeURIComponent(tenant.tenant_id)}`, {
      method: "PATCH",
      body: jsonBody({ status: active ? "active" : "disabled" }),
    });
    tenant.status = active ? "active" : "disabled";
    ElMessage.success(active ? "租户已启用" : "租户已停用");
  } catch (error) {
    ElMessage.error(errorText(error, "租户状态更新失败"));
  } finally {
    busyTenantIds.value = busyTenantIds.value.filter((id) => id !== tenant.tenant_id);
  }
}

function showTenantMembers(value: unknown): void {
  const tenant = accessTenant(value);
  memberTenantFilter.value = tenant.tenant_id;
  activeTab.value = "members";
}

async function copySecret(): Promise<void> {
  try {
    await navigator.clipboard.writeText(oneTimeSecret.value);
    ElMessage.success("密钥已复制");
  } catch {
    ElMessage.warning("复制失败，请手动选择密钥");
  }
}

onMounted(() => void loadWorkspace());
</script>

<template>
  <div v-loading="loading" class="identity-page">
    <header class="page-header">
      <div>
        <h1>身份与权限</h1>
        <p>管理租户成员、租户生命周期与角色授权策略。</p>
      </div>
      <div class="page-actions">
        <ElButton v-if="identity?.identity_admin_url" :icon="ExternalLink" @click="openIdentityAdmin">
          企业用户目录
        </ElButton>
        <ElTooltip :content="hasActiveTenant ? '添加租户成员' : '请先创建并启用租户'">
          <span>
            <ElButton type="primary" :icon="Plus" :disabled="!hasActiveTenant" @click="openCreateMember()">
              添加成员
            </ElButton>
          </span>
        </ElTooltip>
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

    <section class="identity-rail" aria-label="身份权限摘要">
      <div class="identity-rail__item">
        <span class="rail-icon"><UserRound :size="19" /></span>
        <div>
          <small>当前身份</small>
          <strong>{{ profile?.display_name || profile?.subject || "--" }}</strong>
          <span>{{ authKindLabels[profile?.auth_kind || ""] || profile?.auth_kind || "--" }}</span>
        </div>
      </div>
      <div class="identity-rail__item">
        <span class="rail-icon"><UsersRound :size="19" /></span>
        <div>
          <small>已授权成员</small>
          <strong>{{ activeMemberCount }}</strong>
          <span>共 {{ members.length }} 条成员关系</span>
        </div>
      </div>
      <div class="identity-rail__item">
        <span class="rail-icon"><Building2 :size="19" /></span>
        <div>
          <small>活跃租户</small>
          <strong>{{ activeTenantCount }}</strong>
          <span>共 {{ tenants.length }} 个租户</span>
        </div>
      </div>
      <div class="identity-rail__item">
        <span class="rail-icon"><ShieldCheck :size="19" /></span>
        <div>
          <small>授权角色</small>
          <strong>{{ roleCatalog.length }}</strong>
          <span>{{ profile?.tenant_id || "当前租户未识别" }}</span>
        </div>
      </div>
    </section>

    <div class="identity-boundary">
      <KeyRound :size="17" />
      <span v-if="identity?.enabled">
        账号凭据由 {{ identity.provider_name }} 维护；影鉴负责租户成员关系与角色授权。
      </span>
      <span v-else>当前使用本地管理员身份；新增成员用于预配置企业身份的租户授权。</span>
    </div>

    <ElTabs v-model="activeTab" class="management-tabs">
      <ElTabPane label="成员授权" name="members">
        <section class="management-surface" aria-labelledby="member-management-title">
          <div class="management-toolbar">
            <div>
              <h2 id="member-management-title">成员授权</h2>
              <span>{{ filteredMembers.length }} / {{ members.length }}</span>
            </div>
            <div class="member-filters">
              <ElInput
                v-model="memberSearch"
                clearable
                placeholder="搜索姓名、手机号或主体"
                :prefix-icon="Search"
              />
              <ElSelect v-model="memberTenantFilter" clearable placeholder="全部租户">
                <ElOption
                  v-for="tenant in tenants"
                  :key="tenant.tenant_id"
                  :label="tenant.name"
                  :value="tenant.tenant_id"
                />
              </ElSelect>
              <ElSelect v-model="memberStatusFilter" clearable placeholder="全部状态">
                <ElOption label="已启用" value="active" />
                <ElOption label="已停用" value="disabled" />
              </ElSelect>
              <ElButton type="primary" :icon="Plus" :disabled="!hasActiveTenant" @click="openCreateMember()">
                添加成员
              </ElButton>
            </div>
          </div>

          <div class="table-wrap">
            <ElTable border :data="membersPager.items" row-key="member_id" empty-text="暂无受管成员">
              <ElTableColumn label="序号" width="72" fixed="left">
                <template #default="{ $index }">{{ membersPager.startIndex + $index + 1 }}</template>
              </ElTableColumn>
              <ElTableColumn label="成员" min-width="230">
                <template #default="{ row }">
                  <div class="member-cell">
                    <span class="member-avatar">{{ row.display_name.slice(0, 1).toUpperCase() }}</span>
                    <div>
                      <strong>{{ row.display_name }}</strong>
                      <span>{{ row.phone }}</span>
                    </div>
                  </div>
                </template>
              </ElTableColumn>
              <ElTableColumn label="租户" min-width="170">
                <template #default="{ row }">
                  <strong class="tenant-name">{{ tenantName(row.tenant_id) }}</strong>
                  <code>{{ row.tenant_id }}</code>
                </template>
              </ElTableColumn>
              <ElTableColumn label="角色" min-width="210">
                <template #default="{ row }">
                  <div class="role-tags">
                    <ElTag v-for="role in row.roles" :key="role" effect="plain" size="small">
                      {{ roleLabels[role] || role }}
                    </ElTag>
                  </div>
                </template>
              </ElTableColumn>
              <ElTableColumn label="身份绑定" min-width="170">
                <template #default="{ row }">
                  <span class="binding-state" :data-bound="Boolean(row.subject)">
                    {{ row.subject || "登录时按已验证手机号绑定" }}
                  </span>
                </template>
              </ElTableColumn>
              <ElTableColumn label="状态" width="110">
                <template #default="{ row }">
                  <ElSwitch
                    :model-value="row.status === 'active'"
                    :loading="busyMemberIds.includes(row.member_id)"
                    :aria-label="`${row.display_name}状态`"
                    @change="(value: string | number | boolean) => setMemberStatus(row, Boolean(value))"
                  />
                </template>
              </ElTableColumn>
              <ElTableColumn label="更新时间" min-width="155">
                <template #default="{ row }">{{ formatTimestamp(row.updated_at) }}</template>
              </ElTableColumn>
              <ElTableColumn label="操作" width="104" fixed="right">
                <template #default="{ row }">
                  <div class="row-actions">
                    <ElTooltip content="编辑授权">
                      <ElButton
                        text
                        circle
                        :icon="Pencil"
                        :aria-label="`编辑${row.display_name}`"
                        @click="openEditMember(row)"
                      />
                    </ElTooltip>
                    <ElTooltip content="移除成员">
                      <ElButton
                        text
                        circle
                        type="danger"
                        :icon="Trash2"
                        :aria-label="`移除${row.display_name}`"
                        @click="removeMember(row)"
                      />
                    </ElTooltip>
                  </div>
                </template>
              </ElTableColumn>
            </ElTable>
          </div>
          <DataTablePagination
            v-model:page="membersPager.page"
            v-model:page-size="membersPager.pageSize"
            :total="membersPager.total"
          />
        </section>
      </ElTabPane>

      <ElTabPane label="租户管理" name="tenants">
        <section class="management-surface" aria-labelledby="tenant-management-title">
          <div class="management-toolbar">
            <div>
              <h2 id="tenant-management-title">租户管理</h2>
              <span>{{ tenants.length }} 个租户</span>
            </div>
            <ElButton type="primary" :icon="Plus" @click="openCreateTenant">添加租户</ElButton>
          </div>
          <div class="table-wrap">
            <ElTable border :data="tenantsPager.items" row-key="tenant_id" empty-text="暂无租户">
              <ElTableColumn label="序号" width="72" fixed="left">
                <template #default="{ $index }">{{ tenantsPager.startIndex + $index + 1 }}</template>
              </ElTableColumn>
              <ElTableColumn label="租户" min-width="230">
                <template #default="{ row }">
                  <div class="tenant-cell">
                    <span class="tenant-mark"><Building2 :size="18" /></span>
                    <div>
                      <strong>{{ row.name }}</strong>
                      <code>{{ row.tenant_id }}</code>
                    </div>
                  </div>
                </template>
              </ElTableColumn>
              <ElTableColumn label="成员" width="100" prop="member_count" />
              <ElTableColumn label="应用" width="100" prop="application_count" />
              <ElTableColumn label="回调" width="100" prop="webhook_count" />
              <ElTableColumn label="状态" width="120">
                <template #default="{ row }">
                  <ElSwitch
                    :model-value="row.status === 'active'"
                    :loading="busyTenantIds.includes(row.tenant_id)"
                    :aria-label="`${row.name}状态`"
                    @change="(value: string | number | boolean) => setTenantStatus(row, Boolean(value))"
                  />
                </template>
              </ElTableColumn>
              <ElTableColumn label="创建时间" min-width="155">
                <template #default="{ row }">{{ formatTimestamp(row.created_at) }}</template>
              </ElTableColumn>
              <ElTableColumn label="操作" width="160" fixed="right">
                <template #default="{ row }">
                  <div class="row-actions">
                    <ElButton text @click="showTenantMembers(row)">成员</ElButton>
                    <ElTooltip content="编辑租户">
                      <ElButton
                        text
                        circle
                        :icon="Pencil"
                        :aria-label="`编辑${row.name}`"
                        @click="openEditTenant(row)"
                      />
                    </ElTooltip>
                  </div>
                </template>
              </ElTableColumn>
            </ElTable>
          </div>
          <DataTablePagination
            v-model:page="tenantsPager.page"
            v-model:page-size="tenantsPager.pageSize"
            :total="tenantsPager.total"
          />
        </section>
      </ElTabPane>

      <ElTabPane label="角色权限" name="roles">
        <section class="management-surface" aria-labelledby="role-management-title">
          <div class="management-toolbar">
            <div>
              <h2 id="role-management-title">角色权限</h2>
              <span>平台内置策略</span>
            </div>
          </div>
          <div class="table-wrap">
            <ElTable border :data="rolesPager.items" row-key="role" empty-text="暂无角色策略">
              <ElTableColumn label="序号" width="72" fixed="left">
                <template #default="{ $index }">{{ rolesPager.startIndex + $index + 1 }}</template>
              </ElTableColumn>
              <ElTableColumn label="角色" min-width="170">
                <template #default="{ row }">
                  <strong>{{ roleLabels[row.role] || row.role }}</strong>
                  <code>{{ row.role }}</code>
                </template>
              </ElTableColumn>
              <ElTableColumn label="职责边界" min-width="220">
                <template #default="{ row }">{{ roleDescriptions[row.role] || "自定义角色" }}</template>
              </ElTableColumn>
              <ElTableColumn label="权限范围" min-width="520">
                <template #default="{ row }">
                  <div class="permission-list">
                    <ElTag v-for="permission in row.permissions" :key="permission" effect="plain">
                      {{ permissionLabel(permission) }}
                    </ElTag>
                  </div>
                </template>
              </ElTableColumn>
            </ElTable>
          </div>
          <DataTablePagination
            v-model:page="rolesPager.page"
            v-model:page-size="rolesPager.pageSize"
            :total="rolesPager.total"
          />
        </section>
      </ElTabPane>
    </ElTabs>

    <ElDialog v-model="memberDialogOpen" :title="memberDialogTitle" width="min(560px, calc(100vw - 24px))">
      <ElForm label-position="top">
        <div class="form-grid form-grid--two">
          <ElFormItem label="所属租户" required>
            <ElSelect
              v-model="memberForm.tenant_id"
              :disabled="Boolean(editingMemberId)"
              placeholder="选择租户"
            >
              <ElOption
                v-for="tenant in tenants"
                :key="tenant.tenant_id"
                :label="tenant.name"
                :value="tenant.tenant_id"
                :disabled="tenant.status !== 'active'"
              />
            </ElSelect>
          </ElFormItem>
          <ElFormItem label="状态" required>
            <ElSelect v-model="memberForm.status">
              <ElOption label="已启用" value="active" />
              <ElOption label="已停用" value="disabled" />
            </ElSelect>
          </ElFormItem>
        </div>
        <div class="form-grid form-grid--two">
          <ElFormItem label="姓名" required>
            <ElInput v-model="memberForm.display_name" maxlength="256" placeholder="例如：张明" />
          </ElFormItem>
          <ElFormItem label="手机号码" required>
            <ElInput
              v-model="memberForm.phone"
              maxlength="32"
              placeholder="例如：13800138000 或 +8613800138000"
            />
          </ElFormItem>
        </div>
        <ElFormItem label="身份主体">
          <ElInput v-model="memberForm.subject" maxlength="256" placeholder="可选，OIDC subject" />
        </ElFormItem>
        <ElFormItem label="角色" required>
          <ElCheckboxGroup v-model="memberForm.roles" class="role-selector">
            <ElCheckbox v-for="role in roleCatalog" :key="role.role" :value="role.role">
              <span>{{ roleLabels[role.role] || role.role }}</span>
              <small>{{ roleDescriptions[role.role] }}</small>
            </ElCheckbox>
          </ElCheckboxGroup>
        </ElFormItem>
        <ElAlert
          v-if="grantsPlatformAdmin"
          title="超级管理员拥有平台及所有租户的完整权限，请仅分配给平台负责人。"
          type="warning"
          show-icon
          :closable="false"
        />
      </ElForm>
      <template #footer>
        <ElButton @click="memberDialogOpen = false">取消</ElButton>
        <ElButton type="primary" :loading="submitting" :disabled="!memberFormValid" @click="submitMember"
          >保存授权</ElButton
        >
      </template>
    </ElDialog>

    <ElDialog v-model="tenantDialogOpen" :title="tenantDialogTitle" width="min(560px, calc(100vw - 24px))">
      <ElForm label-position="top">
        <div class="form-grid form-grid--two">
          <ElFormItem label="租户名称" required>
            <ElInput v-model="tenantForm.name" maxlength="256" placeholder="例如：华东运营中心" />
          </ElFormItem>
          <ElFormItem label="状态" required>
            <ElSelect v-model="tenantForm.status">
              <ElOption label="已启用" value="active" />
              <ElOption label="已停用" value="disabled" />
            </ElSelect>
          </ElFormItem>
        </div>
        <ElFormItem v-if="!editingTenantId" label="租户 ID">
          <ElInput v-model="tenantForm.tenant_id" maxlength="64" placeholder="留空自动生成" />
        </ElFormItem>
        <template v-if="!editingTenantId">
          <ElFormItem label="默认接入应用">
            <ElSwitch v-model="tenantForm.create_default_application" aria-label="创建默认接入应用" />
          </ElFormItem>
          <div v-if="tenantForm.create_default_application" class="form-grid form-grid--two">
            <ElFormItem label="应用名称">
              <ElInput v-model="tenantForm.application_name" maxlength="256" placeholder="留空使用租户名称" />
            </ElFormItem>
            <ElFormItem label="每日调用额度">
              <ElInputNumber
                v-model="tenantForm.daily_quota"
                :min="0"
                :max="1000000000"
                controls-position="right"
                placeholder="不限"
              />
            </ElFormItem>
            <ElFormItem label="应用负责人">
              <ElInput v-model="tenantForm.owner" maxlength="256" />
            </ElFormItem>
            <ElFormItem label="每分钟限流">
              <ElInputNumber
                v-model="tenantForm.rate_limit_per_minute"
                :min="0"
                :max="1000000000"
                controls-position="right"
                placeholder="不限"
              />
            </ElFormItem>
            <ElFormItem label="突发限流">
              <ElInputNumber
                v-model="tenantForm.rate_limit_burst"
                :min="0"
                :max="1000000000"
                controls-position="right"
                placeholder="不限"
              />
            </ElFormItem>
            <ElFormItem label="权限范围" class="form-grid__wide">
              <ElInput v-model="tenantForm.scopes" type="textarea" maxlength="2000" />
            </ElFormItem>
          </div>
        </template>
      </ElForm>
      <template #footer>
        <ElButton @click="tenantDialogOpen = false">取消</ElButton>
        <ElButton type="primary" :loading="submitting" :disabled="!tenantFormValid" @click="submitTenant">
          {{ editingTenantId ? "保存设置" : "创建租户" }}
        </ElButton>
      </template>
    </ElDialog>

    <ElDialog v-model="secretDialogOpen" title="保存一次性密钥" width="min(520px, calc(100vw - 24px))">
      <ElAlert title="该密钥关闭后不再显示" type="warning" show-icon :closable="false" />
      <p class="secret-tenant">{{ secretTenantName }} 默认接入应用</p>
      <div class="secret-value">
        <code>{{ oneTimeSecret }}</code>
        <ElTooltip content="复制密钥">
          <ElButton :icon="Copy" circle aria-label="复制密钥" @click="copySecret" />
        </ElTooltip>
      </div>
      <template #footer>
        <ElButton type="primary" :icon="Check" @click="secretDialogOpen = false">我已保存</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.identity-page {
  min-height: 520px;
}
.identity-rail {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-bottom: 14px;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.identity-rail__item {
  min-width: 0;
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 16px;
  border-right: 1px solid var(--line);
}
.identity-rail__item:last-child {
  border-right: 0;
}
.rail-icon,
.tenant-mark {
  width: 34px;
  height: 34px;
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  color: #075f69;
  background: #e8f3f2;
  border-radius: 4px;
}
.identity-rail__item > div {
  min-width: 0;
  display: grid;
  gap: 2px;
}
.identity-rail small,
.identity-rail span,
.management-toolbar span,
.member-cell span,
.binding-state {
  color: var(--muted);
  font-size: 12px;
}
.identity-rail strong {
  overflow: hidden;
  font-size: 18px;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.identity-boundary {
  min-height: 42px;
  display: flex;
  align-items: center;
  gap: 9px;
  margin-bottom: 18px;
  padding: 9px 12px;
  color: #705112;
  background: #fff8e8;
  border: 1px solid #ead7a4;
  border-radius: 4px;
  font-size: 13px;
}
.identity-boundary svg {
  flex: 0 0 auto;
}
.management-tabs :deep(.el-tabs__header) {
  margin-bottom: 14px;
}
.management-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
}
.management-surface {
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 6px;
}
.management-toolbar {
  min-height: 62px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}
.management-toolbar > div:first-child {
  display: flex;
  align-items: baseline;
  gap: 9px;
}
.management-toolbar h2 {
  margin: 0;
  font-size: 16px;
}
.member-filters {
  display: grid;
  grid-template-columns: minmax(220px, 1.4fr) minmax(150px, 0.8fr) minmax(120px, 0.65fr) auto;
  gap: 8px;
  width: min(780px, 72%);
}
.table-wrap :deep(.el-table) {
  --el-table-header-bg-color: #f8faf9;
}
.member-cell,
.tenant-cell,
.row-actions {
  display: flex;
  align-items: center;
}
.member-cell,
.tenant-cell {
  gap: 10px;
}
.member-cell > div,
.tenant-cell > div {
  min-width: 0;
  display: grid;
  gap: 3px;
}
.member-cell strong,
.member-cell span,
.tenant-cell strong,
.tenant-cell code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.member-avatar {
  width: 34px;
  height: 34px;
  display: grid;
  flex: 0 0 34px;
  place-items: center;
  color: #fff !important;
  background: #315c62;
  border-radius: 50%;
  font-size: 14px !important;
  font-weight: 700;
}
.tenant-name,
.tenant-cell strong,
.role-tags,
.permission-list {
  display: block;
}
.tenant-name + code,
.management-surface td > code {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 12px;
}
.role-tags,
.permission-list {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.binding-state {
  display: block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.binding-state[data-bound="true"] {
  color: #2f5b52;
  font-family: "Cascadia Code", Consolas, monospace;
}
.row-actions {
  gap: 2px;
}
.form-grid {
  display: grid;
  gap: 12px;
}
.form-grid--two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.form-grid :deep(.el-select),
.form-grid :deep(.el-input-number),
.identity-page :deep(.el-form-item .el-select) {
  width: 100%;
}
.role-selector {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.role-selector :deep(.el-checkbox) {
  height: auto;
  min-height: 54px;
  align-items: flex-start;
  margin: 0;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 4px;
}
.role-selector :deep(.el-checkbox__input) {
  margin-top: 3px;
}
.role-selector :deep(.el-checkbox__label) {
  min-width: 0;
  display: grid;
  gap: 2px;
  line-height: 1.35;
  white-space: normal;
}
.role-selector small {
  color: var(--muted);
  font-size: 12px;
}
.secret-tenant {
  margin: 16px 0 7px;
  color: var(--muted);
  font-size: 13px;
}
.secret-value {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  background: #f3f6f5;
  border: 1px solid var(--line);
  border-radius: 4px;
}
.secret-value code {
  min-width: 0;
  flex: 1;
  overflow-wrap: anywhere;
  font-size: 12px;
}

@media (max-width: 1100px) {
  .identity-rail {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .identity-rail__item:nth-child(2) {
    border-right: 0;
  }
  .identity-rail__item:nth-child(-n + 2) {
    border-bottom: 1px solid var(--line);
  }
  .member-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: min(560px, 70%);
  }
}

@media (max-width: 700px) {
  .page-actions,
  .page-actions > :deep(.el-button) {
    width: 100%;
  }
  .identity-rail {
    grid-template-columns: 1fr;
  }
  .identity-rail__item,
  .identity-rail__item:nth-child(2) {
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  .identity-rail__item:last-child {
    border-bottom: 0;
  }
  .management-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .member-filters {
    grid-template-columns: 1fr;
    width: 100%;
  }
  .management-toolbar > :deep(.el-button) {
    width: 100%;
  }
  .form-grid--two,
  .role-selector {
    grid-template-columns: 1fr;
  }
}
</style>
