const state = {
  tenantId: localStorage.getItem("portraitHubTenant") || "default",
  apiKey: localStorage.getItem("portraitHubApiKey") || "",
  bearer: localStorage.getItem("portraitHubBearer") || "",
  view: "dashboard",
  dashboard: {},
  galleryExport: {},
  alertConfig: loadAlertConfig(),
  sockets: {},
};

function loadAlertConfig() {
  try {
    const payload = JSON.parse(localStorage.getItem("portraitHubAlertConfig") || "{}");
    return {
      maxErrorRate: Number(payload.maxErrorRate ?? 0.05),
      maxP95Latency: Number(payload.maxP95Latency ?? 1.5),
      minFreeGpuMemoryGb: Number(payload.minFreeGpuMemoryGb ?? 1),
    };
  } catch {
    return { maxErrorRate: 0.05, maxP95Latency: 1.5, minFreeGpuMemoryGb: 1 };
  }
}

const template = `
  <header class="topbar">
    <div>
      <h1>PortraitHub 运维控制台</h1>
      <p>运维 API 交互界面与<a href="/docs" target="_blank">接口文档</a> (/models, /gallery, /jobs, /streams)</p>
    </div>
    <nav aria-label="console views">
      <button type="button" data-nav="dashboard">运行仪表盘</button>
      <button type="button" data-nav="models">模型管理</button>
      <button type="button" data-nav="gallery">底库检索</button>
      <button type="button" data-nav="jobs">离线任务</button>
      <button type="button" data-nav="streams">视频流管理</button>
      <button type="button" data-nav="admin">全局治理</button>
      <button type="button" data-nav="alerts">告警配置</button>
    </nav>
  </header>
  <main class="shell">
    <aside class="panel auth-panel">
      <h2>鉴权配置</h2>
      <label>租户 ID <input id="tenant-input" autocomplete="off" value="default" /></label>
      <label>API 令牌 <input id="api-key-input" type="password" autocomplete="off" /></label>
      <label>JWT 令牌 <input id="bearer-input" type="password" autocomplete="off" /></label>
      <div class="actions">
        <button type="button" id="save-auth-button">保存</button>
        <button type="button" id="refresh-button" class="primary">刷新</button>
      </div>
      <div id="status-strip" class="status-strip">就绪</div>
    </aside>
    <section class="workspace">
      <section class="view" data-view="dashboard">
        <div class="view-header">
          <h2>运行仪表盘</h2>
          <button type="button" id="dashboard-refresh-button">刷新指标</button>
        </div>
        <div class="metric-grid">
          <div class="metric"><span>推理请求</span><strong id="metric-requests">0</strong></div>
          <div class="metric"><span>错误率</span><strong id="metric-error-rate">0%</strong></div>
          <div class="metric"><span>P95 推理耗时</span><strong id="metric-p95">0s</strong></div>
          <div class="metric"><span>GPU 空闲显存</span><strong id="metric-gpu-free">--</strong></div>
        </div>
        <pre id="dashboard-json" class="json-view">{}</pre>
      </section>
      <section class="view" data-view="models">
        <div class="view-header">
          <h2>模型缓存状态</h2>
          <div class="actions">
            <input id="model-id-input" placeholder="请输入模型ID，例如: portrait_hub/yolov8n.onnx" />
            <button type="button" id="load-model-button">加载模型</button>
            <button type="button" id="unload-model-button">卸载模型</button>
          </div>
        </div>
        <pre id="models-json" class="json-view">{}</pre>
      </section>
      <section class="view" data-view="gallery">
        <div class="view-header">
          <h2>底库管理</h2>
          <button type="button" id="gallery-refresh-button">刷新底库</button>
        </div>
        <form id="enroll-form" class="form-grid">
          <label>人员 ID <input id="enroll-person-id-input" name="person_id" placeholder="请输入人员唯一ID" /></label>
          <label>姓名/展示名 <input id="enroll-display-name-input" name="display_name" placeholder="请输入姓名或展示名称" /></label>
          <label>生物特征模态
            <select id="enroll-modality-input" name="modality">
              <option value="body">人体 (Body)</option>
              <option value="face">人脸 (Face)</option>
              <option value="appearance">衣着外观 (Appearance)</option>
            </select>
          </label>
          <label>注册图片 <input id="enroll-file-input" name="files" type="file" multiple /></label>
          <button type="submit" class="primary">注册登记</button>
        </form>
        <form id="search-form" class="form-grid">
          <label>检索图片 <input id="search-file-input" name="file" type="file" /></label>
          <label>生物特征模态
            <select id="search-modality-input" name="modality">
              <option value="body">人体 (Body)</option>
              <option value="face">人脸 (Face)</option>
              <option value="appearance">衣着外观 (Appearance)</option>
            </select>
          </label>
          <label>返回数量 (Top K) <input id="search-top-k-input" name="top_k" type="number" min="1" value="5" /></label>
          <button type="submit" class="primary">底库检索</button>
        </form>
        <div class="split-grid">
          <div class="list-panel">
            <h2>人员列表</h2>
            <ul id="people-list" class="people-list"></ul>
          </div>
          <div class="list-panel">
            <h2>特征分布</h2>
            <div id="feature-scatter" class="scatter" aria-label="gallery feature distribution"></div>
          </div>
        </div>
        <pre id="gallery-json" class="json-view">{}</pre>
      </section>
      <section class="view" data-view="jobs">
        <div class="view-header">
          <h2>离线视频轨迹任务</h2>
          <input id="job-id-input" placeholder="请输入任务 ID" />
        </div>
        <form id="job-form" class="form-grid">
          <label>视频文件 <input id="job-file-input" name="file" type="file" /></label>
          <label>抽帧间隔 (帧) <input id="job-frame-interval-input" name="frame_interval" type="number" min="1" value="15" /></label>
          <label>最大处理帧数 <input id="job-max-frames-input" name="max_frames" type="number" min="1" value="64" /></label>
          <button type="submit" class="primary">创建任务</button>
        </form>
        <div class="actions">
          <button type="button" id="job-get-button">查询状态</button>
          <button type="button" id="job-result-button">查看结果</button>
          <button type="button" id="job-cancel-button">取消任务</button>
          <button type="button" id="job-watch-button">实时订阅</button>
        </div>
        <div id="job-ws-status" class="ws-status">未订阅任务进度</div>
        <pre id="jobs-json" class="json-view">{}</pre>
      </section>
      <section class="view" data-view="streams">
        <div class="view-header">
          <h2>视频流管理</h2>
          <button type="button" id="streams-refresh-button">刷新状态</button>
        </div>
        <form id="stream-form" class="form-grid">
          <label>视频流地址 (RTSP/HTTP) <input id="stream-url-input" name="stream_url" placeholder="rtsp://..." /></label>
          <label>视频流名称 <input id="stream-name-input" name="name" placeholder="请输入视频流显示名称" /></label>
          <button type="submit" class="primary">创建流</button>
        </form>
        <div class="actions">
          <input id="stream-id-input" placeholder="请输入视频流 ID" />
          <button type="button" id="stream-start-button">启动分析</button>
          <button type="button" id="stream-stop-button">停止分析</button>
          <button type="button" id="stream-events-button">查看事件</button>
          <button type="button" id="stream-watch-button">实时订阅</button>
        </div>
        <div id="stream-ws-status" class="ws-status">未订阅视频流事件</div>
        <pre id="streams-json" class="json-view">{}</pre>
      </section>
      <section class="view" data-view="admin">
        <div class="view-header">
          <h2>全局比对阈值与数据保留策略</h2>
          <button type="button" id="admin-refresh-button">刷新配置</button>
        </div>
        <form id="threshold-form" class="form-grid">
          <label>阈值方案类型 <input id="threshold-profile-input" value="normal" /></label>
          <label>人体比对阈值 <input id="threshold-body-input" type="number" min="0" max="1" step="0.01" /></label>
          <label>人脸比对阈值 <input id="threshold-face-input" type="number" min="0" max="1" step="0.01" /></label>
          <button type="submit" class="primary">保存比对阈值</button>
        </form>
        <form id="retention-form" class="form-grid">
          <label>数据保留天数 <input id="retention-days-input" type="number" min="1" value="30" /></label>
          <label>输入 "cleanup" 确认 <input id="retention-confirm-input" placeholder="cleanup" /></label>
          <button type="submit" class="danger">执行过期数据清理</button>
        </form>
        <pre id="admin-json" class="json-view">{}</pre>
      </section>
      <section class="view" data-view="alerts">
        <div class="view-header">
          <h2>告警配置</h2>
          <button type="button" id="alerts-refresh-button">评估告警</button>
        </div>
        <form id="alert-form" class="form-grid">
          <label>最大错误率 <input id="alert-error-rate-input" type="number" min="0" max="1" step="0.01" /></label>
          <label>最大 P95 延迟(秒) <input id="alert-p95-input" type="number" min="0" step="0.1" /></label>
          <label>最小 GPU 空闲显存(GB) <input id="alert-gpu-free-input" type="number" min="0" step="0.1" /></label>
          <button type="submit" class="primary">保存告警阈值</button>
        </form>
        <div id="alert-list" class="alert-list"></div>
        <pre id="alerts-json" class="json-view">{}</pre>
      </section>
    </section>
  </main>`;

