// 接入中心视图：租户/应用凭证、事件回调、开放接口定义、SDK 示例与接口调试台。
// 从 views/app.js 拆分而来；依赖 app.js 中的全局 state/api/qs 等运行时函数（调用时解析）。

function randomToken(prefix = "phk") {
  const bytes = new Uint8Array(18);
  if (window.crypto && window.crypto.getRandomValues) {
    window.crypto.getRandomValues(bytes);
  } else {
    bytes.forEach((_, index) => { bytes[index] = Math.floor(Math.random() * 256); });
  }
  return `${prefix}_${Array.from(bytes).map((item) => item.toString(16).padStart(2, "0")).join("")}`;
}

function maskToken(value) {
  const text = String(value || "");
  if (!text) return "未配置";
  if (text.length <= 10) return "••••";
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
}

function selectedCheckboxValues(name) {
  return qsa(`input[name="${name}"]:checked`).map((item) => item.value);
}

function optionalLimitValue(selector) {
  const raw = qs(selector).value.trim();
  if (raw === "") return null;
  const value = Math.floor(Number(raw));
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function formatLimitValue(value, fallback = "默认") {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? String(Math.floor(numeric)) : fallback;
}

function accessAppCallSummary(app) {
  const calls = Math.max(0, Math.floor(Number(app.call_count || 0)));
  const errorRate = Math.max(0, Number(app.error_rate || 0) * 100);
  const lastCalled = app.last_called_at ? formatDateTime(app.last_called_at) : "未调用";
  return `${calls} calls · ${formatNumber(errorRate, 2)}% err · ${lastCalled}`;
}

function populateCallLogApplicationOptions() {
  const node = document.querySelector("#call-log-application-input");
  if (!node) return;
  const selected = node.value;
  const apps = state.accessApplications.map(normalizeAccessApplication);
  node.innerHTML = `<option value="">全部应用</option>${apps.map((app) => `<option value="${escapeHtml(app.id)}">${escapeHtml(app.name || app.id)}</option>`).join("")}`;
  if (apps.some((app) => app.id === selected)) node.value = selected;
}

function checkedValues(name, values) {
  const set = new Set(values || []);
  qsa(`input[name="${name}"]`).forEach((item) => { item.checked = set.has(item.value); });
}

function authMode() {
  if (state.bearer && state.apiKey) return "接口密钥 + JWT";
  if (state.bearer) return "JWT";
  if (state.apiKey) return "接口密钥";
  return "未配置令牌";
}

function normalizeAccessApplication(app) {
  const id = app.id || app.app_id || "";
  return { ...app, id, app_id: app.app_id || id };
}

function normalizeAccessTenant(tenant) {
  const id = tenant.tenant_id || tenant.id || "";
  return { ...tenant, id, tenant_id: tenant.tenant_id || id };
}
function normalizeWebhook(webhook) {
  const id = webhook.id || webhook.webhook_id || "";
  return { ...webhook, id, webhook_id: webhook.webhook_id || id };
}

function selectedAccessApp() {
  const id = qs("#access-app-id-input")?.value.trim();
  return state.accessApplications.find((item) => item.id === id) || state.accessApplications[0] || null;
}

function fillAccessAppForm(app) {
  if (!app) return;
  qs("#access-app-id-input").value = app.id || "";
  qs("#access-app-name-input").value = app.name || "";
  qs("#access-app-owner-input").value = app.owner || "";
  qs("#access-jwt-issuer-input").value = app.jwt_issuer || "";
  qs("#access-jwt-audience-input").value = app.jwt_audience || "";
  qs("#access-app-status-input").value = app.status || "active";
  qs("#access-rate-limit-input").value = app.rate_limit_per_minute ?? "";
  qs("#access-burst-input").value = app.rate_limit_burst ?? "";
  qs("#access-daily-quota-input").value = app.daily_quota ?? "";
  checkedValues("access-scope", app.scopes || []);
}

function accessPayload() {
  return {
    tenant_id: state.tenantId,
    auth_mode: authMode(),
    api_key_preview: maskToken(state.apiKey),
    bearer_preview: maskToken(state.bearer),
    last_secret_preview: state.accessLastSecret ? maskToken(state.accessLastSecret.secret) : null,
    tenants: state.accessTenants,
    tenant_catalog_warning: state.accessTenantWarning,
    applications: state.accessApplications,
  };
}

async function refreshAccessTenants() {
  try {
    const payload = await api("/v1/access/tenants");
    state.accessTenants = (payload.tenants || []).map(normalizeAccessTenant);
    state.accessTenantWarning = null;
  } catch (error) {
    state.accessTenantWarning = error.message || String(error);
  }
}

function renderAccessTenantSummary() {
  const activeCount = state.accessTenants.filter((item) => item.status !== "disabled").length;
  const appCount = state.accessTenants.reduce((sum, item) => sum + Number(item.application_count || 0), 0);
  renderSummary("#access-tenant-summary", [
    { label: "租户数", value: state.accessTenants.length || "--" },
    { label: "启用", value: state.accessTenants.length ? activeCount : "--" },
    { label: "接入应用", value: state.accessTenants.length ? appCount : "--" },
    { label: "目录状态", value: state.accessTenantWarning ? "需管理权限" : "可用" },
  ]);
}
async function refreshAccessApplications() {
  try {
    const payload = await api("/v1/access/applications");
    state.accessApplications = (payload.applications || []).map(normalizeAccessApplication);
    saveAccessApplications();
  } catch (error) {
    renderPayload("access-credentials", "#access-credentials-json", { ...accessPayload(), warning: error.message || String(error) });
  }
  await refreshAccessTenants();
  renderAccessApplications();
}

function renderAccessApplications() {
  const apps = state.accessApplications.map(normalizeAccessApplication);
  state.accessApplications = apps;
  const activeCount = apps.filter((item) => item.status !== "disabled").length;
  const scopeCount = new Set(apps.flatMap((item) => item.scopes || [])).size;
  const limitedCount = apps.filter((item) => Number(item.rate_limit_per_minute || 0) > 0 || Number(item.daily_quota || 0) > 0).length;
  const maxErrorRate = apps.reduce((max, item) => Math.max(max, Number(item.error_rate || 0)), 0);
  populateCallLogApplicationOptions();
  renderAccessTenantSummary();
  renderSummary("#access-app-summary", [
    { label: "应用数", value: apps.length },
    { label: "启用", value: activeCount },
    { label: "Scope", value: scopeCount },
    { label: "限额", value: limitedCount },
    { label: "最高错误率", value: `${formatNumber(maxErrorRate * 100, 2)}%` },
  ]);
  const node = qs("#access-app-list");
  if (node) {
    node.innerHTML = apps.length ? `
      <table class="data-table">
        <thead><tr><th>应用</th><th>状态</th><th>Scope</th><th>限额</th><th>调用</th><th>操作</th></tr></thead>
        <tbody>
          ${apps.map((app) => `
            <tr>
              <td><strong>${escapeHtml(app.name || app.id)}</strong><br><small>${escapeHtml(app.id)}</small></td>
              <td>${escapeHtml(localizeValue(app.status || "active"))}</td>
              <td>${escapeHtml((app.scopes || []).join(", ") || "--")}</td>
              <td>${escapeHtml(`${formatLimitValue(app.rate_limit_per_minute)}/min · burst ${formatLimitValue(app.rate_limit_burst, "跟随")} · day ${formatLimitValue(app.daily_quota, "不限")}${Number(app.daily_quota || 0) > 0 ? ` (${Number(app.daily_quota_used || 0)}/${Number(app.daily_quota || 0)})` : ""}`)}</td>
              <td>${escapeHtml(accessAppCallSummary(app))}</td>
              <td>
                <button type="button" class="small" data-access-edit="${escapeHtml(app.id)}">选择</button>
                <button type="button" class="small" data-access-rotate="${escapeHtml(app.id)}">轮换</button>
                <button type="button" class="small" data-access-toggle="${escapeHtml(app.id)}">${app.status === "disabled" ? "启用" : "禁用"}</button>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="data-empty">暂无接入应用</div>`;
  }
  renderPayload("access-credentials", "#access-credentials-json", accessPayload());
}

async function createAccessTenant(event) {
  event.preventDefault();
  const tenantName = qs("#access-tenant-name-input").value.trim();
  if (!tenantName) throw new Error("请输入租户名称");
  const payload = {
    name: tenantName,
    tenant_id: qs("#access-tenant-id-input").value.trim() || null,
    create_default_application: qs("#access-tenant-default-app-input").checked,
    application_name: qs("#access-tenant-app-name-input").value.trim() || null,
  };
  const data = await api("/v1/access/tenants", { method: "POST", json: payload });
  const tenant = data.tenant ? normalizeAccessTenant(data.tenant) : null;
  if (tenant?.id) {
    const tenantIndex = state.accessTenants.findIndex((item) => item.id === tenant.id);
    if (tenantIndex >= 0) state.accessTenants[tenantIndex] = tenant;
    else state.accessTenants.push(tenant);
    state.tenantId = tenant.id;
    localStorage.setItem("portraitHubTenant", state.tenantId);
    if (qs("#current-tenant-display")) qs("#current-tenant-display").textContent = state.tenantId;
    if (qs("#tenant-input")) qs("#tenant-input").value = state.tenantId;
  }
  if (data.application) {
    const app = normalizeAccessApplication(data.application);
    const found = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === app.id);
    if (found >= 0) state.accessApplications[found] = app;
    else state.accessApplications.push(app);
    if (data.one_time_secret) state.accessLastSecret = { app_id: app.id, secret: data.one_time_secret, generated_at: Date.now() };
    fillAccessAppForm(app);
    saveAccessApplications();
  }
  qs("#access-tenant-name-input").value = "";
  qs("#access-tenant-id-input").value = "";
  qs("#access-tenant-app-name-input").value = "";
  renderIntegrationSnippet();
  renderAccessApplications();
  renderPayload("access-credentials", "#access-credentials-json", data.one_time_secret ? { ...accessPayload(), tenant, one_time_secret: state.accessLastSecret } : { ...accessPayload(), tenant });
}
async function saveAccessApp(event) {
  event.preventDefault();
  const id = qs("#access-app-id-input").value.trim() || `app_${Date.now()}`;
  const payload = {
    app_id: id,
    name: qs("#access-app-name-input").value.trim() || id,
    owner: qs("#access-app-owner-input").value.trim() || "platform",
    status: qs("#access-app-status-input").value || "active",
    scopes: selectedCheckboxValues("access-scope"),
    jwt_issuer: qs("#access-jwt-issuer-input").value.trim() || null,
    jwt_audience: qs("#access-jwt-audience-input").value.trim() || null,
    rate_limit_per_minute: optionalLimitValue("#access-rate-limit-input"),
    rate_limit_burst: optionalLimitValue("#access-burst-input"),
    daily_quota: optionalLimitValue("#access-daily-quota-input"),
  };
  const existing = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === id);
  let data;
  if (existing >= 0) {
    try {
      data = await api(`/v1/access/applications/${encodeURIComponent(id)}`, { method: "PATCH", json: payload });
    } catch (error) {
      data = await api("/v1/access/applications", { method: "POST", json: payload });
    }
  } else {
    data = await api("/v1/access/applications", { method: "POST", json: payload });
  }
  const app = normalizeAccessApplication(data.application || payload);
  const found = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === app.id);
  if (found >= 0) state.accessApplications[found] = app;
  else state.accessApplications.push(app);
  if (data.one_time_secret) state.accessLastSecret = { app_id: app.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveAccessApplications();
  qs("#access-app-id-input").value = app.id;
  renderAccessApplications();
  renderPayload("access-credentials", "#access-credentials-json", data.one_time_secret ? { ...accessPayload(), one_time_secret: state.accessLastSecret } : accessPayload());
}

async function rotateAccessApp(id = null) {
  const appId = id || qs("#access-app-id-input").value.trim();
  const app = state.accessApplications.find((item) => normalizeAccessApplication(item).id === appId) || selectedAccessApp();
  if (!app) throw new Error("请先选择接入应用");
  const data = await api(`/v1/access/applications/${encodeURIComponent(normalizeAccessApplication(app).id)}/rotate`, { method: "POST" });
  const updated = normalizeAccessApplication(data.application || app);
  const found = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === updated.id);
  if (found >= 0) state.accessApplications[found] = updated;
  state.accessLastSecret = { app_id: updated.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveAccessApplications();
  renderAccessApplications();
  renderPayload("access-credentials", "#access-credentials-json", {
    ...accessPayload(),
    one_time_secret: state.accessLastSecret,
    note: "密钥只在本次轮换结果中显示；服务器端只保留哈希。",
  });
}

async function toggleAccessApp(id) {
  const app = state.accessApplications.find((item) => normalizeAccessApplication(item).id === id);
  if (!app) return;
  const nextStatus = app.status === "disabled" ? "active" : "disabled";
  const data = await api(`/v1/access/applications/${encodeURIComponent(normalizeAccessApplication(app).id)}`, { method: "PATCH", json: { status: nextStatus } });
  const updated = normalizeAccessApplication(data.application || { ...app, status: nextStatus });
  const found = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === updated.id);
  if (found >= 0) state.accessApplications[found] = updated;
  saveAccessApplications();
  renderAccessApplications();
}
function coreOpenApiPaths() {
  return [
    { scene: "人员入库", method: "POST", path: "/v1/gallery/enroll" },
    { scene: "以图搜人", method: "POST", path: "/v1/gallery/search" },
    { scene: "批量检索", method: "POST", path: "/v1/gallery/search/batch" },
    { scene: "人像比对", method: "POST", path: "/v1/compare/persons" },
    { scene: "批量比对", method: "POST", path: "/v1/compare/batch" },
    { scene: "图片解析", method: "POST", path: "/v1/infer/persons" },
    { scene: "视频任务", method: "POST", path: "/v1/jobs/video" },
    { scene: "视频任务结果", method: "GET", path: "/v1/jobs/{job_id}/result" },
    { scene: "实时流", method: "POST", path: "/v1/streams" },
    { scene: "流事件", method: "GET", path: "/v1/streams/{stream_id}/events" },
    { scene: "应用凭证", method: "GET", path: "/v1/access/applications" },
    { scene: "调用日志", method: "GET", path: "/v1/access/call-logs" },
    { scene: "事件回调", method: "GET", path: "/v1/access/webhooks" },
    { scene: "模型状态", method: "GET", path: "/v1/models" },
    { scene: "阈值", method: "GET", path: "/v1/thresholds" },
    { scene: "多模态融合", method: "POST", path: "/v1/fusion/compare" },
  ];
}

function renderOpenApiDocs(payload = state.openApiCache) {
  const baseUrl = window.location.origin;
  const schema = payload?.schema || null;
  const schemaPaths = schema?.paths || {};
  const rows = coreOpenApiPaths().map((item) => {
    const pathItem = schemaPaths[item.path] || {};
    const available = Boolean(pathItem[item.method.toLowerCase()]);
    return { ...item, available };
  });
  const loaded = Boolean(schema);
  renderSummary("#openapi-summary", [
    { label: "契约状态", value: loaded ? "已加载" : payload?.error ? "不可用" : "待刷新" },
    { label: "核心路径", value: rows.length },
    { label: "已声明", value: loaded ? rows.filter((item) => item.available).length : "--" },
    { label: "租户", value: state.tenantId },
  ]);
  qs("#openapi-path-table").innerHTML = `
    <table class="data-table">
      <thead><tr><th>场景</th><th>方法</th><th>路径</th><th>契约</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.scene)}</td><td>${escapeHtml(row.method)}</td><td>${escapeHtml(row.path)}</td><td>${escapeHtml(loaded ? (row.available ? "已声明" : "缺失") : "待刷新")}</td></tr>`).join("")}</tbody>
    </table>`;
  qs("#openapi-code").textContent = [
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/openapi.json"`,
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/v1/access/applications"`,
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/v1/models"`,
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/v1/thresholds"`,
  ].join("\n");
  renderPayload("openapi-docs", "#openapi-json", payload || {
    tenant_id: state.tenantId,
    openapi_url: `${baseUrl}/openapi.json`,
    docs_url: `${baseUrl}/docs`,
    redoc_url: `${baseUrl}/redoc`,
    core_paths: rows,
  });
}

