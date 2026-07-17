// 解析结果视图：人员库摘要/特征可视化、视频任务与视频流结果网格、图片/比对/入库/检索提交。
// 从 views/app.js 拆分而来；依赖 app.js 中的全局 state/api/qs 等运行时函数（调用时解析）。

function renderGallerySummary(payload) {
  const people = Array.isArray(payload.people) ? payload.people : [];
  const featureCount = people.reduce((total, person) => total + Number(person.feature_count || (person.features || []).length || 0), 0);
  renderSummary("#gallery-summary", [
    { label: "人员数", value: payload.pagination?.people?.total ?? people.length },
    { label: "特征数", value: featureCount },
    { label: "向量后端", value: payload.model_capabilities ? "已配置" : "--" },
    { label: "租户", value: payload.tenant_id || state.tenantId },
  ]);
}

function renderGalleryVisuals(payload) {
  const people = Array.isArray(payload.people) ? payload.people : [];
  const list = qs("#people-list");
  list.innerHTML = people.length
    ? people.map((person) => {
      const name = escapeHtml(person.display_name || person.person_id);
      const id = escapeHtml(person.person_id);
      const count = Number(person.feature_count || (person.features || []).length || 0);
      return `<li><button type="button" class="ghost" data-person-id="${id}"><span>${name}</span><small>${id}</small></button><strong>${count}</strong></li>`;
    }).join("")
    : "<li><span>暂无人员</span><strong>0</strong></li>";
  qsa("[data-person-id]").forEach((button) => {
    button.addEventListener("click", () => {
      const id = button.dataset.personId;
      qs("#person-id-input").value = id;
      setStatus("已填入人员 ID");
      const person = people.find((p) => p.person_id === id);
      renderPersonFeatures(person);
    });
  });
  const scatter = qs("#feature-scatter");
  scatter.innerHTML = people
    .flatMap((person, personIndex) => (person.features || []).map((feature, featureIndex) => {
      const x = (personIndex * 7 + featureIndex * 3) % 12;
      const y = 11 - Math.min(11, Math.max(0, Math.round(Number(feature.quality_score || 0) * 11)));
      return `<span class="scatter-point scatter-x-${x} scatter-y-${y}" title="${escapeHtml(person.person_id)} ${escapeHtml(feature.modality)}"></span>`;
    }))
    .join("");
}

function renderPersonFeatures(person) {
  const container = qs("#person-features-list");
  if (!container) return;
  if (!person || !Array.isArray(person.features) || person.features.length === 0) {
    container.innerHTML = `<div class="result-empty">暂无特征图片</div>`;
    return;
  }
  container.innerHTML = person.features.map((feature) => {
    const modalityMap = {
      face: "人脸",
      body: "人体",
      appearance: "衣着外观",
    };
    const modalityText = modalityMap[feature.modality] || feature.modality || "未知";
    const modalityClass = ["face", "body", "appearance"].includes(feature.modality) ? feature.modality : "";
    const score = typeof feature.quality_score === "number" ? feature.quality_score.toFixed(3) : "--";
    const src = feature.thumbnail || feature.object?.thumbnail || "";
    const createdTime = feature.created_at ? new Date(feature.created_at * 1000).toLocaleString("zh-CN") : "--";
    const featureId = escapeHtml(feature.feature_id || "");
    const modelId = escapeHtml(feature.model_id || "");
    const badgeClass = modalityClass ? ` feature-badge--${modalityClass}` : "";
    const imgHtml = src
      ? `<img src="${escapeHtml(src)}" alt="特征" class="feature-thumbnail" />`
      : `<div class="feature-thumbnail-placeholder">暂无图片</div>`;

    return `
      <div class="result-visual-card">
        <div class="result-visual-stage">
          ${imgHtml}
        </div>
        <figcaption style="margin-top: 8px; display: grid; gap: 4px; font-size: 11px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px;">
            <span class="feature-badge${badgeClass}">${escapeHtml(modalityText)}</span>
            <strong style="color: var(--accent); font-weight: 600;">Q: ${escapeHtml(score)}</strong>
          </div>
          <strong title="${featureId}">ID: ${featureId.slice(0, 8)}...</strong>
          <strong title="${modelId}">模型: ${modelId}</strong>
          <strong>时间: ${escapeHtml(createdTime)}</strong>
        </figcaption>
      </div>
    `;
  }).join("");
}

