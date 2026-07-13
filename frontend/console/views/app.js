const consoleConfig = window.PortraitConsoleConfig || {};

const state = {
  tenantId: localStorage.getItem("portraitHubTenant") || "default",
  apiKey: localStorage.getItem("portraitHubApiKey") || "",
  bearer: localStorage.getItem("portraitHubBearer") || "",
  view: localStorage.getItem("portraitHubView") || "overview",
  analysisResultsTab: localStorage.getItem("portraitHubAnalysisResultsTab") || "image",
  isLoggedIn: localStorage.getItem("portraitHubLoggedIn") === "true",
  accessApplications: loadAccessApplications(),
  accessLastSecret: null,
  webhooks: loadWebhooks(),
  webhookLastSecret: null,
  openApiCache: null,
  dashboard: {},
  galleryExport: {},
  latestPayloads: {},
  callLogs: [],
  errorCodes: null,
  analysisResults: {
    image: [],
    video: null,
    stream: null,
  },
  alertConfig: loadAlertConfig(),
  sockets: {},
  visionPreviews: [],
  visionPreviewSignature: "",
  visionResultVisuals: [],
  visionLightboxIndex: null,
  comparePreviews: { A: [], B: [] },
};

const endpointMap = consoleConfig.endpointMap || {};

function defaultAlertConfig() {
  return {
    maxErrorRate: Number(consoleConfig.alertDefaults?.maxErrorRate ?? 0.05),
    maxP95Latency: Number(consoleConfig.alertDefaults?.maxP95Latency ?? 1.5),
    minFreeGpuMemoryGb: Number(consoleConfig.alertDefaults?.minFreeGpuMemoryGb ?? 1),
  };
}

function loadAlertConfig() {
  const defaults = defaultAlertConfig();
  try {
    const payload = JSON.parse(localStorage.getItem("portraitHubAlertConfig") || "{}");
    return {
      maxErrorRate: Number(payload.maxErrorRate ?? defaults.maxErrorRate),
      maxP95Latency: Number(payload.maxP95Latency ?? defaults.maxP95Latency),
      minFreeGpuMemoryGb: Number(payload.minFreeGpuMemoryGb ?? defaults.minFreeGpuMemoryGb),
    };
  } catch {
    return defaults;
  }
}

function defaultAccessApplications() {
  return [
    {
      id: "default-client",
      name: "默认接入应用",
      owner: "platform",
      status: "active",
      scopes: ["infer", "compare", "gallery:read", "gallery:write"],
      jwt_issuer: "",
      jwt_audience: "",
      created_at: Date.now(),
      last_called_at: null,
      error_rate: 0,
    },
  ];
}

function loadAccessApplications() {
  try {
    const payload = JSON.parse(localStorage.getItem("portraitHubAccessApplications") || "[]");
    return Array.isArray(payload) && payload.length ? payload : defaultAccessApplications();
  } catch {
    return defaultAccessApplications();
  }
}

function saveAccessApplications() {
  localStorage.setItem("portraitHubAccessApplications", JSON.stringify(state.accessApplications));
}

function defaultWebhooks() {
  return [
    {
      id: "default-webhook",
      name: "默认事件回调",
      application_id: "default-client",
      url: "",
      status: "disabled",
      events: ["job.completed", "stream.event", "gallery.enrolled"],
      retry_limit: 3,
      timeout_seconds: 5,
      created_at: Date.now(),
      last_delivery_at: null,
      failure_count: 0,
      signing_secret_preview: null,
    },
  ];
}

function loadWebhooks() {
  try {
    const payload = JSON.parse(localStorage.getItem("portraitHubWebhooks") || "[]");
    return Array.isArray(payload) && payload.length ? payload : defaultWebhooks();
  } catch {
    return defaultWebhooks();
  }
}