function qs(selector) {
  return document.querySelector(selector);
}

function headers(extra = {}) {
  const result = { "X-Tenant-ID": state.tenantId, ...extra };
  if (state.bearer) result.Authorization = `Bearer ${state.bearer}`;
  if (state.apiKey) result["X-API-Key"] = state.apiKey;
  return result;
}

function websocketUrl(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const params = new URLSearchParams({ tenant_id: state.tenantId });
  if (state.apiKey) params.set("token", state.apiKey);
  if (state.bearer) params.set("access_token", state.bearer);
  return `${protocol}//${window.location.host}${path}?${params.toString()}`;
}

function closeSocket(name) {
  const socket = state.sockets[name];
  if (socket) {
    socket.close();
    delete state.sockets[name];
  }
}

function watchJsonSocket(name, path, statusSelector, outputSelector) {
  closeSocket(name);
  const socket = new WebSocket(websocketUrl(path));
  state.sockets[name] = socket;
  qs(statusSelector).textContent = "正在连接实时通道";
  socket.addEventListener("open", () => {
    qs(statusSelector).textContent = "实时通道已连接";
  });
  socket.addEventListener("message", (event) => {
    try {
      renderJson(outputSelector, JSON.parse(event.data));
    } catch {
      qs(outputSelector).textContent = event.data;
    }
  });
  socket.addEventListener("close", () => {
    qs(statusSelector).textContent = "实时通道已断开";
  });
  socket.addEventListener("error", () => {
    qs(statusSelector).textContent = "实时通道连接失败";
  });
}

