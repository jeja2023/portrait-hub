// 模型治理视图：轨迹审阅、回归评估、模型发布、合规审计与备份快照。
// 从 views/app.js 拆分而来；依赖 app.js 中的全局 state/api/qs 等运行时函数（调用时解析）。

function renderTrackReviewAnnotations(annotations) {
  const rows = Array.isArray(annotations) ? annotations : [];
  qs("#track-review-annotation-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>时间</th><th>任务/轨迹</th><th>标注</th><th>帧</th><th>复核人</th><th>备注</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr><td>${escapeHtml(row.created_at ? formatDateTime(row.created_at) : "--")}</td><td>${escapeHtml(`${row.job_id || "--"} / ${row.track_id || "--"}`)}</td><td>${escapeHtml(localizeValue(row.label || "--"))}</td><td>${escapeHtml(row.frame_index ?? "--")}</td><td>${escapeHtml(row.reviewer || "--")}</td><td>${escapeHtml(row.note || row.evidence_ref || "--")}</td></tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无人工标注</div>`;
}
async function refreshTrackReview() {
  const [payload, reviewPayload] = await Promise.all([
    api("/v1/jobs/video/results?limit=24"),
    api("/v1/evaluation/track-reviews?limit=100").catch((error) => ({ data: { annotations: [], warning: error.message || String(error) } })),
  ]);
  const annotations = payloadData(reviewPayload)?.annotations || reviewPayload.annotations || [];
  const info = videoResultsVisualInfo(payload);
  const tracks = info.results.flatMap((entry) => {
    const frames = Array.isArray(entry.result?.frames) ? entry.result.frames : [];
    return frames.flatMap((frame) => frame.persons || frame.tracks || []);
  });
  renderSummary("#track-review-summary", [
    { label: "任务数", value: info.results.length },
    { label: "关键帧", value: info.visuals.length },
    { label: "轨迹/人体", value: tracks.length },
    { label: "人工标注", value: annotations.length },
  ]);
  renderTrackReviewAnnotations(annotations);
  renderVideoVisualGrid("#track-review-visuals", info.visuals, "暂无可审阅轨迹，请先完成视频解析任务", {
    variant: "video",
    maxWidth: 260,
    maxHeight: 180,
  });
  renderPayload("track-review", "#track-review-json", { ...payload, track_count: tracks.length, review_annotations: annotations });
}

function renderCapabilityTable(capabilities) {
  const rows = Object.entries(capabilities || {}).map(([name, item]) => ({ name, ...(item || {}) }));
  qs("#evaluation-capability-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>能力</th><th>状态</th><th>模型</th><th>Adapter</th><th>风险</th></tr></thead>
      <tbody>${rows.map((row) => {
        const risk = ["production", "ready"].includes(row.status) && row.model_id !== row.fallback_model_id ? "clear" : "needs_gate";
        return `<tr><td>${escapeHtml(row.name)}</td><td>${escapeHtml(localizeValue(row.status || "--"))}</td><td>${escapeHtml(row.model_id || "--")}</td><td>${escapeHtml(row.adapter || row.production_adapter || "--")}</td><td>${escapeHtml(risk)}</td></tr>`;
      }).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无模型能力数据</div>`;
}

function renderEvaluationDatasets(datasets) {
  const rows = Array.isArray(datasets) ? datasets : [];
  qs("#evaluation-dataset-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>数据集</th><th>用途</th><th>样本</th><th>任务/轨迹</th><th>最新样本</th><th>证据</th></tr></thead>
      <tbody>${rows.map((row) => {
        const evidence = Array.isArray(row.evidence_index) && row.evidence_index.length ? row.evidence_index[0].evidence_ref : "--";
        return `<tr><td>${escapeHtml(row.name || row.dataset_id || "--")}</td><td>${escapeHtml(localizeValue(row.purpose || "--"))}</td><td>${escapeHtml(row.sample_count ?? 0)}</td><td>${escapeHtml(`${row.job_count ?? 0} / ${row.track_count ?? 0}`)}</td><td>${escapeHtml(row.latest_created_at ? formatDateTime(row.latest_created_at) : "--")}</td><td>${escapeHtml(evidence || "--")}</td></tr>`;
      }).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无评估数据集</div>`;
}
function renderEvaluationThresholdRecommendations(payload) {
  const data = payload || {};
  const rows = Array.isArray(data.recommendations) ? data.recommendations : [];
  qs("#evaluation-threshold-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>模态</th><th>Profile</th><th>当前</th><th>建议</th><th>动作</th><th>证据</th></tr></thead>
      <tbody>${rows.map((row) => {
        const counts = row.evidence_counts || {};
        const evidence = [
          `误检 ${counts.false_positive || 0}`,
          `错配 ${counts.mismatch || 0}`,
          `确认 ${counts.confirmed || 0}`,
          `低质 ${counts.low_quality || 0}`,
        ].join(" / ");
        const current = formatNumber(row.current_threshold, 4);
        const recommended = formatNumber(row.recommended_threshold, 4);
        return `<tr><td>${escapeHtml(localizeValue(row.modality || "--"))}</td><td>${escapeHtml(row.profile || "--")}</td><td>${escapeHtml(current)}</td><td>${escapeHtml(recommended)}</td><td>${escapeHtml(localizeValue(row.action || "--"))}</td><td>${escapeHtml(evidence)}</td></tr>`;
      }).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无阈值推荐</div>`;
}
function renderEvaluationReviewSummary(summary) {
  const data = summary || {};
  const labels = Array.isArray(data.label_counts) ? data.label_counts : [];
  const evidenceRows = Array.isArray(data.evidence_index) && data.evidence_index.length
    ? data.evidence_index
    : (Array.isArray(data.recent_annotations) ? data.recent_annotations : []);
  const attentionLabels = new Set(["false_positive", "mismatch", "low_quality", "uncertain"]);
  const attentionCount = Number(data.review_attention_count ?? labels.reduce((total, row) => (
    attentionLabels.has(row.label) ? total + Number(row.count || 0) : total
  ), 0));
  renderSummary("#evaluation-review-summary", [
    { label: "标注样本", value: data.count ?? data.total_annotations ?? 0 },
    { label: "需复核", value: attentionCount },
    { label: "任务数", value: data.unique_job_count ?? 0 },
    { label: "轨迹数", value: data.unique_track_count ?? 0 },
  ]);
  qs("#evaluation-review-label-table").innerHTML = labels.length ? `
    <table class="data-table">
      <thead><tr><th>标注</th><th>数量</th><th>用途</th></tr></thead>
      <tbody>${labels.map((row) => `<tr><td>${escapeHtml(localizeValue(row.label || "unknown"))}</td><td>${escapeHtml(row.count ?? 0)}</td><td>${attentionLabels.has(row.label) ? "回归留出" : "确认样本"}</td></tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无标注统计</div>`;
  qs("#evaluation-review-evidence-table").innerHTML = evidenceRows.length ? `
    <table class="data-table">
      <thead><tr><th>时间</th><th>任务/轨迹</th><th>标注</th><th>帧</th><th>证据引用</th></tr></thead>
      <tbody>${evidenceRows.map((row) => `
        <tr><td>${escapeHtml(row.created_at ? formatDateTime(row.created_at) : "--")}</td><td>${escapeHtml(`${row.job_id || "--"} / ${row.track_id || "--"}`)}</td><td>${escapeHtml(localizeValue(row.label || "--"))}</td><td>${escapeHtml(row.frame_index ?? "--")}</td><td>${escapeHtml(row.evidence_ref || "--")}</td></tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无证据索引</div>`;
}
function renderEvaluationMetrics(payload) {
  const metrics = payload.metrics || {};
  const rows = [
    { metric: "ReID p95", value: `${formatNumber(metrics.inference_p95_seconds, 2)}s`, gate: "baseline" },
    { metric: "错误率", value: `${formatNumber(Number(metrics.error_rate || 0) * 100, 2)}%`, gate: Number(metrics.error_rate || 0) <= state.alertConfig.maxErrorRate ? "pass" : "review" },
    { metric: "GPU 空闲", value: metrics.gpu_free_gb === null ? "--" : `${formatNumber(metrics.gpu_free_gb, 1)}GB`, gate: "observe" },
    { metric: "阈值方案", value: Object.keys(payload.thresholds?.thresholds || payload.thresholds || {}).join(", ") || "--", gate: "calibrate" },
  ];
  qs("#evaluation-metrics-table").innerHTML = `
    <table class="data-table">
      <thead><tr><th>指标</th><th>当前值</th><th>门禁</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.metric)}</td><td>${escapeHtml(row.value)}</td><td>${escapeHtml(row.gate)}</td></tr>`).join("")}</tbody>
    </table>`;
}


async function submitTrackReviewAnnotation(event) {
  event.preventDefault();
  const jobId = qs("#track-review-job-input").value.trim();
  const trackId = qs("#track-review-track-input").value.trim();
  if (!jobId || !trackId) throw new Error("请输入任务 ID 和轨迹 ID");
  const frameValue = qs("#track-review-frame-input").value.trim();
  const payload = {
    job_id: jobId,
    track_id: trackId,
    label: qs("#track-review-label-input").value,
    reviewer: qs("#track-review-reviewer-input").value.trim() || null,
    note: qs("#track-review-note-input").value.trim() || null,
    evidence_ref: qs("#track-review-evidence-input").value.trim() || null,
  };
  if (frameValue) payload.frame_index = Number(frameValue);
  await api("/v1/evaluation/track-reviews", { method: "POST", json: payload });
  qs("#track-review-note-input").value = "";
  await refreshTrackReview();
}

async function refreshEvaluationCenter() {
  const [status, thresholds, models, reviewPayload, datasetsPayload, thresholdPayload] = await Promise.all([
    api("/v1/admin/status"),
    api("/v1/thresholds"),
    api("/v1/models"),
    api("/v1/evaluation/track-reviews/summary?limit=10").catch((error) => ({ summary: { count: 0, label_counts: [], evidence_index: [], warning: error.message || String(error) } })),
    api("/v1/evaluation/datasets?limit=20").catch((error) => ({ datasets: [], warning: error.message || String(error) })),
    api("/v1/evaluation/threshold-recommendations").catch((error) => ({ threshold_recommendations: { sample_count: 0, recommendations: [], warning: error.message || String(error) } })),
  ]);
  if (!state.dashboard.metrics) await refreshDashboard();
  const capabilities = status.model_capabilities || {};
  const reviewSummary = reviewPayload.summary || payloadData(reviewPayload)?.summary || {};
  const datasets = datasetsPayload.datasets || payloadData(datasetsPayload)?.datasets || [];
  const thresholdRecommendations = thresholdPayload.threshold_recommendations || payloadData(thresholdPayload)?.threshold_recommendations || {};
  const thresholdRows = Array.isArray(thresholdRecommendations.recommendations) ? thresholdRecommendations.recommendations : [];
  const nonProduction = Object.values(capabilities).filter((item) => !["ready", "production"].includes(item?.status) || item?.model_id === item?.fallback_model_id).length;
  const payload = { tenant_id: state.tenantId, status, thresholds, models, metrics: state.dashboard.metrics || {}, capabilities, review_summary: reviewSummary, datasets, threshold_recommendations: thresholdRecommendations };
  renderSummary("#evaluation-summary", [
    { label: "能力数", value: Object.keys(capabilities).length },
    { label: "需门禁", value: nonProduction },
    { label: "数据集", value: datasets.length },
    { label: "阈值建议", value: thresholdRows.length },
    { label: "标注样本", value: reviewSummary.count ?? reviewSummary.total_annotations ?? 0 },
  ]);
  renderCapabilityTable(capabilities);
  renderEvaluationMetrics(payload);
  renderEvaluationDatasets(datasets);
  renderEvaluationThresholdRecommendations(thresholdRecommendations);
  renderEvaluationReviewSummary(reviewSummary);
  renderPayload("evaluation-center", "#evaluation-json", payload);
}

function renderReleaseAuditRows(audit) {
  const rows = Array.isArray(audit?.records) ? audit.records : [];
  qs("#release-audit-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>时间</th><th>事件</th><th>别名</th><th>目标/灰度</th><th>写入</th></tr></thead>
      <tbody>${rows.map((row) => {
        const rollout = Array.isArray(row.rollout)
          ? row.rollout.map((item) => `${item.target || "--"}:${item.weight ?? "--"}${item.status ? `/${item.status}` : ""}`).join(", ")
          : "";
        const target = row.new_target || rollout || "--";
        return `<tr><td>${escapeHtml(row.time ? formatDateTime(row.time) : "--")}</td><td>${escapeHtml(row.event || "--")}</td><td>${escapeHtml(row.alias || "--")}</td><td>${escapeHtml(target)}</td><td>${escapeHtml(row.written === undefined ? "--" : row.written ? "是" : "否")}</td></tr>`;
      }).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无发布审计记录</div>`;
}
async function refreshReleaseCenter(payload = null) {
  const [aliases, models, audit] = await Promise.all([
    api("/v1/admin/models/rollout/aliases").catch((error) => ({ error: error.message || String(error), aliases: [] })),
    api("/v1/models"),
    api("/v1/admin/models/rollout/audit?limit=20").catch((error) => ({ error: error.message || String(error), records: [], count: 0, malformed_count: 0 })),
  ]);
  const data = payload ? { action: payload.action, result: payload.result, aliases, models, audit } : { aliases, models, audit };
  renderSummary("#release-summary", [
    { label: "别名数", value: (aliases.aliases || []).length },
    { label: "模型数", value: models.count ?? 0 },
    { label: "审计记录", value: audit.count ?? (audit.records || []).length },
    { label: "异常行", value: audit.malformed_count ?? 0 },
  ]);
  renderReleaseAuditRows(audit);
  renderPayload("release-center", "#release-json", data);
}

async function submitReleaseAction(event) {
  event.preventDefault();
  const action = qs("#release-action-input").value;
  const aliasName = qs("#release-alias-input").value.trim();
  const target = qs("#release-target-input").value.trim();
  const expected = qs("#release-expected-input").value.trim();
  const dryRun = qs("#release-dry-run-input").checked;
  if (!aliasName) throw new Error("请输入模型别名");
  if (!dryRun && !window.confirm("确认执行非预演模型发布操作？该操作会写入模型别名配置和审计记录。")) return;
  let payload;
  if (action === "preview") {
    const key = encodeURIComponent(qs("#release-traffic-key-input").value.trim() || state.tenantId);
    payload = await api(`/v1/admin/models/rollout/aliases/preview?alias_name=${encodeURIComponent(aliasName)}&traffic_key=${key}`);
  } else if (action === "switch") {
    if (!target) throw new Error("请输入目标模型");
    payload = await api("/v1/admin/models/rollout/aliases/switch", { method: "POST", json: { alias_name: aliasName, target_model_id: target, expected_current_target: expected || null, dry_run: dryRun } });
  } else if (action === "weighted") {
    if (!target) throw new Error("请输入目标模型");
    payload = await api("/v1/admin/models/rollout/aliases/weighted", { method: "POST", json: { alias_name: aliasName, targets: [{ target_model_id: target, weight: Number(qs("#release-weight-input").value || 0), status: "candidate" }], expected_current_target: expected || null, dry_run: dryRun } });
  } else {
    payload = await api("/v1/admin/models/rollout/aliases/rollback", { method: "POST", json: { alias_name: aliasName, dry_run: dryRun } });
  }
  await refreshReleaseCenter({ action, result: payload });
}

function auditEventQueryParams() {
  const params = new URLSearchParams({ limit: "20" });
  const eventFilter = qs("#audit-event-filter-input")?.value.trim() || "";
  const outcomeFilter = qs("#audit-outcome-filter-input")?.value || "";
  const requestFilter = qs("#audit-request-filter-input")?.value.trim() || "";
  const categoryFilter = qs("#audit-category-filter-input")?.value || "";
  const createdSinceFilter = qs("#audit-created-since-input")?.value.trim() || "";
  const createdUntilFilter = qs("#audit-created-until-input")?.value.trim() || "";
  if (eventFilter) params.set("event", eventFilter);
  if (outcomeFilter) params.set("outcome", outcomeFilter);
  if (requestFilter) params.set("request_id", requestFilter);
  if (categoryFilter) params.set("category", categoryFilter);
  if (createdSinceFilter) params.set("created_since", createdSinceFilter);
  if (createdUntilFilter) params.set("created_until", createdUntilFilter);
  return params;
}
function renderAuditEventRows(auditEvents) {
  const rows = Array.isArray(auditEvents?.records) ? auditEvents.records : [];
  qs("#audit-event-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>时间</th><th>分类</th><th>事件</th><th>结果</th><th>请求 ID</th><th>审计哈希</th></tr></thead>
      <tbody>${rows.map((row) => {
        const hash = row.audit_hash ? String(row.audit_hash).slice(0, 16) : "--";
        return `<tr><td>${escapeHtml(row.created_at ? formatDateTime(row.created_at) : "--")}</td><td>${escapeHtml(localizeValue(row.category || "other"))}</td><td>${escapeHtml(row.event || "--")}</td><td>${escapeHtml(localizeValue(row.outcome || "--"))}</td><td>${escapeHtml(row.request_id || "--")}</td><td>${escapeHtml(hash)}</td></tr>`;
      }).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无审计事件</div>`;
}
async function refreshAuditCompliance() {
  const [status, exported, auditVerificationPayload, auditEventsPayload] = await Promise.all([
    api("/v1/admin/status"),
    api("/v1/admin/export?people_limit=10&jobs_limit=10&streams_limit=10&stream_events_limit=5"),
    api("/v1/admin/audit/verify").catch((error) => ({
      audit_chain: {
        ok: false,
        record_count: 0,
        error_count: 1,
        head_hash: null,
        path_hash: null,
        errors: [{ reason: error.message || String(error) }],
      },
    })),
    api(`/v1/admin/audit/events?${auditEventQueryParams().toString()}`).catch((error) => ({
      error: error.message || String(error),
      records: [],
      count: 0,
      malformed_count: 0,
      scanned_count: 0,
    })),
  ]);
  const security = status.security || {};
  const auditChain = auditVerificationPayload.audit_chain || auditVerificationPayload;
  const auditChainErrorCount = Number(auditChain.error_count || 0);
  const auditEventRows = Array.isArray(auditEventsPayload.records) ? auditEventsPayload.records : [];
  const auditEventMalformedCount = Number(auditEventsPayload.malformed_count || 0);
  const auditEventSummary = auditEventsPayload.summary || {};
  const auditCategoryCounts = auditEventSummary.category_counts || {};
  const checks = [
    { name: "强制鉴权", ok: Boolean(security.api_token_enabled || security.jwt_configured), current: security.api_token_enabled || security.jwt_configured, limit: true },
    { name: "租户头", ok: Boolean(security.tenant_header_required), current: security.tenant_header_required, limit: true },
    { name: "审计失败关闭", ok: Boolean(security.audit_write_fail_closed), current: security.audit_write_fail_closed, limit: true },
    { name: "载荷加密", ok: Boolean(security.encryption_enabled || !security.require_encryption), current: security.encryption_enabled, limit: security.require_encryption },
    { name: "审计链校验", ok: Boolean(auditChain.ok) && auditChainErrorCount === 0, current: `${auditChain.record_count ?? 0} records / ${auditChainErrorCount} errors`, limit: "0 errors" },
    { name: "审计事件读回", ok: !auditEventsPayload.error, current: `${auditEventRows.length} events / ${auditEventMalformedCount} malformed`, limit: "tenant scoped" },
  ];
  renderSummary("#audit-summary", [
    { label: "检查数", value: checks.length },
    { label: "通过", value: checks.filter((item) => item.ok).length },
    { label: "审计链", value: auditChain.ok ? "ok" : "warn" },
    { label: "审计记录", value: auditChain.record_count ?? 0 },
    { label: "最近事件", value: auditEventsPayload.matched_count ?? auditEventRows.length },
    { label: "删除", value: auditCategoryCounts.delete_requests ?? 0 },
    { label: "导出", value: auditCategoryCounts.exports ?? 0 },
    { label: "模型", value: auditCategoryCounts.model_versions ?? 0 },
    { label: "保留", value: auditCategoryCounts.retention ?? 0 },
    { label: "链错误", value: auditChainErrorCount },
    { label: "导出人员", value: exported.people?.length ?? 0 },
    { label: "请求 ID", value: auditEventsPayload.request_id || auditVerificationPayload.request_id || exported.request_id || status.request_id || "--" },
  ]);
  qs("#audit-check-list").innerHTML = checks.map((item) => `<div class="alert-item ${item.ok ? "ok" : "warn"}"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(compactValue(item.current))} / ${escapeHtml(compactValue(item.limit))}</span></div>`).join("");
  renderAuditEventRows(auditEventsPayload);
  renderPayload("audit-compliance", "#audit-json", { status, export: exported, audit_chain: auditChain, audit_events: auditEventsPayload, checks });
}
function renderBackupSnapshots(payload) {
  const rows = Array.isArray(payload?.snapshots) ? payload.snapshots : [];
  const backends = Array.from(new Set(rows.map((row) => row.object_backend).filter(Boolean)));
  renderSummary("#backup-snapshot-summary", [
    { label: "快照", value: payload?.count ?? rows.length },
    { label: "扫描", value: payload?.scanned_count ?? 0 },
    { label: "异常行", value: payload?.malformed_count ?? 0 },
    { label: "后端", value: backends.length ? backends.join(", ") : "--" },
    { label: "租户", value: payload?.tenant_id || state.tenantId },
  ]);
  qs("#backup-snapshot-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>时间</th><th>请求 ID</th><th>后端</th><th>字节数</th><th>增量起点</th><th>快照哈希</th></tr></thead>
      <tbody>${rows.map((row) => {
        const createdAt = row.created_at === null || row.created_at === undefined ? "--" : formatDateTime(row.created_at);
        const updatedSince = row.updated_since === null || row.updated_since === undefined ? "--" : formatDateTime(row.updated_since);
        const snapshotId = row.snapshot_id || row.audit_hash || "";
        const hash = snapshotId ? String(snapshotId).slice(0, 16) : "--";
        return `<tr><td>${escapeHtml(createdAt)}</td><td>${escapeHtml(row.request_id || "--")}</td><td>${escapeHtml(row.object_backend || "--")}</td><td>${escapeHtml(formatByteSize(row.bytes))}</td><td>${escapeHtml(updatedSince)}</td><td>${escapeHtml(hash)}</td></tr>`;
      }).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无备份快照</div>`;
}

async function refreshAdminData(payload = null) {
  const backupSnapshots = await api("/v1/admin/backups?limit=20").catch((error) => ({
    error: error.message || String(error),
    snapshots: [],
    count: 0,
    malformed_count: 0,
    scanned_count: 0,
    tenant_id: state.tenantId,
  }));
  renderBackupSnapshots(backupSnapshots);
  renderPayload("admin-data", "#admin-data-json", payload ? { ...payload, backup_snapshots: backupSnapshots } : { backup_snapshots: backupSnapshots });
}