function saveWebhooks() {
  localStorage.setItem("portraitHubWebhooks", JSON.stringify(state.webhooks));
}
const template = `
  <!-- 登录页面 -->
  <div id="login-view" class="login-container hidden">
    <div class="login-card">
      <div class="login-brand">
        <h1><span class="brand-logo brand-logo--large">影</span>影鉴</h1>
        <p>面向业务项目的离线人像解析与比对控制台</p>
      </div>
      <form id="login-form">
        <div class="field">
          <label>租户标识 <input id="tenant-input" autocomplete="off" value="default" /></label>
        </div>
        <div class="field">
          <label>接口密钥 <input id="api-key-input" type="password" autocomplete="off" /></label>
        </div>
        <div class="field">
          <label>JWT 令牌 <input id="bearer-input" type="password" autocomplete="off" /></label>
        </div>
        <button type="submit" class="primary login-button">登录系统</button>
      </form>
    </div>
  </div>

  <!-- 主控制台布局 -->
  <div id="console-view" class="console-layout hidden">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <h2><span class="brand-logo">影</span>影鉴</h2>
        <p>业务控制台</p>
        <div class="brand-links">
          <a href="/docs" target="_blank" rel="noreferrer">接口文档</a>
          <span>·</span>
          <a href="/openapi.json" target="_blank" rel="noreferrer">接口定义</a>
        </div>
      </div>
      <nav class="sidebar-nav" aria-label="控制台视图">
        <button type="button" class="nav-item nav-item--solo" data-nav="overview">总览</button>
        <details class="nav-group" data-nav-group="analysis">
          <summary>解析处理</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="vision">图片解析</button>
            <button type="button" class="nav-item" data-nav="video">视频解析</button>
            <button type="button" class="nav-item" data-nav="streams">视频流解析</button>
            <button type="button" class="nav-item" data-nav="video-results">解析结果</button>
          </div>
        </details>
        <details class="nav-group" data-nav-group="retrieval">
          <summary>比对检索</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="compare">人像比对</button>
            <button type="button" class="nav-item" data-nav="gallery-search">以图搜人</button>
          </div>
        </details>
        <details class="nav-group" data-nav-group="gallery">
          <summary>人员库</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="gallery-enroll">人员注册</button>
            <button type="button" class="nav-item" data-nav="gallery-manage">人员管理</button>
          </div>
        </details>
        <details class="nav-group" data-nav-group="access">
          <summary>接入中心</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="access-credentials">应用凭证</button>
            <button type="button" class="nav-item" data-nav="sdk-examples">开发工具包示例</button>
            <button type="button" class="nav-item" data-nav="openapi-docs">开放接口定义</button>
            <button type="button" class="nav-item" data-nav="api-playground">接口调试台</button>
            <button type="button" class="nav-item" data-nav="call-logs">调用日志</button>
            <button type="button" class="nav-item" data-nav="error-codes">错误码</button>
            <button type="button" class="nav-item" data-nav="webhooks">事件回调</button>
            <button type="button" class="nav-item" data-nav="slo-panel">服务等级目标面板</button>
          </div>
        </details>
        <details class="nav-group" data-nav-group="multimodal">
          <summary>多模态分析</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="multimodal-compare">融合比对</button>
            <button type="button" class="nav-item" data-nav="track-review">轨迹审阅</button>
          </div>
        </details>
        <details class="nav-group" data-nav-group="evaluation">
          <summary>评估中心</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="evaluation-center">回归评估</button>
            <button type="button" class="nav-item" data-nav="release-center">模型发布</button>
          </div>
        </details>
        <details class="nav-group" data-nav-group="ops">
          <summary>运维治理</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="models">模型管理</button>
            <button type="button" class="nav-item" data-nav="admin-threshold">比对阈值</button>
            <button type="button" class="nav-item" data-nav="admin-data">数据保留与备份</button>
            <button type="button" class="nav-item" data-nav="audit-compliance">合规审计</button>
            <button type="button" class="nav-item" data-nav="alerts">告警评估</button>
          </div>
        </details>
      </nav>
      <div class="sidebar-footer">
        <div class="tenant-info">
          <span>当前租户</span>
          <strong id="current-tenant-display">default</strong>
        </div>
        <div id="status-strip" class="status-strip">就绪</div>
        <div class="sidebar-actions">
          <button type="button" id="refresh-button" class="small">刷新全部</button>
          <button type="button" id="logout-button" class="danger small">退出登录</button>
        </div>
      </div>
    </aside>
    <main class="console-main">
      <section class="workspace">
      <section class="view" data-view="overview">
        <div class="view-header">
          <div class="section-title">
            <h2>服务总览</h2>
            <p>查看当前健康状态、业务入口和可复制的集成方式。</p>
          </div>
          <button type="button" id="dashboard-refresh-button">刷新状态</button>
        </div>
        <div class="stats-row">
          <div class="metric"><span>推理请求</span><strong id="metric-requests">0</strong></div>
          <div class="metric"><span>错误率</span><strong id="metric-error-rate">0%</strong></div>
          <div class="metric"><span>P95 推理耗时</span><strong id="metric-p95">0s</strong></div>
          <div class="metric"><span>GPU 空闲显存</span><strong id="metric-gpu-free">--</strong></div>
          <button type="button" class="product-tile" data-nav-shortcut="vision"><strong>图片解析</strong><span>人脸、人体、姿态、衣着、步态、检测和 重识别向量。</span></button>
          <button type="button" class="product-tile" data-nav-shortcut="video"><strong>视频解析</strong><span>离线视频任务创建、状态跟踪和结果回收。</span></button>
          <button type="button" class="product-tile" data-nav-shortcut="streams"><strong>视频流解析</strong><span>RTSP/HTTP 注册、启动、事件查询和实时订阅。</span></button>
          <button type="button" class="product-tile" data-nav-shortcut="video-results"><strong>解析结果</strong><span>按图片、视频和视频流集中查看解析输出与关键快照。</span></button>
          <button type="button" class="product-tile" data-nav-shortcut="gallery-search"><strong>比对检索</strong><span>以图搜人、候选排序和检索结果查看。</span></button>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title">
              <h3>对外接入示例</h3>
              <p>示例会自动带上当前租户和令牌头。</p>
            </div>
            <pre id="integration-code" class="code-view"></pre>
          </div>
          <div class="card">
            <div class="section-title">
              <h3>平台状态</h3>
              <p>包含存储、向量库、对象存储、任务队列和流工作进程状态。</p>
            </div>
            <div id="overview-badges" class="badge-row"></div>
            <div id="dashboard-json" class="json-view data-viewer" role="region" aria-label="平台状态数据"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="vision">
        <div class="view-header">
          <div class="section-title">
            <h2>图片解析</h2>
            <p>上传图片或帧序列，直接调用人像解析与通用检测接口。</p>
          </div>
          <button type="button" id="vision-copy-button">复制调用示例</button>
        </div>
        <form id="vision-form" class="form-grid">
          <label>解析能力
            <select id="vision-mode-input" name="mode">
              <option value="persons">人体解析 /v1/infer/persons</option>
              <option value="faces">人脸解析 /v1/infer/faces</option>
              <option value="appearance">衣着外观 /v1/infer/appearance</option>
              <option value="pose">姿态解析 /v1/infer/pose</option>
              <option value="gait">步态序列 /v1/infer/gait</option>
              <option value="detect">YOLO 人体检测 /infer/persons</option>
              <option value="embeddings">ReID 向量 /infer/person-embeddings</option>
              <option value="tracks">图片序列轨迹 /infer/person-tracks</option>
            </select>
          </label>
          <label class="span-2">图片文件 <input id="vision-files-input" name="files" type="file" accept="image/*" multiple /></label>
          <label>置信度 <input id="vision-confidence-input" name="confidence" type="number" min="0" max="1" step="0.01" value="0.25" /></label>
          <label>交并比（IoU） <input id="vision-iou-input" name="iou" type="number" min="0" max="1" step="0.01" value="0.45" /></label>
          <label>最大目标数 <input id="vision-max-detections-input" name="max_detections" type="number" min="1" value="100" /></label>
          <label class="field-inline"><input id="vision-include-embeddings-input" name="include_embeddings" type="checkbox" /> 返回向量</label>
          <button type="submit" class="primary">开始解析</button>
        </form>
        <div class="split-grid">
          <div class="card">
            <div class="section-title">
              <h3>输入预览</h3>
              <p>只在浏览器本地预览，不会额外上传。</p>
            </div>
            <div id="vision-preview" class="preview-grid"></div>
          </div>
          <div class="result-panel">
            <div class="section-title">
              <h3>解析结果</h3>
              <p>关键计数会在上方汇总，完整响应保留为 JSON 数据。</p>
            </div>
            <div id="vision-summary" class="result-summary"></div>
            <div id="vision-visuals" class="result-visual-grid"></div>
            <div id="vision-json" class="json-view data-viewer" role="region" aria-label="解析结果数据"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="compare">
        <div class="view-header">
          <div class="section-title">
            <h2>人像比对</h2>
            <p>支持人脸、人体、步态、多模态融合和批量成对比对。</p>
          </div>
          <button type="button" id="compare-copy-button">复制调用示例</button>
        </div>
        <form id="compare-form" class="form-grid">
          <label>比对类型
            <select id="compare-mode-input" name="mode">
              <option value="persons">人体比对</option>
              <option value="faces">人脸比对</option>
              <option value="fusion">多模态融合</option>
              <option value="gait">步态序列比对</option>
              <option value="batch">批量成对比对</option>
            </select>
          </label>
          <label>阈值方案 <input id="compare-threshold-input" name="threshold_profile" value="normal" /></label>
          <label>批量模态
            <select id="compare-batch-modality-input" name="modality">
              <option value="body">人体</option>
              <option value="face">人脸</option>
              <option value="appearance">衣着外观</option>
            </select>
          </label>
          <label>融合模态 <input id="compare-modalities-input" name="modalities" value="face,body,appearance" /></label>
          <label class="span-2">图 A / 序列 A <input id="compare-a-input" name="image_a" type="file" accept="image/*" multiple /></label>
          <label class="span-2">图 B / 序列 B <input id="compare-b-input" name="image_b" type="file" accept="image/*" multiple /></label>
          <label class="field-inline"><input id="compare-include-vectors-input" name="include_vectors" type="checkbox" /> 返回向量</label>
          <label class="field-inline"><input id="compare-async-input" name="async_mode" type="checkbox" /> 批量异步</label>
          <button type="submit" class="primary">开始比对</button>
        </form>
        <div class="split-grid">
          <div class="card">
            <div class="section-title">
              <h3>输入预览</h3>
              <p>步态和批量模式会读取多张图作为序列或多组配对。</p>
            </div>
            <div id="compare-preview" class="compare-preview"></div>
          </div>
          <div class="result-panel">
            <div class="section-title">
              <h3>比对结果</h3>
              <p>相似度、阈值和通过状态会优先显示。</p>
            </div>
            <div id="compare-summary" class="result-summary"></div>
            <div id="compare-json" class="json-view data-viewer" role="region" aria-label="比对结果数据"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="gallery-enroll">
        <div class="view-header">
          <div class="section-title">
            <h2>人员注册</h2>
            <p>支持同一人员多图入库，自动跳过重复输入。</p>
          </div>
        </div>
        <form id="enroll-form" class="form-grid">
          <label>人员 ID <input id="enroll-person-id-input" name="person_id" placeholder="留空自动生成" /></label>
          <label>显示名称 <input id="enroll-display-name-input" name="display_name" placeholder="姓名或业务编号" /></label>
          <label>特征模态
            <select id="enroll-modality-input" name="modality">
              <option value="body">人体</option>
              <option value="face">人脸</option>
              <option value="appearance">衣着外观</option>
            </select>
          </label>
          <label class="span-2">注册图片 <input id="enroll-file-input" name="files" type="file" accept="image/*" multiple /></label>
          <label class="span-2">元数据（JSON） <textarea id="enroll-metadata-input" name="metadata" placeholder='{"source":"case-001"}'></textarea></label>
          <button type="submit" class="primary">注册入库</button>
        </form>
        <div class="result-panel">
          <div class="section-title">
            <h3>注册结果</h3>
            <p>入库人员、特征数量和质量信息会在此展示。</p>
          </div>
          <div id="enroll-summary" class="result-summary"></div>
          <div id="enroll-json" class="json-view data-viewer" role="region" aria-label="人员注册响应数据"></div>
        </div>
      </section>

      <section class="view" data-view="gallery-search">
        <div class="view-header">
          <div class="section-title">
            <h2>以图搜人</h2>
            <p>返回人员级候选、质量信息和排序风险。</p>
          </div>
          <button type="button" id="gallery-copy-button">复制检索示例</button>
        </div>
        <form id="search-form" class="form-grid">
          <label class="span-2">检索图片 <input id="search-file-input" name="file" type="file" accept="image/*" /></label>
          <label>特征模态
            <select id="search-modality-input" name="modality">
              <option value="body">人体</option>
              <option value="face">人脸</option>
              <option value="appearance">衣着外观</option>
            </select>
          </label>
          <label>前 K <input id="search-top-k-input" name="top_k" type="number" min="1" value="5" /></label>
          <label>阈值方案 <input id="search-threshold-input" name="threshold_profile" value="normal" /></label>
          <button type="submit" class="primary">图库检索</button>
        </form>
        <div class="result-panel">
          <div class="section-title">
            <h3>检索结果</h3>
            <p>候选人员按相似度排序，附带质量与风险提示。</p>
          </div>
          <div id="search-summary" class="result-summary"></div>
          <div id="search-json" class="json-view data-viewer" role="region" aria-label="以图搜人响应数据"></div>
        </div>
      </section>

      <section class="view" data-view="gallery-manage">
        <div class="view-header">
          <div class="section-title">
            <h2>人员管理</h2>
            <p>浏览人员列表、查改删人员记录，并按模态重建向量索引。</p>
          </div>
          <button type="button" id="gallery-refresh-button">刷新人员库</button>
        </div>
        <div class="split-grid">
          <div class="list-panel">
            <div class="section-title">
              <h3>人员列表</h3>
              <p>点击人员会把 ID 填入维护表单。</p>
            </div>
            <ul id="people-list" class="people-list"></ul>
          </div>
          <div class="list-panel">
            <div class="section-title">
              <h3>特征分布</h3>
              <p>按人员和特征质量绘制的轻量分布图。</p>
            </div>
            <div id="feature-scatter" class="scatter" aria-label="图库特征分布"></div>
          </div>
        </div>
        <div class="card" id="person-features-card">
          <div class="section-title">
            <h3>特征图片列表</h3>
            <p>展示所选人员提取出的所有特征图像及元数据。</p>
          </div>
          <div id="person-features-list" class="result-visual-grid">
            <div class="result-empty">请在人员列表中选择人员以查看特征图片</div>
          </div>
        </div>
        <div class="card">
          <div class="section-title">
            <h3>人员维护</h3>
            <p>查询、更新、删除人员记录，或按模态重建向量索引。</p>
          </div>
          <div class="form-grid">
            <label>人员 ID <input id="person-id-input" placeholder="人员 ID" /></label>
            <label>新显示名称 <input id="person-display-name-input" placeholder="可选" /></label>
            <label class="span-2">新元数据（JSON） <input id="person-metadata-input" placeholder='{"department":"A"}' /></label>
            <button type="button" id="person-get-button">查询人员</button>
            <button type="button" id="person-patch-button">更新人员</button>
            <button type="button" id="person-delete-button" class="danger">删除人员</button>
          </div>
          <div class="form-grid compact">
            <label>重建模态
              <select id="reindex-modality-input">
                <option value="">全部</option>
                <option value="body">人体</option>
                <option value="face">人脸</option>
                <option value="appearance">衣着外观</option>
              </select>
            </label>
            <label>模型 ID <input id="reindex-model-id-input" placeholder="可选" /></label>
            <label class="field-inline"><input id="reindex-dry-run-input" type="checkbox" /> 仅预演</label>
            <button type="button" id="gallery-reindex-button">重建索引</button>
          </div>
          <div id="gallery-summary" class="result-summary"></div>
          <div id="gallery-json" class="json-view data-viewer" role="region" aria-label="人员库响应数据"></div>
        </div>
      </section>

      <section class="view" data-view="video">
        <div class="view-header">
          <div class="section-title">
            <h2>视频解析</h2>
            <p>上传视频创建轨迹任务，支持查询状态、结果、取消和实时进度订阅。</p>
          </div>
          <button type="button" id="video-copy-button">复制调用示例</button>
        </div>
        <form id="video-form" class="form-grid">
          <label class="span-2">视频文件 <input id="job-file-input" name="file" type="file" accept="video/*" /></label>
          <label>抽帧间隔 <input id="job-frame-interval-input" name="frame_interval" type="number" min="1" value="15" /></label>
          <label>最大处理帧数 <input id="job-max-frames-input" name="max_frames" type="number" min="1" value="64" /></label>
          <button type="submit" class="primary">创建任务</button>
        </form>
        <div class="card">
          <div class="form-grid compact">
            <label class="span-2">任务 ID <input id="job-id-input" placeholder="创建任务后自动填入" /></label>
            <button type="button" id="job-get-button">查询状态</button>
            <button type="button" id="job-result-button">查看结果</button>
            <button type="button" id="job-cancel-button" class="danger">取消任务</button>
            <button type="button" id="job-watch-button">实时订阅</button>
          </div>
          <div id="job-ws-status" class="ws-status">未订阅任务进度</div>
          <div id="jobs-summary" class="result-summary"></div>
          <div id="job-visuals" class="result-visual-grid"></div>
          <div id="jobs-json" class="json-view data-viewer" role="region" aria-label="视频任务响应数据"></div>
        </div>
      </section>


            <section class="view" data-view="video-results">
        <div class="view-header">
          <div class="section-title">
            <h2>解析结果</h2>
            <p>统一查看图片解析、视频解析和视频流解析结果，结果图片会标注来源类型和关键上下文。</p>
          </div>
          <button type="button" id="video-results-refresh-button">刷新结果</button>
        </div>
        <div class="tabs result-tabs" role="tablist" aria-label="解析结果分类">
          <button type="button" data-results-tab="image" role="tab">图片解析结果</button>
          <button type="button" data-results-tab="video" role="tab">视频解析结果</button>
          <button type="button" data-results-tab="stream" role="tab">视频流解析结果</button>
        </div>
        <div class="analysis-results-panel" data-results-panel="image" role="tabpanel">
          <div class="result-panel">
            <div class="section-title">
              <h3>图片解析结果</h3>
              <p>展示当前会话最近完成的图片解析结果。</p>
            </div>
            <div id="image-results-summary" class="result-summary"></div>
            <div id="image-results-visuals" class="result-visual-grid"></div>
            <div id="image-results-json" class="json-view data-viewer" role="region" aria-label="图片解析结果数据"></div>
          </div>
        </div>
        <div class="analysis-results-panel" data-results-panel="video" role="tabpanel">
          <div class="result-panel">
            <div class="section-title">
              <h3>视频解析结果</h3>
              <p>汇总当前租户已完成视频任务的解析帧缩略图。</p>
            </div>
            <div id="video-results-summary" class="result-summary"></div>
            <div id="video-results-visuals" class="result-visual-grid"></div>
            <div id="video-results-json" class="json-view data-viewer" role="region" aria-label="视频解析结果数据"></div>
          </div>
        </div>
        <div class="analysis-results-panel" data-results-panel="stream" role="tabpanel">
          <div class="result-panel">
            <div class="section-title">
              <h3>视频流解析结果</h3>
              <p>展示视频流注册状态、worker 会话和最近事件快照。</p>
            </div>
            <div id="stream-results-summary" class="result-summary"></div>
            <div id="stream-results-list" class="stream-result-list"></div>
            <div id="stream-results-json" class="json-view data-viewer" role="region" aria-label="视频流解析结果数据"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="streams">
        <div class="view-header">
          <div class="section-title">
            <h2>视频流解析</h2>
            <p>注册 RTSP/HTTP 流，启动分析工作进程，查看事件并订阅实时快照。</p>
          </div>
          <button type="button" id="streams-refresh-button">刷新视频流解析</button>
        </div>
        <form id="stream-form" class="form-grid">
          <label class="span-2">视频流地址 <input id="stream-url-input" name="stream_url" placeholder="rtsp://user:password@host/stream1" /></label>
          <label>显示名称 <input id="stream-name-input" name="name" placeholder="门岗摄像头 1" /></label>
          <label>元数据（JSON） <input id="stream-metadata-input" placeholder='{"site":"east-gate"}' /></label>
          <button type="submit" class="primary">注册视频流解析</button>
        </form>
        <div class="card">
          <div class="form-grid compact">
            <label class="span-2">视频流 ID <input id="stream-id-input" placeholder="视频流 ID" /></label>
            <button type="button" id="stream-get-button">详情</button>
            <button type="button" id="stream-start-button" class="primary">启动分析</button>
            <button type="button" id="stream-stop-button">停止分析</button>
            <button type="button" id="stream-events-button">事件</button>
            <button type="button" id="stream-watch-button">实时订阅</button>
          </div>
          <div id="stream-ws-status" class="ws-status">未订阅视频流事件</div>
          <div id="streams-summary" class="result-summary"></div>
          <div id="streams-json" class="json-view data-viewer" role="region" aria-label="视频流响应数据"></div>
        </div>
      </section>

      <section class="view" data-view="access-credentials">
        <div class="view-header">
          <div class="section-title">
            <h2>应用凭证</h2>
            <p>维护租户接入应用、scope、JWT 元信息和密钥轮换记录。</p>
          </div>
          <button type="button" id="access-refresh-button">刷新接入清单</button>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title">
              <h3>接入应用</h3>
              <p>本清单用于接入规划、示例生成和租户级 接口密钥鉴权。</p>
            </div>
            <form id="access-app-form" class="form-grid">
              <label>应用 ID <input id="access-app-id-input" placeholder="留空自动生成" /></label>
              <label>应用名称 <input id="access-app-name-input" placeholder="业务项目或服务名" /></label>
              <label>负责人 <input id="access-app-owner-input" placeholder="团队或联系人" /></label>
              <label>JWT 签发方 <input id="access-jwt-issuer-input" placeholder="可选" /></label>
              <label>JWT 接收方 <input id="access-jwt-audience-input" placeholder="可选" /></label>
              <label>状态
                <select id="access-app-status-input">
                  <option value="active">启用</option>
                  <option value="disabled">禁用</option>
                </select>
              </label>
              <label>每分钟限额 <input id="access-rate-limit-input" type="number" min="0" step="1" placeholder="平台默认" /></label>
              <label>突发容量 <input id="access-burst-input" type="number" min="0" step="1" placeholder="跟随限额" /></label>
              <label>每日配额 <input id="access-daily-quota-input" type="number" min="0" step="1" placeholder="不限" /></label>
              <label class="field-inline"><input type="checkbox" name="access-scope" value="infer" checked /> infer</label>
              <label class="field-inline"><input type="checkbox" name="access-scope" value="compare" checked /> compare</label>
              <label class="field-inline"><input type="checkbox" name="access-scope" value="gallery:read" checked /> gallery:read</label>
              <label class="field-inline"><input type="checkbox" name="access-scope" value="gallery:write" /> gallery:write</label>
              <label class="field-inline"><input type="checkbox" name="access-scope" value="jobs" /> jobs</label>
              <label class="field-inline"><input type="checkbox" name="access-scope" value="streams" /> streams</label>
              <button type="submit" class="primary">保存应用</button>
              <button type="button" id="access-rotate-button">轮换密钥</button>
            </form>
            <div id="access-app-summary" class="result-summary"></div>
            <div id="access-credentials-json" class="json-view data-viewer" role="region" aria-label="应用凭证响应数据"></div>
          </div>
          <div class="list-panel">
            <div class="section-title">
              <h3>应用列表</h3>
              <p>选择应用可回填表单并生成对应示例。</p>
            </div>
            <div id="access-app-list" class="data-table-wrap"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="sdk-examples">
        <div class="view-header">
          <div class="section-title">
            <h2>开发工具包示例</h2>
            <p>当前租户的 Python、Node 和 curl 最小调用片段。</p>
          </div>
          <button type="button" id="sdk-refresh-button">刷新示例</button>
        </div>
        <div class="triple-grid">
          <div class="card">
            <div class="section-title"><h3>Python</h3><p>使用 Python SDK 完成以图搜人。</p></div>
            <pre id="sdk-python-code" class="code-view"></pre>
            <button type="button" id="sdk-python-copy-button">复制 Python</button>
          </div>
          <div class="card">
            <div class="section-title"><h3>Node.js</h3><p>使用 Node SDK 完成人像比对。</p></div>
            <pre id="sdk-node-code" class="code-view"></pre>
            <button type="button" id="sdk-node-copy-button">复制 Node</button>
          </div>
          <div class="card">
            <div class="section-title"><h3>curl</h3><p>用于网关和内网联调的直接调用。</p></div>
            <pre id="sdk-curl-code" class="code-view"></pre>
            <button type="button" id="sdk-curl-copy-button">复制 curl</button>
          </div>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>批量异步</h3><p>使用 SDK 提交批量以图搜人任务并记录 batch_id。</p></div>
            <pre id="sdk-batch-code" class="code-view"></pre>
            <button type="button" id="sdk-batch-copy-button">复制批量示例</button>
          </div>
          <div class="card">
            <div class="section-title"><h3>视频轮询</h3><p>创建离线视频任务并按状态查询结果。</p></div>
            <pre id="sdk-video-code" class="code-view"></pre>
            <button type="button" id="sdk-video-copy-button">复制视频示例</button>
          </div>
        </div>
        <div id="sdk-json" class="json-view data-viewer" role="region" aria-label="开发工具包示例数据"></div>
      </section>

      <section class="view" data-view="openapi-docs">
        <div class="view-header">
          <div class="section-title">
            <h2>开放接口定义</h2>
            <p>查看当前服务暴露的稳定接口、核心路径和文档入口。</p>
          </div>
          <div class="toolbar-actions">
            <button type="button" id="openapi-refresh-button">刷新接口定义</button>
            <a href="/openapi.json" target="_blank" rel="noreferrer">打开 JSON</a>
          </div>
        </div>
        <div id="openapi-summary" class="result-summary"></div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>核心路径</h3><p>用于接入验收和网关放行校验。</p></div>
            <div id="openapi-path-table" class="data-table-wrap"></div>
          </div>
          <div class="card">
            <div class="section-title"><h3>文档入口</h3><p>生产环境可关闭交互文档，仅保留受控契约导出。</p></div>
            <pre id="openapi-code" class="code-view"></pre>
            <button type="button" id="openapi-copy-button">复制检查命令</button>
          </div>
        </div>
        <div id="openapi-json" class="json-view data-viewer" role="region" aria-label="开放接口定义数据"></div>
      </section>

      <section class="view" data-view="api-playground">
        <div class="view-header">
          <div class="section-title">
            <h2>接口调试台</h2>
            <p>使用当前租户和令牌发起受控测试请求，展示请求头、耗时和响应。</p>
          </div>
        </div>
        <form id="playground-form" class="form-grid">
          <label>接口
            <select id="playground-endpoint-input">
              <option value="/v1/gallery/search" data-method="POST">POST /v1/gallery/search</option>
              <option value="/v1/gallery/search/batch" data-method="POST">POST /v1/gallery/search/batch</option>
              <option value="/v1/compare/persons" data-method="POST">POST /v1/compare/persons</option>
              <option value="/v1/compare/batch" data-method="POST">POST /v1/compare/batch</option>
              <option value="/v1/fusion/compare" data-method="POST">POST /v1/fusion/compare</option>
              <option value="/v1/infer/persons" data-method="POST">POST /v1/infer/persons</option>
              <option value="/v1/jobs/video" data-method="POST">POST /v1/jobs/video</option>
              <option value="/v1/streams" data-method="POST">POST /v1/streams</option>
              <option value="/v1/streams" data-method="GET">GET /v1/streams</option>
              <option value="/v1/streams/{stream_id}/events" data-method="GET">GET /v1/streams/{stream_id}/events</option>
              <option value="/v1/models" data-method="GET">GET /v1/models</option>
              <option value="/v1/thresholds" data-method="GET">GET /v1/thresholds</option>
            </select>
          </label>
          <label class="span-2">文件 A / 查询图 / 视频 <input id="playground-file-a-input" type="file" accept="image/*,video/*" multiple /></label>
          <label class="span-2">文件 B / 批量右侧图 <input id="playground-file-b-input" type="file" accept="image/*" multiple /></label>
          <label>阈值方案 <input id="playground-threshold-input" value="normal" /></label>
          <label>Top K / limit <input id="playground-top-k-input" type="number" min="1" value="5" /></label>
          <label>流 ID <input id="playground-stream-id-input" placeholder="查询流事件时填写" /></label>
          <label class="span-2">流地址 <input id="playground-stream-url-input" placeholder="rtsp://camera.example/live" /></label>
          <label>流名称 <input id="playground-stream-name-input" placeholder="可选" /></label>
          <label class="field-inline"><input id="playground-async-mode-input" type="checkbox" /> 异步批量</label>
          <button type="submit" class="primary">发送请求</button>
        </form>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>请求预览</h3><p>敏感令牌默认遮罩。</p></div>
            <pre id="playground-request-code" class="code-view"></pre>
          </div>
          <div class="result-panel">
            <div class="section-title"><h3>响应</h3><p>包含请求 ID、耗时和错误码定位信息。</p></div>
            <div id="playground-summary" class="result-summary"></div>
            <div id="playground-json" class="json-view data-viewer" role="region" aria-label="接口调试台 响应数据"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="call-logs">
        <div class="view-header">
          <div class="section-title">
            <h2>调用日志</h2>
            <p>按请求 ID、应用、接口和状态定位服务端调用结果。</p>
          </div>
          <button type="button" id="call-logs-refresh-button">刷新日志</button>
        </div>
        <div class="form-grid compact">
          <label>请求 ID <input id="call-log-request-input" placeholder="精确或部分匹配" /></label>
          <label>页面/接口 <input id="call-log-endpoint-input" placeholder="gallery、compare、models..." /></label>
          <label>状态
            <select id="call-log-status-input">
              <option value="">全部</option>
              <option value="success">成功</option>
              <option value="error">异常</option>
            </select>
          </label>
          <label>错误码 <input id="call-log-error-code-input" placeholder="rate_limited、http_500" /></label>
          <label>起始时间 <input id="call-log-created-since-input" type="number" min="0" step="1" placeholder="Unix 秒" /></label>
          <label>结束时间 <input id="call-log-created-until-input" type="number" min="0" step="1" placeholder="Unix 秒" /></label>
          <label>应用
            <select id="call-log-application-input"><option value="">全部应用</option></select>
          </label>
          <button type="button" id="call-log-filter-button">筛选</button>
        </div>
        <div id="call-log-summary" class="result-summary"></div>
        <div id="call-log-table" class="data-table-wrap"></div>
        <div id="call-logs-json" class="json-view data-viewer" role="region" aria-label="调用日志数据"></div>
      </section>

      <section class="view" data-view="error-codes">
        <div class="view-header">
          <div class="section-title">
            <h2>错误码</h2>
            <p>面向接入方的稳定错误码、HTTP 状态和重试建议。</p>
          </div>
          <button type="button" id="error-codes-refresh-button">刷新错误码</button>
        </div>
        <div id="error-codes-summary" class="result-summary"></div>
        <div class="card">
          <div class="section-title"><h3>错误码目录</h3><p>所有条目均为脱敏说明，定位具体请求请结合请求 ID 和调用日志。</p></div>
          <div id="error-codes-table" class="data-table-wrap"></div>
        </div>
        <div id="error-codes-json" class="json-view data-viewer" role="region" aria-label="错误码目录数据"></div>
      </section>
      <section class="view" data-view="webhooks">
        <div class="view-header">
          <div class="section-title">
            <h2>事件回调</h2>
            <p>维护租户回调端点、订阅事件、签名密钥轮换和重试策略。</p>
          </div>
          <button type="button" id="webhook-refresh-button">刷新回调清单</button>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title">
              <h3>回调端点</h3>
              <p>本地清单用于接入编排；生产投递器应在服务端读取等价配置。</p>
            </div>
            <form id="webhook-form" class="form-grid">
              <label>事件回调 ID <input id="webhook-id-input" placeholder="留空自动生成" /></label>
              <label>名称 <input id="webhook-name-input" placeholder="业务回调" /></label>
              <label>接入应用
                <select id="webhook-app-input"></select>
              </label>
              <label>状态
                <select id="webhook-status-input">
                  <option value="active">启用</option>
                  <option value="disabled">禁用</option>
                </select>
              </label>
              <label class="span-2">回调 URL <input id="webhook-url-input" placeholder="https://service.internal/portrait/events" /></label>
              <label>重试次数 <input id="webhook-retry-input" type="number" min="0" max="10" value="3" /></label>
              <label>超时秒数 <input id="webhook-timeout-input" type="number" min="1" max="60" value="5" /></label>
              <label class="field-inline"><input type="checkbox" name="webhook-event" value="gallery.enrolled" checked /> gallery.enrolled</label>
              <label class="field-inline"><input type="checkbox" name="webhook-event" value="search.completed" /> search.completed</label>
              <label class="field-inline"><input type="checkbox" name="webhook-event" value="compare.completed" /> compare.completed</label>
              <label class="field-inline"><input type="checkbox" name="webhook-event" value="job.completed" checked /> job.completed</label>
              <label class="field-inline"><input type="checkbox" name="webhook-event" value="stream.event" checked /> stream.event</label>
              <label class="field-inline"><input type="checkbox" name="webhook-event" value="model.rollout" /> model.rollout</label>
              <button type="submit" class="primary">保存事件回调</button>
              <button type="button" id="webhook-rotate-button">轮换签名密钥</button>
              <button type="button" id="webhook-sample-button">生成样例事件</button>
            </form>
            <div id="webhook-summary" class="result-summary"></div>
            <div id="webhook-json" class="json-view data-viewer" role="region" aria-label="事件回调响应数据"></div>
          </div>
          <div class="list-panel">
            <div class="section-title">
              <h3>端点列表</h3>
              <p>选择端点可回填表单并生成对应样例。</p>
            </div>
            <div id="webhook-list" class="data-table-wrap"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="slo-panel">
        <div class="view-header">
          <div class="section-title">
            <h2>服务等级目标面板</h2>
            <p>汇总成功率、p95/p99、GPU 队列、活跃流和 worker 热状态。</p>
          </div>
          <button type="button" id="slo-refresh-button">刷新服务等级目标</button>
        </div>
        <div id="slo-summary" class="result-summary"></div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>错误预算</h3><p>基于当前指标做轻量燃烧评估。</p></div>
            <div id="slo-badges" class="badge-row"></div>
            <div id="slo-json" class="json-view data-viewer" role="region" aria-label="服务等级目标数据"></div>
          </div>
          <div class="card">
            <div class="section-title"><h3>工作器状态</h3><p>模型常驻、队列和视频流工作器摘要。</p></div>
            <div id="slo-worker-list" class="simple-list"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="multimodal-compare">
        <div class="view-header">
          <div class="section-title">
            <h2>融合比对</h2>
            <p>左右证据输入，多模态分数、质量和冲突风险展开展示。</p>
          </div>
        </div>
        <form id="multimodal-form" class="form-grid">
          <label class="span-2">证据 A <input id="multimodal-a-input" type="file" accept="image/*" /></label>
          <label class="span-2">证据 B <input id="multimodal-b-input" type="file" accept="image/*" /></label>
          <label>阈值方案 <input id="multimodal-threshold-input" value="normal" /></label>
          <label class="field-inline"><input type="checkbox" name="multimodal-scope" value="face" checked /> face</label>
          <label class="field-inline"><input type="checkbox" name="multimodal-scope" value="body" checked /> body</label>
          <label class="field-inline"><input type="checkbox" name="multimodal-scope" value="appearance" checked /> appearance</label>
          <label class="field-inline"><input type="checkbox" name="multimodal-scope" value="gait" /> gait</label>
          <button type="submit" class="primary">融合比对</button>
        </form>
        <div class="result-panel">
          <div class="section-title"><h3>融合结论</h3><p>通过状态是算法建议，最终身份裁决需结合业务流程。</p></div>
          <div id="multimodal-summary" class="result-summary"></div>
          <div id="multimodal-table" class="data-table-wrap"></div>
          <div id="multimodal-json" class="json-view data-viewer" role="region" aria-label="多模态融合响应数据"></div>
        </div>
      </section>

      <section class="view" data-view="track-review">
        <div class="view-header">
          <div class="section-title">
            <h2>轨迹审阅</h2>
            <p>从视频任务结果中查看关键帧、tracklet 稳定性和候选证据。</p>
          </div>
          <button type="button" id="track-review-refresh-button">刷新轨迹</button>
        </div>
        <div id="track-review-summary" class="result-summary"></div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>人工标注</h3><p>标记误检、错配和低质量样本，进入评估数据池。</p></div>
            <form id="track-review-annotation-form" class="form-grid">
              <label>任务 ID <input id="track-review-job-input" placeholder="任务 ID" /></label>
              <label>轨迹 ID <input id="track-review-track-input" placeholder="轨迹 ID" /></label>
              <label>标注
                <select id="track-review-label-input">
                  <option value="false_positive">误检</option>
                  <option value="mismatch">错配</option>
                  <option value="low_quality">低质量</option>
                  <option value="confirmed">确认正确</option>
                  <option value="uncertain">待复核</option>
                </select>
              </label>
              <label>帧序号 <input id="track-review-frame-input" type="number" min="0" step="1" placeholder="可选" /></label>
              <label>复核人 <input id="track-review-reviewer-input" placeholder="复核人" /></label>
              <label>证据引用 <input id="track-review-evidence-input" placeholder="帧或对象键" /></label>
              <label class="span-2">备注 <textarea id="track-review-note-input" placeholder="原因、场景、复核结论"></textarea></label>
              <button type="submit" class="primary">保存标注</button>
            </form>
          </div>
          <div class="card">
            <div class="section-title"><h3>评估数据池</h3><p>最近人工标注，不直接修改线上模型。</p></div>
            <div id="track-review-annotation-table" class="data-table-wrap"></div>
          </div>
        </div>
        <div id="track-review-visuals" class="result-visual-grid"></div>
        <div id="track-review-json" class="json-view data-viewer" role="region" aria-label="轨迹审阅数据"></div>
      </section>

      <section class="view" data-view="evaluation-center">
        <div class="view-header">
          <div class="section-title">
            <h2>回归评估</h2>
            <p>汇总模型能力、阈值、留出集指标和生产门禁状态。</p>
          </div>
          <button type="button" id="evaluation-refresh-button">刷新评估</button>
        </div>
        <div id="evaluation-summary" class="result-summary"></div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>能力矩阵</h3><p>fallback 和 placeholder 会作为生产风险标记。</p></div>
            <div id="evaluation-capability-table" class="data-table-wrap"></div>
          </div>
          <div class="card">
            <div class="section-title"><h3>指标摘要</h3><p>用于回归报告和阈值标定的控制台视图。</p></div>
            <div id="evaluation-metrics-table" class="data-table-wrap"></div>
          </div>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>评估数据池</h3><p>汇总轨迹审阅产生的人工标注，只展示租户内统计。</p></div>
            <div id="evaluation-review-summary" class="result-summary"></div>
            <div id="evaluation-review-label-table" class="data-table-wrap"></div>
          </div>
          <div class="card">
            <div class="section-title"><h3>证据索引</h3><p>展示最近样本的任务、轨迹、帧和证据引用。</p></div>
            <div id="evaluation-review-evidence-table" class="data-table-wrap"></div>
          </div>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>评估数据集</h3><p>由人工标注池派生的留出集、正例和校准样本。</p></div>
            <div id="evaluation-dataset-table" class="data-table-wrap"></div>
          </div>
          <div class="card">
            <div class="section-title"><h3>阈值推荐</h3><p>基于当前租户标注池生成只读建议，不自动写入配置。</p></div>
            <div id="evaluation-threshold-table" class="data-table-wrap"></div>
          </div>
        </div>
        <div id="evaluation-json" class="json-view data-viewer" role="region" aria-label="评估中心数据"></div>
      </section>

      <section class="view" data-view="release-center">
        <div class="view-header">
          <div class="section-title">
            <h2>模型发布</h2>
            <p>预览别名路由、灰度权重、切换 production 和回滚路径。</p>
          </div>
          <button type="button" id="release-refresh-button">刷新发布状态</button>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>发布操作</h3><p>默认预演，取消勾选才会写入配置和审计。</p></div>
            <form id="release-form" class="form-grid">
              <label>操作
                <select id="release-action-input">
                  <option value="preview">预览</option>
                  <option value="switch">切换</option>
                  <option value="weighted">灰度</option>
                  <option value="rollback">回滚</option>
                </select>
              </label>
              <label>别名 <input id="release-alias-input" placeholder="person_reid_default" /></label>
              <label>目标模型 <input id="release-target-input" placeholder="portrait_hub/model.onnx" /></label>
              <label>当前目标 <input id="release-expected-input" placeholder="可选" /></label>
              <label>流量 key <input id="release-traffic-key-input" placeholder="租户或请求键" /></label>
              <label>灰度权重 <input id="release-weight-input" type="number" min="0" max="100000" value="10000" /></label>
              <label class="field-inline"><input id="release-dry-run-input" type="checkbox" checked /> 预演模式</label>
              <button type="submit" class="primary">执行发布操作</button>
            </form>
          </div>
          <div class="result-panel">
            <div class="section-title"><h3>发布结果</h3><p>包含别名、candidate、previous_target 和回滚依据。</p></div>
            <div id="release-summary" class="result-summary"></div>
            <div class="section-title"><h3>发布审计</h3><p>最近非预演发布、灰度和回滚记录。</p></div>
            <div id="release-audit-table" class="data-table-wrap"></div>
            <div id="release-json" class="json-view data-viewer" role="region" aria-label="模型发布响应数据"></div>
          </div>
        </div>
      </section>

      <section class="view" data-view="audit-compliance">
        <div class="view-header">
          <div class="section-title">
            <h2>合规审计</h2>
            <p>查看审计写入策略、保留策略、备份快照和敏感数据脱敏状态。</p>
          </div>
          <button type="button" id="audit-refresh-button">刷新审计状态</button>
        </div>
        <div id="audit-summary" class="result-summary"></div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title"><h3>合规检查</h3><p>围绕认证、租户头、加密、审计和数据保留。</p></div>
            <div id="audit-check-list" class="alert-list"></div>
            <div class="section-title"><h3>最近审计事件</h3><p>当前租户最近写入的脱敏审计事件。</p></div>
                        <div class="form-grid compact-form">
              <label>事件 <input id="audit-event-filter-input" placeholder="admin_export / model" /></label>
              <label>结果 <select id="audit-outcome-filter-input"><option value="">全部</option><option value="success">success</option><option value="started">started</option><option value="failure">failure</option><option value="error">error</option></select></label>
              <label>分类 <select id="audit-category-filter-input"><option value="">全部</option><option value="delete_requests">删除</option><option value="exports">导出</option><option value="model_versions">模型</option><option value="retention">保留</option><option value="other">其它</option></select></label>
              <label>请求 ID <input id="audit-request-filter-input" placeholder="精确或部分匹配" /></label>
              <label>起始时间戳 <input id="audit-created-since-input" inputmode="decimal" placeholder="Unix 秒" /></label>
              <label>结束时间戳 <input id="audit-created-until-input" inputmode="decimal" placeholder="Unix 秒" /></label>
              <button type="button" id="audit-event-filter-button">筛选</button>
            </div>
            <div id="audit-event-table" class="data-table-wrap"></div>
          </div>
          <div class="card">
            <div class="section-title"><h3>导出与保留</h3><p>当前租户的导出分页、对象状态和最近管理动作。</p></div>
            <div id="audit-json" class="json-view data-viewer" role="region" aria-label="合规审计数据"></div>
          </div>
        </div>
      </section>
      <section class="view" data-view="models">
        <div class="view-header">
          <div class="section-title">
            <h2>模型管理</h2>
            <p>查看模型配置、加载/卸载模型，以及确认各能力是否已切到生产模型。</p>
          </div>
          <button type="button" id="models-refresh-button">刷新模型</button>
        </div>
        <div class="card">
          <div class="form-grid">
            <label class="span-2">模型 ID / 别名 <input id="model-id-input" placeholder="person_detector_default 或 portrait_hub/yolov8n.onnx" /></label>
            <button type="button" id="model-detail-button">查看详情</button>
            <button type="button" id="load-model-button" class="primary">加载模型</button>
            <button type="button" id="unload-model-button">卸载模型</button>
          </div>
          <div id="models-summary" class="result-summary"></div>
          <div id="models-json" class="json-view data-viewer" role="region" aria-label="模型响应数据"></div>
        </div>
      </section>

      <section class="view" data-view="admin-threshold">
        <div class="view-header">
          <div class="section-title">
            <h2>比对阈值</h2>
            <p>按方案更新各模态比对阈值。</p>
          </div>
          <button type="button" id="admin-refresh-button">刷新治理状态</button>
        </div>
        <form id="threshold-form" class="form-grid">
          <label>阈值方案 <input id="threshold-profile-input" value="normal" /></label>
          <label>人体 <input id="threshold-body-input" type="number" min="0" max="1" step="0.01" /></label>
          <label>人脸 <input id="threshold-face-input" type="number" min="0" max="1" step="0.01" /></label>
          <label>步态 <input id="threshold-gait-input" type="number" min="0" max="1" step="0.01" /></label>
          <label>外观 <input id="threshold-appearance-input" type="number" min="0" max="1" step="0.01" /></label>
          <label>融合 <input id="threshold-fusion-input" type="number" min="0" max="1" step="0.01" /></label>
          <button type="submit" class="primary">保存阈值</button>
        </form>
        <div id="admin-threshold-json" class="json-view data-viewer" role="region" aria-label="比对阈值响应数据"></div>
      </section>

      <section class="view" data-view="admin-data">
        <div class="view-header">
          <div class="section-title">
            <h2>数据保留与备份</h2>
            <p>清理和备份会按当前租户执行。</p>
          </div>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title">
              <h3>数据保留</h3>
              <p>按保留天数清理过期数据，需输入确认词。</p>
            </div>
            <form id="retention-form" class="form-grid">
              <label>保留天数 <input id="retention-days-input" type="number" min="0" value="30" /></label>
              <label>输入 cleanup 确认 <input id="retention-confirm-input" placeholder="cleanup" /></label>
              <button type="submit" class="danger">执行清理</button>
            </form>
          </div>
          <div class="card">
            <div class="section-title">
              <h3>数据备份</h3>
              <p>导出当前租户数据，需输入确认词。</p>
            </div>
            <form id="backup-form" class="form-grid">
              <label>更新时间起点 (updated_since) <input id="backup-updated-since-input" type="number" min="0" placeholder="可选 Unix 秒" /></label>
              <label>输入 backup 确认 <input id="backup-confirm-input" placeholder="backup" /></label>
              <button type="submit">创建备份</button>
            </form>
          </div>
        </div>
        <div class="card">
          <div class="section-title">
            <h3>最近备份快照</h3>
            <p>从审计链读取当前租户的备份快照索引，只展示安全白名单字段。</p>
          </div>
          <button type="button" id="backup-snapshot-refresh-button">刷新快照</button>
          <div id="backup-snapshot-summary" class="summary-grid"></div>
          <div id="backup-snapshot-table" class="data-table-wrap"></div>
        </div>
        <div id="admin-data-json" class="json-view data-viewer" role="region" aria-label="数据保留与备份响应数据"></div>
      </section>

      <section class="view" data-view="alerts">
        <div class="view-header">
          <div class="section-title">
            <h2>告警评估</h2>
            <p>基于当前 metrics 做本地阈值评估，方便交付前巡检。</p>
          </div>
          <button type="button" id="alerts-refresh-button">评估告警</button>
        </div>
        <form id="alert-form" class="form-grid">
          <label>最大错误率 <input id="alert-error-rate-input" type="number" min="0" max="1" step="0.01" /></label>
          <label>最大 P95 延迟秒 <input id="alert-p95-input" type="number" min="0" step="0.1" /></label>
          <label>最小 GPU 空闲 GB <input id="alert-gpu-free-input" type="number" min="0" step="0.1" /></label>
          <button type="submit" class="primary">保存告警阈值</button>
        </form>
        <div id="alert-list" class="alert-list"></div>
        <div id="alerts-json" class="json-view data-viewer" role="region" aria-label="告警响应数据"></div>
      </section>
    </section>
  </main>
  </div>
  <div id="vision-lightbox" class="vision-lightbox hidden" aria-hidden="true"></div>`;

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
  frame_interval: "视频抽帧间隔",
  max_frames: "最大允许抽帧数",
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
        msg = parsed.detail || parsed.message || msg;
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
  state.visionLightboxIndex = null;
  const node = qs("#vision-lightbox");
  if (!node) return;
  node.classList.add("hidden");
  node.setAttribute("aria-hidden", "true");
  node.innerHTML = "";
  document.body.classList.remove("lightbox-open");
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
}

