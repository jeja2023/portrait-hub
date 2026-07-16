// Shared formatting and structured-data rendering helpers.
// Loaded before app.js; state is resolved when a helper is invoked.
function qs(selector) {
  return document.querySelector(selector);
}

function qsa(selector) {
  return Array.from(document.querySelectorAll(selector));
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

function setStatus(message, isError = false) {
  const strip = qs("#status-strip");
  strip.textContent = message;
  strip.classList.toggle("error", isError);
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isScalar(value) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function formatDateTime(val) {
  let date;
  if (typeof val === "number") {
    date = new Date(val < 10000000000 ? val * 1000 : val);
  } else if (typeof val === "string") {
    date = new Date(val);
  } else {
    return String(val);
  }
  if (isNaN(date.getTime())) return String(val);
  
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return `${y}年${m}月${d}日 ${hh}时${mm}分${ss}秒`;
}

function compactValue(value, key = "") {
  if (typeof value === "string" && value.startsWith("data:image/")) return "[image]";
  if (value === null || value === undefined) return "--";
  if (typeof value === "boolean") return value ? "是" : "否";
  
  const isTime = (typeof key === "string" && (
    key.toLowerCase().endsWith("_at") || 
    key.toLowerCase().includes("time") || 
    key.toLowerCase().includes("date") || 
    key.toLowerCase() === "since" || 
    key.toLowerCase().endsWith("_since")
  ))
  || (typeof value === "number" && value > 1000000000 && value < 3000000000)
  || (typeof value === "string" && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/.test(value));
  
  if (isTime) {
    try {
      return formatDateTime(value);
    } catch (e) {
      // 忽略格式化失败，继续使用兜底展示。
    }
  }
  
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
  if (typeof value === "string") return localizeValue(value) || "--";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (isPlainObject(value)) return `${Object.keys(value).length} 个字段`;
  return String(value);
}

const fieldLabels = {
  aliases: "模型别名映射",
  appearance: "衣着外观",
  async_mode: "异步模式",
  backend: "后端服务",
  body: "人体",
  candidate_count: "候选数",
  confidence: "置信度",
  config: "配置",
  config_loaded: "配置已加载",
  count: "数量",
  data: "数据",
  detail: "详情",
  display_name: "显示名称",
  dry_run: "仅预演（模拟运行）",
  error_rate: "错误率",
  face: "人脸",
  face_count: "人脸数",
  frames: "帧列表",
  gait: "步态",
  gpu_free_gb: "GPU 空闲显存",
  include_embeddings: "返回向量",
  include_vectors: "返回向量",
  inference_p95_seconds: "P95 推理耗时",
  iou: "交并比（IoU）",
  job_id: "任务 ID",
  loaded: "已加载",
  loaded_models: "已加载模型",
  max_detections: "最大目标数",
  message: "消息",
  metadata: "元数据",
  method: "请求方法",
  modality: "特征模态",
  modalities: "模态列表",
  model_id: "模型 ID",
  name: "名称",
  pagination: "分页信息",
  path: "请求路径",
  people: "人员列表",
  person_count: "人员数",
  person_id: "人员 ID",
  progress: "进度",
  records: "记录",
  request_id: "请求 ID",
  results: "结果列表",
  score: "分数",
  settings: "设置",
  similarity: "相似度",
  status: "状态",
  stream_id: "视频流 ID",
  stream_url: "流地址",
  tenant_id: "租户 ID",
  threshold: "比对阈值",
  threshold_profile: "阈值方案",
  thresholds: "各模态阈值",
  top_k: "前 K 个候选",
  total: "总数",
  track_count: "轨迹数",
  transport: "传输方式",
  updated_since: "更新时间起点",
  version: "版本",
  visuals: "可视化结果",
  storage: "数据库存储",
  vector_store: "特征向量库",
  object_storage: "对象存储服务",
  task_queue: "异步任务队列",
  stream_worker: "视频流分析进程",
  security: "安全鉴权配置",
  rbac_enabled: "角色控制 (RBAC)",
  auth_required: "强制鉴权",
  tenant_header_required: "强制要求租户头",
  trusted_hosts: "可信任主机列表",
  require_encryption: "传输加密",
  configured_backends: "已配置后台服务",
  app_version: "系统应用版本",
  metrics: "系统指标监控",
  error_count: "推理请求错误数",
  requests_total: "推理总请求量",
  errors_total: "推理总错误数",
  p95_seconds: "P95 响应耗时（秒）",
  timing: "耗时分析",
  total_seconds: "总耗时（秒）",
  image_count: "图片数",
  frame_count: "视频帧数",
  result_count: "检测结果数",
  passed: "比对是否通过",
  risk: "安全与质量风险",
  adjusted_threshold: "自适应调整阈值",
  quality_adjusted_similarity: "质量自适应相似度",
  people_limit: "人员导出上限",
  jobs_limit: "任务导出上限",
  streams_limit: "视频流导出上限",
  next_cursor: "下一页分页游标",
  reindex_modality: "索引重建特征模态",
  updated_at: "最后更新时间",
  created_at: "任务创建时间",
  finished_at: "任务结束时间",
  sample_interval_seconds: "视频采样间隔（秒）",
  batch_size: "每批推理帧数",
  device: "运行设备 (GPU/CPU)",
  running: "运行状态",
  error: "错误信息",
  feature_count: "已注册特征数",
  quality_score: "特征质量分",
  quality: "特征质量",
  combined_quality_score: "多模态组合质量分",
  features: "特征数据列表",
  job: "后台任务状态",
  result: "任务提取结果",
  stream: "注册视频流详情",
  person: "人员注册详情",
  gpu_worker_requests_total: "总推理请求计数",
  gpu_worker_predict_errors_total: "通用预测错误计数",
  gpu_worker_persons_errors_total: "人体解析错误计数",
  gpu_worker_embeddings_errors_total: "特征向量错误计数",
  gpu_worker_tracks_errors_total: "轨迹跟踪错误计数",
  gpu_worker_vision_errors_total: "图像解析错误计数",
  gpu_worker_gpu_memory_free_bytes: "显卡空闲显存 (字节)",
  gpu_worker_inference_seconds: "显卡推理延迟 (秒)",
  gpu_worker_requests_active: "当前活跃推理量",
  comparison: "比对结果明细",
  reason: "未通过原因/异常",
  checks: "告警健康指标检查",
  config: "本地评估配置",
  maxErrorRate: "容许最大错误率",
  maxP95Latency: "容许最大P95延迟 (秒)",
  minFreeGpuMemoryGb: "容许最小空闲显存 (GB)",
  current: "当前监测值",
  limit: "监控阈值线",
  ok: "指标状态是否正常",
  unit: "指标计量单位",
  scale: "单位换算倍率",
  totals: "总计数据汇总",
  configured: "配置状态",
  driver: "驱动类型",
  host: "主机地址",
  port: "端口号",
  database: "数据库名称",
  user: "用户名",
  url: "流地址",
  jobs: "任务统计",
  streams: "视频流解析统计",
  active_workers: "活跃工作进程",
  max_workers: "最大工作进程数",
  queue_length: "队列排队长度",
  redis_url: "Redis 连接地址",
  postgres_url: "PostgreSQL 连接地址",
  sqlite_path: "SQLite 文件路径",
  index: "索引类型",
  production_ready: "生产环境就绪",
  note: "备注",
  notes: "备注",
  storage_dir_configured: "存储目录已配置",
  queued_messages: "队列排队消息数",
  active_sessions: "活动会话数",
  sessions: "会话列表",
  daemon_entrypoint: "守护进程入口",
  configured: "已配置",
  driver_available: "驱动是否可用",
  bucket_configured: "存储桶已配置",
  endpoint_configured: "服务终结点已配置",
  applications: "接入应用",
  app_id: "应用 ID",
  application_id: "接入应用 ID",
  owner: "负责人",
  scopes: "权限范围",
  jwt_issuer: "JWT 签发方",
  jwt_audience: "JWT 接收方",
  last_called_at: "最近调用时间",
  last_error_at: "最近错误时间",
  call_count: "调用次数",
  error_count: "错误次数",
  error_rate: "错误率",
  api_key_preview: "接口密钥预览",
  endpoint: "接口",
  webhooks: "事件回调端点",
  webhook_id: "事件回调 ID",
  sample_delivery: "样例投递",
  delivery_status: "投递状态",
  signing_secret_preview: "签名密钥预览",
  one_time_secret: "一次性密钥",
  retry_limit: "重试次数",
  timeout_seconds: "超时秒数",
  openapi_url: "开放接口定义地址",
  docs_url: "Swagger 文档地址",
  redoc_url: "ReDoc 文档地址",
  core_paths: "核心接口路径",
  path_count: "路径数量",
  schema: "接口契约",
  http_status: "HTTP 状态码",
  latency_ms: "耗时（毫秒）",
  success_rate: "成功率",
  p99_seconds: "P99 响应耗时（秒）",
  error_budget: "错误预算",
  worker: "GPU worker",
  workers: "工作进程",
  modalities: "模态明细",
  final_score: "融合分数",
  raw_score: "原始融合分数",
  decision: "融合决策",
  consistency: "模态一致性",
  used: "参与融合",
  weight: "融合权重",
  alias: "模型别名",
  target: "目标模型",
  previous_target: "上一版本模型",
  expected_current_target: "期望当前目标",
  traffic_key: "流量 key",
  action: "操作",
  datasets: "评估数据集",
  capabilities: "能力矩阵",
  production: "生产状态",
  audit: "审计",
  audit_hash: "审计哈希",
  audit_prev_hash: "上一条审计哈希",
  region_configured: "区域已配置",
};

const valueLabels = {
  active: "运行中",
  appearance: "衣着外观",
  backup: "系统备份",
  body: "人体",
  collect_more_samples: "继续收集样本",
  cleanup: "数据清理",
  completed: "已完成",
  detect: "通用检测",
  embeddings: "提取特征向量",
  error: "发生异常",
  face: "人脸",
  failed: "失败已终止",
  false: "否",
  fusion: "多模态融合",
  gait: "步态序列",
  hold_threshold: "保持阈值",
  inactive: "未启用",
  loaded: "模型已加载",
  no: "否",
  normal: "标准模式",
  ok: "指标正常",
  pending: "排队中",
  persons: "人体解析",
  pose: "姿态解析",
  ready: "就绪",
  running: "分析中",
  raise_threshold: "提高阈值",
  review_quality_gate: "复核质量门禁",
  success: "成功",
  text: "文本",
  tracks: "轨迹提取",
  true: "是",
  unloaded: "模型未加载",
  disabled: "已禁用",
  dry_run: "预演投递",
  strict: "严格模式",
  loose: "宽松模式",
  preview: "预览",
  switch: "切换",
  weighted: "灰度",
  rollback: "回滚",
  candidate: "候选版本",
  fallback: "兜底能力",
  placeholder: "占位能力",
  production: "生产能力",
  yes: "是",
  local: "本地服务",
  json: "JSON持久化",
  sqlite: "SQLite数据库",
  postgresql: "Postgres数据库",
  memory: "运行内存",
  none: "无",
  null: "无",
  local_numpy: "本地 NumPy 矩阵",
  numpy_matrix_topk: "NumPy 矩阵 Top-K",
  local_file: "本地文件系统",
  local_background: "本地后台线程",
  daemon_capable_session_controller: "守护进程会话控制器",
  json_file: "JSON 文件持久化",
  pgvector: "Postgres 向量索引",
  qdrant: "Qdrant 向量数据库",
  external_queue: "外部消息队列",
  "use portrait_vector_backend=pgvector or qdrant for production galleries.": "生产环境推荐使用 pgvector 或 qdrant 向量后端。",
  "run the daemon entrypoint as a separate process for production stream pulling.": "生产环境请将守护进程作为独立进程运行以拉取视频流。",
};

function localizeValue(value) {
  const text = String(value ?? "");
  const key = text.toLowerCase();
  return Object.prototype.hasOwnProperty.call(valueLabels, key) ? valueLabels[key] : text;
}

function titleFromKey(key) {
  const normalized = String(key || "");
  if (!normalized) return "数据";
  const labelKey = normalized.toLowerCase();
  if (Object.prototype.hasOwnProperty.call(fieldLabels, labelKey)) return fieldLabels[labelKey];
  return normalized
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function describePayload(payload) {
  if (Array.isArray(payload)) return `${payload.length} 条记录`;
  if (isPlainObject(payload)) return `${Object.keys(payload).length} 个顶层字段`;
  if (payload === null || payload === undefined) return "暂无数据";
  return "接口响应";
}

function payloadLabel(name) {
  const labels = {
    dashboard: "平台状态",
    vision: "图片解析响应",
    compare: "人像比对响应",
    gallery: "人员库响应",
    "gallery-rebuild": "特征重建响应",
    "image-results": "图片解析结果",
    "video-results": "视频解析结果",
    "stream-results": "视频流解析结果",
    enroll: "人员注册响应",
    search: "以图搜人响应",
    jobs: "视频任务响应",
    streams: "视频流解析响应",
    models: "模型管理响应",
    admin: "治理配置响应",
    "admin-threshold": "比对阈值响应",
    "admin-data": "数据保留与备份响应",
    alerts: "告警评估响应",
  "access-credentials": "应用凭证响应",
  "sdk-examples": "开发工具包示例响应",
  "openapi-docs": "开放接口定义响应",
  "api-playground": "接口调试台 响应",
  "call-logs": "调用日志响应",
  webhooks: "事件回调响应",
  "slo-panel": "服务等级目标面板响应",
  "multimodal-compare": "融合比对响应",
  "track-review": "轨迹审阅响应",
  "evaluation-center": "评估中心响应",
  "release-center": "模型发布响应",
  "audit-compliance": "合规审计响应",
  };
  return labels[name] || "接口响应";
}

function collectInsights(payload) {
  const root = payloadData(payload);
  const source = isPlainObject(root) ? root : isPlainObject(payload) ? payload : {};
  const preferred = [
    "status",
    "tenant_id",
    "count",
    "total",
    "candidate_count",
    "person_count",
    "face_count",
    "track_count",
    "job_id",
    "stream_id",
    "model_id",
    "loaded",
    "config_loaded",
    "request_id",
  ];
  const items = [];
  preferred.forEach((key) => {
    if (source[key] !== undefined && isScalar(source[key])) {
      items.push({ label: titleFromKey(key), value: compactValue(source[key], key) });
    }
  });
  Object.entries(source).forEach(([key, value]) => {
    if (items.length >= 6) return;
    if (preferred.includes(key) || !isScalar(value)) return;
    items.push({ label: titleFromKey(key), value: compactValue(value, key) });
  });
  if (!items.length && Array.isArray(root)) {
    items.push({ label: "记录数", value: root.length });
  }
  return items.slice(0, 6);
}

function objectPreview(value, key = "") {
  if (isScalar(value)) return escapeHtml(compactValue(value, key));
  return escapeHtml(compactValue(value, key));
}

function tableFromObjects(items) {
  const rows = items.filter(isPlainObject).slice(0, 12);
  if (!rows.length) return "";
  const keys = Array.from(rows.reduce((set, row) => {
    Object.keys(row).forEach((key) => {
      if (set.size < 6) set.add(key);
    });
    return set;
  }, new Set()));
  if (!keys.length) return "";
  return `
    <div class="data-table-wrap">
      <table class="data-table">
        <thead><tr>${keys.map((key) => `<th>${escapeHtml(titleFromKey(key))}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows.map((row) => `<tr>${keys.map((key) => `<td>${objectPreview(row[key], key)}</td>`).join("")}</tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}

function kvGridMarkup(value) {
  if (!isPlainObject(value)) return "";
  const entries = Object.entries(value).filter(([, item]) => isScalar(item)).slice(0, 18);
  if (!entries.length) return "";
  return `
    <div class="data-kv-grid">
      ${entries.map(([key, item]) => `
        <div class="data-kv-item">
          <span>${escapeHtml(titleFromKey(key))}</span>
          <strong title="${escapeHtml(compactValue(item, key))}">${escapeHtml(compactValue(item, key))}</strong>
        </div>`).join("")}
    </div>`;
}

function listMarkup(items) {
  const values = items.slice(0, 12);
  if (!values.length) return "";
  return `<ul class="data-list">${values.map((item) => `<li>${objectPreview(item)}</li>`).join("")}</ul>`;
}

function dataSectionMarkup(key, value, index) {
  const label = titleFromKey(key);
  const summary = compactValue(value, key);
  let body = "";
  if (Array.isArray(value)) {
    body = tableFromObjects(value) || listMarkup(value) || `<div class="data-empty">暂无记录</div>`;
  } else if (isPlainObject(value)) {
    body = kvGridMarkup(value);
    const nested = Object.entries(value)
      .filter(([, item]) => Array.isArray(item) || isPlainObject(item))
      .slice(0, 4)
      .map(([nestedKey, nestedValue], nestedIndex) => dataSectionMarkup(nestedKey, nestedValue, nestedIndex + 10))
      .join("");
    body = [body, nested].filter(Boolean).join("");
  } else {
    body = `<div class="data-list"><li>${objectPreview(value, key)}</li></div>`;
  }
  return `
    <details class="data-section" ${index < 2 ? "open" : ""}>
      <summary><strong>${escapeHtml(label)}</strong><span>${escapeHtml(summary)}</span></summary>
      <div class="data-section-body">${body || `<div class="data-empty">暂无可展示字段</div>`}</div>
    </details>`;
}

function renderDataViewer(selector, payload, name = "") {
  const node = qs(selector);
  if (!node) return;
  const raw = JSON.stringify(payload || {}, null, 2);
  const root = payloadData(payload);
  const sections = isPlainObject(root)
    ? Object.entries(root).filter(([, value]) => Array.isArray(value) || isPlainObject(value)).slice(0, 8)
    : Array.isArray(root)
      ? [["records", root]]
      : [];
  const directFields = isPlainObject(root) ? kvGridMarkup(root) : "";
  const insights = collectInsights(payload);
  node.innerHTML = `
    <div class="data-viewer-head">
      <div class="data-viewer-title">
        <strong>${escapeHtml(payloadLabel(name))}</strong>
        <span>${escapeHtml(describePayload(root))}</span>
      </div>
      <button type="button" class="small" data-copy-json="${escapeHtml(name || selector)}">复制数据</button>
    </div>
    ${insights.length ? `<div class="data-insight-grid">${insights.map((item) => `
      <div class="data-insight">
        <span>${escapeHtml(item.label)}</span>
        <strong title="${escapeHtml(item.value)}">${escapeHtml(item.value)}</strong>
      </div>`).join("")}</div>` : ""}
    ${directFields || (!sections.length ? `<div class="data-empty">暂无结构化数据</div>` : "")}
    ${sections.length ? `<div class="data-section-list">${sections.map(([key, value], index) => dataSectionMarkup(key, value, index)).join("")}</div>` : ""}
    <details class="raw-json">
      <summary>查看完整数据（JSON）</summary>
      <pre class="json-raw">${escapeHtml(raw)}</pre>
    </details>`;
  const copyButton = node.querySelector("[data-copy-json]");
  if (copyButton) copyButton.addEventListener("click", () => copyText(raw, "数据已复制"));
}

function renderJson(selector, payload) {
  const node = qs(selector);
  if (!node) return;
  if (node.classList.contains("data-viewer")) {
    renderDataViewer(selector, payload);
    return;
  }
  node.textContent = JSON.stringify(payload || {}, null, 2);
}


function isImageData(value) {
  return typeof value === "string" && value.startsWith("data:image/");
}

function sanitizeVideoPayload(value) {
  if (isImageData(value)) return "[image]";
  if (Array.isArray(value)) return value.map(sanitizeVideoPayload);
  if (!value || typeof value !== "object") return value;
  const output = {};
  Object.entries(value).forEach(([key, item]) => {
    output[key] = sanitizeVideoPayload(item);
  });
  return output;
}

function renderPayload(name, selector, payload) {
  state.latestPayloads[name] = payload;
  const renderedPayload = ["jobs", "job", "video-results", "image-results", "stream-results", "track-review"].includes(name) ? sanitizeVideoPayload(payload) : payload;
  const node = qs(selector);
  if (node && node.classList.contains("data-viewer")) {
    renderDataViewer(selector, renderedPayload, name);
    return;
  }
  renderJson(selector, renderedPayload);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function formatByteSize(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "--";
  if (Math.abs(bytes) < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let size = bytes;
  let unit = "B";
  for (const item of units) {
    size /= 1024;
    unit = item;
    if (Math.abs(size) < 1024) break;
  }
  const digits = Math.abs(size) >= 100 ? 0 : Math.abs(size) >= 10 ? 1 : 2;
  return `${formatNumber(size, digits)} ${unit}`;
}
function fitVisualSize(width, height, maxWidth, maxHeight, allowUpscale = false) {
  const sourceWidth = Math.max(1, Number(width) || 1);
  const sourceHeight = Math.max(1, Number(height) || 1);
  const limitWidth = Math.max(1, Number(maxWidth) || sourceWidth);
  const limitHeight = Math.max(1, Number(maxHeight) || sourceHeight);
  const scale = Math.min(limitWidth / sourceWidth, limitHeight / sourceHeight);
  const boundedScale = allowUpscale ? scale : Math.min(scale, 1);
  return {
    width: Math.max(1, Math.round(sourceWidth * boundedScale)),
    height: Math.max(1, Math.round(sourceHeight * boundedScale)),
  };
}
