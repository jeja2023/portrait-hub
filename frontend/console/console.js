const state = {
  tenantId: localStorage.getItem("portraitHubTenant") || "default",
  apiKey: localStorage.getItem("portraitHubApiKey") || "",
  bearer: localStorage.getItem("portraitHubBearer") || "",
  view: localStorage.getItem("portraitHubView") || "overview",
  analysisResultsTab: localStorage.getItem("portraitHubAnalysisResultsTab") || "image",
  isLoggedIn: localStorage.getItem("portraitHubLoggedIn") === "true",
  dashboard: {},
  galleryExport: {},
  latestPayloads: {},
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

const endpointMap = {
  vision: {
    faces: "/v1/infer/faces",
    persons: "/v1/infer/persons",
    pose: "/v1/infer/pose",
    appearance: "/v1/infer/appearance",
    gait: "/v1/infer/gait",
    detect: "/infer/persons",
    embeddings: "/infer/person-embeddings",
    tracks: "/infer/person-tracks",
  },
  compare: {
    faces: "/v1/compare/faces",
    persons: "/v1/compare/persons",
    gait: "/v1/compare/gait",
    fusion: "/v1/fusion/compare",
    batch: "/v1/compare/batch",
  },
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
  <!-- 登录页面 -->
  <div id="login-view" class="login-container hidden">
    <div class="login-card">
      <div class="login-brand">
        <h1><span class="brand-logo brand-logo--large">影</span>影鉴</h1>
        <p>面向业务项目的离线人像解析与比对控制台</p>
      </div>
      <form id="login-form">
        <div class="field">
          <label>租户 ID (Tenant ID) <input id="tenant-input" autocomplete="off" value="default" /></label>
        </div>
        <div class="field">
          <label>接口令牌 (API Key) <input id="api-key-input" type="password" autocomplete="off" /></label>
        </div>
        <div class="field">
          <label>JWT 令牌 (Bearer Token) <input id="bearer-input" type="password" autocomplete="off" /></label>
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
        <details class="nav-group" data-nav-group="ops">
          <summary>运维治理</summary>
          <div class="nav-group-items">
            <button type="button" class="nav-item" data-nav="models">模型管理</button>
            <button type="button" class="nav-item" data-nav="admin-threshold">比对阈值</button>
            <button type="button" class="nav-item" data-nav="admin-data">数据保留与备份</button>
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
          <button type="button" class="product-tile" data-nav-shortcut="vision"><strong>图片解析</strong><span>人脸、人体、姿态、衣着、步态、检测和 ReID embedding。</span></button>
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
            <div id="feature-scatter" class="scatter" aria-label="gallery feature distribution"></div>
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
            <p>按 profile 更新各模态比对阈值。</p>
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
      // ignore and fallback
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
  region_configured: "区域已配置",
};

const valueLabels = {
  active: "运行中",
  appearance: "衣着外观",
  backup: "系统备份",
  body: "人体",
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
  success: "成功",
  text: "文本",
  tracks: "轨迹提取",
  true: "是",
  unloaded: "模型未加载",
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
  const renderedPayload = ["jobs", "job", "video-results", "image-results", "stream-results"].includes(name) ? sanitizeVideoPayload(payload) : payload;
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
    reader.addEventListener("error", () => reject(reader.error || new Error("file preview failed")));
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
  if (state.apiKey) lines.push(`  -H "X-API-Key: ${state.apiKey}"`);
  if (state.bearer) lines.push(`  -H "Authorization: Bearer ${state.bearer.slice(0, 12)}..."`);
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
  await Promise.allSettled([refreshDashboard(), refreshModels(), refreshGallery(), refreshStreams(), refreshAdmin(), refreshAnalysisResults()]);
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
  qs("#alerts-refresh-button").addEventListener("click", wrapHandler(async () => {
    await refreshDashboard();
    renderAlerts();
  }));

  qs("#vision-form").addEventListener("submit", wrapHandler(submitVision));
  qs("#compare-form").addEventListener("submit", wrapHandler(submitCompare));
  qs("#enroll-form").addEventListener("submit", wrapHandler(submitGalleryEnroll));
  qs("#search-form").addEventListener("submit", wrapHandler(submitGallerySearch));
  qs("#video-form").addEventListener("submit", wrapHandler(submitVideoJob));
  qs("#stream-form").addEventListener("submit", wrapHandler(submitStream));
  ["#vision-visuals", "#job-visuals", "#image-results-visuals", "#video-results-visuals"].forEach((selector) => qs(selector).addEventListener("click", (event) => {
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
    renderPayload("admin-threshold", "#admin-threshold-json", await api(`/v1/thresholds/${encodeURIComponent(profile)}`, { method: "PUT", json: payload }));
  }));
  qs("#retention-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    renderPayload("admin-data", "#admin-data-json", await api("/v1/admin/retention/cleanup", {
      method: "POST",
      json: { retention_days: Number(qs("#retention-days-input").value), confirm: qs("#retention-confirm-input").value },
    }));
  }));
  qs("#backup-form").addEventListener("submit", wrapHandler(async (event) => {
    event.preventDefault();
    const updatedSince = qs("#backup-updated-since-input").value;
    renderPayload("admin-data", "#admin-data-json", await api("/v1/admin/backup", {
      method: "POST",
      json: {
        updated_since: updatedSince === "" ? null : Number(updatedSince),
        confirm: qs("#backup-confirm-input").value,
      },
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
  setAlertInputs();
  renderIntegrationSnippet();
  setupEvents();
  updateSnippetButtons();
  setView(state.view);
  updateAuthView();
}

init();