function setStatus(message, isError = false) {
  const strip = qs("#status-strip");
  strip.textContent = message;
  strip.classList.toggle("error", isError);
}

function renderJson(selector, payload) {
  qs(selector).textContent = JSON.stringify(payload, null, 2);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function metricValue(metrics, name) {
  const found = metrics.find((item) => item.name === name && Object.keys(item.labels).length === 0);
  return found ? Number(found.value) : 0;
}

function histogramP95(metrics, baseName) {
  const buckets = metrics
    .filter((item) => item.name === `${baseName}_bucket` && item.labels.le !== "+Inf")
    .map((item) => ({ le: Number(item.labels.le), count: Number(item.value) }))
    .sort((left, right) => left.le - right.le);
  if (!buckets.length) return 0;
  const total = buckets[buckets.length - 1].count;
  if (total <= 0) return 0;
  const target = total * 0.95;
  const bucket = buckets.find((item) => item.count >= target);
  return bucket ? bucket.le : buckets[buckets.length - 1].le;
}

function parsePrometheus(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"))
    .map((line) => {
      const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+0-9.eE]+)$/);
      if (!match) return null;
      const labels = {};
      if (match[2]) {
        match[2].split(",").forEach((item) => {
          const index = item.indexOf("=");
          if (index > 0) labels[item.slice(0, index)] = item.slice(index + 1).replace(/^"|"$/g, "");
        });
      }
      return { name: match[1], labels, value: Number(match[3]) };
    })
    .filter(Boolean);
}

async function textApi(path) {
  const response = await fetch(path, { headers: headers() });
  const text = await response.text();
  if (!response.ok) throw new Error(text || response.statusText);
  return text;
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: headers(options.headers || {}) };
  if (options.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.json);
  }
  if (options.body !== undefined) init.body = options.body;
  const response = await fetch(path, init);
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { body: text };
    }
  }
  if (!response.ok) throw new Error(JSON.stringify(payload));
  return payload.data || payload;
}

function setView(view) {
  state.view = view;
  document.querySelectorAll("[data-view]").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  document.querySelectorAll("[data-nav]").forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.nav === view)));
}

function wrapHandler(fn) {
  return async (...args) => {
    try {
      setStatus("处理中...");
      await fn(...args);
      setStatus("就绪");
    } catch (error) {
      let msg = error.message || String(error);
      try {
        const parsed = JSON.parse(msg);
        msg = parsed.detail || parsed.message || msg;
      } catch {}
      setStatus(msg, true);
    }
  };
}

async function refreshModels() {
  renderJson("#models-json", await api("/v1/models"));
}

