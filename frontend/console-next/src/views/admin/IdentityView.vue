<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { Building2, ExternalLink, ShieldCheck, UserRound } from "@lucide/vue";
import { ElAlert, ElButton, ElTag } from "element-plus";

import { ApiError, apiRequest } from "../../api/client";
import type { IdentityAdminPayload } from "../../api/contracts";
import { useCapabilitiesStore } from "../../stores/capabilities";

const capabilities = useCapabilitiesStore();
const profile = computed(() => capabilities.capabilities);
const adminPayload = ref<IdentityAdminPayload | null>(null);
const loading = ref(false);
const errorMessage = ref("");
const identity = computed(() => adminPayload.value?.identity ?? profile.value?.identity);
const roleLabels: Record<string, string> = {
  admin: "超级管理员",
  operator: "业务管理员",
  algorithm: "算法人员",
  auditor: "审计人员",
  viewer: "只读用户",
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
};
const authKindLabels: Record<string, string> = {
  oidc: "企业账号",
  local: "本地账号",
  jwt: "JWT",
  global_api_token: "全局接口令牌",
  application_api_key: "应用接口密钥",
  development_anonymous: "本地开发身份",
};

function permissionLabel(value: string): string {
  return permissionLabels[value] ?? value;
}

async function loadIdentityAdmin(): Promise<void> {
  loading.value = true;
  errorMessage.value = "";
  try {
    adminPayload.value = await apiRequest<IdentityAdminPayload>("/v1/admin/identity");
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : "身份权限信息加载失败";
  } finally {
    loading.value = false;
  }
}

function openIdentityAdmin(): void {
  const url = identity.value?.identity_admin_url;
  if (url) window.open(url, "_blank", "noopener,noreferrer");
}

onMounted(() => void loadIdentityAdmin());
</script>

<template>
  <div>
    <header class="page-header">
      <div>
        <h1>身份与权限</h1>
        <p>查看当前身份来源、租户角色和权限策略；用户生命周期由企业身份平台统一管理。</p>
      </div>
      <ElButton
        v-if="identity?.identity_admin_url"
        type="primary"
        :icon="ExternalLink"
        @click="openIdentityAdmin"
      >
        管理企业用户
      </ElButton>
    </header>

    <ElAlert v-if="errorMessage" :title="errorMessage" type="error" show-icon :closable="false" />

    <section class="identity-summary" aria-label="身份摘要">
      <div>
        <span class="summary-icon"><UserRound :size="20" /></span>
        <dl>
          <dt>当前用户</dt>
          <dd>{{ profile?.display_name || profile?.subject || "--" }}</dd>
          <small>{{ profile?.email || profile?.subject || "--" }}</small>
        </dl>
      </div>
      <div>
        <span class="summary-icon"><Building2 :size="20" /></span>
        <dl>
          <dt>身份来源</dt>
          <dd>{{ authKindLabels[profile?.auth_kind || ""] || profile?.auth_kind || "--" }}</dd>
          <small>{{ identity?.enabled ? identity.provider_name : "企业登录未启用" }}</small>
        </dl>
      </div>
      <div>
        <span class="summary-icon"><ShieldCheck :size="20" /></span>
        <dl>
          <dt>租户与角色</dt>
          <dd>{{ profile?.tenant_id || "--" }}</dd>
          <small>{{ profile?.roles.map((role) => roleLabels[role] || role).join("、") || "未分配角色" }}</small>
        </dl>
      </div>
    </section>

    <section v-loading="loading" class="role-section" aria-labelledby="role-title">
      <div class="section-heading">
        <div>
          <h2 id="role-title">角色权限矩阵</h2>
          <p>角色由企业身份平台的用户组或声明映射，影鉴按以下策略执行授权。</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>角色</th>
              <th>定位</th>
              <th>权限</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="role in adminPayload?.roles || []" :key="role.role">
              <td>
                <strong>{{ roleLabels[role.role] || role.role }}</strong>
                <code>{{ role.role }}</code>
              </td>
              <td>
                {{
                  role.role === "admin"
                    ? "平台全局管理"
                    : role.role === "operator"
                      ? "日常业务操作与资源管理"
                      : role.role === "algorithm"
                        ? "模型、算法与阈值维护"
                        : role.role === "auditor"
                          ? "审计、导出与只读运维"
                          : "业务数据只读访问"
                }}
              </td>
              <td>
                <div class="permission-list">
                  <ElTag v-for="permission in role.permissions" :key="permission" effect="plain">
                    {{ permissionLabel(permission) }}
                  </ElTag>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.identity-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 1px;
  margin-bottom: 24px;
  background: #d8e0de;
  border: 1px solid #d8e0de;
}
.identity-summary > div {
  min-width: 0;
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 17px;
  background: #fff;
}
.summary-icon {
  width: 36px;
  height: 36px;
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  color: #075f69;
  background: #e8f3f2;
  border-radius: 4px;
}
.identity-summary dl {
  min-width: 0;
  margin: 0;
}
.identity-summary dt,
.identity-summary small,
.role-section p {
  color: #62706d;
  font-size: 12px;
}
.identity-summary dd {
  margin: 4px 0;
  overflow-wrap: anywhere;
  font-weight: 650;
}
.role-section {
  margin-top: 4px;
}
.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-bottom: 12px;
}
.section-heading h2 {
  margin: 0;
  font-size: 17px;
}
.section-heading p {
  margin: 5px 0 0;
}
.data-table td:first-child {
  min-width: 150px;
}
.data-table td:first-child strong,
.data-table td:first-child code {
  display: block;
}
.data-table td:first-child code {
  margin-top: 4px;
  color: #62706d;
  font-size: 12px;
}
.permission-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
@media (max-width: 900px) {
  .identity-summary {
    grid-template-columns: 1fr;
  }
}
</style>
