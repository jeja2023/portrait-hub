// Dashboard and top-level resource refresh functions.
// Loaded before app.js; shared runtime helpers are resolved when invoked.
function renderDashboard(summary) {
  const metrics = summary.metrics || {};
  qs("#metric-requests").textContent = String(metrics.requests || 0);
  qs("#metric-error-rate").textContent = `${((metrics.error_rate || 0) * 100).toFixed(1)}%`;
  qs("#metric-p95").textContent = `${formatNumber(metrics.inference_p95_seconds, 2)}s`;
  qs("#metric-gpu-free").textContent = metrics.gpu_free_gb === null ? "--" : `${formatNumber(metrics.gpu_free_gb, 1)}GB`;
  const status = summary.status || {};
  renderBadges("#overview-badges", [
    { label: "图库", value: localizeValue(status.configured_backends?.gallery || "--"), tone: "ok" },
    { label: "向量库", value: localizeValue(status.configured_backends?.vector || "--"), tone: "ok" },
    { label: "对象存储", value: localizeValue(status.configured_backends?.object_storage || "--"), tone: "ok" },
    { label: "队列", value: localizeValue(status.configured_backends?.task_queue || "--"), tone: "ok" },
    { label: "RBAC", value: status.security?.rbac_enabled ? "开启" : "关闭", tone: status.security?.rbac_enabled ? "ok" : "warn" },
  ]);
}

function renderAlerts() {
  const metrics = state.dashboard.metrics || {};
  const checks = [
    {
      name: "错误率",
      current: metrics.error_rate || 0,
      limit: state.alertConfig.maxErrorRate,
      ok: (metrics.error_rate || 0) <= state.alertConfig.maxErrorRate,
      unit: "%",
      scale: 100,
    },
    {
      name: "P95 延迟",
      current: metrics.inference_p95_seconds || 0,
      limit: state.alertConfig.maxP95Latency,
      ok: (metrics.inference_p95_seconds || 0) <= state.alertConfig.maxP95Latency,
      unit: "s",
      scale: 1,
    },
    {
      name: "GPU 空闲显存",
      current: metrics.gpu_free_gb,
      limit: state.alertConfig.minFreeGpuMemoryGb,
      ok: metrics.gpu_free_gb === null || Number(metrics.gpu_free_gb) >= state.alertConfig.minFreeGpuMemoryGb,
      unit: "GB",
      scale: 1,
    },
  ];
  qs("#alert-list").innerHTML = checks.map((item) => {
    const current = item.current === null ? "--" : `${formatNumber(Number(item.current) * item.scale, 2)}${item.unit}`;
    const limit = `${formatNumber(Number(item.limit) * item.scale, 2)}${item.unit}`;
    return `<div class="alert-item ${item.ok ? "ok" : "warn"}"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(current)} / ${escapeHtml(limit)}</span></div>`;
  }).join("");
  renderPayload("alerts", "#alerts-json", { config: state.alertConfig, checks });
}

function selectedMultimodalScopes() {
  const scopes = selectedCheckboxValues("multimodal-scope");
  return scopes.length ? scopes : ["body"];
}