function renderVisionSummary(payload) {
  const data = payloadData(payload);
  renderSummary("#vision-summary", [
    { label: "状态", value: data.status || "success" },
    { label: "帧/图数量", value: data.frame_count ?? data.image_count ?? data.count ?? "--" },
    { label: "人员/目标", value: data.person_count ?? data.face_count ?? data.result_count ?? "--" },
    { label: "耗时", value: data.timing?.total_seconds !== undefined ? `${formatNumber(data.timing.total_seconds, 3)}s` : "--" },
  ]);
}

function renderCompareSummary(payload) {
  const data = payloadData(payload);
  const comparison = data.comparison || data;
  renderSummary("#compare-summary", [
    { label: "是否通过", value: comparison.passed === undefined ? "--" : comparison.passed ? "通过" : "未通过" },
    { label: "相似度", value: formatNumber(comparison.similarity ?? comparison.quality_adjusted_similarity, 4) },
    { label: "阈值", value: formatNumber(comparison.threshold ?? comparison.adjusted_threshold, 4) },
    { label: "风险", value: comparison.risk || comparison.reason || "--" },
  ]);
}


function videoFrameVisual(frame, frameIndex, jobLabel) {
  const displaySrc = frame?.thumbnail || frame?.image || frame?.preview || "";
  if (!displaySrc) return null;
  const lightboxSrc = frame?.preview || frame?.image || frame?.thumbnail || displaySrc;
  const label = `第 ${frameIndex + 1} 帧`;
  const title = jobLabel ? `${jobLabel} / ${label}` : label;
  return {
    item: {
      src: displaySrc,
      displaySrc,
      lightboxSrc,
      name: title,
      label,
      width: frame.width || 1,
      height: frame.height || 1,
    },
    frame,
    frameIndex,
  };
}

function videoJobVisualInfo(payload) {
  const data = payloadData(payload);
  const job = data.job || {};
  const result = data.result || job.result || {};
  const frames = Array.isArray(result.frames) ? result.frames : [];
  const jobLabel = job.job_id || data.job_id || "视频任务";
  return {
    data,
    job,
    result,
    frames,
    visuals: frames.map((frame, index) => videoFrameVisual(frame, index, jobLabel)).filter(Boolean),
  };
}

function renderVideoVisualGrid(selector, visuals, emptyText, options = {}) {
  const node = qs(selector);
  if (!node) return;
  const variant = options.variant || "thumb";
  const maxWidth = options.maxWidth ?? 180;
  const maxHeight = options.maxHeight ?? 130;
  node.dataset.visualSource = selector;
  node.__visuals = visuals;
  node.classList.toggle("result-visual-grid--analysis", variant === "analysis");
  node.classList.toggle("result-visual-grid--video", variant === "video");
  if (!visuals.length) {
    node.innerHTML = emptyText ? `<div class="result-empty">${escapeHtml(emptyText)}</div>` : "";
    return;
  }
  node.innerHTML = visuals.map((entry, index) => resultVisualMarkup(entry, index, {
    variant,
    maxWidth,
    maxHeight,
    allowUpscale: options.allowUpscale,
  })).join("");
}

function renderJobVisuals(payload) {
  const info = videoJobVisualInfo(payload);
  renderVideoVisualGrid("#job-visuals", info.visuals, info.job.status === "completed" ? "该任务暂无可视化结果" : "解析进行中，有帧结果后会实时显示", {
    variant: "video",
    maxWidth: 260,
    maxHeight: 180,
  });
}