function openVisionLightbox(index) {
  if (!Number.isInteger(index) || index < 0 || index >= state.visionResultVisuals.length) return;
  state.visionLightboxIndex = index;
  renderVisionLightbox();
}

function requestSnippet(path, formFieldExamples = []) {
  const lines = [
    `curl -X POST "${window.location.origin}${path}"`,
    `  -H "X-Tenant-ID: ${state.tenantId}"`,
  ];
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
  qs("#vision-copy-button").onclick = wrapHandler(() => copyText(requestSnippet(vision, ["files=@frame.jpg", "include_embeddings=false"]), "调用示例已复制"));
  qs("#compare-copy-button").onclick = wrapHandler(() => copyText(requestSnippet(compare, ["image_a=@a.jpg", "image_b=@b.jpg", "threshold_profile=normal"]), "调用示例已复制"));
  qs("#gallery-copy-button").onclick = wrapHandler(() => copyText(requestSnippet("/v1/gallery/search", ["file=@query.jpg", "modality=body", "top_k=5"]), "调用示例已复制"));
  qs("#video-copy-button").onclick = wrapHandler(() => copyText(requestSnippet("/v1/jobs/video", ["file=@demo.mp4", "frame_interval=15", "max_frames=64"]), "调用示例已复制"));
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

function renderDashboard(summary) {
  const metrics = summary.metrics || {};
  qs("#metric-requests").textContent = String(metrics.requests || 0);
  qs("#metric-error-rate").textContent = `${((metrics.error_rate || 0) * 100).toFixed(1)}%`;
  qs("#metric-p95").textContent = `${formatNumber(metrics.inference_p95_seconds, 2)}s`;
  qs("#metric-gpu-free").textContent = metrics.gpu_free_gb === null ? "--" : `${formatNumber(metrics.gpu_free_gb, 1)}GB`;
  const status = summary.status || {};
  renderBadges("#overview-badges", [
    { label: "图库", value: localizeValue(status.configured_backends?.gallery || "--"), tone: "ok" },
    { label: "向量库", value: localizeValue(status.configured_backends?.vector || "--"), tone: "ok" },
    { label: "对象存储", value: localizeValue(status.configured_backends?.object_storage || "--"), tone: "ok" },
    { label: "队列", value: localizeValue(status.configured_backends?.task_queue || "--"), tone: "ok" },
    { label: "RBAC", value: status.security?.rbac_enabled ? "开启" : "关闭", tone: status.security?.rbac_enabled ? "ok" : "warn" },
  ]);
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
    const current = item.current === null ? "--" : `${formatNumber(Number(item.current) * item.scale, 2)}${item.unit}`;
    const limit = `${formatNumber(Number(item.limit) * item.scale, 2)}${item.unit}`;
    return `<div class="alert-item ${item.ok ? "ok" : "warn"}"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(current)} / ${escapeHtml(limit)}</span></div>`;
  }).join("");
  renderPayload("alerts", "#alerts-json", { config: state.alertConfig, checks });
}

