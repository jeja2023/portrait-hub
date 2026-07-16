const consoleConfig = window.PortraitConsoleConfig || {};

const state = {
  tenantId: localStorage.getItem("portraitHubTenant") || "default",
  apiKey: localStorage.getItem("portraitHubApiKey") || "",
  bearer: localStorage.getItem("portraitHubBearer") || "",
  view: localStorage.getItem("portraitHubView") || "overview",
  analysisResultsTab: localStorage.getItem("portraitHubAnalysisResultsTab") || "image",
  isLoggedIn: localStorage.getItem("portraitHubLoggedIn") === "true",
  accessApplications: loadAccessApplications(),
  accessTenants: [],
  accessTenantWarning: null,
  accessLastSecret: null,
  webhooks: loadWebhooks(),
  webhookLastSecret: null,
  openApiCache: null,
  dashboard: {},
  galleryExport: {},
  latestPayloads: {},
  callLogs: [],
  errorCodes: null,
  analysisResults: {
    image: [],
    video: null,
    stream: null,
  },
  alertConfig: loadAlertConfig(),
  sockets: {},
  visionPreviews: [],
  visionPreviewSignature: "",
  visionResultVisuals: [],
  visionLightboxIndex: null,
  visionLightboxReturnFocus: null,
  comparePreviews: { A: [], B: [] },
};