async function refreshGallery() {
  const payload = await api("/v1/admin/export?people_limit=50&jobs_limit=0&streams_limit=0");
  state.galleryExport = payload;
  renderGalleryVisuals(payload);
  renderJson("#gallery-json", payload);
}

async function refreshStreams() {
  renderJson("#streams-json", await api("/v1/streams?limit=50"));
}

async function refreshAdmin() {
  const [status, thresholds] = await Promise.all([api("/v1/admin/status"), api("/v1/thresholds")]);
  renderJson("#admin-json", { status, thresholds });
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
  const gpuFreeBytes = metrics
    .filter((item) => item.name === "gpu_worker_gpu_memory_free_bytes")
    .reduce((total, item) => total + Number(item.value), 0);
  const summary = {
    status,
    totals: exportPayload.pagination || {},
    metrics: {
      requests,
      errors,
      error_rate: requests > 0 ? errors / requests : 0,
      inference_p95_seconds: histogramP95(metrics, "gpu_worker_inference_seconds"),
      gpu_free_gb: gpuFreeBytes ? gpuFreeBytes / (1024 ** 3) : null,
    },
  };
  state.dashboard = summary;
  renderDashboard(summary);
  renderAlerts();
  renderJson("#dashboard-json", summary);
}

async function refreshAll() {
  await Promise.allSettled([refreshDashboard(), refreshModels(), refreshGallery(), refreshStreams(), refreshAdmin()]);
}

function renderDashboard(summary) {
  const metrics = summary.metrics || {};
  qs("#metric-requests").textContent = String(metrics.requests || 0);
  qs("#metric-error-rate").textContent = `${((metrics.error_rate || 0) * 100).toFixed(1)}%`;
  qs("#metric-p95").textContent = `${Number(metrics.inference_p95_seconds || 0).toFixed(2)}s`;
  qs("#metric-gpu-free").textContent = metrics.gpu_free_gb === null ? "--" : `${Number(metrics.gpu_free_gb).toFixed(1)}GB`;
}

function renderGalleryVisuals(payload) {
  const people = Array.isArray(payload.people) ? payload.people : [];
  const list = qs("#people-list");
  list.innerHTML = people.length
    ? people.map((person) => `<li><span>${escapeHtml(person.display_name || person.person_id)}</span><strong>${Number(person.feature_count || 0)}</strong></li>`).join("")
    : "<li><span>暂无人员</span><strong>0</strong></li>";
  const scatter = qs("#feature-scatter");
  scatter.innerHTML = people
    .flatMap((person, personIndex) => (person.features || []).map((feature, featureIndex) => {
      const x = (personIndex * 7 + featureIndex * 3) % 12;
      const y = 11 - Math.min(11, Math.max(0, Math.round(Number(feature.quality_score || 0) * 11)));
      return `<span class="scatter-point scatter-x-${x} scatter-y-${y}" title="${escapeHtml(person.person_id)} ${escapeHtml(feature.modality)}"></span>`;
    }))
    .join("");
}

function setAlertInputs() {
  qs("#alert-error-rate-input").value = state.alertConfig.maxErrorRate;
  qs("#alert-p95-input").value = state.alertConfig.maxP95Latency;
  qs("#alert-gpu-free-input").value = state.alertConfig.minFreeGpuMemoryGb;
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
    const current = item.current === null ? "--" : `${(Number(item.current) * item.scale).toFixed(2)}${item.unit}`;
    const limit = `${(Number(item.limit) * item.scale).toFixed(2)}${item.unit}`;
    return `<div class="alert-item ${item.ok ? "ok" : "warn"}"><strong>${item.name}</strong><span>${current} / ${limit}</span></div>`;
  }).join("");
  renderJson("#alerts-json", { config: state.alertConfig, checks });
}

function saveAuth() {
  state.tenantId = qs("#tenant-input").value.trim() || "default";
  state.apiKey = qs("#api-key-input").value.trim();
  state.bearer = qs("#bearer-input").value.trim();
  localStorage.setItem("portraitHubTenant", state.tenantId);
  localStorage.setItem("portraitHubApiKey", state.apiKey);
  localStorage.setItem("portraitHubBearer", state.bearer);
  closeSocket("job");
  closeSocket("stream");
  setStatus("保存成功");
}

function encodedInput(selector, label) {
  const val = qs(selector).value.trim();
  if (!val) {
    setStatus(`请输入${label}`, true);
    return null;
  }
  return encodeURIComponent(val);
}