function randomToken(prefix = "phk") {
  const bytes = new Uint8Array(18);
  if (window.crypto && window.crypto.getRandomValues) {
    window.crypto.getRandomValues(bytes);
  } else {
    bytes.forEach((_, index) => { bytes[index] = Math.floor(Math.random() * 256); });
  }
  return `${prefix}_${Array.from(bytes).map((item) => item.toString(16).padStart(2, "0")).join("")}`;
}

function maskToken(value) {
  const text = String(value || "");
  if (!text) return "未配置";
  if (text.length <= 10) return "••••";
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
}

function selectedCheckboxValues(name) {
  return qsa(`input[name="${name}"]:checked`).map((item) => item.value);
}

function optionalLimitValue(selector) {
  const raw = qs(selector).value.trim();
  if (raw === "") return null;
  const value = Math.floor(Number(raw));
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function formatLimitValue(value, fallback = "默认") {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? String(Math.floor(numeric)) : fallback;
}

function accessAppCallSummary(app) {
  const calls = Math.max(0, Math.floor(Number(app.call_count || 0)));
  const errorRate = Math.max(0, Number(app.error_rate || 0) * 100);
  const lastCalled = app.last_called_at ? formatDateTime(app.last_called_at) : "未调用";
  return `${calls} calls · ${formatNumber(errorRate, 2)}% err · ${lastCalled}`;
}

function populateCallLogApplicationOptions() {
  const node = document.querySelector("#call-log-application-input");
  if (!node) return;
  const selected = node.value;
  const apps = state.accessApplications.map(normalizeAccessApplication);
  node.innerHTML = `<option value="">全部应用</option>${apps.map((app) => `<option value="${escapeHtml(app.id)}">${escapeHtml(app.name || app.id)}</option>`).join("")}`;
  if (apps.some((app) => app.id === selected)) node.value = selected;
}

function checkedValues(name, values) {
  const set = new Set(values || []);
  qsa(`input[name="${name}"]`).forEach((item) => { item.checked = set.has(item.value); });
}

function authMode() {
  if (state.bearer && state.apiKey) return "接口密钥 + JWT";
  if (state.bearer) return "JWT";
  if (state.apiKey) return "接口密钥";
  return "未配置令牌";
}

function normalizeAccessApplication(app) {
  const id = app.id || app.app_id || "";
  return { ...app, id, app_id: app.app_id || id };
}

function normalizeWebhook(webhook) {
  const id = webhook.id || webhook.webhook_id || "";
  return { ...webhook, id, webhook_id: webhook.webhook_id || id };
}

function selectedAccessApp() {
  const id = qs("#access-app-id-input")?.value.trim();
  return state.accessApplications.find((item) => item.id === id) || state.accessApplications[0] || null;
}

function fillAccessAppForm(app) {
  if (!app) return;
  qs("#access-app-id-input").value = app.id || "";
  qs("#access-app-name-input").value = app.name || "";
  qs("#access-app-owner-input").value = app.owner || "";
  qs("#access-jwt-issuer-input").value = app.jwt_issuer || "";
  qs("#access-jwt-audience-input").value = app.jwt_audience || "";
  qs("#access-app-status-input").value = app.status || "active";
  qs("#access-rate-limit-input").value = app.rate_limit_per_minute ?? "";
  qs("#access-burst-input").value = app.rate_limit_burst ?? "";
  qs("#access-daily-quota-input").value = app.daily_quota ?? "";
  checkedValues("access-scope", app.scopes || []);
}

function accessPayload() {
  return {
    tenant_id: state.tenantId,
    auth_mode: authMode(),
    api_key_preview: maskToken(state.apiKey),
    bearer_preview: maskToken(state.bearer),
    last_secret_preview: state.accessLastSecret ? maskToken(state.accessLastSecret.secret) : null,
    applications: state.accessApplications,
  };
}

async function refreshAccessApplications() {
  try {
    const payload = await api("/v1/access/applications");
    state.accessApplications = (payload.applications || []).map(normalizeAccessApplication);
    saveAccessApplications();
  } catch (error) {
    renderPayload("access-credentials", "#access-credentials-json", { ...accessPayload(), warning: error.message || String(error) });
  }
  renderAccessApplications();
}

function renderAccessApplications() {
  const apps = state.accessApplications.map(normalizeAccessApplication);
  state.accessApplications = apps;
  const activeCount = apps.filter((item) => item.status !== "disabled").length;
  const scopeCount = new Set(apps.flatMap((item) => item.scopes || [])).size;
  const limitedCount = apps.filter((item) => Number(item.rate_limit_per_minute || 0) > 0 || Number(item.daily_quota || 0) > 0).length;
  const maxErrorRate = apps.reduce((max, item) => Math.max(max, Number(item.error_rate || 0)), 0);
  populateCallLogApplicationOptions();
  renderSummary("#access-app-summary", [
    { label: "应用数", value: apps.length },
    { label: "启用", value: activeCount },
    { label: "Scope", value: scopeCount },
    { label: "限额", value: limitedCount },
    { label: "最高错误率", value: `${formatNumber(maxErrorRate * 100, 2)}%` },
  ]);
  const node = qs("#access-app-list");
  if (node) {
    node.innerHTML = apps.length ? `
      <table class="data-table">
        <thead><tr><th>应用</th><th>状态</th><th>Scope</th><th>限额</th><th>调用</th><th>操作</th></tr></thead>
        <tbody>
          ${apps.map((app) => `
            <tr>
              <td><strong>${escapeHtml(app.name || app.id)}</strong><br><small>${escapeHtml(app.id)}</small></td>
              <td>${escapeHtml(localizeValue(app.status || "active"))}</td>
              <td>${escapeHtml((app.scopes || []).join(", ") || "--")}</td>
              <td>${escapeHtml(`${formatLimitValue(app.rate_limit_per_minute)}/min · burst ${formatLimitValue(app.rate_limit_burst, "跟随")} · day ${formatLimitValue(app.daily_quota, "不限")}${Number(app.daily_quota || 0) > 0 ? ` (${Number(app.daily_quota_used || 0)}/${Number(app.daily_quota || 0)})` : ""}`)}</td>
              <td>${escapeHtml(accessAppCallSummary(app))}</td>
              <td>
                <button type="button" class="small" data-access-edit="${escapeHtml(app.id)}">选择</button>
                <button type="button" class="small" data-access-rotate="${escapeHtml(app.id)}">轮换</button>
                <button type="button" class="small" data-access-toggle="${escapeHtml(app.id)}">${app.status === "disabled" ? "启用" : "禁用"}</button>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="data-empty">暂无接入应用</div>`;
  }
  renderPayload("access-credentials", "#access-credentials-json", accessPayload());
}

async function saveAccessApp(event) {
  event.preventDefault();
  const id = qs("#access-app-id-input").value.trim() || `app_${Date.now()}`;
  const payload = {
    app_id: id,
    name: qs("#access-app-name-input").value.trim() || id,
    owner: qs("#access-app-owner-input").value.trim() || "platform",
    status: qs("#access-app-status-input").value || "active",
    scopes: selectedCheckboxValues("access-scope"),
    jwt_issuer: qs("#access-jwt-issuer-input").value.trim() || null,
    jwt_audience: qs("#access-jwt-audience-input").value.trim() || null,
    rate_limit_per_minute: optionalLimitValue("#access-rate-limit-input"),
    rate_limit_burst: optionalLimitValue("#access-burst-input"),
    daily_quota: optionalLimitValue("#access-daily-quota-input"),
  };
  const existing = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === id);
  let data;
  if (existing >= 0) {
    try {
      data = await api(`/v1/access/applications/${encodeURIComponent(id)}`, { method: "PATCH", json: payload });
    } catch (error) {
      data = await api("/v1/access/applications", { method: "POST", json: payload });
    }
  } else {
    data = await api("/v1/access/applications", { method: "POST", json: payload });
  }
  const app = normalizeAccessApplication(data.application || payload);
  const found = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === app.id);
  if (found >= 0) state.accessApplications[found] = app;
  else state.accessApplications.push(app);
  if (data.one_time_secret) state.accessLastSecret = { app_id: app.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveAccessApplications();
  qs("#access-app-id-input").value = app.id;
  renderAccessApplications();
  renderPayload("access-credentials", "#access-credentials-json", data.one_time_secret ? { ...accessPayload(), one_time_secret: state.accessLastSecret } : accessPayload());
}

async function rotateAccessApp(id = null) {
  const appId = id || qs("#access-app-id-input").value.trim();
  const app = state.accessApplications.find((item) => normalizeAccessApplication(item).id === appId) || selectedAccessApp();
  if (!app) throw new Error("请先选择接入应用");
  const data = await api(`/v1/access/applications/${encodeURIComponent(normalizeAccessApplication(app).id)}/rotate`, { method: "POST" });
  const updated = normalizeAccessApplication(data.application || app);
  const found = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === updated.id);
  if (found >= 0) state.accessApplications[found] = updated;
  state.accessLastSecret = { app_id: updated.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveAccessApplications();
  renderAccessApplications();
  renderPayload("access-credentials", "#access-credentials-json", {
    ...accessPayload(),
    one_time_secret: state.accessLastSecret,
    note: "密钥只在本次轮换结果中显示；服务器端只保留哈希。",
  });
}

async function toggleAccessApp(id) {
  const app = state.accessApplications.find((item) => normalizeAccessApplication(item).id === id);
  if (!app) return;
  const nextStatus = app.status === "disabled" ? "active" : "disabled";
  const data = await api(`/v1/access/applications/${encodeURIComponent(normalizeAccessApplication(app).id)}`, { method: "PATCH", json: { status: nextStatus } });
  const updated = normalizeAccessApplication(data.application || { ...app, status: nextStatus });
  const found = state.accessApplications.findIndex((item) => normalizeAccessApplication(item).id === updated.id);
  if (found >= 0) state.accessApplications[found] = updated;
  saveAccessApplications();
  renderAccessApplications();
}
function coreOpenApiPaths() {
  return [
    { scene: "人员入库", method: "POST", path: "/v1/gallery/enroll" },
    { scene: "以图搜人", method: "POST", path: "/v1/gallery/search" },
    { scene: "批量检索", method: "POST", path: "/v1/gallery/search/batch" },
    { scene: "人像比对", method: "POST", path: "/v1/compare/persons" },
    { scene: "批量比对", method: "POST", path: "/v1/compare/batch" },
    { scene: "图片解析", method: "POST", path: "/v1/infer/persons" },
    { scene: "视频任务", method: "POST", path: "/v1/jobs/video" },
    { scene: "视频任务结果", method: "GET", path: "/v1/jobs/{job_id}/result" },
    { scene: "实时流", method: "POST", path: "/v1/streams" },
    { scene: "流事件", method: "GET", path: "/v1/streams/{stream_id}/events" },
    { scene: "应用凭证", method: "GET", path: "/v1/access/applications" },
    { scene: "调用日志", method: "GET", path: "/v1/access/call-logs" },
    { scene: "事件回调", method: "GET", path: "/v1/access/webhooks" },
    { scene: "模型状态", method: "GET", path: "/v1/models" },
    { scene: "阈值", method: "GET", path: "/v1/thresholds" },
    { scene: "多模态融合", method: "POST", path: "/v1/fusion/compare" },
  ];
}

function renderOpenApiDocs(payload = state.openApiCache) {
  const baseUrl = window.location.origin;
  const schema = payload?.schema || null;
  const schemaPaths = schema?.paths || {};
  const rows = coreOpenApiPaths().map((item) => {
    const pathItem = schemaPaths[item.path] || {};
    const available = Boolean(pathItem[item.method.toLowerCase()]);
    return { ...item, available };
  });
  const loaded = Boolean(schema);
  renderSummary("#openapi-summary", [
    { label: "契约状态", value: loaded ? "已加载" : payload?.error ? "不可用" : "待刷新" },
    { label: "核心路径", value: rows.length },
    { label: "已声明", value: loaded ? rows.filter((item) => item.available).length : "--" },
    { label: "租户", value: state.tenantId },
  ]);
  qs("#openapi-path-table").innerHTML = `
    <table class="data-table">
      <thead><tr><th>场景</th><th>方法</th><th>路径</th><th>契约</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.scene)}</td><td>${escapeHtml(row.method)}</td><td>${escapeHtml(row.path)}</td><td>${escapeHtml(loaded ? (row.available ? "已声明" : "缺失") : "待刷新")}</td></tr>`).join("")}</tbody>
    </table>`;
  qs("#openapi-code").textContent = [
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/openapi.json"`,
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/v1/access/applications"`,
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/v1/models"`,
    `curl -H "X-Tenant-ID: ${state.tenantId}" "${baseUrl}/v1/thresholds"`,
  ].join("\n");
  renderPayload("openapi-docs", "#openapi-json", payload || {
    tenant_id: state.tenantId,
    openapi_url: `${baseUrl}/openapi.json`,
    docs_url: `${baseUrl}/docs`,
    redoc_url: `${baseUrl}/redoc`,
    core_paths: rows,
  });
}