function renderVideoResults(payload) {
  const records = Array.isArray(payload?.archive_results) ? payload.archive_results : [];
  const visuals = records.flatMap((record) => record.visuals || []);
  state.analysisResults.video = payload;
  renderSummary("#video-results-summary", [
    { label: "结果批次", value: records.length },
    { label: "结果图片", value: visuals.length },
    { label: "全部批次", value: state.analysisResultsPagination.video?.total ?? records.length },
    { label: "租户", value: state.tenantId || "--" },
  ]);
  renderVideoVisualGrid("#video-results-visuals", visuals, "暂无已归档的视频解析图片", {
    variant: "video",
    maxWidth: 260,
    maxHeight: 180,
  });
  renderPayload("video-results", "#video-results-json", {
    results: records.map(({ visuals: _visuals, ...record }) => record),
  });
  updateArchiveLoadMore("video");
}

async function refreshVideoResults() {
  return refreshArchivedResults("video");
}

function visionModeLabel(mode) {
  return localizeValue(mode || "image") || "图片解析";
}

function imageAnalysisVisuals(mode, payload, previews, sourceType = "image") {
  const sourceLabel = { image: "图片", video: "视频", stream: "视频流" }[sourceType] || "解析";
  return visionVisualEntries(payload, previews).map((entry) => ({
    ...entry,
    item: {
      ...entry.item,
      label: `${sourceLabel} / ${visionModeLabel(mode)} / ${entry.item?.label || `#${(entry.frameIndex ?? 0) + 1}`}`,
      name: entry.item?.name || entry.item?.label || "图片解析结果",
    },
  }));
}

function archivedAnalysisRecord(record) {
  const payload = record?.payload || {};
  const mode = record?.mode || payload?.model?.task || "image";
  const sourceType = record?.source_type || "image";
  const previews = Array.isArray(record?.previews) ? record.previews : [];
  const visuals = imageAnalysisVisuals(mode, payload, previews, sourceType);
  return {
    id: record?.archive_id || `archive_${Date.now()}`,
    archive_id: record?.archive_id,
    result_id: record?.result_id,
    request_id: record?.request_id,
    created_at: Number(record?.created_at || 0) * 1000 || Date.now(),
    mode,
    mode_label: visionModeLabel(mode),
    endpoint: record?.endpoint || "/v1/vision/infer",
    source_type: sourceType,
    source_ref: record?.source_ref || "",
    payload,
    artifact_count: Number(record?.artifact_count || visuals.length),
    visual_count: visuals.length,
    frame_count: visuals.length,
    visuals,
  };
}

function archivePayloadRecords(payload) {
  const records = Array.isArray(payload?.results) ? payload.results : [];
  return records.map(archivedAnalysisRecord);
}

function archivePayloadPagination(payload) {
  return {
    next_cursor: payload?.next_cursor || null,
    has_more: Boolean(payload?.has_more && payload?.next_cursor),
    total: Number(payload?.total || 0),
  };
}

function updateArchiveLoadMore(sourceType) {
  const button = qs(`[data-results-load-more="${sourceType}"]`);
  if (!button) return;
  button.classList.toggle("hidden", !state.analysisResultsPagination[sourceType]?.has_more);
}

function renderImageResults() {
  const records = state.analysisResults.image;
  const visuals = records.flatMap((record) => record.visuals || []);
  const latest = records[0];
  renderSummary("#image-results-summary", [
    { label: "结果批次", value: records.length },
    { label: "图片数", value: visuals.length },
    { label: "全部批次", value: state.analysisResultsPagination.image?.total ?? records.length },
    { label: "最近能力", value: latest?.mode_label || "--" },
    { label: "租户", value: state.tenantId || "--" },
  ]);
  renderVideoVisualGrid("#image-results-visuals", visuals, "暂无图片解析结果，请先在图片解析页完成一次解析", {
    variant: "analysis",
    maxWidth: 420,
    maxHeight: 320,
    allowUpscale: true,
  });
  renderPayload("image-results", "#image-results-json", {
    results: records.map(({ visuals: _visuals, ...record }) => ({ ...record, visual_count: record.visual_count || 0 })),
  });
  updateArchiveLoadMore("image");
}

function latestStreamEvent(events) {
  return [...events].sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0))[0] || null;
}