const endpointMap = consoleConfig.endpointMap || {};
const fallbackNavigation = {
  sections: [
    { id: "overview", label: "\u603b\u89c8", standalone: true },
    {
      id: "analysis",
      label: "\u667a\u80fd\u5206\u6790",
      items: [
        { view: "vision", label: "\u56fe\u7247\u89e3\u6790" },
        { view: "video", label: "\u89c6\u9891\u4efb\u52a1" },
        { view: "streams", label: "\u5b9e\u65f6\u89c6\u9891\u6d41" },
        { view: "video-results", label: "\u89e3\u6790\u7ed3\u679c" },
      ],
    },
    {
      id: "retrieval",
      label: "\u6bd4\u5bf9\u68c0\u7d22",
      items: [
        { view: "compare", label: "\u4eba\u50cf\u6bd4\u5bf9" },
        { view: "multimodal-compare", label: "\u878d\u5408\u6bd4\u5bf9" },
        { view: "gallery-search", label: "\u4ee5\u56fe\u641c\u4eba" },
      ],
    },
    {
      id: "gallery",
      label: "\u4eba\u5458\u5e93",
      items: [
        { view: "gallery-enroll", label: "\u4eba\u5458\u6ce8\u518c" },
        { view: "gallery-manage", label: "\u4eba\u5458\u7ba1\u7406" },
        { view: "gallery-rebuild", label: "\u7279\u5f81\u91cd\u5efa" },
      ],
    },
    {
      id: "access",
      label: "\u63a5\u5165\u4e2d\u5fc3",
      items: [
        { view: "access-credentials", label: "\u5e94\u7528\u51ed\u8bc1" },
        { view: "sdk-examples", label: "SDK \u793a\u4f8b" },
        { view: "api-playground", label: "\u63a5\u53e3\u8c03\u8bd5\u53f0" },
        { view: "openapi-docs", label: "\u5f00\u653e\u63a5\u53e3\u5b9a\u4e49" },
        { view: "error-codes", label: "\u9519\u8bef\u7801" },
        { view: "webhooks", label: "\u4e8b\u4ef6\u56de\u8c03" },
        { view: "call-logs", label: "\u8c03\u7528\u65e5\u5fd7" },
      ],
    },
    {
      id: "model-governance",
      label: "\u6a21\u578b\u4e0e\u8bc4\u4f30",
      items: [
        { view: "models", label: "\u6a21\u578b\u7ba1\u7406" },
        { view: "admin-threshold", label: "\u6bd4\u5bf9\u9608\u503c" },
        { view: "track-review", label: "\u8f68\u8ff9\u5ba1\u9605" },
        { view: "evaluation-center", label: "\u56de\u5f52\u8bc4\u4f30" },
        { view: "release-center", label: "\u6a21\u578b\u53d1\u5e03" },
      ],
    },
    {
      id: "ops",
      label: "\u8fd0\u7ef4\u5408\u89c4",
      items: [
        { view: "slo-panel", label: "SLO \u9762\u677f" },
        { view: "alerts", label: "\u544a\u8b66\u8bc4\u4f30" },
        { view: "admin-data", label: "\u6570\u636e\u4fdd\u7559\u4e0e\u5907\u4efd" },
        { view: "audit-compliance", label: "\u5408\u89c4\u5ba1\u8ba1" },
      ],
    },
  ],
  overviewShortcuts: [
    { view: "vision", title: "\u56fe\u7247\u89e3\u6790", description: "\u4eba\u8138\u3001\u4eba\u4f53\u3001\u59ff\u6001\u3001\u8863\u7740\u3001\u6b65\u6001\u548c ReID \u5411\u91cf\u3002" },
    { view: "video", title: "\u89c6\u9891\u4efb\u52a1", description: "\u79bb\u7ebf\u89c6\u9891\u4efb\u52a1\u521b\u5efa\u3001\u72b6\u6001\u8ddf\u8e2a\u548c\u7ed3\u679c\u56de\u6536\u3002" },
    { view: "streams", title: "\u5b9e\u65f6\u89c6\u9891\u6d41", description: "RTSP/HTTP \u6ce8\u518c\u3001\u542f\u52a8\u3001\u4e8b\u4ef6\u67e5\u8be2\u548c\u8ba2\u9605\u3002" },
    { view: "compare", title: "\u4eba\u50cf\u6bd4\u5bf9", description: "1:1 \u4eba\u8138\u3001\u4eba\u4f53\u3001\u6b65\u6001\u548c\u6279\u91cf\u6210\u5bf9\u6bd4\u5bf9\u3002" },
    { view: "gallery-search", title: "\u4ee5\u56fe\u641c\u4eba", description: "1:N \u68c0\u7d22\u3001\u5019\u9009\u6392\u5e8f\u548c\u4eba\u5458\u7ea7\u805a\u5408\u7ed3\u679c\u3002" },
    { view: "gallery-enroll", title: "\u4eba\u5458\u6ce8\u518c", description: "\u591a\u56fe\u5165\u5e93\u3001\u91cd\u590d\u8df3\u8fc7\u548c\u7279\u5f81\u8d28\u91cf\u6821\u9a8c\u3002" },
    { view: "gallery-rebuild", title: "\u7279\u5f81\u91cd\u5efa", description: "\u6309\u6a21\u6001\u548c\u6a21\u578b\u91cd\u5efa\u5e95\u5e93\u5411\u91cf\u7d22\u5f15\u3002" },
    { view: "access-credentials", title: "\u63a5\u5165\u914d\u7f6e", description: "\u5e94\u7528\u51ed\u8bc1\u3001\u8c03\u7528\u6743\u9650\u548c\u5bc6\u94a5\u8f6e\u6362\u3002" },
    { view: "models", title: "\u6a21\u578b\u7ba1\u7406", description: "\u6a21\u578b\u72b6\u6001\u3001\u52a0\u8f7d\u5378\u8f7d\u3001\u522b\u540d\u4e0e\u751f\u4ea7\u80fd\u529b\u68c0\u67e5\u3002" },
  ],
};

function currentNavigation() {
  const navigation = (window.PortraitConsoleModules || {}).navigation || {};
  return {
    sections: Array.isArray(navigation.sections) && navigation.sections.length
      ? navigation.sections
      : fallbackNavigation.sections,
    overviewShortcuts: Array.isArray(navigation.overviewShortcuts) && navigation.overviewShortcuts.length
      ? navigation.overviewShortcuts
      : fallbackNavigation.overviewShortcuts,
  };
}

function navigationSections() {
  return currentNavigation().sections;
}

function overviewShortcuts() {
  return currentNavigation().overviewShortcuts;
}
function renderNavigation() {
  const items = navigationSections().map((section) => {
    if (section.standalone) {
      return `<button type="button" class="nav-item nav-item--solo" data-nav="${section.id}">${section.label}</button>`;
    }
    const buttons = (section.items || [])
      .map((item) => `<button type="button" class="nav-item" data-nav="${item.view}">${item.label}</button>`)
      .join("");
    return `
        <details class="nav-group" data-nav-group="${section.id}">
          <summary>${section.label}</summary>
          <div class="nav-group-items">
            ${buttons}
          </div>
        </details>`;
  });
  return `<nav class="sidebar-nav" aria-label="控制台视图">${items.join("")}</nav>`;
}