async function refreshOpenApiDocs() {
  const baseUrl = window.location.origin;
  const payload = {
    tenant_id: state.tenantId,
    openapi_url: `${baseUrl}/openapi.json`,
    docs_url: `${baseUrl}/docs`,
    redoc_url: `${baseUrl}/redoc`,
    schema: null,
  };
  try {
    const schema = await api("/openapi.json");
    payload.schema = schema;
    payload.title = schema.info?.title || "PortraitHub API";
    payload.version = schema.info?.version || "--";
    payload.path_count = Object.keys(schema.paths || {}).length;
  } catch (error) {
    payload.error = error.message || String(error);
    payload.note = "开放接口定义可能在生产环境被关闭；受控环境可启用 ENABLE_API_DOCS 后刷新。";
  }
  state.openApiCache = payload;
  renderOpenApiDocs(payload);
}

async function refreshWebhooks() {
  try {
    const payload = await api("/v1/access/webhooks");
    state.webhooks = (payload.webhooks || []).map(normalizeWebhook);
    saveWebhooks();
  } catch (error) {
    renderPayload("webhooks", "#webhook-json", webhookPayload({ warning: error.message || String(error) }));
  }
  renderWebhooks();
}

function selectedWebhook() {
  const id = qs("#webhook-id-input")?.value.trim();
  return state.webhooks.find((item) => normalizeWebhook(item).id === id) || state.webhooks[0] || null;
}

function populateWebhookAppOptions(selectedId = "") {
  const options = state.accessApplications.map((app) => {
    const normalized = normalizeAccessApplication(app);
    return `<option value="${escapeHtml(normalized.id)}" ${normalized.id === selectedId ? "selected" : ""}>${escapeHtml(normalized.name || normalized.id)}</option>`;
  }).join("");
  qs("#webhook-app-input").innerHTML = options || `<option value="default-client">默认接入应用</option>`;
}

function fillWebhookForm(webhook) {
  if (!webhook) return;
  const normalized = normalizeWebhook(webhook);
  populateWebhookAppOptions(normalized.application_id || state.accessApplications[0]?.id || "default-client");
  qs("#webhook-id-input").value = normalized.id || "";
  qs("#webhook-name-input").value = normalized.name || "";
  qs("#webhook-url-input").value = normalized.url || "";
  qs("#webhook-status-input").value = normalized.status || "disabled";
  qs("#webhook-retry-input").value = normalized.retry_limit ?? 3;
  qs("#webhook-timeout-input").value = normalized.timeout_seconds ?? 5;
  checkedValues("webhook-event", normalized.events || []);
}

function webhookPayload(extra = {}) {
  return {
    tenant_id: state.tenantId,
    last_secret_preview: state.webhookLastSecret ? maskToken(state.webhookLastSecret.secret) : null,
    webhooks: state.webhooks,
    ...extra,
  };
}