async function refreshOpenApiDocs() {
  const baseUrl = window.location.origin;
  const payload = {
    tenant_id: state.tenantId,
    openapi_url: `${baseUrl}/openapi.json`,
    docs_url: `${baseUrl}/docs`,
    redoc_url: `${baseUrl}/redoc`,
    schema: null,
  };
  try {
    const schema = await api("/openapi.json");
    payload.schema = schema;
    payload.title = schema.info?.title || "PortraitHub API";
    payload.version = schema.info?.version || "--";
    payload.path_count = Object.keys(schema.paths || {}).length;
  } catch (error) {
    payload.error = error.message || String(error);
    payload.note = "开放接口定义可能在生产环境被关闭；受控环境可启用 ENABLE_API_DOCS 后刷新。";
  }
  state.openApiCache = payload;
  renderOpenApiDocs(payload);
}

async function refreshWebhooks() {
  try {
    const payload = await api("/v1/access/webhooks");
    state.webhooks = (payload.webhooks || []).map(normalizeWebhook);
    saveWebhooks();
  } catch (error) {
    renderPayload("webhooks", "#webhook-json", webhookPayload({ warning: error.message || String(error) }));
  }
  renderWebhooks();
}

function selectedWebhook() {
  const id = qs("#webhook-id-input")?.value.trim();
  return state.webhooks.find((item) => normalizeWebhook(item).id === id) || state.webhooks[0] || null;
}

