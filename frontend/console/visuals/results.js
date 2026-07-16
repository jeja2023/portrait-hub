// Form encoding, previews, and result visualization helpers.
// Loaded before app.js; state and endpointMap are resolved when invoked.
function formDataWithBooleans(form, booleanFields = []) {
  const data = new FormData(form);
  booleanFields.forEach((name) => data.set(name, data.get(name) === "on" ? "true" : "false"));
  return data;
}

function copySharedFields(source, target, fields) {
  fields.forEach((name) => {
    if (source.has(name)) target.set(name, source.get(name));
  });
}

function formFiles(input) {
  return Array.from(input.files || []);
}

function filesSignature(files) {
  return files.map((file) => `${file.name}:${file.size}:${file.lastModified}`).join("|");
}

function ensureFiles(input, label) {
  const files = formFiles(input);
  if (!files.length) {
    setStatus(`请选择${label}`, true);
    return null;
  }
  return files;
}

function encodedInput(selector, label) {
  const value = qs(selector).value.trim();
  if (!value) {
    setStatus(`请输入${label}`, true);
    return null;
  }
  return encodeURIComponent(value);
}

function parseOptionalJson(selector, fallback = {}) {
  const raw = qs(selector).value.trim();
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not object");
    return parsed;
  } catch {
    throw new Error("JSON 字段必须是对象");
  }
}

function renderSummary(selector, items) {
  const node = qs(selector);
  if (!node) return;
  node.innerHTML = items
    .map((item) => `<div class="summary-item"><span>${escapeHtml(item.label)}</span><strong>${escapeHtml(item.value)}</strong></div>`)
    .join("");
}

function renderBadges(selector, items) {
  const node = qs(selector);
  if (!node) return;
  node.innerHTML = items.map((item) => `<span class="badge ${item.tone || ""}">${escapeHtml(item.label)}: ${escapeHtml(item.value)}</span>`).join("");
}

function payloadData(payload) {
  return payload && payload.data ? payload.data : payload;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result || "")));
    reader.addEventListener("error", () => reject(reader.error || new Error("文件预览失败")));
    reader.readAsDataURL(file);
  });
}

function imageSize(src) {
  return new Promise((resolve) => {
    const image = new Image();
    image.addEventListener("load", () => resolve({ width: image.naturalWidth || 1, height: image.naturalHeight || 1 }));
    image.addEventListener("error", () => resolve({ width: 1, height: 1 }));
    image.src = src;
  });
}

async function previewItems(input, limit = 8, prefix = "") {
  const files = formFiles(input).slice(0, limit);
  return Promise.all(files.map(async (file, index) => ({
    name: file.name,
    label: `${prefix}${index + 1}. ${file.name}`,
    src: await readFileAsDataUrl(file),
  }))).then((items) => Promise.all(items.map(async (item) => ({ ...item, ...(await imageSize(item.src)) }))));
}

function frameImageIndex(frame, fallbackIndex) {
  const raw = frame?.image_index ?? frame?.frame_index ?? frame?.index;
  const value = Number(raw);
  return Number.isInteger(value) && value >= 0 ? value : fallbackIndex;
}

function numericBox(box) {
  if (!Array.isArray(box) || box.length < 4) return null;
  const values = box.slice(0, 4).map((value) => Number(value));
  return values.every((value) => Number.isFinite(value)) ? values : null;
}

function recordLabel(record, fallback) {
  const name = record.label || record.class_name || record.name || record.track_id || fallback;
  const score = record.score ?? record.confidence;
  return score === undefined ? String(name) : `${name} ${formatNumber(score, 2)}`;
}

function frameRecords(frame) {
  const groups = [
    ["persons", "person"],
    ["faces", "face"],
    ["detections", "object"],
  ];
  const records = [];
  groups.forEach(([key, fallback]) => {
    const items = Array.isArray(frame?.[key]) ? frame[key] : [];
    items.forEach((item, index) => records.push({ ...item, _label: recordLabel(item, `${fallback} ${index + 1}`) }));
  });
  if (frame?.appearance?.box) records.push({ ...frame.appearance, _label: "appearance" });
  return records;
}

function poseKeypoints(frame) {
  const keypoints = frame?.pose?.keypoints;
  return Array.isArray(keypoints) ? keypoints : [];
}

