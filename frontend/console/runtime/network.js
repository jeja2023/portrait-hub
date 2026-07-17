// View, API, WebSocket, and metrics runtime helpers.
// Loaded before app.js; state is resolved when a helper is invoked.
function setView(view) {
  // 兼容旧版本残留的视图名（如 gallery/admin 已拆分），无匹配时回退到总览
  if (!document.querySelector(`[data-view="${view}"]`)) view = "overview";
  state.view = view;
  localStorage.setItem("portraitHubView", view);
  qsa("[data-view]").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  const activeNav = qsa("[data-nav]").reduce((found, item) => {
    const isActive = item.dataset.nav === view;
    item.setAttribute("aria-pressed", String(isActive));
    return isActive ? item : found;
  }, null);
  // 自动展开当前视图所在的侧栏分组，便于定位
  const activeGroup = activeNav ? activeNav.closest(".nav-group") : null;
  qsa(".nav-group").forEach((group) => {
    group.open = group === activeGroup;
  });
  closeVisionLightbox();
  if (view === "video-results") {
    renderAnalysisResultsTab(state.analysisResultsTab);
    if (state.isLoggedIn) refreshAnalysisResults().catch(() => {});
  }
  if (view === "access-credentials") refreshAccessApplications().catch(() => renderAccessApplications());
  if (view === "sdk-examples") renderSdkExamples();
  if (view === "openapi-docs") refreshOpenApiDocs().catch(() => renderOpenApiDocs());
  if (view === "api-playground") renderPlaygroundRequestPreview();
  if (view === "call-logs") refreshCallLogs().catch(() => renderCallLogs());
  if (view === "error-codes") refreshErrorCodes().catch(() => renderErrorCodes());
  if (view === "webhooks") refreshWebhooks().catch(() => renderWebhooks());
  if (view === "slo-panel" && state.isLoggedIn) refreshSloPanel().catch(() => renderSloPanel());
  if (view === "track-review" && state.isLoggedIn) refreshTrackReview().catch(() => {});
  if (view === "evaluation-center" && state.isLoggedIn) refreshEvaluationCenter().catch(() => {});
  if (view === "release-center" && state.isLoggedIn) refreshReleaseCenter().catch(() => {});
  if (view === "audit-compliance" && state.isLoggedIn) refreshAuditCompliance().catch(() => {});
  if (view === "admin-data" && state.isLoggedIn) refreshAdminData().catch(() => renderBackupSnapshots({ snapshots: [] }));
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
      const payload = JSON.parse(event.data);
      state.latestPayloads[name] = payload;
      if (name === "job") {
        renderJobSummary(payload);
        renderJobVisuals(payload);
        renderPayload("job", outputSelector, payload);
        if (state.view === "video-results" && state.analysisResultsTab === "video") {
          refreshVideoResults().catch(() => {});
        }
      } else if (name === "stream") {
        renderLiveStreamResults(payload);
        if (state.view === "video-results" && state.analysisResultsTab === "stream") {
          refreshStreamResults().catch(() => {});
        }
        renderPayload("streams", outputSelector, payload);
      } else {
        renderDataViewer(outputSelector, payload, name);
      }
    } catch {
      const payload = { transport: "text", message: event.data };
      state.latestPayloads[name] = payload;
      renderDataViewer(outputSelector, payload, name);
    }
  });
  socket.addEventListener("close", () => {
    qs(statusSelector).textContent = "实时通道已断开";
  });
  socket.addEventListener("error", () => {
    qs(statusSelector).textContent = "实时通道连接失败";
  });
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
        msg = parsed.error?.message || parsed.detail || parsed.message || msg;
      } catch {}
      setStatus(msg, true);
    }
  };
}

async function api(path, options = {}) {
  const raw = await apiRaw(path, options);
  if (!raw.ok) throw new Error(JSON.stringify(raw.payload));
  return raw.data;
}

async function apiRaw(path, options = {}) {
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
  return {
    ok: response.ok,
    status_code: response.status,
    status_text: response.statusText,
    payload,
    data: payload.data || payload,
    request_id: payload.request_id || payload.data?.request_id || payload.detail?.request_id || null,
    error_code: payload.detail?.code || payload.error?.code || payload.code || null,
  };
}

async function textApi(path) {
  const response = await fetch(path, { headers: headers() });
  const text = await response.text();
  if (!response.ok) throw new Error(text || response.statusText);
  return text;
}

function metricValue(metrics, name) {
  const found = metrics.find((item) => item.name === name && Object.keys(item.labels).length === 0);
  return found ? Number(found.value) : 0;
}

function metricRows(metrics, name) {
  return metrics.filter((item) => item.name === name);
}

function metricSum(metrics, name) {
  return metricRows(metrics, name).reduce((total, item) => total + Number(item.value || 0), 0);
}

function metricMax(metrics, name) {
  const values = metricRows(metrics, name).map((item) => Number(item.value || 0));
  return values.length ? Math.max(...values) : 0;
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