function populateWebhookAppOptions(selectedId = "") {
  const options = state.accessApplications.map((app) => {
    const normalized = normalizeAccessApplication(app);
    return `<option value="${escapeHtml(normalized.id)}" ${normalized.id === selectedId ? "selected" : ""}>${escapeHtml(normalized.name || normalized.id)}</option>`;
  }).join("");
  qs("#webhook-app-input").innerHTML = options || `<option value="default-client">默认接入应用</option>`;
}

function fillWebhookForm(webhook) {
  if (!webhook) return;
  const normalized = normalizeWebhook(webhook);
  populateWebhookAppOptions(normalized.application_id || state.accessApplications[0]?.id || "default-client");
  qs("#webhook-id-input").value = normalized.id || "";
  qs("#webhook-name-input").value = normalized.name || "";
  qs("#webhook-url-input").value = normalized.url || "";
  qs("#webhook-status-input").value = normalized.status || "disabled";
  qs("#webhook-retry-input").value = normalized.retry_limit ?? 3;
  qs("#webhook-timeout-input").value = normalized.timeout_seconds ?? 5;
  checkedValues("webhook-event", normalized.events || []);
}

function webhookPayload(extra = {}) {
  return {
    tenant_id: state.tenantId,
    last_secret_preview: state.webhookLastSecret ? maskToken(state.webhookLastSecret.secret) : null,
    webhooks: state.webhooks,
    ...extra,
  };
}

