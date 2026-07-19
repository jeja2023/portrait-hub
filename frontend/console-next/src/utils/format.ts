const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatTimestamp(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "--";
  const numeric = typeof value === "number" ? value : Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(numeric < 10_000_000_000 ? numeric * 1000 : numeric)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : dateTimeFormatter.format(date);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "--";
  return new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 }).format(value);
}

export const statusLabels: Record<string, string> = {
  queued: "等待中",
  running: "处理中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  active: "运行中",
  stopped: "已停止",
  disabled: "已停用",
  pending: "待处理",
  ready: "就绪",
  not_ready: "未就绪",
};

export const capabilityLabels: Record<string, string> = {
  person_detection: "人体检测",
  body_embedding: "人体特征",
  face_embedding: "人脸特征",
  person_reidentification: "人员重识别",
  pose_estimation: "姿态估计",
  appearance: "衣着外观",
  gait: "步态特征",
};

export const modalityLabels: Record<string, string> = {
  body: "人体",
  face: "人脸",
  appearance: "衣着",
  gait: "步态",
  fusion: "多模态融合",
  image: "图片",
  video: "视频",
  stream: "视频流",
  persons: "人体解析",
  faces: "人脸检测",
  pose: "姿态估计",
  tracks: "人员轨迹",
  person_tracks: "人员轨迹解析",
  detection: "目标检测",
  review_annotation: "复核标注",
  regression_holdout: "回归验证保留集",
  manual_review_pool: "人工复核样本池",
  positive_control: "正样本对照集",
  quality_calibration: "质量校准集",
  association_regression: "关联回归集",
};

export const thresholdProfileLabels: Record<string, string> = {
  strict: "严格",
  normal: "标准",
  loose: "宽松",
};

export const riskLabels: Record<string, string> = {
  clear: "风险可控",
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  low_quality: "低质量",
  mismatch: "身份不匹配",
  false_positive: "误检",
  uncertain: "不确定",
};

export const reviewLabels: Record<string, string> = {
  confirmed: "已确认",
  mismatch: "身份不匹配",
  false_positive: "误检",
  low_quality: "低质量",
  uncertain: "不确定",
};

export const actionLabels: Record<string, string> = {
  collect_more_samples: "继续收集样本",
  raise_threshold: "提高阈值",
  hold_threshold: "保持阈值",
  review_quality_gate: "复核质量门槛",
};

export const confidenceLabels: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

export const auditCategoryLabels: Record<string, string> = {
  delete_requests: "删除请求",
  exports: "导出与备份",
  model_versions: "模型版本",
  retention: "保留策略",
  other: "其他",
};

export const outcomeLabels: Record<string, string> = {
  success: "成功",
  failure: "失败",
  started: "已开始",
  completed: "已完成",
  failed: "失败",
  error: "失败",
  active: "运行中",
  disabled: "已停用",
};

export const backendAreaLabels: Record<string, string> = {
  gallery: "人员库",
  vector: "向量检索",
  object_storage: "对象存储",
  task_queue: "任务队列",
};

export const backendLabels: Record<string, string> = {
  local: "本地",
  postgres: "PostgreSQL",
  qdrant: "Qdrant",
  s3: "对象存储",
  redis: "Redis",
  json: "本地文件",
};

export const eventLabels: Record<string, string> = {
  stream_worker_session_started: "流处理会话已启动",
  stream_worker_session_stopped: "流处理会话已停止",
  stream_worker_start_requested: "已请求启动流处理",
  stream_worker_stop_requested: "已请求停止流处理",
  stream_worker_reconnecting: "流处理正在重连",
  stream_worker_failed: "流处理失败",
  stream_analysis_completed: "流分析已完成",
  stream_worker_heartbeat: "流处理心跳",
  "job.completed": "视频任务已完成",
  "job.failed": "视频任务失败",
  "stream.started": "视频流已启动",
  "stream.stopped": "视频流已停止",
  retention_cleanup: "保留策略清理",
  config_hot_reload: "配置热重载",
  alias_switch: "模型别名切换",
  alias_weighted_rollout: "模型加权发布",
  alias_rollback: "模型别名回滚",
  access_tenant_created: "租户已创建",
  access_application_created: "接入应用已创建",
  access_application_updated: "接入应用已更新",
  access_application_secret_rotated: "应用密钥已轮换",
  access_webhook_created: "事件回调已创建",
  access_webhook_updated: "事件回调已更新",
  access_webhook_secret_rotated: "回调签名已轮换",
  track_review_annotation_created: "轨迹标注已创建",
};

