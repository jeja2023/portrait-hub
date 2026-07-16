// 观测与合规视图：调用日志、错误码目录与 SLO 面板。
// 从 views/app.js 拆分而来；依赖 app.js 中的全局 state/api/qs 等运行时函数（调用时解析）。

function normalizeCallLog(row) {
  const statusText = row.status || (Number(row.http_status || 0) >= 400 ? "error" : "success");
  return {
    page: row.application_id || row.page || "--",
    application_id: row.application_id || "--",
    request_id: row.request_id || "--",
    endpoint: row.endpoint || row.path || "--",
    method: row.method || "",
    status: statusText,
    http_status: row.http_status || (statusText === "error" ? 500 : 200),
    error_code: row.error_code || null,
    latency_ms: row.latency_ms ?? "--",
    model_version: row.model_version || row.model_id || "--",
    worker: row.worker || row.gpu_worker || "--",
    created_at: row.created_at || null,
  };
}

function localCallLogRows() {
  return Object.entries(state.latestPayloads).map(([name, payload]) => {
    const data = payloadData(payload) || {};
    const error = data.error || payload?.error;
    return normalizeCallLog({
      page: name,
      application_id: "当前会话",
      request_id: data.request_id || payload?.request_id || data.response?.request_id || "--",
      endpoint: data.endpoint || data.path || name,
      status: error ? "error" : "success",
      http_status: data.http_status || (error ? 500 : 200),
      error_code: data.error_code || (error ? "client_error" : null),
      latency_ms: data.latency_ms ?? (data.timing?.total_seconds !== undefined ? Math.round(Number(data.timing.total_seconds || 0) * 1000) : "--"),
      model_version: data.model_version || data.model_id || data.response?.model_version || "--",
      worker: data.worker || data.gpu_worker || "--",
    });
  });
}

function buildCallLogRows() {
  const rows = state.callLogs.length ? state.callLogs.map(normalizeCallLog) : localCallLogRows();
  return rows.sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0));
}

async function refreshCallLogs() {
  const params = new URLSearchParams({ limit: "200" });
  const requestFilter = qs("#call-log-request-input").value.trim();
  const endpointFilter = qs("#call-log-endpoint-input").value.trim();
  const statusFilter = qs("#call-log-status-input").value;
  const errorCodeFilter = qs("#call-log-error-code-input").value.trim();
  const createdSinceFilter = qs("#call-log-created-since-input").value.trim();
  const createdUntilFilter = qs("#call-log-created-until-input").value.trim();
  const applicationFilter = qs("#call-log-application-input")?.value || "";
  if (requestFilter) params.set("request_id", requestFilter);
  if (endpointFilter) params.set("endpoint", endpointFilter);
  if (statusFilter) params.set("status", statusFilter);
  if (errorCodeFilter) params.set("error_code", errorCodeFilter);
  if (createdSinceFilter) params.set("created_since", createdSinceFilter);
  if (createdUntilFilter) params.set("created_until", createdUntilFilter);
  if (applicationFilter) params.set("application_id", applicationFilter);
  try {
    const payload = await api(`/v1/access/call-logs?${params.toString()}`);
    state.callLogs = (payload.logs || []).map(normalizeCallLog);
  } catch (error) {
    state.callLogs = [];
    renderPayload("call-logs", "#call-logs-json", { tenant_id: state.tenantId, warning: error.message || String(error), rows: localCallLogRows() });
  }
  renderCallLogs();
}