function renderOverviewShortcuts() {
  return overviewShortcuts()
    .map((item) => `<button type="button" class="product-tile" data-nav-shortcut="${item.view}"><strong>${item.title}</strong><span>${item.description}</span></button>`)
    .join("");
}
function defaultAlertConfig() {
  return {
    maxErrorRate: Number(consoleConfig.alertDefaults?.maxErrorRate ?? 0.05),
    maxP95Latency: Number(consoleConfig.alertDefaults?.maxP95Latency ?? 1.5),
    minFreeGpuMemoryGb: Number(consoleConfig.alertDefaults?.minFreeGpuMemoryGb ?? 1),
  };
}

function loadAlertConfig() {
  const defaults = defaultAlertConfig();
  try {
    const payload = JSON.parse(localStorage.getItem("portraitHubAlertConfig") || "{}");
    return {
      maxErrorRate: Number(payload.maxErrorRate ?? defaults.maxErrorRate),
      maxP95Latency: Number(payload.maxP95Latency ?? defaults.maxP95Latency),
      minFreeGpuMemoryGb: Number(payload.minFreeGpuMemoryGb ?? defaults.minFreeGpuMemoryGb),
    };
  } catch {
    return defaults;
  }
}

function defaultAccessApplications() {
  return [
    {
      id: "default-client",
      name: "默认接入应用",
      owner: "platform",
      status: "active",
      scopes: ["infer", "compare", "gallery:read", "gallery:write"],
      jwt_issuer: "",
      jwt_audience: "",
      created_at: Date.now(),
      last_called_at: null,
      error_rate: 0,
    },
  ];
}

function loadAccessApplications() {
  try {
    const payload = JSON.parse(localStorage.getItem("portraitHubAccessApplications") || "[]");
    return Array.isArray(payload) && payload.length ? payload : defaultAccessApplications();
  } catch {
    return defaultAccessApplications();
  }
}

function saveAccessApplications() {
  localStorage.setItem("portraitHubAccessApplications", JSON.stringify(state.accessApplications));
}

function defaultWebhooks() {
  return [
    {
      id: "default-webhook",
      name: "默认事件回调",
      application_id: "default-client",
      url: "",
      status: "disabled",
      events: ["job.completed", "stream.event", "gallery.enrolled"],
      retry_limit: 3,
      timeout_seconds: 5,
      created_at: Date.now(),
      last_delivery_at: null,
      failure_count: 0,
      signing_secret_preview: null,
    },
  ];
}

function loadWebhooks() {
  try {
    const payload = JSON.parse(localStorage.getItem("portraitHubWebhooks") || "[]");
    return Array.isArray(payload) && payload.length ? payload : defaultWebhooks();
  } catch {
    return defaultWebhooks();
  }
}

function saveWebhooks() {
  localStorage.setItem("portraitHubWebhooks", JSON.stringify(state.webhooks));
}

const template = (window.PortraitConsoleModules || {}).templates.build({
  renderNavigation,
  renderOverviewShortcuts,
});


const viewRefreshHandlers = {
  overview: refreshDashboard,
  "video-results": refreshActiveAnalysisResults,
  streams: refreshStreams,
  "gallery-manage": refreshGallery,
  "gallery-rebuild": async () => setStatus("特征重建配置已就绪"),
  "access-credentials": refreshAccessApplications,
  "sdk-examples": async () => {
    renderSdkExamples();
    setStatus("已刷新当前页面");
  },
  "openapi-docs": refreshOpenApiDocs,
  "api-playground": async () => {
    renderPlaygroundRequestPreview();
    setStatus("已刷新当前页面");
  },
  "call-logs": refreshCallLogs,
  "error-codes": refreshErrorCodes,
  webhooks: refreshWebhooks,
  "slo-panel": refreshSloPanel,
  "track-review": refreshTrackReview,
  "evaluation-center": refreshEvaluationCenter,
  "release-center": refreshReleaseCenter,
  models: refreshModels,
  "admin-threshold": refreshAdmin,
  "admin-data": refreshAdminData,
  "audit-compliance": refreshAuditCompliance,
  alerts: async () => {
    await refreshDashboard();
    renderAlerts();
  },
};