function pointForKeypoint(keypoint) {
  const point = keypoint?.point || keypoint?.xy || keypoint;
  if (!Array.isArray(point) || point.length < 2) return null;
  const x = Number(point[0]);
  const y = Number(point[1]);
  return Number.isFinite(x) && Number.isFinite(y) ? [x, y] : null;
}

function buildVisualMeta(item, frame, frameIndex) {
  const width = Number(frame?.width) || Number(item?.width) || 1;
  const height = Number(frame?.height) || Number(item?.height) || 1;
  const boxes = frameRecords(frame)
    .map((record) => ({ box: numericBox(record.box || record.bbox || record.smoothed_box), label: record._label }))
    .filter((record) => record.box);
  const keypoints = poseKeypoints(frame)
    .map((keypoint) => ({ point: pointForKeypoint(keypoint), name: keypoint?.name || "" }))
    .filter((keypoint) => keypoint.point);
  const skeleton = Array.isArray(frame?.pose?.skeleton) ? frame.pose.skeleton : [];
  const pointByName = new Map(keypoints.map((keypoint) => [keypoint.name, keypoint.point]));
  const overlay = [
    ...boxes.map(({ box, label }) => {
      const [x1, y1, x2, y2] = box;
      const x = Math.max(0, Math.min(width, Math.min(x1, x2)));
      const y = Math.max(0, Math.min(height, Math.min(y1, y2)));
      const w = Math.max(1, Math.min(width, Math.max(x1, x2)) - x);
      const h = Math.max(1, Math.min(height, Math.max(y1, y2)) - y);
      return `<rect x="${x}" y="${y}" width="${w}" height="${h}" /><text x="${x + 4}" y="${Math.max(14, y + 16)}">${escapeHtml(label)}</text>`;
    }),
    ...skeleton.map((pair) => {
      if (!Array.isArray(pair) || pair.length < 2) return "";
      const left = pointByName.get(pair[0]);
      const right = pointByName.get(pair[1]);
      if (!left || !right) return "";
      return `<line x1="${left[0]}" y1="${left[1]}" x2="${right[0]}" y2="${right[1]}" />`;
    }),
    ...keypoints.map(({ point, name }) => `<circle cx="${point[0]}" cy="${point[1]}" r="4"><title>${escapeHtml(name)}</title></circle>`),
  ].join("");
  const count = boxes.length + keypoints.length;
  const frameLabel = `第 ${frameIndex + 1} 帧`;
  const caption = count ? `${count} 个标注` : "无可绘制标注";
  return { width, height, overlay, count, frameLabel, caption };
}

function resultVisualStageMarkup(item, meta, size, variant = "thumb") {
  const src = variant === "lightbox"
    ? item?.lightboxSrc || item?.src || item?.displaySrc || ""
    : item?.displaySrc || item?.src || "";
  return `
      <div class="result-visual-stage">
        <svg width="${size.width}" height="${size.height}" viewBox="0 0 ${meta.width} ${meta.height}" role="img" aria-label="${escapeHtml(item?.name || meta.frameLabel)}">
          <image href="${escapeHtml(src)}" x="0" y="0" width="${meta.width}" height="${meta.height}" preserveAspectRatio="none" />
          ${meta.overlay}
        </svg>
      </div>`;
}

function resultVisualMarkup(entry, visualIndex, options = {}) {
  const item = entry?.item || {};
  const frame = entry?.frame || {};
  const frameIndex = entry?.frameIndex ?? visualIndex;
  const meta = buildVisualMeta(item, frame, frameIndex);
  const variant = options.variant || "thumb";
  const interactive = options.interactive ?? variant !== "lightbox";
  const maxWidth = options.maxWidth ?? (variant === "lightbox" ? Math.max(320, Math.floor(window.innerWidth * 0.86)) : 180);
  const maxHeight = options.maxHeight ?? (variant === "lightbox" ? Math.max(240, Math.floor(window.innerHeight * 0.78)) : 130);
  const allowUpscale = options.allowUpscale ?? variant === "lightbox";
  const size = fitVisualSize(meta.width, meta.height, maxWidth, maxHeight, allowUpscale);
  const label = escapeHtml(item?.label || meta.frameLabel);
  const title = escapeHtml(item?.name || item?.label || meta.frameLabel);
  const stage = resultVisualStageMarkup(item, meta, size, variant);
  const cardStyle = variant === "analysis"
    ? ` style="--visual-card-width: ${Math.max(150, size.width)}px;"`
    : variant === "video"
      ? ` style="--visual-card-width: ${Math.max(220, size.width)}px;"`
      : "";
  return `
    <figure class="result-visual-card result-visual-card--${variant}"${cardStyle}>
      ${interactive ? `<button type="button" class="result-visual-trigger" data-result-visual-index="${visualIndex}" aria-label="放大查看 ${title}">${stage}</button>` : stage}
      <figcaption><span>${label}</span><strong>${meta.caption}</strong></figcaption>
    </figure>`;
}

