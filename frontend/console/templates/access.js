(function registerConsoleAccessTemplate(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});
  const templates = (modules.templates = modules.templates || {});

  templates.buildAccess = () => `
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
        <div class="card">
          <div class="section-title">
            <h3>租户开通</h3>
            <p>输入租户名称后生成租户标识和默认接入应用。</p>
          </div>
          <form id="access-tenant-form" class="form-grid compact">
            <label>租户名称 <input id="access-tenant-name-input" placeholder="客户或业务项目名称" /></label>
            <label>租户标识 <input id="access-tenant-id-input" placeholder="留空自动生成" /></label>
            <label>默认应用名称 <input id="access-tenant-app-name-input" placeholder="留空使用租户名称" /></label>
            <label class="field-inline"><input id="access-tenant-default-app-input" type="checkbox" checked /> 创建默认应用</label>
            <button type="submit" class="primary">开通租户</button>
          </form>
          <div id="access-tenant-summary" class="result-summary"></div>
        </div>
        <div class="split-grid">
          <div class="card">
            <div class="section-title">
              <h3>接入应用</h3>
              <p>本清单用于接入规划、示例生成和租户级接口密钥鉴权。</p>
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

`;
})(window);
