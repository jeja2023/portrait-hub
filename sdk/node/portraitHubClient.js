const fs = require("fs");

class PortraitHubHTTPError extends Error {
  constructor(status, payload, headers) {
    super(`PortraitHub 请求失败 with HTTP ${status}`);
    this.name = "PortraitHubHTTPError";
    this.status = status;
    this.payload = payload;
    this.headers = headers;
  }
}

class PortraitHubClient {
  constructor({ baseUrl, apiToken = null, tenantId = null, authScheme = "bearer" }) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiToken = apiToken;
    this.tenantId = tenantId;
    this.authScheme = this.normalizeAuthScheme(authScheme);
  }

  normalizeAuthScheme(value) {
    const normalized = String(value).trim().toLowerCase().replace(/-/g, "_");
    if (!["bearer", "api_key"].includes(normalized)) {
      throw new Error("authScheme 必须是 'bearer' 或 'api_key'");
    }
    return normalized;
  }

  headers(extra = {}) {
    const headers = { ...extra };
    if (this.tenantId) headers["X-Tenant-ID"] = this.tenantId;
    if (this.apiToken) {
      if (this.authScheme === "api_key") headers["X-API-Key"] = this.apiToken;
      else headers.Authorization = `Bearer ${this.apiToken}`;
    }
    return headers;
  }

  pathSegment(value) {
    return encodeURIComponent(String(value));
  }

  pathWithQuery(path, params = {}) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
      if (value === null || value === undefined) continue;
      query.append(key, typeof value === "boolean" ? String(value).toLowerCase() : String(value));
    }
    const suffix = query.toString();
    return suffix ? `${path}?${suffix}` : path;
  }

  async json(method, path, body = null) {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: this.headers(body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    return this.parseResponse(response);
  }

  async multipart(path, fields, files) {
    const form = new FormData();
    for (const [key, value] of Object.entries(fields || {})) {
      if (value === null || value === undefined) continue;
      form.append(key, String(value));
    }
    for (const [key, filePath] of files || []) {
      const blob = new Blob([fs.readFileSync(filePath)]);
      form.append(key, blob, filePath.split(/[\\/]/).pop());
    }
    const response = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: this.headers(),
      body: form,
    });
    return this.parseResponse(response);
  }

  async parseResponse(response) {
    const payload = await this.decodeBody(response);
    if (!response.ok) {
      throw new PortraitHubHTTPError(response.status, payload, response.headers);
    }
    if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
      throw new PortraitHubHTTPError(502, payload, response.headers);
    }
    return payload;
  }

  async decodeBody(response) {
    const contentType = response.headers.get("content-type") || "";
    const text = await response.text();
    if (!text) return {};
    if (!contentType.includes("application/json")) return text;
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }

  health() {
    return this.json("GET", "/health");
  }

  adminStatus() {
    return this.json("GET", "/v1/admin/status");
  }

  thresholds() {
    return this.json("GET", "/v1/thresholds");
  }

  updateThresholds(profile, thresholds) {
    return this.json("PUT", `/v1/thresholds/${this.pathSegment(profile)}`, thresholds);
  }

  comparePersons(imageA, imageB, thresholdProfile = "normal") {
    return this.multipart(
      "/v1/compare/persons",
      { threshold_profile: thresholdProfile },
      [["image_a", imageA], ["image_b", imageB]],
    );
  }

  enroll(personId, images, modality = "body") {
    return this.multipart(
      "/v1/gallery/enroll",
      { person_id: personId, modality },
      images.map((path) => ["files", path]),
    );
  }

  search(image, modality = "body", topK = 5, thresholdProfile = "normal") {
    return this.multipart(
      "/v1/gallery/search",
      { modality, top_k: topK, threshold_profile: thresholdProfile },
      [["file", image]],
    );
  }

  searchBatch(images, { modality = "body", topK = 5, thresholdProfile = "normal", asyncMode = false } = {}) {
    return this.multipart(
      "/v1/gallery/search/batch",
      { modality, top_k: topK, threshold_profile: thresholdProfile, async_mode: asyncMode },
      images.map((path) => ["files", path]),
    );
  }

  compareBatch(
    imageA,
    imageB,
    { modality = "body", thresholdProfile = "normal", includeVectors = false, asyncMode = false } = {},
  ) {
    return this.multipart(
      "/v1/compare/batch",
      {
        modality,
        threshold_profile: thresholdProfile,
        include_vectors: includeVectors,
        async_mode: asyncMode,
      },
      [...imageA.map((path) => ["image_a", path]), ...imageB.map((path) => ["image_b", path])],
    );
  }

  reindexGallery({ modality = null, modelId = null, dryRun = false } = {}) {
    return this.json("POST", this.pathWithQuery("/v1/gallery/reindex", { modality, model_id: modelId, dry_run: dryRun }));
  }

  createVideoJob(video, { frameInterval = null, maxFrames = null } = {}) {
    return this.multipart(
      "/v1/jobs/video",
      { frame_interval: frameInterval, max_frames: maxFrames },
      [["file", video]],
    );
  }

  getJob(jobId) {
    return this.json("GET", `/v1/jobs/${this.pathSegment(jobId)}`);
  }

  jobResult(jobId) {
    return this.json("GET", `/v1/jobs/${this.pathSegment(jobId)}/result`);
  }

  cancelJob(jobId) {
    return this.json("POST", `/v1/jobs/${this.pathSegment(jobId)}/cancel`);
  }

  createStream(streamUrl, { name = null, settings = {}, metadata = {} } = {}) {
    return this.json("POST", "/v1/streams", { stream_url: streamUrl, name, settings, metadata });
  }

  listStreams({ limit = null, offset = null, cursor = null } = {}) {
    return this.json("GET", this.pathWithQuery("/v1/streams", { limit, offset, cursor }));
  }

  getStream(streamId) {
    return this.json("GET", `/v1/streams/${this.pathSegment(streamId)}`);
  }

  startStream(streamId) {
    return this.json("POST", `/v1/streams/${this.pathSegment(streamId)}/start`);
  }

  stopStream(streamId) {
    return this.json("POST", `/v1/streams/${this.pathSegment(streamId)}/stop`);
  }

  streamStatus(streamId) {
    return this.json("GET", `/v1/streams/${this.pathSegment(streamId)}/status`);
  }

  streamEvents(streamId, { limit = null, offset = null, cursor = null } = {}) {
    return this.json("GET", this.pathWithQuery(`/v1/streams/${this.pathSegment(streamId)}/events`, { limit, offset, cursor }));
  }

  models() {
    return this.json("GET", "/v1/models");
  }

  getModel(modelId) {
    return this.json("GET", `/v1/models/${this.pathSegment(modelId)}`);
  }

  loadModel(modelId) {
    return this.json("POST", `/v1/models/${this.pathSegment(modelId)}/load`);
  }

  unloadModel(modelId) {
    return this.json("POST", `/v1/models/${this.pathSegment(modelId)}/unload`);
  }
}

module.exports = { PortraitHubClient, PortraitHubHTTPError };
