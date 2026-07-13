(function registerConsoleNavigation(global) {
  const modules = (global.PortraitConsoleModules = global.PortraitConsoleModules || {});

  const sections = [
    {
      id: "overview",
      label: "总览",
      standalone: true,
    },
    {
      id: "analysis",
      label: "智能分析",
      items: [
        { view: "vision", label: "图片解析" },
        { view: "video", label: "视频任务" },
        { view: "streams", label: "实时视频流" },
        { view: "video-results", label: "解析结果" },
      ],
    },
    {
      id: "retrieval",
      label: "比对检索",
      items: [
        { view: "compare", label: "人像比对" },
        { view: "multimodal-compare", label: "融合比对" },
        { view: "gallery-search", label: "以图搜人" },
      ],
    },
    {
      id: "gallery",
      label: "人员库",
      items: [
        { view: "gallery-enroll", label: "人员注册" },
        { view: "gallery-manage", label: "人员管理" },
        { view: "gallery-rebuild", label: "特征重建" },
      ],
    },
    {
      id: "access",
      label: "接入中心",
      items: [
        { view: "access-credentials", label: "应用凭证" },
        { view: "sdk-examples", label: "SDK 示例" },
        { view: "api-playground", label: "接口调试台" },
        { view: "openapi-docs", label: "开放接口定义" },
        { view: "error-codes", label: "错误码" },
        { view: "webhooks", label: "事件回调" },
        { view: "call-logs", label: "调用日志" },
      ],
    },
    {
      id: "model-governance",
      label: "模型与评估",
      items: [
        { view: "models", label: "模型管理" },
        { view: "admin-threshold", label: "比对阈值" },
        { view: "track-review", label: "轨迹审阅" },
        { view: "evaluation-center", label: "回归评估" },
        { view: "release-center", label: "模型发布" },
      ],
    },
    {
      id: "ops",
      label: "运维合规",
      items: [
        { view: "slo-panel", label: "SLO 面板" },
        { view: "alerts", label: "告警评估" },
        { view: "admin-data", label: "数据保留与备份" },
        { view: "audit-compliance", label: "合规审计" },
      ],
    },
  ];

  const overviewShortcuts = [
    { view: "vision", title: "图片解析", description: "人脸、人体、姿态、衣着、步态和 ReID 向量。" },
    { view: "video", title: "视频任务", description: "离线视频任务创建、状态跟踪和结果回收。" },
    { view: "streams", title: "实时视频流", description: "RTSP/HTTP 注册、启动、事件查询和订阅。" },
    { view: "compare", title: "人像比对", description: "1:1 人脸、人体、步态和批量成对比对。" },
    { view: "gallery-search", title: "以图搜人", description: "1:N 检索、候选排序和人员级聚合结果。" },
    { view: "gallery-enroll", title: "人员注册", description: "多图入库、重复跳过和特征质量核验。" },
    { view: "gallery-rebuild", title: "特征重建", description: "按模态和模型重建底库向量索引。" },
    { view: "access-credentials", title: "接入配置", description: "应用凭证、调用权限和密钥轮换。" },
    { view: "models", title: "模型管理", description: "模型状态、加载卸载、别名与生产能力检查。" },
  ];

  modules.navigation = { sections, overviewShortcuts };
})(window);