async function refreshCurrentView() {
  const handler = viewRefreshHandlers[state.view];
  if (handler) {
    await handler();
    return;
  }
  setStatus("当前页面没有远端列表需要刷新");
}
function handleLogin(event) {
  if (event) event.preventDefault();
  state.tenantId = qs("#tenant-input").value.trim() || "default";
  state.apiKey = qs("#api-key-input").value.trim();
  state.bearer = qs("#bearer-input").value.trim();
  state.isLoggedIn = true;
  localStorage.setItem("portraitHubTenant", state.tenantId);
  localStorage.setItem("portraitHubApiKey", state.apiKey);
  localStorage.setItem("portraitHubBearer", state.bearer);
  localStorage.setItem("portraitHubLoggedIn", "true");
  closeSocket("job");
  closeSocket("stream");
  renderIntegrationSnippet();
  updateSnippetButtons();
  updateAuthView();
}

function handleLogout() {
  state.isLoggedIn = false;
  localStorage.setItem("portraitHubLoggedIn", "false");
  closeSocket("job");
  closeSocket("stream");
  updateAuthView();
}

function updateAuthView() {
  if (state.isLoggedIn) {
    qs("#login-view").classList.add("hidden");
    qs("#console-view").classList.remove("hidden");
    qs("#current-tenant-display").textContent = state.tenantId;
    wrapHandler(refreshCurrentView)();
  } else {
    qs("#login-view").classList.remove("hidden");
    qs("#console-view").classList.add("hidden");
    qs("#tenant-input").value = state.tenantId;
    qs("#api-key-input").value = state.apiKey;
    qs("#bearer-input").value = state.bearer;
  }
}

