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
  try {
    setStatus("Refreshing");
    await Promise.allSettled([refreshModels(), refreshGallery(), refreshStreams(), refreshAdmin()]);
    setStatus("Ready");
  } catch (error) {
    setStatus(String(error.message || error), true);
  }
}

function saveAuth() {
  state.tenantId = qs("#tenant-input").value.trim() || "default";
  state.apiKey = qs("#api-key-input").value.trim();
  state.bearer = qs("#bearer-input").value.trim();
  localStorage.setItem("portraitHubTenant", state.tenantId);
  localStorage.setItem("portraitHubApiKey", state.apiKey);
  localStorage.setItem("portraitHubBearer", state.bearer);
  setStatus("Saved");
}

function modelId() {
  return encodeURIComponent(qs("#model-id-input").value.trim());
}

function selectedJobId() {
  return encodeURIComponent(qs("#job-id-input").value.trim());
}

function selectedStreamId() {
  return encodeURIComponent(qs("#stream-id-input").value.trim());
}

function setupEvents() {
  document.querySelectorAll("[data-nav]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.nav)));
  qs("#save-auth-button").addEventListener("click", saveAuth);
  qs("#refresh-button").addEventListener("click", refreshAll);
  qs("#gallery-refresh-button").addEventListener("click", refreshGallery);
  qs("#streams-refresh-button").addEventListener("click", refreshStreams);
  qs("#admin-refresh-button").addEventListener("click", refreshAdmin);

  qs("#load-model-button").addEventListener("click", async () => renderJson("#models-json", await api(`/v1/models/${modelId()}/load`, { method: "POST" })));
  qs("#unload-model-button").addEventListener("click", async () => renderJson("#models-json", await api(`/v1/models/${modelId()}/unload`, { method: "POST" })));

  qs("#enroll-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = new FormData(event.target);
    renderJson("#gallery-json", await api("/v1/gallery/enroll", { method: "POST", body }));
  });

  qs("#search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = new FormData(event.target);
    renderJson("#gallery-json", await api("/v1/gallery/search", { method: "POST", body }));
  });

  qs("#job-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const body = new FormData(event.target);
    renderJson("#jobs-json", await api("/v1/jobs/video", { method: "POST", body }));
  });
  qs("#job-get-button").addEventListener("click", async () => renderJson("#jobs-json", await api(`/v1/jobs/${selectedJobId()}`)));
  qs("#job-result-button").addEventListener("click", async () => renderJson("#jobs-json", await api(`/v1/jobs/${selectedJobId()}/result`)));
  qs("#job-cancel-button").addEventListener("click", async () => renderJson("#jobs-json", await api(`/v1/jobs/${selectedJobId()}/cancel`, { method: "POST" })));

  qs("#stream-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      stream_url: qs("#stream-url-input").value.trim(),
      name: qs("#stream-name-input").value.trim() || null,
      settings: {},
      metadata: {},
    };
    renderJson("#streams-json", await api("/v1/streams", { method: "POST", json: payload }));
  });
  qs("#stream-start-button").addEventListener("click", async () => renderJson("#streams-json", await api(`/v1/streams/${selectedStreamId()}/start`, { method: "POST" })));
  qs("#stream-stop-button").addEventListener("click", async () => renderJson("#streams-json", await api(`/v1/streams/${selectedStreamId()}/stop`, { method: "POST" })));
  qs("#stream-events-button").addEventListener("click", async () => renderJson("#streams-json", await api(`/v1/streams/${selectedStreamId()}/events`)));

  qs("#threshold-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {};
    const body = qs("#threshold-body-input").value;
    const face = qs("#threshold-face-input").value;
    if (body !== "") payload.body = Number(body);
    if (face !== "") payload.face = Number(face);
    renderJson("#admin-json", await api(`/v1/thresholds/${encodeURIComponent(qs("#threshold-profile-input").value.trim())}`, { method: "PUT", json: payload }));
  });

  qs("#retention-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    renderJson("#admin-json", await api("/v1/admin/retention/cleanup", {
      method: "POST",
      json: { retention_days: Number(qs("#retention-days-input").value), confirm: qs("#retention-confirm-input").value },
    }));
  });
}

function init() {
  qs("#tenant-input").value = state.tenantId;
  qs("#api-key-input").value = state.apiKey;
  qs("#bearer-input").value = state.bearer;
  setupEvents();
  setView(state.view);
  refreshAll();
}

init();
