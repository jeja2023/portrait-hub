(function registerConsoleCoreTemplate(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});
  const templates = (modules.templates = modules.templates || {});

  templates.buildCore = ({ renderNavigation, renderOverviewShortcuts }) => `
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
      ${renderNavigation()}
      <div class="sidebar-footer">
        <div class="tenant-info">
          <span>当前租户</span>
          <strong id="current-tenant-display">default</strong>
        </div>
        <div id="status-strip" class="status-strip">就绪</div>
        <div class="sidebar-actions">
          <button type="button" id="refresh-button" class="small">刷新当前</button>
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
          ${renderOverviewShortcuts()}
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
              <option value="detect">YOLO 人体检测 /v1/vision/infer</option>
              <option value="embeddings">ReID 向量 /v1/vision/infer</option>
              <option value="tracks">图片序列轨迹 /v1/infer/tracks</option>
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
            <p>浏览人员列表，查改删人员记录，并核验已入库特征。</p>
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
            <p>查询、更新或删除人员记录。</p>
          </div>
          <div class="form-grid">
            <label>人员 ID <input id="person-id-input" placeholder="人员 ID" /></label>
            <label>新显示名称 <input id="person-display-name-input" placeholder="可选" /></label>
            <label class="span-2">新元数据（JSON） <input id="person-metadata-input" placeholder='{"department":"A"}' /></label>
            <button type="button" id="person-get-button">查询人员</button>
            <button type="button" id="person-patch-button">更新人员</button>
            <button type="button" id="person-delete-button" class="danger">删除人员</button>
          </div>

          <div id="gallery-summary" class="result-summary"></div>
          <div id="gallery-json" class="json-view data-viewer" role="region" aria-label="人员库响应数据"></div>
        </div>
      </section>

      <section class="view" data-view="gallery-rebuild">
        <div class="view-header">
          <div class="section-title">
            <h2>特征重建</h2>
            <p>按模态和模型重建人员库向量索引，用于模型切换、阈值校准或索引修复后的批量更新。</p>
          </div>
        </div>
        <div class="card">
          <div class="section-title">
            <h3>重建配置</h3>
            <p>预演模式会返回影响范围，不写入新的索引结果。</p>
          </div>
          <div class="form-grid compact">
            <label>重建模态
              <select id="feature-rebuild-modality-input">
                <option value="">全部</option>
                <option value="body">人体</option>
                <option value="face">人脸</option>
                <option value="appearance">衣着外观</option>
              </select>
            </label>
            <label>模型 ID <input id="feature-rebuild-model-id-input" placeholder="可选" /></label>
            <label class="field-inline"><input id="feature-rebuild-dry-run-input" type="checkbox" checked /> 仅预演</label>
            <button type="button" id="feature-rebuild-button" class="primary">开始重建</button>
          </div>
          <div id="feature-rebuild-summary" class="result-summary"></div>
          <div id="feature-rebuild-json" class="json-view data-viewer" role="region" aria-label="特征重建响应数据"></div>
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
          <label>采样间隔（秒） <input id="job-sample-interval-input" name="sample_interval_seconds" type="number" min="0.1" step="0.1" value="1.0" /></label>
          <label>批次大小 <input id="job-batch-size-input" name="batch_size" type="number" min="1" value="16" /></label>
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
              <p>展示当前租户最近完成的图片解析结果，刷新页面后仍可查看。</p>
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
            <div id="stream-results-visuals" class="result-visual-grid"></div>
            <div id="stream-results-list" class="stream-result-list"></div>
            <div id="stream-results-json" class="json-view data-viewer" role="region" aria-label="视频流解析结果数据"></div>
          </div>
        </div>
      </section>

`;
})(window);