function setupEvents() {
  qsa("[data-nav]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.nav)));
  qsa("[data-nav-shortcut]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.navShortcut)));
  qs("#login-form").addEventListener("submit", handleLogin);
  qs("#logout-button").addEventListener("click", handleLogout);
  qs("#refresh-button").addEventListener("click", wrapHandler(refreshCurrentView));
  qs("#dashboard-refresh-button").addEventListener("click", wrapHandler(refreshDashboard));
  qs("#models-refresh-button").addEventListener("click", wrapHandler(refreshModels));
  qs("#gallery-refresh-button").addEventListener("click", wrapHandler(refreshGallery));
  qs("#streams-refresh-button").addEventListener("click", wrapHandler(refreshStreams));
  qs("#admin-refresh-button").addEventListener("click", wrapHandler(refreshAdmin));
  qs("#backup-snapshot-refresh-button").addEventListener("click", wrapHandler(refreshAdminData));
  qs("#alerts-refresh-button").addEventListener("click", wrapHandler(async () => {
    await refreshDashboard();
    renderAlerts();
  }));

  qs("#access-refresh-button").addEventListener("click", wrapHandler(refreshAccessApplications));
  qs("#access-tenant-form").addEventListener("submit", wrapHandler(createAccessTenant));
  qs("#access-app-form").addEventListener("submit", wrapHandler(saveAccessApp));
  qs("#access-rotate-button").addEventListener("click", wrapHandler(() => rotateAccessApp()));
  qs("#access-app-list").addEventListener("click", wrapHandler((event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-access-edit], [data-access-rotate], [data-access-toggle]") : null;
    if (!target) return;
    const editId = target.dataset.accessEdit;
    const rotateId = target.dataset.accessRotate;
    const toggleId = target.dataset.accessToggle;
    if (editId) fillAccessAppForm(state.accessApplications.find((item) => item.id === editId));
    if (rotateId) rotateAccessApp(rotateId);
    if (toggleId) toggleAccessApp(toggleId);
  }));
  qs("#sdk-refresh-button").addEventListener("click", renderSdkExamples);
  qs("#sdk-python-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-python-code").textContent, "Python 代码示例已复制")));
  qs("#sdk-node-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-node-code").textContent, "Node.js 代码示例已复制")));
  qs("#sdk-curl-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-curl-code").textContent, "curl 命令示例已复制")));
  qs("#sdk-batch-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-batch-code").textContent, "批量示例已复制")));
  qs("#sdk-video-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-video-code").textContent, "视频示例已复制")));
  qs("#openapi-refresh-button").addEventListener("click", wrapHandler(refreshOpenApiDocs));
  qs("#openapi-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#openapi-code").textContent, "开放接口定义检查命令已复制")));
  qs("#playground-form").addEventListener("submit", wrapHandler(submitPlayground));
  qs("#playground-endpoint-input").addEventListener("change", renderPlaygroundRequestPreview);
  [
    "#playground-file-a-input",
    "#playground-file-b-input",
    "#playground-threshold-input",
    "#playground-top-k-input",
    "#playground-stream-id-input",
    "#playground-stream-url-input",
    "#playground-stream-name-input",
    "#playground-async-mode-input",
  ].forEach((selector) => {
    const element = qs(selector);
    element.addEventListener(element.type === "file" || element.type === "checkbox" ? "change" : "input", renderPlaygroundRequestPreview);
  });
  qs("#call-logs-refresh-button").addEventListener("click", wrapHandler(refreshCallLogs));
  qs("#call-log-filter-button").addEventListener("click", wrapHandler(refreshCallLogs));
  qs("#error-codes-refresh-button").addEventListener("click", wrapHandler(refreshErrorCodes));
  qs("#webhook-refresh-button").addEventListener("click", wrapHandler(refreshWebhooks));
  qs("#webhook-form").addEventListener("submit", wrapHandler(saveWebhook));
  qs("#webhook-rotate-button").addEventListener("click", wrapHandler(() => rotateWebhookSecret()));
  qs("#webhook-sample-button").addEventListener("click", wrapHandler(() => renderWebhookSample()));
  qs("#webhook-list").addEventListener("click", wrapHandler((event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-webhook-edit], [data-webhook-rotate], [data-webhook-toggle], [data-webhook-sample]") : null;
    if (!target) return;
    const editId = target.dataset.webhookEdit;
    const rotateId = target.dataset.webhookRotate;
    const toggleId = target.dataset.webhookToggle;
    const sampleId = target.dataset.webhookSample;
    if (editId) fillWebhookForm(state.webhooks.find((item) => item.id === editId));
    if (rotateId) rotateWebhookSecret(rotateId);
    if (toggleId) toggleWebhook(toggleId);
    if (sampleId) renderWebhookSample(sampleId);
  }));
  qs("#slo-refresh-button").addEventListener("click", wrapHandler(refreshSloPanel));
  qs("#multimodal-form").addEventListener("submit", wrapHandler(submitMultimodalCompare));
  qs("#track-review-refresh-button").addEventListener("click", wrapHandler(refreshTrackReview));
  qs("#track-review-annotation-form").addEventListener("submit", wrapHandler(submitTrackReviewAnnotation));
  qs("#evaluation-refresh-button").addEventListener("click", wrapHandler(refreshEvaluationCenter));
  qs("#release-refresh-button").addEventListener("click", wrapHandler(refreshReleaseCenter));
  qs("#release-form").addEventListener("submit", wrapHandler(submitReleaseAction));
  qs("#audit-refresh-button").addEventListener("click", wrapHandler(refreshAuditCompliance));
  qs("#audit-event-filter-button").addEventListener("click", wrapHandler(refreshAuditCompliance));
  qs("#vision-form").addEventListener("submit", wrapHandler(submitVision));
  qs("#compare-form").addEventListener("submit", wrapHandler(submitCompare));
  qs("#enroll-form").addEventListener("submit", wrapHandler(submitGalleryEnroll));
  qs("#search-form").addEventListener("submit", wrapHandler(submitGallerySearch));
  qs("#video-form").addEventListener("submit", wrapHandler(submitVideoJob));
  qs("#stream-form").addEventListener("submit", wrapHandler(submitStream));
  ["#vision-visuals", "#job-visuals", "#image-results-visuals", "#video-results-visuals", "#stream-live-visuals", "#stream-results-visuals", "#track-review-visuals"].forEach((selector) => qs(selector).addEventListener("click", (event) => {
    const trigger = event.target instanceof Element ? event.target.closest("[data-result-visual-index]") : null;
    if (!trigger) return;
    const index = Number(trigger.dataset.resultVisualIndex);
    if (Number.isFinite(index)) {
      const visuals = Array.isArray(event.currentTarget.__visuals) ? event.currentTarget.__visuals : state.visionResultVisuals;
      state.visionResultVisuals = visuals;
      openVisionLightbox(index, trigger);
    }
  }));
  qs("#vision-lightbox").addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-lightbox-close]") : null;
    if (target) closeVisionLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.visionLightboxIndex !== null) {
      event.preventDefault();
      closeVisionLightbox();
      return;
    }
    trapVisionLightboxFocus(event);
  });

  qs("#vision-files-input").addEventListener("change", wrapHandler(() => renderPreviews(qs("#vision-files-input"), "#vision-preview")));
  qs("#compare-a-input").addEventListener("change", wrapHandler(renderComparePreviews));
  qs("#compare-b-input").addEventListener("change", wrapHandler(renderComparePreviews));
  qs("#vision-mode-input").addEventListener("change", updateSnippetButtons);
  qs("#compare-mode-input").addEventListener("change", updateSnippetButtons);

  qs("#model-detail-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型 ID");
    if (!id) return;
    renderPayload("models", "#models-json", await api(`/v1/models/${id}`));
  }));
  qs("#load-model-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型 ID");
    if (!id) return;
    renderPayload("models", "#models-json", await api(`/v1/models/${id}/load`, { method: "POST" }));
    await refreshModels();
  }));
  qs("#unload-model-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型 ID");
    if (!id) return;
    if (!window.confirm("确认卸载该模型？正在使用的请求可能回退到冷加载。")) return;
    renderPayload("models", "#models-json", await api(`/v1/models/${id}/unload`, { method: "POST" }));
    await refreshModels();
  }));

  qs("#person-get-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#person-id-input", "人员 ID");
    if (!id) return;
    const payload = await api(`/v1/gallery/${id}`);
    renderPayload("gallery", "#gallery-json", payload);
    if (payload && payload.person) {
      renderPersonFeatures(payload.person);
    } else {
      renderPersonFeatures(null);
    }
  }));
  qs("#person-patch-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#person-id-input", "人员 ID");
    if (!id) return;
    const payload = {};
    const name = qs("#person-display-name-input").value.trim();
    if (name) payload.display_name = name;
    const metadata = qs("#person-metadata-input").value.trim();
    if (metadata) payload.metadata = parseOptionalJson("#person-metadata-input");
    renderPayload("gallery", "#gallery-json", await api(`/v1/gallery/${id}`, { method: "PATCH", json: payload }));
    await refreshGallery();
  }));
  qs("#person-delete-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#person-id-input", "人员 ID");
    if (!id) return;
    if (!window.confirm("确认删除该人员并清理相关特征、对象和向量索引？")) return;
    renderPayload("gallery", "#gallery-json", await api(`/v1/gallery/${id}`, { method: "DELETE" }));
    await refreshGallery();
  }));
  qs("#feature-rebuild-button").addEventListener("click", wrapHandler(async () => {
    const params = new URLSearchParams();
    const modality = qs("#feature-rebuild-modality-input").value;
    const modelId = qs("#feature-rebuild-model-id-input").value.trim();
    const dryRun = qs("#feature-rebuild-dry-run-input").checked;
    if (modality) params.set("modality", modality);
    if (modelId) params.set("model_id", modelId);
    params.set("dry_run", dryRun ? "true" : "false");
    const payload = await api(`/v1/gallery/reindex?${params.toString()}`, { method: "POST" });
    renderSummary("#feature-rebuild-summary", [
      { label: "重建模态", value: modality || "全部" },
      { label: "模型 ID", value: modelId || "默认" },
      { label: "预演", value: dryRun ? "是" : "否" },
      { label: "租户", value: payload.tenant_id || state.tenantId },
    ]);
    renderPayload("gallery-rebuild", "#feature-rebuild-json", payload);
  }));

  qs("#job-get-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    const payload = await api(`/v1/jobs/${id}`);
    renderJobSummary(payload);
    renderJobVisuals(payload);
    renderPayload("jobs", "#jobs-json", payload);
  }));
  qs("#job-result-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    const payload = await api(`/v1/jobs/${id}/result`);
    renderJobSummary(payload);
    renderJobVisuals(payload);
    renderPayload("jobs", "#jobs-json", payload);
  }));
  qs("#job-cancel-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    const payload = await api(`/v1/jobs/${id}/cancel`, { method: "POST" });
    renderJobSummary(payload);
    renderJobVisuals(payload);
    renderPayload("jobs", "#jobs-json", payload);
  }));
  qs("#job-watch-button").addEventListener("click", () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    watchJsonSocket("job", `/ws/jobs/${id}`, "#job-ws-status", "#jobs-json");
  });
  qs("#video-results-refresh-button").addEventListener("click", wrapHandler(refreshActiveAnalysisResults));
  qsa("[data-results-tab]").forEach((button) => button.addEventListener("click", () => {
    renderAnalysisResultsTab(button.dataset.resultsTab);
    if (state.isLoggedIn && state.view === "video-results") wrapHandler(refreshActiveAnalysisResults)();
  }));

  qs("#stream-get-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    const payload = await api(`/v1/streams/${id}`);
    renderLiveStreamResults(payload);
    renderPayload("streams", "#streams-json", payload);
  }));
  qs("#stream-start-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    const payload = await api(`/v1/streams/${id}/start`, { method: "POST" });
    renderLiveStreamResults(payload);
    renderPayload("streams", "#streams-json", payload);
    watchJsonSocket("stream", `/ws/streams/${id}`, "#stream-ws-status", "#streams-json");
    await refreshStreams();
  }));
  qs("#stream-stop-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    const payload = await api(`/v1/streams/${id}/stop`, { method: "POST" });
    renderLiveStreamResults(payload);
    renderPayload("streams", "#streams-json", payload);
    await refreshStreams();
  }));
  qs("#stream-events-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    const payload = await api(`/v1/streams/${id}/events`);
    renderLiveStreamResults(payload);
    renderPayload("streams", "#streams-json", payload);
  }));
  qs("#stream-watch-button").addEventListener("click", () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    watchJsonSocket("stream", `/ws/streams/${id}`, "#stream-ws-status", "#streams-json");
  });

  qs("#threshold-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const profile = qs("#threshold-profile-input").value.trim();
    if (!profile) {
      setStatus("请输入阈值方案", true);
      return;
    }
    const payload = {};
    [
      ["body", "#threshold-body-input"],
      ["face", "#threshold-face-input"],
      ["gait", "#threshold-gait-input"],
      ["appearance", "#threshold-appearance-input"],
      ["fusion", "#threshold-fusion-input"],
    ].forEach(([key, selector]) => {
      const value = qs(selector).value;
      if (value !== "") payload[key] = Number(value);
    });
    if (!window.confirm("确认保存该阈值方案？它会影响后续比对和检索判断。")) return;
    renderPayload("admin-threshold", "#admin-threshold-json", await api(`/v1/thresholds/${encodeURIComponent(profile)}`, { method: "PUT", json: payload }));
  }));
  qs("#retention-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const result = await api("/v1/admin/retention/cleanup", {
      method: "POST",
      json: { retention_days: Number(qs("#retention-days-input").value), confirm: qs("#retention-confirm-input").value },
    });
    await refreshAdminData({ action: "retention_cleanup", result });
  }));
  qs("#backup-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const updatedSince = qs("#backup-updated-since-input").value;
    const result = await api("/v1/admin/backup", {
      method: "POST",
      json: {
        updated_since: updatedSince === "" ? null : Number(updatedSince),
        confirm: qs("#backup-confirm-input").value,
      },
    });
    await refreshAdminData({ action: "backup", result });
  }));
  qs("#alert-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    state.alertConfig = {
      maxErrorRate: Number(qs("#alert-error-rate-input").value),
      maxP95Latency: Number(qs("#alert-p95-input").value),
      minFreeGpuMemoryGb: Number(qs("#alert-gpu-free-input").value),
    };
    localStorage.setItem("portraitHubAlertConfig", JSON.stringify(state.alertConfig));
    renderAlerts();
  }));
}

function init() {
  qs("#console-app").innerHTML = template;
  setAlertInputs();
  renderIntegrationSnippet();
  setupEvents();
  updateSnippetButtons();
  setView(state.view);
  updateAuthView();
}

window.PortraitConsoleRuntime = { init };