export function displayLabel(
  value: unknown,
  labels: Record<string, string>,
  fallback = "未知",
  preserveUnknown = false,
): string {
  const key = String(value ?? "");
  return labels[key] ?? (preserveUnknown && key.trim() ? key : fallback);
}

export function statusLabel(value: unknown): string {
  return displayLabel(value, { ...statusLabels, ...outcomeLabels }, "未知状态");
}

export function capabilityLabel(value: unknown): string {
  return displayLabel(value, capabilityLabels, "未知能力");
}

export function modalityLabel(value: unknown): string {
  return displayLabel(value, modalityLabels, "未知模态");
}

export function thresholdProfileLabel(value: unknown): string {
  return displayLabel(value, thresholdProfileLabels, "未知方案");
}

export function riskLabel(value: unknown): string {
  return displayLabel(value, riskLabels, "未知风险");
}

export function reviewLabel(value: unknown): string {
  return displayLabel(value, reviewLabels, "未知复核结论");
}

export function actionLabel(value: unknown): string {
  return displayLabel(value, actionLabels, "待人工判断");
}

export function confidenceLabel(value: unknown): string {
  return displayLabel(value, confidenceLabels, "未知");
}

export function auditCategoryLabel(value: unknown): string {
  return displayLabel(value, auditCategoryLabels, "其他");
}

export function outcomeLabel(value: unknown): string {
  return displayLabel(value, outcomeLabels, "未知结果");
}

export function backendAreaLabel(value: unknown): string {
  return displayLabel(value, backendAreaLabels, "其他后端");
}

export function backendLabel(value: unknown): string {
  return displayLabel(value, backendLabels, "未知后端", true);
}

export function eventLabel(value: unknown): string {
  return displayLabel(value, eventLabels, "其他事件");
}
export const datasetNameLabels: Record<string, string> = {
  review_all_annotations: "全部复核标注",
  review_attention_holdout: "重点复核保留集",
  review_confirmed_samples: "已确认样本集",
  review_low_quality_samples: "低质量样本集",
  review_mismatch_samples: "身份不匹配样本集",
};

export const datasetPurposeLabels: Record<string, string> = {
  manual_review_pool: "人工复核样本池",
  regression_holdout: "回归验证保留集",
  positive_control: "正样本对照集",
  quality_calibration: "质量校准集",
  association_regression: "关联回归集",
};

export const recommendationReasonLabels: Record<string, string> = {
  "false positive or mismatch annotations outnumber confirmed samples": "误检或身份不匹配标注多于已确认样本",
  "confirmed samples dominate current review pool": "已确认样本在当前复核池中占主导",
  "low quality annotations should tune upstream quality filters before lowering identity thresholds":
    "降低身份阈值前，应先根据低质量标注调整上游质量过滤条件",
};

export function datasetNameLabel(value: unknown): string {
  return displayLabel(value, datasetNameLabels, "自定义评估数据集");
}

export function datasetPurposeLabel(value: unknown): string {
  return displayLabel(value, datasetPurposeLabels, "未说明");
}

export function recommendationReasonLabel(value: unknown): string {
  return displayLabel(value, recommendationReasonLabels, "未说明");
}

export const comparisonReasonLabels: Record<string, string> = {
  exact_or_perceptual_duplicate_input: "输入内容完全相同或高度重复",
  near_duplicate_input: "输入内容接近重复",
  independent_input_evidence: "输入证据相互独立",
  template_embedding_missing: "缺少可用特征",
  score_missing: "缺少有效分数",
  invalid_score_or_quality: "分数或质量无效",
  quality_too_low: "证据质量过低",
  not_enough_frames: "有效帧不足",
  not_enough_unique_frames: "不同有效帧不足",
  embedding_missing: "缺少可用特征",
};

export function comparisonReasonLabel(value: unknown): string {
  return displayLabel(value, comparisonReasonLabels, "其他原因");
}

export function artifactLabel(value: unknown, index = 0): string {
  const text = String(value ?? "").trim();
  const imageMatch = text.match(/^(?:\d+\.\s*)?image-(\d+)$/i);
  if (imageMatch) return "第 " + imageMatch[1] + " 张图片";
  const frameMatch = text.match(/^(?:\d+\.\s*)?frame[-\s]?(\d+)$/i);
  if (frameMatch) return "第 " + frameMatch[1] + " 帧";
  if (/[\u3400-\u9fff]/u.test(text)) return text;
  return "预览 " + (index + 1);
}
