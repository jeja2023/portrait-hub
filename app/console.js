const state = {
  tenantId: localStorage.getItem("portraitHubTenant") || "default",
  apiKey: localStorage.getItem("portraitHubApiKey") || "",
  bearer: localStorage.getItem("portraitHubBearer") || "",
  view: "models",
};

function qs(selector) {
  return document.querySelector(selector);
}

function headers(extra = {}) {
  const result = { "X-Tenant-ID": state.tenantId, ...extra };
  if (state.bearer) result.Authorization = `Bearer ${state.bearer}`;
  if (state.apiKey) result["X-API-Key"] = state.apiKey;
  return result;
}

function setStatus(message, isError = false) {
  const strip = qs("#status-strip");
  strip.textContent = message;
  strip.classList.toggle("error", isError);
}

function renderJson(selector, payload) {
  qs(selector).textContent = JSON.stringify(payload, null, 2);
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

// 事件处理器包装函数，统一处理 loading 状态及错误捕获展示
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
  renderJson("#gallery-json", await api("/v1/admin/export?people_limit=50&jobs_limit=0&streams_limit=0"));
}

async function refreshStreams() {
  renderJson("#streams-json", await api("/v1/streams?limit=50"));
}

async function refreshAdmin() {
  const [status, thresholds] = await Promise.all([api("/v1/admin/status"), api("/v1/thresholds")]);
  renderJson("#admin-json", { status, thresholds });
}

async function refreshAll() {
  await Promise.allSettled([refreshModels(), refreshGallery(), refreshStreams(), refreshAdmin()]);
}

function saveAuth() {
  state.tenantId = qs("#tenant-input").value.trim() || "default";
  state.apiKey = qs("#api-key-input").value.trim();
  state.bearer = qs("#bearer-input").value.trim();
  localStorage.setItem("portraitHubTenant", state.tenantId);
  localStorage.setItem("portraitHubApiKey", state.apiKey);
  localStorage.setItem("portraitHubBearer", state.bearer);
  setStatus("保存成功");
}

function modelId() {
  const val = qs("#model-id-input").value.trim();
  if (!val) {
    setStatus("请输入模型ID", true);
    return null;
  }
  return encodeURIComponent(val);
}

function selectedJobId() {
  const val = qs("#job-id-input").value.trim();
  if (!val) {
    setStatus("请输入任务ID", true);
    return null;
  }
  return encodeURIComponent(val);
}

function selectedStreamId() {
  const val = qs("#stream-id-input").value.trim();
  if (!val) {
    setStatus("请输入视频流ID", true);
    return null;
  }
  return encodeURIComponent(val);
}

function setupEvents() {
  document.querySelectorAll("[data-nav]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.nav)));
  qs("#save-auth-button").addEventListener("click", saveAuth);
  qs("#refresh-button").addEventListener("click", wrapHandler(refreshAll));
  qs("#gallery-refresh-button").addEventListener("click", wrapHandler(refreshGallery));
  qs("#streams-refresh-button").addEventListener("click", wrapHandler(refreshStreams));
  qs("#admin-refresh-button").addEventListener("click", wrapHandler(refreshAdmin));

  qs("#load-model-button").addEventListener("click", wrapHandler(async () => {
    const id = modelId();
    if (!id) return;
    renderJson("#models-json", await api(`/v1/models/${id}/load`, { method: "POST" }));
  }));
  qs("#unload-model-button").addEventListener("click", wrapHandler(async () => {
    const id = modelId();
    if (!id) return;
    renderJson("#models-json", await api(`/v1/models/${id}/unload`, { method: "POST" }));
  }));

  qs("#enroll-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const body = new FormData(event.target);
    renderJson("#gallery-json", await api("/v1/gallery/enroll", { method: "POST", body }));
  }));

  qs("#search-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const body = new FormData(event.target);
    renderJson("#gallery-json", await api("/v1/gallery/search", { method: "POST", body }));
  }));

  qs("#job-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const body = new FormData(event.target);
    renderJson("#jobs-json", await api("/v1/jobs/video", { method: "POST", body }));
  }));
  qs("#job-get-button").addEventListener("click", wrapHandler(async () => {
    const id = selectedJobId();
    if (!id) return;
    renderJson("#jobs-json", await api(`/v1/jobs/${id}`));
  }));
  qs("#job-result-button").addEventListener("click", wrapHandler(async () => {
    const id = selectedJobId();
    if (!id) return;
    renderJson("#jobs-json", await api(`/v1/jobs/${id}/result`));
  }));
  qs("#job-cancel-button").addEventListener("click", wrapHandler(async () => {
    const id = selectedJobId();
    if (!id) return;
    renderJson("#jobs-json", await api(`/v1/jobs/${id}/cancel`, { method: "POST" }));
  }));

  qs("#stream-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const url = qs("#stream-url-input").value.trim();
    if (!url) {
      setStatus("请输入视频流地址", true);
      return;
    }
    const payload = {
      stream_url: url,
      name: qs("#stream-name-input").value.trim() || null,
      settings: {},
      metadata: {},
    };
    renderJson("#streams-json", await api("/v1/streams", { method: "POST", json: payload }));
  }));
  qs("#stream-start-button").addEventListener("click", wrapHandler(async () => {
    const id = selectedStreamId();
    if (!id) return;
    renderJson("#streams-json", await api(`/v1/streams/${id}/start`, { method: "POST" }));
  }));
  qs("#stream-stop-button").addEventListener("click", wrapHandler(async () => {
    const id = selectedStreamId();
    if (!id) return;
    renderJson("#streams-json", await api(`/v1/streams/${id}/stop`, { method: "POST" }));
  }));
  qs("#stream-events-button").addEventListener("click", wrapHandler(async () => {
    const id = selectedStreamId();
    if (!id) return;
    renderJson("#streams-json", await api(`/v1/streams/${id}/events`));
  }));

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
}

function init() {
  qs("#tenant-input").value = state.tenantId;
  qs("#api-key-input").value = state.apiKey;
  qs("#bearer-input").value = state.bearer;
  setupEvents();
  setView(state.view);
  wrapHandler(refreshAll)();
}

init();