function renderWebhooks() {
  state.webhooks = state.webhooks.map(normalizeWebhook);
  populateWebhookAppOptions(selectedWebhook()?.application_id || state.accessApplications[0]?.id || "default-client");
  if (!qs("#webhook-id-input").value && state.webhooks[0]) fillWebhookForm(state.webhooks[0]);
  const enabledCount = state.webhooks.filter((item) => item.status !== "disabled").length;
  const eventCount = new Set(state.webhooks.flatMap((item) => item.events || [])).size;
  renderSummary("#webhook-summary", [
    { label: "端点数", value: state.webhooks.length },
    { label: "启用", value: enabledCount },
    { label: "事件", value: eventCount },
    { label: "租户", value: state.tenantId },
  ]);
  qs("#webhook-list").innerHTML = state.webhooks.length ? `
    <table class="data-table">
      <thead><tr><th>端点</th><th>应用</th><th>事件</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${state.webhooks.map((webhook) => `
        <tr>
          <td><strong>${escapeHtml(webhook.name || webhook.id)}</strong><br><small>${escapeHtml(webhook.url || "未配置 URL")}</small></td>
          <td>${escapeHtml(webhook.application_id || "--")}</td>
          <td>${escapeHtml((webhook.events || []).join(", ") || "--")}</td>
          <td>${escapeHtml(localizeValue(webhook.status || "disabled"))}</td>
          <td>
            <button type="button" class="small" data-webhook-edit="${escapeHtml(webhook.id)}">选择</button>
            <button type="button" class="small" data-webhook-rotate="${escapeHtml(webhook.id)}">轮换</button>
            <button type="button" class="small" data-webhook-sample="${escapeHtml(webhook.id)}">样例</button>
            <button type="button" class="small" data-webhook-toggle="${escapeHtml(webhook.id)}">${webhook.status === "disabled" ? "启用" : "禁用"}</button>
          </td>
        </tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无 事件回调端点</div>`;
  renderPayload("webhooks", "#webhook-json", webhookPayload());
}

async function saveWebhook(event) {
  event.preventDefault();
  const id = qs("#webhook-id-input").value.trim() || `wh_${Date.now()}`;
  const payload = {
    webhook_id: id,
    name: qs("#webhook-name-input").value.trim() || id,
    application_id: qs("#webhook-app-input").value || state.accessApplications[0]?.id || "default-client",
    url: qs("#webhook-url-input").value.trim() || null,
    status: qs("#webhook-status-input").value || "disabled",
    events: selectedCheckboxValues("webhook-event"),
    retry_limit: Number(qs("#webhook-retry-input").value || 0),
    timeout_seconds: Number(qs("#webhook-timeout-input").value || 5),
  };
  let data;
  const existing = state.webhooks.findIndex((item) => normalizeWebhook(item).id === id);
  if (existing >= 0) {
    try {
      data = await api(`/v1/access/webhooks/${encodeURIComponent(id)}`, { method: "PATCH", json: payload });
    } catch (error) {
      data = await api("/v1/access/webhooks", { method: "POST", json: payload });
    }
  } else {
    data = await api("/v1/access/webhooks", { method: "POST", json: payload });
  }
  const webhook = normalizeWebhook(data.webhook || payload);
  const found = state.webhooks.findIndex((item) => normalizeWebhook(item).id === webhook.id);
  if (found >= 0) state.webhooks[found] = webhook;
  else state.webhooks.push(webhook);
  if (data.one_time_secret) state.webhookLastSecret = { webhook_id: webhook.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveWebhooks();
  qs("#webhook-id-input").value = webhook.id;
  renderWebhooks();
  renderPayload("webhooks", "#webhook-json", data.one_time_secret ? webhookPayload({ one_time_secret: state.webhookLastSecret }) : webhookPayload());
}

async function rotateWebhookSecret(id = null) {
  const webhookId = id || qs("#webhook-id-input").value.trim();
  const webhook = state.webhooks.find((item) => normalizeWebhook(item).id === webhookId) || selectedWebhook();
  if (!webhook) throw new Error("请先选择事件回调");
  const data = await api(`/v1/access/webhooks/${encodeURIComponent(normalizeWebhook(webhook).id)}/rotate`, { method: "POST" });
  const updated = normalizeWebhook(data.webhook || webhook);
  const found = state.webhooks.findIndex((item) => normalizeWebhook(item).id === updated.id);
  if (found >= 0) state.webhooks[found] = updated;
  state.webhookLastSecret = { webhook_id: updated.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveWebhooks();
  renderWebhooks();
  renderPayload("webhooks", "#webhook-json", webhookPayload({ one_time_secret: state.webhookLastSecret }));
}

async function toggleWebhook(id) {
  const webhook = state.webhooks.find((item) => normalizeWebhook(item).id === id);
  if (!webhook) return;
  const nextStatus = webhook.status === "disabled" ? "active" : "disabled";
  const data = await api(`/v1/access/webhooks/${encodeURIComponent(normalizeWebhook(webhook).id)}`, { method: "PATCH", json: { status: nextStatus } });
  const updated = normalizeWebhook(data.webhook || { ...webhook, status: nextStatus });
  const found = state.webhooks.findIndex((item) => normalizeWebhook(item).id === updated.id);
  if (found >= 0) state.webhooks[found] = updated;
  saveWebhooks();
  renderWebhooks();
}

async function renderWebhookSample(id = null) {
  const webhook = state.webhooks.find((item) => normalizeWebhook(item).id === id) || selectedWebhook();
  if (!webhook) throw new Error("请先选择事件回调");
  const data = await api(`/v1/access/webhooks/${encodeURIComponent(normalizeWebhook(webhook).id)}/sample`, { method: "POST" });
  renderPayload("webhooks", "#webhook-json", webhookPayload(data));
}

function renderSdkExamples() {
  const baseUrl = window.location.origin;
  const app = selectedAccessApp() || state.accessApplications[0] || {};
  const python = `import os\nfrom pathlib import Path\nfrom sdk.python.portrait_hub_client import PortraitHubClient\n\nclient = PortraitHubClient(\n    base_url="${baseUrl}",\n    api_token=os.getenv("PORTRAIT_HUB_API_TOKEN"),\n    auth_scheme="api_key",\n)\nresult = client.search(Path("query.jpg"), modality="body", top_k=5, threshold_profile="normal")\nprint(result["request_id"], result.get("data", {}).get("candidate_count"))`;
  const nodeSnippet = `const { PortraitHubClient } = require("./sdk/node/portraitHubClient");\n\nconst client = new PortraitHubClient({\n  baseUrl: "${baseUrl}",\n  apiToken: process.env.PORTRAIT_HUB_API_TOKEN,\n  authScheme: "api_key",\n});\n\nconst result = await client.comparePersons("a.jpg", "b.jpg", "normal");\nconsole.log(result.request_id, result.data?.passed);`;
  const curl = requestSnippet("/v1/gallery/search", ["file=@query.jpg", "modality=body", "top_k=5", "threshold_profile=normal"]);
  const batch = `import os\nfrom pathlib import Path\nfrom sdk.python.portrait_hub_client import PortraitHubClient\n\nclient = PortraitHubClient(\n    base_url="${baseUrl}",\n    api_token=os.getenv("PORTRAIT_HUB_API_TOKEN"),\n    auth_scheme="api_key",\n)\nbatch = client.search_batch(\n    [Path("query-a.jpg"), Path("query-b.jpg")],\n    modality="body",\n    top_k=10,\n    threshold_profile="normal",\n    async_mode=True,\n)\nbatch_id = batch.get("data", {}).get("batch_id")\nprint(batch["request_id"], batch_id)`;
  const video = `const { PortraitHubClient } = require("./sdk/node/portraitHubClient");\n\nconst wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));\nconst terminal = new Set(["completed", "failed", "cancelled"]);\nconst client = new PortraitHubClient({\n  baseUrl: "${baseUrl}",\n  apiToken: process.env.PORTRAIT_HUB_API_TOKEN,\n  authScheme: "api_key",\n});\n\nconst job = await client.createVideoJob("sample.mp4", { frameInterval: 5, maxFrames: 120 });\nconst jobId = job.data?.job?.job_id ?? job.data?.job_id;\nlet status = job;\nwhile (jobId && !terminal.has(status.data?.job?.status)) {\n  await wait(2000);\n  status = await client.getJob(jobId);\n}\nconst result = jobId ? await client.jobResult(jobId) : {};\nconsole.log(jobId, result.request_id);`;
  qs("#sdk-python-code").textContent = python;
  qs("#sdk-node-code").textContent = nodeSnippet;
  qs("#sdk-curl-code").textContent = curl;
  qs("#sdk-batch-code").textContent = batch;
  qs("#sdk-video-code").textContent = video;
  renderPayload("sdk-examples", "#sdk-json", {
    tenant_id: state.tenantId,
    selected_application: app.id || null,
    scopes: app.scopes || [],
    examples: { python, node: nodeSnippet, curl, batch, video },
  });
}

function playgroundMethod(endpoint) {
  return endpoint === "/v1/models" || endpoint === "/v1/thresholds" || endpoint === "/v1/streams/{stream_id}/events" ? "GET" : "POST";
}

function playgroundSelection() {
  const select = qs("#playground-endpoint-input");
  const option = select.options[select.selectedIndex];
  return {
    template: select.value,
    method: option?.dataset.method || playgroundMethod(select.value),
  };
}

function resolvePlaygroundPath(template, strict = false) {
  if (!template.includes("{stream_id}")) return template;
  const streamId = qs("#playground-stream-id-input").value.trim();
  if (!streamId) {
    if (strict) throw new Error("请填写流 ID");
    return template;
  }
  return template.replace("{stream_id}", encodeURIComponent(streamId));
}

function withPlaygroundLimit(path, template) {
  if (template !== "/v1/streams" && template !== "/v1/streams/{stream_id}/events") return path;
  const limit = qs("#playground-top-k-input").value || "5";
  const params = new URLSearchParams({ limit });
  return `${path}${path.includes("?") ? "&" : "?"}${params.toString()}`;
}

function appendFile(form, name, input) {
  const file = formFiles(input)[0];
  if (file) form.set(name, file);
  return file;
}

function appendFiles(form, name, input) {
  const files = formFiles(input);
  files.forEach((file) => form.append(name, file));
  return files;
}

function requirePlaygroundFiles(files, label) {
  if (!files.length) throw new Error(`请选择${label}`);
  return files;
}

function playgroundErrorCode(raw) {
  if (raw.error_code) return raw.error_code;
  const detail = raw.payload?.detail;
  if (typeof detail === "string") return detail;
  return null;
}

function renderPlaygroundRequestPreview() {
  const selection = playgroundSelection();
  const resolved = resolvePlaygroundPath(selection.template, false);
  const method = selection.method;
  const fileACount = formFiles(qs("#playground-file-a-input")).length;
  const fileBCount = formFiles(qs("#playground-file-b-input")).length;
  const lines = [
    `${method} ${method === "GET" ? withPlaygroundLimit(resolved, selection.template) : resolved}`,
    selection.template !== resolved ? `Template: ${selection.template}` : null,
    `X-Tenant-ID: ${state.tenantId}`,
    state.apiKey ? `X-API-Key: ${maskToken(state.apiKey)}` : "X-API-Key: 未配置",
    state.bearer ? `Authorization: Bearer ${maskToken(state.bearer)}` : "Authorization: 未配置",
  ].filter(Boolean);
  if (method === "POST" && selection.template === "/v1/streams") {
    lines.push("Content-Type: application/json");
    lines.push(`stream_url: ${qs("#playground-stream-url-input").value.trim() || "<required>"}`);
    lines.push(`name: ${qs("#playground-stream-name-input").value.trim() || "<optional>"}`);
  } else if (method === "POST") {
    lines.push("Content-Type: multipart/form-data");
    lines.push(`file_a_count: ${fileACount}`);
    lines.push(`file_b_count: ${fileBCount}`);
    lines.push(`threshold_profile: ${qs("#playground-threshold-input").value.trim() || "normal"}`);
    lines.push(`top_k: ${qs("#playground-top-k-input").value || "5"}`);
    lines.push(`async_mode: ${qs("#playground-async-mode-input").checked ? "true" : "false"}`);
  }
  lines.push("controlled_use: dev_or_approved_intranet; server_call_logs_audit=true");
  qs("#playground-request-code").textContent = lines.join("\n");
}

function buildPlaygroundForm(endpoint) {
  const form = new FormData();
  const fileAInput = qs("#playground-file-a-input");
  const fileBInput = qs("#playground-file-b-input");
  const thresholdProfile = qs("#playground-threshold-input").value.trim() || "normal";
  const topK = qs("#playground-top-k-input").value || "5";
  const asyncMode = qs("#playground-async-mode-input").checked ? "true" : "false";

  if (endpoint === "/v1/gallery/search") {
    requirePlaygroundFiles([appendFile(form, "file", fileAInput)].filter(Boolean), "查询图片");
    form.set("modality", "body");
    form.set("top_k", topK);
    form.set("threshold_profile", thresholdProfile);
  } else if (endpoint === "/v1/gallery/search/batch") {
    requirePlaygroundFiles(appendFiles(form, "files", fileAInput), "批量查询图片");
    form.set("modality", "body");
    form.set("top_k", topK);
    form.set("threshold_profile", thresholdProfile);
    form.set("async_mode", asyncMode);
  } else if (endpoint === "/v1/compare/persons" || endpoint === "/v1/fusion/compare") {
    requirePlaygroundFiles([appendFile(form, "image_a", fileAInput)].filter(Boolean), "文件 A");
    requirePlaygroundFiles([appendFile(form, "image_b", fileBInput)].filter(Boolean), "文件 B");
    form.set("threshold_profile", thresholdProfile);
    if (endpoint === "/v1/fusion/compare") form.set("modalities", "face,body,appearance");
  } else if (endpoint === "/v1/compare/batch") {
    const filesA = appendFiles(form, "image_a", fileAInput);
    const filesB = appendFiles(form, "image_b", fileBInput);
    requirePlaygroundFiles(filesA, "批量文件 A");
    requirePlaygroundFiles(filesB, "批量文件 B");
    if (filesA.length !== filesB.length) throw new Error("批量比对的文件 A/B 数量需要一致");
    form.set("modality", "body");
    form.set("threshold_profile", thresholdProfile);
    form.set("async_mode", asyncMode);
  } else if (endpoint === "/v1/infer/persons") {
    requirePlaygroundFiles(appendFiles(form, "files", fileAInput), "解析图片");
  } else if (endpoint === "/v1/jobs/video") {
    requirePlaygroundFiles([appendFile(form, "file", fileAInput)].filter(Boolean), "视频文件");
  }
  return form;
}

async function submitPlayground(event) {
  event.preventDefault();
  const selection = playgroundSelection();
  const method = selection.method;
  const endpoint = resolvePlaygroundPath(selection.template, true);
  const started = performance.now();
  try {
    let raw;
    if (method === "GET") {
      raw = await apiRaw(withPlaygroundLimit(endpoint, selection.template));
    } else if (selection.template === "/v1/streams") {
      const streamUrl = qs("#playground-stream-url-input").value.trim();
      if (!streamUrl) throw new Error("请输入流地址");
      raw = await apiRaw(endpoint, {
        method: "POST",
        json: {
          stream_url: streamUrl,
          name: qs("#playground-stream-name-input").value.trim() || null,
          settings: {},
          metadata: { source: "api_playground" },
        },
      });
    } else {
      raw = await apiRaw(endpoint, { method: "POST", body: buildPlaygroundForm(selection.template) });
    }
    const latency = Math.round(performance.now() - started);
    const errorCode = playgroundErrorCode(raw);
    renderSummary("#playground-summary", [
      { label: "状态", value: raw.ok ? "成功" : "异常" },
      { label: "HTTP", value: raw.status_code },
      { label: "耗时", value: `${latency}ms` },
      { label: "请求 ID", value: raw.request_id || "--" },
      { label: "错误码", value: errorCode || "--" },
      { label: "接口", value: endpoint },
    ]);
    renderPayload("api-playground", "#playground-json", {
      endpoint,
      endpoint_template: selection.template,
      method,
      http_status: raw.status_code,
      latency_ms: latency,
      request_id: raw.request_id,
      error_code: errorCode,
      controlled_use: "dev_or_approved_intranet",
      response: raw.payload,
    });
    if (!raw.ok) setStatus(errorCode || raw.status_text || "接口调试请求失败", true);
  } catch (error) {
    const latency = Math.round(performance.now() - started);
    renderSummary("#playground-summary", [
      { label: "状态", value: "异常" },
      { label: "HTTP", value: "--" },
      { label: "耗时", value: `${latency}ms` },
      { label: "请求 ID", value: "--" },
      { label: "错误码", value: "client_error" },
      { label: "接口", value: selection.template },
    ]);
    renderPayload("api-playground", "#playground-json", {
      endpoint: selection.template,
      endpoint_template: selection.template,
      method,
      latency_ms: latency,
      error_code: "client_error",
      error: error.message || String(error),
    });
    throw error;
  }
}