function visionVisualEntries(payload, items) {
  const data = payloadData(payload);
  const frames = Array.isArray(data?.frames)
    ? data.frames
    : Array.isArray(data?.results)
      ? data.results
      : [];
  return !frames.length
    ? items.map((item, index) => ({ item, frame: { image_index: index }, frameIndex: index }))
    : frames
      .map((frame, index) => {
        const imageIndex = frameImageIndex(frame, index);
        return { item: items[imageIndex] || items[index], frame, frameIndex: index };
      })
      .filter((entry) => entry.item);
}

function renderVisionVisuals(payload, items) {
  const node = qs("#vision-visuals");
  if (!node) return;
  const visuals = visionVisualEntries(payload, items);
  state.visionResultVisuals = visuals;
  closeVisionLightbox();
  if (!items.length) {
    node.innerHTML = "";
    node.__visuals = [];
    return;
  }
  renderVideoVisualGrid("#vision-visuals", visuals, "", {
    variant: "analysis",
    maxWidth: 420,
    maxHeight: 320,
    allowUpscale: true,
  });
}
function closeVisionLightbox() {
  const returnFocus = state.visionLightboxReturnFocus;
  state.visionLightboxIndex = null;
  state.visionLightboxReturnFocus = null;
  const node = qs("#vision-lightbox");
  if (!node) return;
  node.classList.add("hidden");
  node.setAttribute("aria-hidden", "true");
  node.innerHTML = "";
  document.body.classList.remove("lightbox-open");
  if (returnFocus instanceof HTMLElement && returnFocus.isConnected) {
    returnFocus.focus();
  }
}

function renderVisionLightbox() {
  const node = qs("#vision-lightbox");
  if (!node) return;
  const visual = state.visionResultVisuals[state.visionLightboxIndex];
  if (!visual) {
    closeVisionLightbox();
    return;
  }
  node.innerHTML = `
    <div class="vision-lightbox-scrim" data-lightbox-close></div>
    <section class="vision-lightbox-panel" role="dialog" aria-modal="true" aria-label="解析结果放大图">
      <button type="button" class="vision-lightbox-close" data-lightbox-close aria-label="关闭放大预览">×</button>
      ${resultVisualMarkup(visual, state.visionLightboxIndex, { variant: "lightbox", interactive: false })}
    </section>`;
  node.classList.remove("hidden");
  node.setAttribute("aria-hidden", "false");
  document.body.classList.add("lightbox-open");
  node.querySelector(".vision-lightbox-close")?.focus();
}

function openVisionLightbox(index, trigger = document.activeElement) {
  if (!Number.isInteger(index) || index < 0 || index >= state.visionResultVisuals.length) return;
  state.visionLightboxReturnFocus = trigger instanceof HTMLElement ? trigger : null;
  state.visionLightboxIndex = index;
  renderVisionLightbox();
}