function renderWebhooks() {
  state.webhooks = state.webhooks.map(normalizeWebhook);
  populateWebhookAppOptions(selectedWebhook()?.application_id || state.accessApplications[0]?.id || "default-client");
  if (!qs("#webhook-id-input").value && state.webhooks[0]) fillWebhookForm(state.webhooks[0]);
  const enabledCount = state.webhooks.filter((item) => item.status !== "disabled").length;
  const eventCount = new Set(state.webhooks.flatMap((item) => item.events || [])).size;
  renderSummary("#webhook-summary", [
    { label: "端点数", value: state.webhooks.length },
    { label: "启用", value: enabledCount },
    { label: "事件", value: eventCount },
    { label: "租户", value: state.tenantId },
  ]);
  qs("#webhook-list").innerHTML = state.webhooks.length ? `
    <table class="data-table">
      <thead><tr><th>端点</th><th>应用</th><th>事件</th><th>状态</th><th>操作</th></tr></thead>
      <tbody>${state.webhooks.map((webhook) => `
        <tr>
          <td><strong>${escapeHtml(webhook.name || webhook.id)}</strong><br><small>${escapeHtml(webhook.url || "未配置 URL")}</small></td>
          <td>${escapeHtml(webhook.application_id || "--")}</td>
          <td>${escapeHtml((webhook.events || []).join(", ") || "--")}</td>
          <td>${escapeHtml(localizeValue(webhook.status || "disabled"))}</td>
          <td>
            <button type="button" class="small" data-webhook-edit="${escapeHtml(webhook.id)}">选择</button>
            <button type="button" class="small" data-webhook-rotate="${escapeHtml(webhook.id)}">轮换</button>
            <button type="button" class="small" data-webhook-sample="${escapeHtml(webhook.id)}">样例</button>
            <button type="button" class="small" data-webhook-toggle="${escapeHtml(webhook.id)}">${webhook.status === "disabled" ? "启用" : "禁用"}</button>
          </td>
        </tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无 事件回调端点</div>`;
  renderPayload("webhooks", "#webhook-json", webhookPayload());
}

async function saveWebhook(event) {
  event.preventDefault();
  const id = qs("#webhook-id-input").value.trim() || `wh_${Date.now()}`;
  const payload = {
    webhook_id: id,
    name: qs("#webhook-name-input").value.trim() || id,
    application_id: qs("#webhook-app-input").value || state.accessApplications[0]?.id || "default-client",
    url: qs("#webhook-url-input").value.trim() || null,
    status: qs("#webhook-status-input").value || "disabled",
    events: selectedCheckboxValues("webhook-event"),
    retry_limit: Number(qs("#webhook-retry-input").value || 0),
    timeout_seconds: Number(qs("#webhook-timeout-input").value || 5),
  };
  let data;
  const existing = state.webhooks.findIndex((item) => normalizeWebhook(item).id === id);
  if (existing >= 0) {
    try {
      data = await api(`/v1/access/webhooks/${encodeURIComponent(id)}`, { method: "PATCH", json: payload });
    } catch (error) {
      data = await api("/v1/access/webhooks", { method: "POST", json: payload });
    }
  } else {
    data = await api("/v1/access/webhooks", { method: "POST", json: payload });
  }
  const webhook = normalizeWebhook(data.webhook || payload);
  const found = state.webhooks.findIndex((item) => normalizeWebhook(item).id === webhook.id);
  if (found >= 0) state.webhooks[found] = webhook;
  else state.webhooks.push(webhook);
  if (data.one_time_secret) state.webhookLastSecret = { webhook_id: webhook.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveWebhooks();
  qs("#webhook-id-input").value = webhook.id;
  renderWebhooks();
  renderPayload("webhooks", "#webhook-json", data.one_time_secret ? webhookPayload({ one_time_secret: state.webhookLastSecret }) : webhookPayload());
}

async function rotateWebhookSecret(id = null) {
  const webhookId = id || qs("#webhook-id-input").value.trim();
  const webhook = state.webhooks.find((item) => normalizeWebhook(item).id === webhookId) || selectedWebhook();
  if (!webhook) throw new Error("请先选择事件回调");
  const data = await api(`/v1/access/webhooks/${encodeURIComponent(normalizeWebhook(webhook).id)}/rotate`, { method: "POST" });
  const updated = normalizeWebhook(data.webhook || webhook);
  const found = state.webhooks.findIndex((item) => normalizeWebhook(item).id === updated.id);
  if (found >= 0) state.webhooks[found] = updated;
  state.webhookLastSecret = { webhook_id: updated.id, secret: data.one_time_secret, generated_at: Date.now() };
  saveWebhooks();
  renderWebhooks();
  renderPayload("webhooks", "#webhook-json", webhookPayload({ one_time_secret: state.webhookLastSecret }));
}

async function toggleWebhook(id) {
  const webhook = state.webhooks.find((item) => normalizeWebhook(item).id === id);
  if (!webhook) return;
  const nextStatus = webhook.status === "disabled" ? "active" : "disabled";
  const data = await api(`/v1/access/webhooks/${encodeURIComponent(normalizeWebhook(webhook).id)}`, { method: "PATCH", json: { status: nextStatus } });
  const updated = normalizeWebhook(data.webhook || { ...webhook, status: nextStatus });
  const found = state.webhooks.findIndex((item) => normalizeWebhook(item).id === updated.id);
  if (found >= 0) state.webhooks[found] = updated;
  saveWebhooks();
  renderWebhooks();
}

async function renderWebhookSample(id = null) {
  const webhook = state.webhooks.find((item) => normalizeWebhook(item).id === id) || selectedWebhook();
  if (!webhook) throw new Error("请先选择事件回调");
  const data = await api(`/v1/access/webhooks/${encodeURIComponent(normalizeWebhook(webhook).id)}/sample`, { method: "POST" });
  renderPayload("webhooks", "#webhook-json", webhookPayload(data));
}

function renderSdkExamples() {
  const baseUrl = window.location.origin;
  const app = selectedAccessApp() || state.accessApplications[0] || {};
  const python = `import os\nfrom pathlib import Path\nfrom sdk.python.portrait_hub_client import PortraitHubClient\n\nclient = PortraitHubClient(\n    base_url="${baseUrl}",\n    tenant_id="${state.tenantId}",\n    api_token=os.getenv("PORTRAIT_HUB_API_TOKEN"),\n    auth_scheme="api_key",\n)\nresult = client.search(Path("query.jpg"), modality="body", top_k=5, threshold_profile="normal")\nprint(result["request_id"], result.get("data", {}).get("candidate_count"))`;
  const nodeSnippet = `const { PortraitHubClient } = require("./sdk/node/portraitHubClient");\n\nconst client = new PortraitHubClient({\n  baseUrl: "${baseUrl}",\n  tenantId: "${state.tenantId}",\n  apiToken: process.env.PORTRAIT_HUB_API_TOKEN,\n  authScheme: "api_key",\n});\n\nconst result = await client.comparePersons("a.jpg", "b.jpg", "normal");\nconsole.log(result.request_id, result.data?.passed);`;
  const curl = requestSnippet("/v1/gallery/search", ["file=@query.jpg", "modality=body", "top_k=5", "threshold_profile=normal"]);
  const batch = `import os\nfrom pathlib import Path\nfrom sdk.python.portrait_hub_client import PortraitHubClient\n\nclient = PortraitHubClient(\n    base_url="${baseUrl}",\n    tenant_id="${state.tenantId}",\n    api_token=os.getenv("PORTRAIT_HUB_API_TOKEN"),\n    auth_scheme="api_key",\n)\nbatch = client.search_batch(\n    [Path("query-a.jpg"), Path("query-b.jpg")],\n    modality="body",\n    top_k=10,\n    threshold_profile="normal",\n    async_mode=True,\n)\nbatch_id = batch.get("data", {}).get("batch_id")\nprint(batch["request_id"], batch_id)`;
  const video = `const { PortraitHubClient } = require("./sdk/node/portraitHubClient");\n\nconst wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));\nconst terminal = new Set(["completed", "failed", "cancelled"]);\nconst client = new PortraitHubClient({\n  baseUrl: "${baseUrl}",\n  tenantId: "${state.tenantId}",\n  apiToken: process.env.PORTRAIT_HUB_API_TOKEN,\n  authScheme: "api_key",\n});\n\nconst job = await client.createVideoJob("sample.mp4", { frameInterval: 5, maxFrames: 120 });\nconst jobId = job.data?.job?.job_id ?? job.data?.job_id;\nlet status = job;\nwhile (jobId && !terminal.has(status.data?.job?.status)) {\n  await wait(2000);\n  status = await client.getJob(jobId);\n}\nconst result = jobId ? await client.jobResult(jobId) : {};\nconsole.log(jobId, result.request_id);`;
  qs("#sdk-python-code").textContent = python;
  qs("#sdk-node-code").textContent = nodeSnippet;
  qs("#sdk-curl-code").textContent = curl;
  qs("#sdk-batch-code").textContent = batch;
  qs("#sdk-video-code").textContent = video;
  renderPayload("sdk-examples", "#sdk-json", {
    tenant_id: state.tenantId,
    selected_application: app.id || null,
    scopes: app.scopes || [],
    examples: { python, node: nodeSnippet, curl, batch, video },
  });
}

function playgroundMethod(endpoint) {
  return endpoint === "/v1/models" || endpoint === "/v1/thresholds" || endpoint === "/v1/streams/{stream_id}/events" ? "GET" : "POST";
}

function playgroundSelection() {
  const select = qs("#playground-endpoint-input");
  const option = select.options[select.selectedIndex];
  return {
    template: select.value,
    method: option?.dataset.method || playgroundMethod(select.value),
  };
}

function resolvePlaygroundPath(template, strict = false) {
  if (!template.includes("{stream_id}")) return template;
  const streamId = qs("#playground-stream-id-input").value.trim();
  if (!streamId) {
    if (strict) throw new Error("请填写流 ID");
    return template;
  }
  return template.replace("{stream_id}", encodeURIComponent(streamId));
}

function withPlaygroundLimit(path, template) {
  if (template !== "/v1/streams" && template !== "/v1/streams/{stream_id}/events") return path;
  const limit = qs("#playground-top-k-input").value || "5";
  const params = new URLSearchParams({ limit });
  return `${path}${path.includes("?") ? "&" : "?"}${params.toString()}`;
}

function appendFile(form, name, input) {
  const file = formFiles(input)[0];
  if (file) form.set(name, file);
  return file;
}

function appendFiles(form, name, input) {
  const files = formFiles(input);
  files.forEach((file) => form.append(name, file));
  return files;
}

function requirePlaygroundFiles(files, label) {
  if (!files.length) throw new Error(`请选择${label}`);
  return files;
}

function playgroundErrorCode(raw) {
  if (raw.error_code) return raw.error_code;
  const detail = raw.payload?.detail;
  if (typeof detail === "string") return detail;
  return null;
}

function renderPlaygroundRequestPreview() {
  const selection = playgroundSelection();
  const resolved = resolvePlaygroundPath(selection.template, false);
  const method = selection.method;
  const fileACount = formFiles(qs("#playground-file-a-input")).length;
  const fileBCount = formFiles(qs("#playground-file-b-input")).length;
  const lines = [
    `${method} ${method === "GET" ? withPlaygroundLimit(resolved, selection.template) : resolved}`,
    selection.template !== resolved ? `Template: ${selection.template}` : null,
    `X-Tenant-ID: ${state.tenantId}`,
    state.apiKey ? `X-API-Key: ${maskToken(state.apiKey)}` : "X-API-Key: 未配置",
    state.bearer ? `Authorization: Bearer ${maskToken(state.bearer)}` : "Authorization: 未配置",
  ].filter(Boolean);
  if (method === "POST" && selection.template === "/v1/streams") {
    lines.push("Content-Type: application/json");
    lines.push(`stream_url: ${qs("#playground-stream-url-input").value.trim() || "<required>"}`);
    lines.push(`name: ${qs("#playground-stream-name-input").value.trim() || "<optional>"}`);
  } else if (method === "POST") {
    lines.push("Content-Type: multipart/form-data");
    lines.push(`file_a_count: ${fileACount}`);
    lines.push(`file_b_count: ${fileBCount}`);
    lines.push(`threshold_profile: ${qs("#playground-threshold-input").value.trim() || "normal"}`);
    lines.push(`top_k: ${qs("#playground-top-k-input").value || "5"}`);
    lines.push(`async_mode: ${qs("#playground-async-mode-input").checked ? "true" : "false"}`);
  }
  lines.push("controlled_use: dev_or_approved_intranet; server_call_logs_audit=true");
  qs("#playground-request-code").textContent = lines.join("\n");
}

function buildPlaygroundForm(endpoint) {
  const form = new FormData();
  const fileAInput = qs("#playground-file-a-input");
  const fileBInput = qs("#playground-file-b-input");
  const thresholdProfile = qs("#playground-threshold-input").value.trim() || "normal";
  const topK = qs("#playground-top-k-input").value || "5";
  const asyncMode = qs("#playground-async-mode-input").checked ? "true" : "false";

  if (endpoint === "/v1/gallery/search") {
    requirePlaygroundFiles([appendFile(form, "file", fileAInput)].filter(Boolean), "查询图片");
    form.set("modality", "body");
    form.set("top_k", topK);
    form.set("threshold_profile", thresholdProfile);
  } else if (endpoint === "/v1/gallery/search/batch") {
    requirePlaygroundFiles(appendFiles(form, "files", fileAInput), "批量查询图片");
    form.set("modality", "body");
    form.set("top_k", topK);
    form.set("threshold_profile", thresholdProfile);
    form.set("async_mode", asyncMode);
  } else if (endpoint === "/v1/compare/persons" || endpoint === "/v1/fusion/compare") {
    requirePlaygroundFiles([appendFile(form, "image_a", fileAInput)].filter(Boolean), "文件 A");
    requirePlaygroundFiles([appendFile(form, "image_b", fileBInput)].filter(Boolean), "文件 B");
    form.set("threshold_profile", thresholdProfile);
    if (endpoint === "/v1/fusion/compare") form.set("modalities", "face,body,appearance");
  } else if (endpoint === "/v1/compare/batch") {
    const filesA = appendFiles(form, "image_a", fileAInput);
    const filesB = appendFiles(form, "image_b", fileBInput);
    requirePlaygroundFiles(filesA, "批量文件 A");
    requirePlaygroundFiles(filesB, "批量文件 B");
    if (filesA.length !== filesB.length) throw new Error("批量比对的文件 A/B 数量需要一致");
    form.set("modality", "body");
    form.set("threshold_profile", thresholdProfile);
    form.set("async_mode", asyncMode);
  } else if (endpoint === "/v1/infer/persons") {
    requirePlaygroundFiles(appendFiles(form, "files", fileAInput), "解析图片");
  } else if (endpoint === "/v1/jobs/video") {
    requirePlaygroundFiles([appendFile(form, "file", fileAInput)].filter(Boolean), "视频文件");
  }
  return form;
}

async function submitPlayground(event) {
  event.preventDefault();
  const selection = playgroundSelection();
  const method = selection.method;
  const endpoint = resolvePlaygroundPath(selection.template, true);
  const started = performance.now();
  try {
    let raw;
    if (method === "GET") {
      raw = await apiRaw(withPlaygroundLimit(endpoint, selection.template));
    } else if (selection.template === "/v1/streams") {
      const streamUrl = qs("#playground-stream-url-input").value.trim();
      if (!streamUrl) throw new Error("请输入流地址");
      raw = await apiRaw(endpoint, {
        method: "POST",
        json: {
          stream_url: streamUrl,
          name: qs("#playground-stream-name-input").value.trim() || null,
          settings: {},
          metadata: { source: "api_playground" },
        },
      });
    } else {
      raw = await apiRaw(endpoint, { method: "POST", body: buildPlaygroundForm(selection.template) });
    }
    const latency = Math.round(performance.now() - started);
    const errorCode = playgroundErrorCode(raw);
    renderSummary("#playground-summary", [
      { label: "状态", value: raw.ok ? "成功" : "异常" },
      { label: "HTTP", value: raw.status_code },
      { label: "耗时", value: `${latency}ms` },
      { label: "请求 ID", value: raw.request_id || "--" },
      { label: "错误码", value: errorCode || "--" },
      { label: "接口", value: endpoint },
    ]);
    renderPayload("api-playground", "#playground-json", {
      endpoint,
      endpoint_template: selection.template,
      method,
      http_status: raw.status_code,
      latency_ms: latency,
      request_id: raw.request_id,
      error_code: errorCode,
      controlled_use: "dev_or_approved_intranet",
      response: raw.payload,
    });
    if (!raw.ok) setStatus(errorCode || raw.status_text || "接口调试请求失败", true);
  } catch (error) {
    const latency = Math.round(performance.now() - started);
    renderSummary("#playground-summary", [
      { label: "状态", value: "异常" },
      { label: "HTTP", value: "--" },
      { label: "耗时", value: `${latency}ms` },
      { label: "请求 ID", value: "--" },
      { label: "错误码", value: "client_error" },
      { label: "接口", value: selection.template },
    ]);
    renderPayload("api-playground", "#playground-json", {
      endpoint: selection.template,
      endpoint_template: selection.template,
      method,
      latency_ms: latency,
      error_code: "client_error",
      error: error.message || String(error),
    });
    throw error;
  }
}

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

function selectedMultimodalScopes() {
  const scopes = selectedCheckboxValues("multimodal-scope");
  return scopes.length ? scopes : ["body"];
}

function renderMultimodalDetails(payload) {
  const data = payloadData(payload) || {};
  const modalities = data.modalities || {};
  const rows = Object.entries(modalities).map(([name, item]) => ({ name, ...item }));
  qs("#multimodal-table").innerHTML = rows.length ? `
    <table class="data-table">
      <thead><tr><th>模态</th><th>参与</th><th>原始分数</th><th>质量</th><th>权重</th><th>原因</th></tr></thead>
      <tbody>${rows.map((row) => `
        <tr><td>${escapeHtml(localizeValue(row.name))}</td><td>${row.used ? "是" : "否"}</td><td>${escapeHtml(formatNumber(row.score, 4))}</td><td>${escapeHtml(formatNumber(row.quality, 4))}</td><td>${escapeHtml(formatNumber(row.weight, 2))}</td><td>${escapeHtml(row.reason || "--")}</td></tr>`).join("")}</tbody>
    </table>` : `<div class="data-empty">暂无模态明细</div>`;
}

async function submitMultimodalCompare(event) {
  event.preventDefault();
  const filesA = formFiles(qs("#multimodal-a-input"));
  const filesB = formFiles(qs("#multimodal-b-input"));
  if (!filesA.length || !filesB.length) throw new Error("请选择左右两侧证据图片");
  const form = new FormData();
  form.set("image_a", filesA[0]);
  form.set("image_b", filesB[0]);
  form.set("threshold_profile", qs("#multimodal-threshold-input").value.trim() || "normal");
  form.set("modalities", selectedMultimodalScopes().join(","));
  const payload = await api("/v1/fusion/compare", { method: "POST", body: form });
  const data = payloadData(payload) || {};
  renderSummary("#multimodal-summary", [
    { label: "结论", value: data.passed === undefined ? "--" : data.passed ? "通过" : "未通过" },
    { label: "融合分", value: formatNumber(data.final_score, 4) },
    { label: "阈值", value: formatNumber(data.threshold, 4) },
    { label: "风险", value: data.decision?.risk || "--" },
  ]);
  renderMultimodalDetails(payload);
  renderPayload("multimodal-compare", "#multimodal-json", payload);
}

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
    api("/rollout/aliases").catch((error) => ({ error: error.message || String(error), aliases: [] })),
    api("/v1/models"),
    api("/rollout/audit?limit=20").catch((error) => ({ error: error.message || String(error), records: [], count: 0, malformed_count: 0 })),
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
    payload = await api(`/rollout/aliases/preview?alias_name=${encodeURIComponent(aliasName)}&traffic_key=${key}`);
  } else if (action === "switch") {
    if (!target) throw new Error("请输入目标模型");
    payload = await api("/rollout/aliases/switch", { method: "POST", json: { alias_name: aliasName, target_model_id: target, expected_current_target: expected || null, dry_run: dryRun } });
  } else if (action === "weighted") {
    if (!target) throw new Error("请输入目标模型");
    payload = await api("/rollout/aliases/weighted", { method: "POST", json: { alias_name: aliasName, targets: [{ target_model_id: target, weight: Number(qs("#release-weight-input").value || 0), status: "candidate" }], expected_current_target: expected || null, dry_run: dryRun } });
  } else {
    payload = await api("/rollout/aliases/rollback", { method: "POST", json: { alias_name: aliasName, dry_run: dryRun } });
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
function setAlertInputs() {
  qs("#alert-error-rate-input").value = state.alertConfig.maxErrorRate;
  qs("#alert-p95-input").value = state.alertConfig.maxP95Latency;
  qs("#alert-gpu-free-input").value = state.alertConfig.minFreeGpuMemoryGb;
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
  const gpuFreeBytes = metricSum(metrics, "gpu_worker_gpu_memory_free_bytes");
  const gpuUsedBytes = metricSum(metrics, "gpu_worker_gpu_memory_used_bytes");
  const gpuDeviceQueues = metricRows(metrics, "gpu_worker_gpu_device_queue_depth").map((item) => ({
    device: item.labels.device || "default",
    depth: Number(item.value || 0),
  }));
  const summary = {
    status,
    totals: exportPayload.pagination || {},
    raw_metrics: metrics,
    metrics: {
      requests,
      errors,
      error_rate: requests > 0 ? errors / requests : 0,
      inference_p95_seconds: histogramP95(metrics, "gpu_worker_inference_seconds"),
      inference_p99_seconds: histogramQuantile(metrics, "gpu_worker_inference_seconds", 0.99),
      queue_p95_seconds: histogramQuantile(metrics, "gpu_worker_queue_seconds", 0.95),
      queue_p99_seconds: histogramQuantile(metrics, "gpu_worker_queue_seconds", 0.99),
      gpu_queue_depth: metricValue(metrics, "gpu_worker_gpu_queue_depth"),
      gpu_device_queue_depths: gpuDeviceQueues,
      gpu_device_queue_max: metricMax(metrics, "gpu_worker_gpu_device_queue_depth"),
      gpu_free_gb: gpuFreeBytes ? gpuFreeBytes / (1024 ** 3) : null,
      gpu_used_gb: gpuUsedBytes ? gpuUsedBytes / (1024 ** 3) : null,
      stream_active_sessions_metric: metricValue(metrics, "gpu_worker_stream_active_sessions"),
      loaded_models_metric: metricValue(metrics, "gpu_worker_loaded_models"),
    },
  };
  state.dashboard = summary;
  renderDashboard(summary);
  renderAlerts();
  renderPayload("dashboard", "#dashboard-json", summary);
}

async function refreshModels() {
  const payload = await api("/v1/models");
  renderSummary("#models-summary", [
    { label: "模型数", value: payload.count ?? 0 },
    { label: "已加载", value: (payload.loaded_models || []).length },
    { label: "别名数", value: Object.keys(payload.aliases || {}).length },
    { label: "配置", value: payload.config_loaded ? "已加载" : "异常" },
  ]);
  renderPayload("models", "#models-json", payload);
}

async function refreshGallery() {
  const payload = await api("/v1/admin/export?people_limit=50&jobs_limit=0&streams_limit=0");
  state.galleryExport = payload;
  renderGalleryVisuals(payload);
  renderGallerySummary(payload);
  renderPayload("gallery", "#gallery-json", payload);

  // 更新或清空特征图片列表
  const currentId = qs("#person-id-input").value.trim();
  if (currentId && Array.isArray(payload.people)) {
    const person = payload.people.find((p) => p.person_id === currentId);
    if (person) {
      renderPersonFeatures(person);
    } else {
      renderPersonFeatures(null);
    }
  } else {
    renderPersonFeatures(null);
  }
}

async function refreshStreams() {
  const payload = await api("/v1/streams?limit=50");
  renderSummary("#streams-summary", [
    { label: "视频流解析", value: payload.total ?? (payload.streams || []).length },
    { label: "本页数量", value: payload.count ?? (payload.streams || []).length },
    { label: "下一页", value: payload.next_cursor ? "有" : "无" },
    { label: "租户", value: state.tenantId },
  ]);
  renderPayload("streams", "#streams-json", payload);
}

async function refreshAdmin() {
  const [status, thresholds] = await Promise.all([api("/v1/admin/status"), api("/v1/thresholds")]);
  renderPayload("admin-threshold", "#admin-threshold-json", { status, thresholds });
}

async function refreshAll() {
  await Promise.allSettled([refreshDashboard(), refreshModels(), refreshGallery(), refreshStreams(), refreshAdmin(), refreshAdminData(), refreshAnalysisResults(), refreshTrackReview(), refreshEvaluationCenter(), refreshReleaseCenter(), refreshAuditCompliance()]);
}

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

function videoResultsVisualInfo(payload) {
  const data = payloadData(payload);
  const results = Array.isArray(data.results) ? data.results : [];
  const visuals = [];
  results.forEach((entry) => {
    const job = entry.job || {};
    const result = entry.result || {};
    const frames = Array.isArray(result.frames) ? result.frames : [];
    const jobLabel = job.job_id || "视频任务";
    frames.forEach((frame, index) => {
      const visual = videoFrameVisual(frame, index, jobLabel);
      if (visual) visuals.push(visual);
    });
  });
  return { data, results, visuals };
}

function renderVideoResults(payload) {
  const info = videoResultsVisualInfo(payload);
  state.analysisResults.video = payload;
  renderSummary("#video-results-summary", [
    { label: "任务数", value: info.results.length },
    { label: "图片数", value: info.visuals.length },
    { label: "租户", value: state.tenantId || "--" },
  ]);
  renderVideoVisualGrid("#video-results-visuals", info.visuals, "暂无已完成的视频解析图片", {
    variant: "video",
    maxWidth: 260,
    maxHeight: 180,
  });
  renderPayload("video-results", "#video-results-json", payload);
}

async function refreshVideoResults() {
  const payload = await api("/v1/jobs/video/results?limit=48");
  renderVideoResults(payload);
  return payload;
}

function visionModeLabel(mode) {
  return localizeValue(mode || "image") || "图片解析";
}

function addImageAnalysisResult(mode, endpoint, payload, previews) {
  const visuals = visionVisualEntries(payload, previews).map((entry) => ({
    ...entry,
    item: {
      ...entry.item,
      label: `图片 / ${visionModeLabel(mode)} / ${entry.item?.label || `第${(entry.frameIndex ?? 0) + 1}帧`}`,
      name: entry.item?.name || entry.item?.label || "图片解析结果",
    },
  }));
  state.analysisResults.image.unshift({
    id: payload?.request_id || payload?.data?.request_id || `image_${Date.now()}`,
    created_at: Date.now(),
    mode,
    mode_label: visionModeLabel(mode),
    endpoint,
    payload,
    visual_count: visuals.length,
    frame_count: visuals.length,
    visuals,
  });
  state.analysisResults.image = state.analysisResults.image.slice(0, 12);
  if (state.view === "video-results" || state.analysisResultsTab === "image") renderImageResults();
}

function renderImageResults() {
  const records = state.analysisResults.image;
  const visuals = records.flatMap((record) => record.visuals || []);
  const latest = records[0];
  renderSummary("#image-results-summary", [
    { label: "结果批次", value: records.length },
    { label: "图片数", value: visuals.length },
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
}

function streamEventPayloads(results) {
  return results.map((result) => (result.status === "fulfilled" ? result.value : { events: [], error: String(result.reason || "events unavailable") }));
}

function latestStreamEvent(events) {
  return [...events].sort((left, right) => Number(right.created_at || 0) - Number(left.created_at || 0))[0] || null;
}

function renderStreamResults(payload) {
  state.analysisResults.stream = payload;
  const streams = Array.isArray(payload.streams) ? payload.streams : [];
  const eventPayloads = Array.isArray(payload.event_payloads) ? payload.event_payloads : [];
  const events = eventPayloads.flatMap((item) => Array.isArray(item.events) ? item.events : []);
  const sessions = Array.isArray(payload.stream_worker?.sessions) ? payload.stream_worker.sessions : [];
  const runningCount = streams.filter((stream) => stream.status === "running").length;
  renderSummary("#stream-results-summary", [
    { label: "视频流数", value: payload.total ?? streams.length },
    { label: "运行中", value: runningCount },
    { label: "最近事件", value: events.length },
    { label: "活跃会话", value: payload.stream_worker?.active_sessions ?? sessions.length ?? 0 },
  ]);
  const list = qs("#stream-results-list");
  if (list) {
    if (!streams.length) {
      list.innerHTML = `<div class="result-empty">暂无视频流解析结果，请先注册并启动视频流解析</div>`;
    } else {
      const eventMap = new Map(eventPayloads.map((item) => [item.stream_id, Array.isArray(item.events) ? item.events : []]));
      const sessionMap = new Map(sessions.map((session) => [session.stream_id, session]));
      list.innerHTML = streams.slice(0, 24).map((stream) => {
        const streamEvents = eventMap.get(stream.stream_id) || [];
        const latest = latestStreamEvent(streamEvents);
        const session = sessionMap.get(stream.stream_id) || {};
        const title = stream.name || stream.stream_id;
        return `
          <article class="stream-result-card">
            <div class="stream-result-head">
              <strong title="${escapeHtml(stream.stream_id)}">视频流 / ${escapeHtml(title)}</strong>
              <span class="badge ${stream.status === "running" ? "ok" : stream.status === "failed" ? "danger" : ""}">${escapeHtml(localizeValue(stream.status || "--"))}</span>
            </div>
            <div class="stream-result-meta">
              <span>流地址：${escapeHtml(stream.stream_url || "--")}</span>
              <span>处理帧：${escapeHtml(session.frames_processed ?? "--")}</span>
              <span>最近事件：${escapeHtml(latest?.type || "--")}</span>
              <span>更新时间：${escapeHtml(stream.updated_at ? formatDateTime(stream.updated_at) : "--")}</span>
            </div>
          </article>`;
      }).join("");
    }
  }
  renderPayload("stream-results", "#stream-results-json", payload);
}

async function refreshStreamResults() {
  const streamsPayload = await api("/v1/streams?limit=50");
  const streams = Array.isArray(streamsPayload.streams) ? streamsPayload.streams : [];
  const [statusResult, eventResults] = await Promise.all([
    api("/v1/admin/status").catch(() => ({})),
    Promise.allSettled(streams.slice(0, 24).map((stream) => api(`/v1/streams/${encodeURIComponent(stream.stream_id)}/events?limit=5`))),
  ]);
  const eventPayloads = streamEventPayloads(eventResults).map((item, index) => ({
    stream_id: item.stream_id || streams[index]?.stream_id,
    events: Array.isArray(item.events) ? item.events : [],
    error: item.error,
  }));
  const payload = {
    ...streamsPayload,
    streams,
    stream_worker: statusResult.stream_worker || {},
    event_payloads: eventPayloads,
  };
  renderStreamResults(payload);
  return payload;
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
    renderStreamResults(state.analysisResults.stream || { streams: [], stream_worker: {}, event_payloads: [] });
  }
}

async function refreshAnalysisResults() {
  renderImageResults();
  await Promise.allSettled([refreshVideoResults(), refreshStreamResults()]);
  renderAnalysisResultsTab(state.analysisResultsTab);
}

async function refreshActiveAnalysisResults() {
  if (state.analysisResultsTab === "video") return refreshVideoResults();
  if (state.analysisResultsTab === "stream") return refreshStreamResults();
  renderImageResults();
  return state.analysisResults.image;
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
    form.set("include_vectors", qs("#vision-include-embeddings-input").checked ? "true" : "false");
  } else if (["detect", "tracks"].includes(mode)) {
    form.set("confidence", qs("#vision-confidence-input").value);
    form.set("iou", qs("#vision-iou-input").value);
    form.set("max_detections", qs("#vision-max-detections-input").value);
    if (mode === "tracks") form.set("include_embeddings", qs("#vision-include-embeddings-input").checked ? "true" : "false");
  }
  const payload = await api(endpoint, { method: "POST", body: form });
  renderVisionSummary(payload);
  renderVisionVisuals(payload, state.visionPreviews);
  renderPayload("vision", "#vision-json", payload);
  addImageAnalysisResult(mode, endpoint, payload, state.visionPreviews);
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

function handleLogin(event) {
  if (event) event.preventDefault();
  state.tenantId = qs("#tenant-input").value.trim() || "default";
  state.apiKey = qs("#api-key-input").value.trim();
  state.bearer = qs("#bearer-input").value.trim();
  state.isLoggedIn = true;
  localStorage.setItem("portraitHubTenant", state.tenantId);
  localStorage.setItem("portraitHubApiKey", state.apiKey);
  localStorage.setItem("portraitHubBearer", state.bearer);
  localStorage.setItem("portraitHubLoggedIn", "true");
  closeSocket("job");
  closeSocket("stream");
  renderIntegrationSnippet();
  updateSnippetButtons();
  updateAuthView();
}

function handleLogout() {
  state.isLoggedIn = false;
  localStorage.setItem("portraitHubLoggedIn", "false");
  closeSocket("job");
  closeSocket("stream");
  updateAuthView();
}

function updateAuthView() {
  if (state.isLoggedIn) {
    qs("#login-view").classList.add("hidden");
    qs("#console-view").classList.remove("hidden");
    qs("#current-tenant-display").textContent = state.tenantId;
    wrapHandler(refreshAll)();
  } else {
    qs("#login-view").classList.remove("hidden");
    qs("#console-view").classList.add("hidden");
    qs("#tenant-input").value = state.tenantId;
    qs("#api-key-input").value = state.apiKey;
    qs("#bearer-input").value = state.bearer;
  }
}

function setupEvents() {
  qsa("[data-nav]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.nav)));
  qsa("[data-nav-shortcut]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.navShortcut)));
  qs("#login-form").addEventListener("submit", handleLogin);
  qs("#logout-button").addEventListener("click", handleLogout);
  qs("#refresh-button").addEventListener("click", wrapHandler(refreshAll));
  qs("#dashboard-refresh-button").addEventListener("click", wrapHandler(refreshDashboard));
  qs("#models-refresh-button").addEventListener("click", wrapHandler(refreshModels));
  qs("#gallery-refresh-button").addEventListener("click", wrapHandler(refreshGallery));
  qs("#streams-refresh-button").addEventListener("click", wrapHandler(refreshStreams));
  qs("#admin-refresh-button").addEventListener("click", wrapHandler(refreshAdmin));
  qs("#backup-snapshot-refresh-button").addEventListener("click", wrapHandler(refreshAdminData));
  qs("#alerts-refresh-button").addEventListener("click", wrapHandler(async () => {
    await refreshDashboard();
    renderAlerts();
  }));

  qs("#access-refresh-button").addEventListener("click", wrapHandler(refreshAccessApplications));
  qs("#access-app-form").addEventListener("submit", wrapHandler(saveAccessApp));
  qs("#access-rotate-button").addEventListener("click", wrapHandler(() => rotateAccessApp()));
  qs("#access-app-list").addEventListener("click", wrapHandler((event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-access-edit], [data-access-rotate], [data-access-toggle]") : null;
    if (!target) return;
    const editId = target.dataset.accessEdit;
    const rotateId = target.dataset.accessRotate;
    const toggleId = target.dataset.accessToggle;
    if (editId) fillAccessAppForm(state.accessApplications.find((item) => item.id === editId));
    if (rotateId) rotateAccessApp(rotateId);
    if (toggleId) toggleAccessApp(toggleId);
  }));
  qs("#sdk-refresh-button").addEventListener("click", renderSdkExamples);
  qs("#sdk-python-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-python-code").textContent, "Python 代码示例已复制")));
  qs("#sdk-node-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-node-code").textContent, "Node.js 代码示例已复制")));
  qs("#sdk-curl-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-curl-code").textContent, "curl 命令示例已复制")));
  qs("#sdk-batch-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-batch-code").textContent, "批量示例已复制")));
  qs("#sdk-video-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#sdk-video-code").textContent, "视频示例已复制")));
  qs("#openapi-refresh-button").addEventListener("click", wrapHandler(refreshOpenApiDocs));
  qs("#openapi-copy-button").addEventListener("click", wrapHandler(() => copyText(qs("#openapi-code").textContent, "开放接口定义检查命令已复制")));
  qs("#playground-form").addEventListener("submit", wrapHandler(submitPlayground));
  qs("#playground-endpoint-input").addEventListener("change", renderPlaygroundRequestPreview);
  [
    "#playground-file-a-input",
    "#playground-file-b-input",
    "#playground-threshold-input",
    "#playground-top-k-input",
    "#playground-stream-id-input",
    "#playground-stream-url-input",
    "#playground-stream-name-input",
    "#playground-async-mode-input",
  ].forEach((selector) => {
    const element = qs(selector);
    element.addEventListener(element.type === "file" || element.type === "checkbox" ? "change" : "input", renderPlaygroundRequestPreview);
  });
  qs("#call-logs-refresh-button").addEventListener("click", wrapHandler(refreshCallLogs));
  qs("#call-log-filter-button").addEventListener("click", wrapHandler(refreshCallLogs));
  qs("#error-codes-refresh-button").addEventListener("click", wrapHandler(refreshErrorCodes));
  qs("#webhook-refresh-button").addEventListener("click", wrapHandler(refreshWebhooks));
  qs("#webhook-form").addEventListener("submit", wrapHandler(saveWebhook));
  qs("#webhook-rotate-button").addEventListener("click", wrapHandler(() => rotateWebhookSecret()));
  qs("#webhook-sample-button").addEventListener("click", wrapHandler(() => renderWebhookSample()));
  qs("#webhook-list").addEventListener("click", wrapHandler((event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-webhook-edit], [data-webhook-rotate], [data-webhook-toggle], [data-webhook-sample]") : null;
    if (!target) return;
    const editId = target.dataset.webhookEdit;
    const rotateId = target.dataset.webhookRotate;
    const toggleId = target.dataset.webhookToggle;
    const sampleId = target.dataset.webhookSample;
    if (editId) fillWebhookForm(state.webhooks.find((item) => item.id === editId));
    if (rotateId) rotateWebhookSecret(rotateId);
    if (toggleId) toggleWebhook(toggleId);
    if (sampleId) renderWebhookSample(sampleId);
  }));
  qs("#slo-refresh-button").addEventListener("click", wrapHandler(refreshSloPanel));
  qs("#multimodal-form").addEventListener("submit", wrapHandler(submitMultimodalCompare));
  qs("#track-review-refresh-button").addEventListener("click", wrapHandler(refreshTrackReview));
  qs("#track-review-annotation-form").addEventListener("submit", wrapHandler(submitTrackReviewAnnotation));
  qs("#evaluation-refresh-button").addEventListener("click", wrapHandler(refreshEvaluationCenter));
  qs("#release-refresh-button").addEventListener("click", wrapHandler(refreshReleaseCenter));
  qs("#release-form").addEventListener("submit", wrapHandler(submitReleaseAction));
  qs("#audit-refresh-button").addEventListener("click", wrapHandler(refreshAuditCompliance));
  qs("#audit-event-filter-button").addEventListener("click", wrapHandler(refreshAuditCompliance));
  qs("#vision-form").addEventListener("submit", wrapHandler(submitVision));
  qs("#compare-form").addEventListener("submit", wrapHandler(submitCompare));
  qs("#enroll-form").addEventListener("submit", wrapHandler(submitGalleryEnroll));
  qs("#search-form").addEventListener("submit", wrapHandler(submitGallerySearch));
  qs("#video-form").addEventListener("submit", wrapHandler(submitVideoJob));
  qs("#stream-form").addEventListener("submit", wrapHandler(submitStream));
  ["#vision-visuals", "#job-visuals", "#image-results-visuals", "#video-results-visuals", "#track-review-visuals"].forEach((selector) => qs(selector).addEventListener("click", (event) => {
    const trigger = event.target instanceof Element ? event.target.closest("[data-result-visual-index]") : null;
    if (!trigger) return;
    const index = Number(trigger.dataset.resultVisualIndex);
    if (Number.isFinite(index)) {
      const visuals = Array.isArray(event.currentTarget.__visuals) ? event.currentTarget.__visuals : state.visionResultVisuals;
      state.visionResultVisuals = visuals;
      openVisionLightbox(index);
    }
  }));
  qs("#vision-lightbox").addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-lightbox-close]") : null;
    if (target) closeVisionLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.visionLightboxIndex !== null) closeVisionLightbox();
  });

  qs("#vision-files-input").addEventListener("change", wrapHandler(() => renderPreviews(qs("#vision-files-input"), "#vision-preview")));
  qs("#compare-a-input").addEventListener("change", wrapHandler(renderComparePreviews));
  qs("#compare-b-input").addEventListener("change", wrapHandler(renderComparePreviews));
  qs("#vision-mode-input").addEventListener("change", updateSnippetButtons);
  qs("#compare-mode-input").addEventListener("change", updateSnippetButtons);

  qs("#model-detail-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型 ID");
    if (!id) return;
    renderPayload("models", "#models-json", await api(`/v1/models/${id}`));
  }));
  qs("#load-model-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型 ID");
    if (!id) return;
    renderPayload("models", "#models-json", await api(`/v1/models/${id}/load`, { method: "POST" }));
    await refreshModels();
  }));
  qs("#unload-model-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#model-id-input", "模型 ID");
    if (!id) return;
    if (!window.confirm("确认卸载该模型？正在使用的请求可能回退到冷加载。")) return;
    renderPayload("models", "#models-json", await api(`/v1/models/${id}/unload`, { method: "POST" }));
    await refreshModels();
  }));

  qs("#person-get-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#person-id-input", "人员 ID");
    if (!id) return;
    const payload = await api(`/v1/gallery/${id}`);
    renderPayload("gallery", "#gallery-json", payload);
    if (payload && payload.person) {
      renderPersonFeatures(payload.person);
    } else {
      renderPersonFeatures(null);
    }
  }));
  qs("#person-patch-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#person-id-input", "人员 ID");
    if (!id) return;
    const payload = {};
    const name = qs("#person-display-name-input").value.trim();
    if (name) payload.display_name = name;
    const metadata = qs("#person-metadata-input").value.trim();
    if (metadata) payload.metadata = parseOptionalJson("#person-metadata-input");
    renderPayload("gallery", "#gallery-json", await api(`/v1/gallery/${id}`, { method: "PATCH", json: payload }));
    await refreshGallery();
  }));
  qs("#person-delete-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#person-id-input", "人员 ID");
    if (!id) return;
    if (!window.confirm("确认删除该人员并清理相关特征、对象和向量索引？")) return;
    renderPayload("gallery", "#gallery-json", await api(`/v1/gallery/${id}`, { method: "DELETE" }));
    await refreshGallery();
  }));
  qs("#gallery-reindex-button").addEventListener("click", wrapHandler(async () => {
    const params = new URLSearchParams();
    const modality = qs("#reindex-modality-input").value;
    const modelId = qs("#reindex-model-id-input").value.trim();
    if (modality) params.set("modality", modality);
    if (modelId) params.set("model_id", modelId);
    params.set("dry_run", qs("#reindex-dry-run-input").checked ? "true" : "false");
    renderPayload("gallery", "#gallery-json", await api(`/v1/gallery/reindex?${params.toString()}`, { method: "POST" }));
  }));

  qs("#job-get-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    const payload = await api(`/v1/jobs/${id}`);
    renderJobSummary(payload);
    renderJobVisuals(payload);
    renderPayload("jobs", "#jobs-json", payload);
  }));
  qs("#job-result-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    const payload = await api(`/v1/jobs/${id}/result`);
    renderJobSummary(payload);
    renderJobVisuals(payload);
    renderPayload("jobs", "#jobs-json", payload);
  }));
  qs("#job-cancel-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    const payload = await api(`/v1/jobs/${id}/cancel`, { method: "POST" });
    renderJobSummary(payload);
    renderJobVisuals(payload);
    renderPayload("jobs", "#jobs-json", payload);
  }));
  qs("#job-watch-button").addEventListener("click", () => {
    const id = encodedInput("#job-id-input", "任务 ID");
    if (!id) return;
    watchJsonSocket("job", `/ws/jobs/${id}`, "#job-ws-status", "#jobs-json");
  });
  qs("#video-results-refresh-button").addEventListener("click", wrapHandler(refreshActiveAnalysisResults));
  qsa("[data-results-tab]").forEach((button) => button.addEventListener("click", () => {
    renderAnalysisResultsTab(button.dataset.resultsTab);
    if (state.isLoggedIn && state.view === "video-results") wrapHandler(refreshActiveAnalysisResults)();
  }));

  qs("#stream-get-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    renderPayload("streams", "#streams-json", await api(`/v1/streams/${id}`));
  }));
  qs("#stream-start-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    renderPayload("streams", "#streams-json", await api(`/v1/streams/${id}/start`, { method: "POST" }));
    await refreshStreams();
  }));
  qs("#stream-stop-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    renderPayload("streams", "#streams-json", await api(`/v1/streams/${id}/stop`, { method: "POST" }));
    await refreshStreams();
  }));
  qs("#stream-events-button").addEventListener("click", wrapHandler(async () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    renderPayload("streams", "#streams-json", await api(`/v1/streams/${id}/events`));
  }));
  qs("#stream-watch-button").addEventListener("click", () => {
    const id = encodedInput("#stream-id-input", "视频流 ID");
    if (!id) return;
    watchJsonSocket("stream", `/ws/streams/${id}`, "#stream-ws-status", "#streams-json");
  });

  qs("#threshold-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const profile = qs("#threshold-profile-input").value.trim();
    if (!profile) {
      setStatus("请输入阈值方案", true);
      return;
    }
    const payload = {};
    [
      ["body", "#threshold-body-input"],
      ["face", "#threshold-face-input"],
      ["gait", "#threshold-gait-input"],
      ["appearance", "#threshold-appearance-input"],
      ["fusion", "#threshold-fusion-input"],
    ].forEach(([key, selector]) => {
      const value = qs(selector).value;
      if (value !== "") payload[key] = Number(value);
    });
    if (!window.confirm("确认保存该阈值方案？它会影响后续比对和检索判断。")) return;
    renderPayload("admin-threshold", "#admin-threshold-json", await api(`/v1/thresholds/${encodeURIComponent(profile)}`, { method: "PUT", json: payload }));
  }));
  qs("#retention-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const result = await api("/v1/admin/retention/cleanup", {
      method: "POST",
      json: { retention_days: Number(qs("#retention-days-input").value), confirm: qs("#retention-confirm-input").value },
    });
    await refreshAdminData({ action: "retention_cleanup", result });
  }));
  qs("#backup-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const updatedSince = qs("#backup-updated-since-input").value;
    const result = await api("/v1/admin/backup", {
      method: "POST",
      json: {
        updated_since: updatedSince === "" ? null : Number(updatedSince),
        confirm: qs("#backup-confirm-input").value,
      },
    });
    await refreshAdminData({ action: "backup", result });
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
  setAlertInputs();
  renderIntegrationSnippet();
  setupEvents();
  updateSnippetButtons();
  setView(state.view);
  updateAuthView();
}

window.PortraitConsoleRuntime = { init };