function renderLiveStreamResults(payload) {
  const cachedStream = state.latestPayloads.stream?.stream || {};
  const stream = payload?.stream
    || (cachedStream.stream_id === payload?.stream_id ? cachedStream : null)
    || { stream_id: payload?.stream_id };
  const events = Array.isArray(payload?.events)
    ? payload.events
    : Array.isArray(stream.events)
      ? stream.events
      : [];
  const latestEvent = latestStreamEvent(events);
  const latestAnalysis = latestStreamEvent(events.filter((event) => event.type === "stream_analysis_completed"));
  const analysis = latestAnalysis?.payload || {};
  const frames = Array.isArray(analysis.frames) ? analysis.frames : [];
  const streamLabel = stream.name || stream.stream_id || payload?.stream_id || "\u89c6\u9891\u6d41";
  const visuals = frames.map((frame, index) => {
    const visual = videoFrameVisual(frame, index, streamLabel);
    if (!visual) return null;
    visual.stream = stream;
    visual.event = latestAnalysis;
    return visual;
  }).filter(Boolean);

  renderSummary("#stream-live-summary", [
    { label: "\u6d41\u72b6\u6001", value: localizeValue(stream.status || "--") },
    { label: "\u6700\u65b0\u6279\u6b21\u5e27", value: analysis.frame_count ?? frames.length },
    { label: "\u4eba\u5458", value: analysis.person_count ?? "--" },
    { label: "\u8f68\u8ff9", value: analysis.track_count ?? "--" },
    { label: "\u6700\u65b0\u4e8b\u4ef6", value: latestEvent?.type || "--" },
    { label: "\u89e3\u6790\u65f6\u95f4", value: latestAnalysis?.created_at ? formatDateTime(latestAnalysis.created_at) : "--" },
  ]);
  renderVideoVisualGrid("#stream-live-visuals", visuals, "\u7b49\u5f85\u89c6\u9891\u6d41\u4ea7\u751f\u5b9e\u65f6\u89e3\u6790\u5e27", {
    variant: "video",
    maxWidth: 260,
    maxHeight: 180,
  });
}

function renderStreamResults(payload) {
  const records = Array.isArray(payload?.archive_results) ? payload.archive_results : [];
  const visuals = records.flatMap((record) => record.visuals || []);
  state.analysisResults.stream = payload;
  renderSummary("#stream-results-summary", [
    { label: "结果批次", value: records.length },
    { label: "结果图片", value: visuals.length },
    { label: "全部批次", value: state.analysisResultsPagination.stream?.total ?? records.length },
    { label: "租户", value: state.tenantId || "--" },
  ]);
  renderVideoVisualGrid("#stream-results-visuals", visuals, "暂无已归档的视频流解析图片", {
    variant: "video",
    maxWidth: 260,
    maxHeight: 180,
  });
  const list = qs("#stream-results-list");
  if (list) list.innerHTML = "";
  renderPayload("stream-results", "#stream-results-json", {
    results: records.map(({ visuals: _visuals, ...record }) => record),
  });
  updateArchiveLoadMore("stream");
}

async function refreshStreamResults() {
  return refreshArchivedResults("stream");
}