function renderCallLogs() {
  const requestFilter = qs("#call-log-request-input").value.trim().toLowerCase();
  const endpointFilter = qs("#call-log-endpoint-input").value.trim().toLowerCase();
  const statusFilter = qs("#call-log-status-input").value;
  const errorCodeFilter = qs("#call-log-error-code-input").value.trim();
  const normalizedErrorCodeFilter = errorCodeFilter.toLowerCase();
  const createdSinceRaw = qs("#call-log-created-since-input").value.trim();
  const createdUntilRaw = qs("#call-log-created-until-input").value.trim();
  const createdSinceFilter = createdSinceRaw === "" ? null : Number(createdSinceRaw);
  const createdUntilFilter = createdUntilRaw === "" ? null : Number(createdUntilRaw);
  const applicationFilter = qs("#call-log-application-input")?.value || "";
  populateCallLogApplicationOptions();
  const rows = buildCallLogRows().filter((row) => {
    if (requestFilter && !String(row.request_id).toLowerCase().includes(requestFilter)) return false;
    if (endpointFilter && !`${row.page} ${row.endpoint} ${row.method}`.toLowerCase().includes(endpointFilter)) return false;
    if (statusFilter && row.status !== statusFilter) return false;
    if (normalizedErrorCodeFilter && !String(row.error_code || "").toLowerCase().includes(normalizedErrorCodeFilter)) return false;
    const createdAt = Number(row.created_at || 0);
    if (createdSinceFilter !== null && (!Number.isFinite(createdAt) || createdAt < createdSinceFilter)) return false;
    if (createdUntilFilter !== null && (!Number.isFinite(createdAt) || createdAt > createdUntilFilter)) return false;
    if (applicationFilter && row.application_id !== applicationFilter) return false;
    return true;
  });
  const source = state.callLogs.length ? "服务端" : "当前会话";
  renderSummary("#call-log-summary", [
    { label: "记录数", value: rows.length },
    { label: "异常", value: rows.filter((row) => row.status === "error").length },
    { label: "租户", value: state.tenantId },
    { label: "来源", value: source },
  ]);
  qs("#call-log-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>时间</th><th>应用</th><th>请求 ID</th><th>接口</th><th>状态</th><th>耗时</th><th>模型/工作器</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr><td>${escapeHtml(row.created_at ? formatDateTime(row.created_at) : "--")}</td><td>${escapeHtml(row.application_id || row.page)}</td><td>${escapeHtml(row.request_id)}</td><td>${escapeHtml(`${row.method ? `${row.method} ` : ""}${row.endpoint}`)}</td><td>${escapeHtml(`${localizeValue(row.status)} ${row.http_status || ""}${row.error_code ? ` / ${row.error_code}` : ""}`)}</td><td>${escapeHtml(row.latency_ms)}</td><td>${escapeHtml(row.model_version)} / ${escapeHtml(row.worker)}</td></tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无调用记录</div>`;
  renderPayload("call-logs", "#call-logs-json", {
    tenant_id: state.tenantId,
    source,
    filters: {
      application_id: applicationFilter || null,
      error_code: errorCodeFilter || null,
      created_since: createdSinceFilter,
      created_until: createdUntilFilter,
    },
    rows,
  });
}

function renderErrorCodes(payload = state.errorCodes) {
  const rows = Array.isArray(payload?.error_codes) ? payload.error_codes : [];
  const retryable = rows.filter((row) => row.retryable).length;
  renderSummary("#error-codes-summary", [
    { label: "错误码", value: rows.length },
    { label: "可重试", value: retryable },
    { label: "不可重试", value: rows.length - retryable },
    { label: "租户", value: payload?.tenant_id || state.tenantId },
  ]);
  qs("#error-codes-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>Code</th><th>HTTP</th><th>类别</th><th>重试</th><th>说明</th><th>处理建议</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.code || "--")}</td><td>${escapeHtml(row.http_status ?? "--")}</td><td>${escapeHtml(localizeValue(row.category || "--"))}</td><td>${row.retryable ? "是" : "否"}</td><td>${escapeHtml(row.description || "--")}</td><td>${escapeHtml(row.operator_action || "--")}</td></tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无错误码目录</div>`;
  renderPayload("error-codes", "#error-codes-json", { tenant_id: state.tenantId, ...(payload || {}), error_codes: rows });
}

async function refreshErrorCodes() {
  const payload = await api("/v1/access/error-codes");
  state.errorCodes = payloadData(payload) || payload;
  renderErrorCodes(state.errorCodes);
}
function histogramQuantile(metrics, baseName, quantile) {
  const buckets = metrics
    .filter((item) => item.name === `${baseName}_bucket` && item.labels.le !== "+Inf")
    .map((item) => ({ le: Number(item.labels.le), count: Number(item.value) }))
    .sort((left, right) => left.le - right.le);
  if (!buckets.length) return 0;
  const total = buckets[buckets.length - 1].count;
  if (total <= 0) return 0;
  const target = total * quantile;
  const bucket = buckets.find((item) => item.count >= target);
  return bucket ? bucket.le : buckets[buckets.length - 1].le;
}

function summarizeSloCallLogs(logs) {
  const rows = Array.isArray(logs) ? logs : [];
  const total = rows.length;
  const errors = rows.filter((row) => Number(row.http_status || 0) >= 400 || row.status === "error" || row.error_code).length;
  const success = Math.max(0, total - errors);
  return {
    total,
    success,
    errors,
    success_rate: total ? success / total : null,
    error_rate: total ? errors / total : null,
  };
}

function sloTone(ok, warn = false) {
  if (ok) return "ok";
  return warn ? "warn" : "danger";
}

function renderSloPanel() {
  const metrics = state.dashboard.metrics || {};
  const rawMetrics = state.dashboard.raw_metrics || [];
  const status = state.dashboard.status || {};
  const callLogSummary = summarizeSloCallLogs(state.dashboard.slo_call_logs || []);
  const p99 = metrics.inference_p99_seconds ?? histogramQuantile(rawMetrics, "gpu_worker_inference_seconds", 0.99);
  const queueP95 = metrics.queue_p95_seconds ?? histogramQuantile(rawMetrics, "gpu_worker_queue_seconds", 0.95);
  const queueP99 = metrics.queue_p99_seconds ?? histogramQuantile(rawMetrics, "gpu_worker_queue_seconds", 0.99);
  const observedErrorRate = callLogSummary.error_rate ?? Number(metrics.error_rate || 0);
  const successRate = callLogSummary.success_rate ?? (1 - Number(metrics.error_rate || 0));
  const errorBudgetLimit = Math.max(0.0001, Number(state.alertConfig.maxErrorRate || 0.005));
  const errorBudgetRemaining = Math.max(0, errorBudgetLimit - observedErrorRate);
  const errorBudgetBurn = observedErrorRate / errorBudgetLimit;
  const queueLimitSeconds = 0.5;
  const p95LimitSeconds = Number(state.alertConfig.maxP95Latency || 0);
  const activeStreams = Number(status.stream_worker?.active_sessions ?? metrics.stream_active_sessions_metric ?? 0);
  const loadedModels = Number((status.loaded_models || []).length || metrics.loaded_models_metric || 0);
  const gpuQueueDepth = Number(metrics.gpu_queue_depth || 0);
  const gpuDeviceQueues = Array.isArray(metrics.gpu_device_queue_depths) ? metrics.gpu_device_queue_depths : [];
  renderSummary("#slo-summary", [
    { label: "30天成功率", value: `${formatNumber(successRate * 100, 2)}%` },
    { label: "P95/P99", value: `${formatNumber(metrics.inference_p95_seconds, 2)}s / ${formatNumber(p99, 2)}s` },
    { label: "队列 P95/P99", value: `${formatNumber(queueP95, 3)}s / ${formatNumber(queueP99, 3)}s` },
    { label: "GPU 队列", value: gpuDeviceQueues.length ? gpuDeviceQueues.map((item) => `${item.device}:${item.depth}`).join(" / ") : String(gpuQueueDepth) },
    { label: "GPU 显存", value: metrics.gpu_free_gb === null ? "--" : `${formatNumber(metrics.gpu_free_gb, 1)}GB free${metrics.gpu_used_gb ? ` / ${formatNumber(metrics.gpu_used_gb, 1)}GB used` : ""}` },
  ]);
  renderBadges("#slo-badges", [
    { label: "错误预算剩余", value: `${formatNumber(errorBudgetRemaining * 100, 2)}%`, tone: sloTone(errorBudgetRemaining > 0, errorBudgetBurn <= 1.5) },
    { label: "燃烧率", value: `${formatNumber(errorBudgetBurn, 2)}x`, tone: sloTone(errorBudgetBurn <= 1, errorBudgetBurn <= 2) },
    { label: "近30天样本", value: callLogSummary.total || "metrics", tone: callLogSummary.total ? "ok" : "warn" },
    { label: "活跃流", value: activeStreams, tone: "ok" },
    { label: "GPU队列", value: gpuQueueDepth, tone: sloTone(gpuQueueDepth === 0, gpuQueueDepth <= 2) },
    { label: "队列P95", value: `${formatNumber(queueP95, 3)}s`, tone: sloTone(queueP95 <= queueLimitSeconds, queueP95 <= queueLimitSeconds * 2) },
  ]);
  const workerItems = [
    { name: "模型热状态", value: `${loadedModels} loaded`, ok: loadedModels > 0 },
    { name: "推理延迟", value: `p95 ${formatNumber(metrics.inference_p95_seconds, 2)}s / p99 ${formatNumber(p99, 2)}s`, ok: !p95LimitSeconds || Number(metrics.inference_p95_seconds || 0) <= p95LimitSeconds },
    { name: "GPU 队列", value: gpuDeviceQueues.length ? gpuDeviceQueues.map((item) => `${item.device}:${item.depth}`).join(" / ") : String(gpuQueueDepth), ok: gpuQueueDepth === 0 },
    { name: "流 worker", value: `${activeStreams}/${status.stream_worker?.max_workers ?? "--"}`, ok: true },
    { name: "任务队列", value: String(status.task_queue?.queue_length ?? "--"), ok: Number(status.task_queue?.queue_length || 0) === 0 },
    { name: "向量库", value: localizeValue(status.configured_backends?.vector || "--"), ok: true },
    { name: "对象存储", value: localizeValue(status.configured_backends?.object_storage || "--"), ok: true },
  ];
  qs("#slo-worker-list").innerHTML = workerItems.map((item) => `<div class="alert-item ${item.ok ? "ok" : "warn"}"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.value)}</span></div>`).join("");
  renderPayload("slo-panel", "#slo-json", {
    tenant_id: state.tenantId,
    success_rate: successRate,
    success_rate_source: callLogSummary.total ? "call_logs_30d" : "metrics_counter",
    call_log_window_seconds: 30 * 24 * 3600,
    call_log_summary: callLogSummary,
    p95_seconds: metrics.inference_p95_seconds,
    p99_seconds: p99,
    queue_p95_seconds: queueP95,
    queue_p99_seconds: queueP99,
    gpu_queue_depth: gpuQueueDepth,
    gpu_device_queue_depths: gpuDeviceQueues,
    error_budget_limit: errorBudgetLimit,
    error_budget_remaining: errorBudgetRemaining,
    error_budget_burn_rate: errorBudgetBurn,
    active_streams: activeStreams,
    loaded_models: loadedModels,
    status,
    metrics,
  });
}

async function refreshSloPanel() {
  await refreshDashboard();
  const createdSince = Math.floor(Date.now() / 1000) - (30 * 24 * 3600);
  try {
    const logsPayload = await api(`/v1/access/call-logs?limit=500&created_since=${createdSince}`);
    state.dashboard.slo_call_logs = logsPayload.logs || [];
  } catch (error) {
    state.dashboard.slo_call_logs = [];
    state.dashboard.slo_call_logs_warning = error.message || String(error);
  }
  renderSloPanel();
}