function trapVisionLightboxFocus(event) {
  if (event.key !== "Tab" || state.visionLightboxIndex === null) return;
  const panel = qs("#vision-lightbox .vision-lightbox-panel");
  if (!panel) return;
  const focusable = qsa(
    '#vision-lightbox button:not([disabled]), #vision-lightbox [href], #vision-lightbox [tabindex]:not([tabindex="-1"])',
  ).filter((element) => element instanceof HTMLElement && !element.hidden);
  if (!focusable.length) {
    event.preventDefault();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function requestSnippet(path, formFieldExamples = []) {
  const lines = [`curl -X POST "${window.location.origin}${path}"`];
  if (!state.apiKey) lines.push(`  -H "X-Tenant-ID: ${state.tenantId}"`);
  if (state.apiKey) lines.push('  -H "X-API-Key: ${PORTRAIT_HUB_API_TOKEN}"');
  if (state.bearer) lines.push('  -H "Authorization: Bearer ${PORTRAIT_HUB_BEARER_TOKEN}"');
  formFieldExamples.forEach((item) => lines.push(`  -F "${item}"`));
  return lines.join(" \\\n");
}
function renderIntegrationSnippet() {
  qs("#integration-code").textContent = requestSnippet("/v1/gallery/search", [
    "file=@query.jpg",
    "modality=body",
    "top_k=5",
    "threshold_profile=normal",
  ]);
}

async function copyText(text, notice = "内容已复制") {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
  }
  setStatus(notice);
}

function selectedVisionEndpoint() {
  return endpointMap.vision[qs("#vision-mode-input").value] || "/v1/infer/persons";
}

function selectedCompareEndpoint() {
  return endpointMap.compare[qs("#compare-mode-input").value] || "/v1/compare/persons";
}

function updateSnippetButtons() {
  const vision = selectedVisionEndpoint();
  const compare = selectedCompareEndpoint();
  const mode = qs("#vision-mode-input").value;
  const visionFields = ["files=@frame.jpg"];
  if (mode === "detect") visionFields.push("model_id=person_detector_default", "confidence=0.25", "iou=0.45");
  else if (mode === "embeddings") visionFields.push("model_id=person_reid_default", "include_vectors=false");
  else visionFields.push("include_embeddings=false");
  qs("#vision-copy-button").onclick = wrapHandler(() => copyText(requestSnippet(vision, visionFields), "调用示例已复制"));
  qs("#compare-copy-button").onclick = wrapHandler(() => copyText(requestSnippet(compare, ["image_a=@a.jpg", "image_b=@b.jpg", "threshold_profile=normal"]), "调用示例已复制"));
  qs("#gallery-copy-button").onclick = wrapHandler(() => copyText(requestSnippet("/v1/gallery/search", ["file=@query.jpg", "modality=body", "top_k=5"]), "调用示例已复制"));
  qs("#video-copy-button").onclick = wrapHandler(() => copyText(requestSnippet("/v1/jobs/video", ["file=@demo.mp4", "sample_interval_seconds=1.0", "batch_size=16"]), "调用示例已复制"));
}

async function renderPreviews(input, selector, prefix = "") {
  const files = formFiles(input);
  const signature = filesSignature(files);
  const node = qs(selector);
  if (!node) return;
  node.innerHTML = "";
  const items = await previewItems(input, 8, prefix);
  if (filesSignature(formFiles(input)) !== signature) return;
  if (selector === "#vision-preview") {
    state.visionPreviews = items;
    state.visionPreviewSignature = signature;
    state.visionResultVisuals = [];
    closeVisionLightbox();
    qs("#vision-visuals").innerHTML = "";
  }
  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "preview-card";
    card.innerHTML = `<img alt="${escapeHtml(item.name)}" src="${escapeHtml(item.src)}" /><span>${escapeHtml(item.label)}</span>`;
    node.appendChild(card);
  });
  if (files.length > 8) {
    const card = document.createElement("div");
    card.className = "preview-card";
    card.innerHTML = `<span>还有 ${files.length - 8} 个文件未预览</span>`;
    node.appendChild(card);
  }
}

async function renderComparePreviews() {
  const node = qs("#compare-preview");
  node.innerHTML = "";
  state.comparePreviews = { A: [], B: [] };
  for (const [input, prefix] of [
    [qs("#compare-a-input"), "A"],
    [qs("#compare-b-input"), "B"],
  ]) {
    const signature = filesSignature(formFiles(input));
    const items = await previewItems(input, 4, prefix);
    if (filesSignature(formFiles(input)) !== signature) return;
    state.comparePreviews[prefix] = items;
    items.forEach((item) => {
      const card = document.createElement("div");
      card.className = "preview-card";
      card.innerHTML = `<img alt="${escapeHtml(item.name)}" src="${escapeHtml(item.src)}" /><span>${escapeHtml(item.label)}</span>`;
      node.appendChild(card);
    });
  }
}