function renderAnalysisResultsTab(tab = state.analysisResultsTab) {
  const nextTab = ["image", "video", "stream"].includes(tab) ? tab : "image";
  state.analysisResultsTab = nextTab;
  localStorage.setItem("portraitHubAnalysisResultsTab", nextTab);
  qsa("[data-results-tab]").forEach((button) => {
    const isActive = button.dataset.resultsTab === nextTab;
    button.setAttribute("aria-pressed", String(isActive));
    button.setAttribute("aria-selected", String(isActive));
  });
  qsa("[data-results-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.resultsPanel === nextTab));
  if (nextTab === "image") {
    renderImageResults();
  } else if (nextTab === "video") {
    renderVideoResults(state.analysisResults.video || { results: [] });
  } else if (nextTab === "stream") {
    renderStreamResults(state.analysisResults.stream || { archive_results: [] });
  }
}

async function refreshImageResults() {
  return refreshArchivedResults("image");
}

async function refreshArchivedResults(sourceType, options = {}) {
  const append = Boolean(options.append);
  const pagination = state.analysisResultsPagination[sourceType];
  const params = new URLSearchParams({ source_type: sourceType, limit: "24" });
  if (append && pagination?.next_cursor) params.set("cursor", pagination.next_cursor);
  const payload = await api(`/v1/analysis/results?${params.toString()}`);
  const incoming = archivePayloadRecords(payload);
  state.analysisResultsPagination[sourceType] = archivePayloadPagination(payload);
  if (sourceType === "image") {
    state.analysisResults.image = append ? [...state.analysisResults.image, ...incoming] : incoming;
    renderImageResults();
  } else {
    const previous = append ? state.analysisResults[sourceType]?.archive_results || [] : [];
    const resultPayload = { ...payload, archive_results: [...previous, ...incoming] };
    if (sourceType === "video") renderVideoResults(resultPayload);
    else renderStreamResults(resultPayload);
  }
  return payload;
}

async function loadMoreAnalysisResults(sourceType) {
  if (!["image", "video", "stream"].includes(sourceType)) return null;
  if (!state.analysisResultsPagination[sourceType]?.has_more) return null;
  return refreshArchivedResults(sourceType, { append: true });
}

async function refreshAnalysisResults() {
  await Promise.allSettled([refreshImageResults(), refreshVideoResults(), refreshStreamResults()]);
  renderAnalysisResultsTab(state.analysisResultsTab);
}

async function refreshActiveAnalysisResults() {
  if (state.analysisResultsTab === "video") return refreshVideoResults();
  if (state.analysisResultsTab === "stream") return refreshStreamResults();
  return refreshImageResults();
}

function renderJobSummary(payload) {
  const data = payloadData(payload);
  const job = data.job || {};
  const result = data.result || job.result || {};
  const frames = Array.isArray(result.frames) ? result.frames : [];
  const visualCount = frames.filter((frame) => isImageData(frame?.thumbnail || frame?.image || frame?.preview)).length;
  renderSummary("#jobs-summary", [
    { label: "任务状态", value: job.status || "--" },
    { label: "进度", value: job.progress !== undefined ? `${formatNumber(job.progress * 100, 1)}%` : "--" },
    { label: "解析帧", value: result.frame_count ?? frames.length ?? "--" },
    { label: "结果图片", value: visualCount },
  ]);
}

async function submitVision(event) {
  event.preventDefault();
  const mode = qs("#vision-mode-input").value;
  const files = ensureFiles(qs("#vision-files-input"), "图片文件");
  if (!files) return;
  const signature = filesSignature(files);
  if (state.visionPreviewSignature !== signature || state.visionPreviews.length !== Math.min(files.length, 8)) {
    state.visionPreviews = await previewItems(qs("#vision-files-input"), 8);
    state.visionPreviewSignature = signature;
  }
  const endpoint = selectedVisionEndpoint();
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  if (["faces", "persons", "appearance"].includes(mode)) {
    form.set("include_embeddings", qs("#vision-include-embeddings-input").checked ? "true" : "false");
    if (mode === "faces") form.set("fallback_to_image", "true");
  } else if (mode === "gait") {
    form.set("include_embedding", qs("#vision-include-embeddings-input").checked ? "true" : "false");
  } else if (mode === "embeddings") {
    form.set("model_id", "person_reid_default");
    form.set("include_vectors", qs("#vision-include-embeddings-input").checked ? "true" : "false");
  } else if (["detect", "tracks"].includes(mode)) {
    if (mode === "detect") form.set("model_id", "person_detector_default");
    form.set("confidence", qs("#vision-confidence-input").value);
    form.set("iou", qs("#vision-iou-input").value);
    form.set("max_detections", qs("#vision-max-detections-input").value);
    if (mode === "tracks") form.set("include_embeddings", qs("#vision-include-embeddings-input").checked ? "true" : "false");
  }
  const payload = await api(endpoint, { method: "POST", body: form });
  renderVisionSummary(payload);
  renderVisionVisuals(payload, state.visionPreviews);
  renderPayload("vision", "#vision-json", payload);
  await refreshImageResults();
}

async function submitCompare(event) {
  event.preventDefault();
  const mode = qs("#compare-mode-input").value;
  const leftFiles = ensureFiles(qs("#compare-a-input"), "图 A 或序列 A");
  const rightFiles = ensureFiles(qs("#compare-b-input"), "图 B 或序列 B");
  if (!leftFiles || !rightFiles) return;
  const form = new FormData();
  form.set("threshold_profile", qs("#compare-threshold-input").value.trim() || "normal");
  if (mode === "gait") {
    leftFiles.forEach((file) => form.append("sequence_a", file));
    rightFiles.forEach((file) => form.append("sequence_b", file));
    form.set("include_vectors", qs("#compare-include-vectors-input").checked ? "true" : "false");
  } else if (mode === "batch") {
    leftFiles.forEach((file) => form.append("image_a", file));
    rightFiles.forEach((file) => form.append("image_b", file));
    form.set("modality", qs("#compare-batch-modality-input").value);
    form.set("include_vectors", qs("#compare-include-vectors-input").checked ? "true" : "false");
    form.set("async_mode", qs("#compare-async-input").checked ? "true" : "false");
  } else {
    form.set("image_a", leftFiles[0]);
    form.set("image_b", rightFiles[0]);
    if (mode === "fusion") {
      form.set("modalities", qs("#compare-modalities-input").value.trim() || "face,body,appearance");
    } else {
      form.set("include_vectors", qs("#compare-include-vectors-input").checked ? "true" : "false");
    }
  }
  const payload = await api(selectedCompareEndpoint(), { method: "POST", body: form });
  renderCompareSummary(payload);
  renderPayload("compare", "#compare-json", payload);
  if (payload.batch_id) qs("#job-id-input").value = payload.batch_id;
}

async function submitGalleryEnroll(event) {
  event.preventDefault();
  const form = formDataWithBooleans(event.target);
  if (!formFiles(qs("#enroll-file-input")).length) {
    setStatus("请选择注册图片", true);
    return;
  }
  const payload = await api("/v1/gallery/enroll", { method: "POST", body: form });
  renderPayload("enroll", "#enroll-json", payload);
  const person = payload.person || {};
  renderSummary("#enroll-summary", [
    { label: "人员 ID", value: person.person_id || "--" },
    { label: "显示名称", value: person.display_name || "--" },
    { label: "特征数", value: person.feature_count ?? (person.features || []).length ?? "--" },
    { label: "租户", value: payload.tenant_id || state.tenantId },
  ]);
  await refreshGallery();
}

async function submitGallerySearch(event) {
  event.preventDefault();
  if (!formFiles(qs("#search-file-input")).length) {
    setStatus("请选择检索图片", true);
    return;
  }
  const payload = await api("/v1/gallery/search", { method: "POST", body: new FormData(event.target) });
  renderPayload("search", "#search-json", payload);
  renderSummary("#search-summary", [
    { label: "候选数", value: payload.candidate_count ?? 0 },
    { label: "前 K", value: payload.query?.top_k ?? "--" },
    { label: "模态", value: payload.query?.modality ?? "--" },
    { label: "质量", value: formatNumber(payload.query?.combined_quality_score, 3) },
  ]);
}

async function submitVideoJob(event) {
  event.preventDefault();
  if (!formFiles(qs("#job-file-input")).length) {
    setStatus("请选择视频文件", true);
    return;
  }
  const payload = await api("/v1/jobs/video", { method: "POST", body: new FormData(event.target) });
  const jobId = payload.job?.job_id;
  if (jobId) qs("#job-id-input").value = jobId;
  renderJobSummary(payload);
  renderJobVisuals(payload);
  renderPayload("jobs", "#jobs-json", payload);
}

async function submitStream(event) {
  event.preventDefault();
  const url = qs("#stream-url-input").value.trim();
  if (!url) {
    setStatus("请输入视频流地址", true);
    return;
  }
  const payload = await api("/v1/streams", {
    method: "POST",
    json: {
      stream_url: url,
      name: qs("#stream-name-input").value.trim() || null,
      settings: {},
      metadata: parseOptionalJson("#stream-metadata-input", {}),
    },
  });
  if (payload.stream?.stream_id) qs("#stream-id-input").value = payload.stream.stream_id;
  await refreshStreams();
  renderPayload("streams", "#streams-json", payload);
}