function setupEvents() {
  document.querySelectorAll("[data-nav]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.nav)));
  qs("#save-auth-button").addEventListener("click", saveAuth);
  qs("#refresh-button").addEventListener("click", wrapHandler(refreshAll));
  qs("#dashboard-refresh-button").addEventListener("click", wrapHandler(refreshDashboard));
  qs("#gallery-refresh-button").addEventListener("click", wrapHandler(refreshGallery));
  qs("#streams-refresh-button").addEventListener("click", wrapHandler(refreshStreams));
  qs("#admin-refresh-button").addEventListener("click", wrapHandler(refreshAdmin));
  qs("#alerts-refresh-button").addEventListener("click", wrapHandler(async () => {
    await refreshDashboard();
    renderAlerts();
  }));

  qs("#load-model-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型ID");
    if (!id) return;
    renderJson("#models-json", await api(`/v1/models/${id}/load`, { method: "POST" }));
  }));
  qs("#unload-model-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型ID");
    if (!id) return;
    renderJson("#models-json", await api(`/v1/models/${id}/unload`, { method: "POST" }));
  }));
  qs("#enroll-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    renderJson("#gallery-json", await api("/v1/gallery/enroll", { method: "POST", body: new FormData(event.target) }));
  }));
  qs("#search-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    renderJson("#gallery-json", await api("/v1/gallery/search", { method: "POST", body: new FormData(event.target) }));
  }));
  qs("#job-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    renderJson("#jobs-json", await api("/v1/jobs/video", { method: "POST", body: new FormData(event.target) }));
  }));
  qs("#job-get-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务ID");
    if (!id) return;
    renderJson("#jobs-json", await api(`/v1/jobs/${id}`));
  }));
  qs("#job-result-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务ID");
    if (!id) return;
    renderJson("#jobs-json", await api(`/v1/jobs/${id}/result`));
  }));
  qs("#job-cancel-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务ID");
    if (!id) return;
    renderJson("#jobs-json", await api(`/v1/jobs/${id}/cancel`, { method: "POST" }));
  }));
  qs("#job-watch-button").addEventListener("click", () => {
    const id = encodedInput("#job-id-input", "任务ID");
    if (!id) return;
    watchJsonSocket("job", `/ws/jobs/${id}`, "#job-ws-status", "#jobs-json");
  });
  qs("#stream-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const url = qs("#stream-url-input").value.trim();
    if (!url) {
      setStatus("请输入视频流地址", true);
      return;
    }
    renderJson("#streams-json", await api("/v1/streams", {
      method: "POST",
      json: { stream_url: url, name: qs("#stream-name-input").value.trim() || null, settings: {}, metadata: {} },
    }));
  }));
  qs("#stream-start-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流ID");
    if (!id) return;
    renderJson("#streams-json", await api(`/v1/streams/${id}/start`, { method: "POST" }));
  }));
  qs("#stream-stop-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流ID");
    if (!id) return;
    renderJson("#streams-json", await api(`/v1/streams/${id}/stop`, { method: "POST" }));
  }));
  qs("#stream-events-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流ID");
    if (!id) return;
    renderJson("#streams-json", await api(`/v1/streams/${id}/events`));
  }));
  qs("#stream-watch-button").addEventListener("click", () => {
    const id = encodedInput("#stream-id-input", "视频流ID");
    if (!id) return;
    watchJsonSocket("stream", `/ws/streams/${id}`, "#stream-ws-status", "#streams-json");
  });
  qs("#threshold-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const profile = qs("#threshold-profile-input").value.trim();
    if (!profile) {
      setStatus("请输入阈值方案类型", true);
      return;
    }
    const payload = {};
    const body = qs("#threshold-body-input").value;
    const face = qs("#threshold-face-input").value;
    if (body !== "") payload.body = Number(body);
    if (face !== "") payload.face = Number(face);
    renderJson("#admin-json", await api(`/v1/thresholds/${encodeURIComponent(profile)}`, { method: "PUT", json: payload }));
  }));
  qs("#retention-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    renderJson("#admin-json", await api("/v1/admin/retention/cleanup", {
      method: "POST",
      json: { retention_days: Number(qs("#retention-days-input").value), confirm: qs("#retention-confirm-input").value },
    }));
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
  qs("#tenant-input").value = state.tenantId;
  qs("#api-key-input").value = state.apiKey;
  qs("#bearer-input").value = state.bearer;
  setAlertInputs();
  setupEvents();
  setView(state.view);
  wrapHandler(refreshAll)();
}

init();
