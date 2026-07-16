(function registerConsoleGovernanceTemplate(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});
  const templates = (modules.templates = modules.templates || {});

  templates.buildGovernance = () => `
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
  <div id="vision-lightbox" class="vision-lightbox hidden" aria-hidden="true"></div>
`;
})(window);