function renderMultimodalDetails(payload) {
  const data = payloadData(payload) || {};
  const modalities = data.modalities || {};
  const rows = Object.entries(modalities).map(([name, item]) => ({ name, ...item }));
  qs("#multimodal-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>模态</th><th>参与</th><th>原始分数</th><th>质量</th><th>权重</th><th>原因</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr><td>${escapeHtml(localizeValue(row.name))}</td><td>${row.used ? "是" : "否"}</td><td>${escapeHtml(formatNumber(row.score, 4))}</td><td>${escapeHtml(formatNumber(row.quality, 4))}</td><td>${escapeHtml(formatNumber(row.weight, 2))}</td><td>${escapeHtml(row.reason || "--")}</td></tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无模态明细</div>`;
}

async function submitMultimodalCompare(event) {
  event.preventDefault();
  const filesA = formFiles(qs("#multimodal-a-input"));
  const filesB = formFiles(qs("#multimodal-b-input"));
  if (!filesA.length || !filesB.length) throw new Error("请选择左右两侧证据图片");
  const form = new FormData();
  form.set("image_a", filesA[0]);
  form.set("image_b", filesB[0]);
  form.set("threshold_profile", qs("#multimodal-threshold-input").value.trim() || "normal");
  form.set("modalities", selectedMultimodalScopes().join(","));
  const payload = await api("/v1/fusion/compare", { method: "POST", body: form });
  const data = payloadData(payload) || {};
  renderSummary("#multimodal-summary", [
    { label: "结论", value: data.passed === undefined ? "--" : data.passed ? "通过" : "未通过" },
    { label: "融合分", value: formatNumber(data.final_score, 4) },
    { label: "阈值", value: formatNumber(data.threshold, 4) },
    { label: "风险", value: data.decision?.risk || "--" },
  ]);
  renderMultimodalDetails(payload);
  renderPayload("multimodal-compare", "#multimodal-json", payload);
}

function setAlertInputs() {
  qs("#alert-error-rate-input").value = state.alertConfig.maxErrorRate;
  qs("#alert-p95-input").value = state.alertConfig.maxP95Latency;
  qs("#alert-gpu-free-input").value = state.alertConfig.minFreeGpuMemoryGb;
}

async function refreshDashboard() {
  const [status, exportPayload, metricsText] = await Promise.all([
    api("/v1/admin/status"),
    api("/v1/admin/export?people_limit=0&jobs_limit=0&streams_limit=0"),
    textApi("/metrics"),
  ]);
  const metrics = parsePrometheus(metricsText);
  const requests = metricValue(metrics, "gpu_worker_requests_total");
  const errors = metricValue(metrics, "gpu_worker_predict_errors_total")
    + metricValue(metrics, "gpu_worker_persons_errors_total")
    + metricValue(metrics, "gpu_worker_embeddings_errors_total")
    + metricValue(metrics, "gpu_worker_tracks_errors_total")
    + metricValue(metrics, "gpu_worker_vision_errors_total");
  const gpuFreeBytes = metricSum(metrics, "gpu_worker_gpu_memory_free_bytes");
  const gpuUsedBytes = metricSum(metrics, "gpu_worker_gpu_memory_used_bytes");
  const gpuDeviceQueues = metricRows(metrics, "gpu_worker_gpu_device_queue_depth").map((item) => ({
    device: item.labels.device || "default",
    depth: Number(item.value || 0),
  }));
  const summary = {
    status,
    totals: exportPayload.pagination || {},
    raw_metrics: metrics,
    metrics: {
      requests,
      errors,
      error_rate: requests > 0 ? errors / requests : 0,
      inference_p95_seconds: histogramP95(metrics, "gpu_worker_inference_seconds"),
      inference_p99_seconds: histogramQuantile(metrics, "gpu_worker_inference_seconds", 0.99),
      queue_p95_seconds: histogramQuantile(metrics, "gpu_worker_queue_seconds", 0.95),
      queue_p99_seconds: histogramQuantile(metrics, "gpu_worker_queue_seconds", 0.99),
      gpu_queue_depth: metricValue(metrics, "gpu_worker_gpu_queue_depth"),
      gpu_device_queue_depths: gpuDeviceQueues,
      gpu_device_queue_max: metricMax(metrics, "gpu_worker_gpu_device_queue_depth"),
      gpu_free_gb: gpuFreeBytes ? gpuFreeBytes / (1024 ** 3) : null,
      gpu_used_gb: gpuUsedBytes ? gpuUsedBytes / (1024 ** 3) : null,
      stream_active_sessions_metric: metricValue(metrics, "gpu_worker_stream_active_sessions"),
      loaded_models_metric: metricValue(metrics, "gpu_worker_loaded_models"),
    },
  };
  state.dashboard = summary;
  renderDashboard(summary);
  renderAlerts();
  renderPayload("dashboard", "#dashboard-json", summary);
}

async function refreshModels() {
  const payload = await api("/v1/models");
  renderSummary("#models-summary", [
    { label: "模型数", value: payload.count ?? 0 },
    { label: "已加载", value: (payload.loaded_models || []).length },
    { label: "别名数", value: Object.keys(payload.aliases || {}).length },
    { label: "配置", value: payload.config_loaded ? "已加载" : "异常" },
  ]);
  renderPayload("models", "#models-json", payload);
}

async function refreshGallery() {
  const payload = await api("/v1/admin/export?people_limit=50&jobs_limit=0&streams_limit=0");
  state.galleryExport = payload;
  renderGalleryVisuals(payload);
  renderGallerySummary(payload);
  renderPayload("gallery", "#gallery-json", payload);

  // 更新或清空特征图片列表
  const currentId = qs("#person-id-input").value.trim();
  if (currentId && Array.isArray(payload.people)) {
    const person = payload.people.find((p) => p.person_id === currentId);
    if (person) {
      renderPersonFeatures(person);
    } else {
      renderPersonFeatures(null);
    }
  } else {
    renderPersonFeatures(null);
  }
}

async function refreshStreams() {
  const payload = await api("/v1/streams?limit=50");
  renderSummary("#streams-summary", [
    { label: "视频流解析", value: payload.total ?? (payload.streams || []).length },
    { label: "本页数量", value: payload.count ?? (payload.streams || []).length },
    { label: "下一页", value: payload.next_cursor ? "有" : "无" },
    { label: "租户", value: state.tenantId },
  ]);
  renderPayload("streams", "#streams-json", payload);
}

async function refreshAdmin() {
  const [status, thresholds] = await Promise.all([api("/v1/admin/status"), api("/v1/thresholds")]);
  renderPayload("admin-threshold", "#admin-threshold-json", { status, thresholds });
}

async function refreshAll() {
  await Promise.allSettled([refreshDashboard(), refreshModels(), refreshGallery(), refreshStreams(), refreshAdmin(), refreshAdminData(), refreshAnalysisResults(), refreshTrackReview(), refreshEvaluationCenter(), refreshReleaseCenter(), refreshAuditCompliance()]);
}

