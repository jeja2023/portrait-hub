import { ApiError } from "../api/client";

// error_code → 中文说明（方案 §11.2）。后端 error.message 已是中文，此表用于
// message 缺失或不可读时的兜底，以及为常见错误补充一句处置提示。
export const errorCodeLabels: Record<string, string> = {
  validation_error: "请求参数校验失败",
  client_error: "请求参数无效",
  unauthorized: "登录凭证缺失或已失效",
  forbidden: "当前凭证没有执行该操作的权限",
  not_found: "请求的资源不存在或不属于当前租户",
  conflict: "操作与当前状态冲突，请刷新后重试",
  too_large: "上传内容超过大小限制",
  batch_job_error: "批量任务请求无效",
  rate_limited: "请求过于频繁，请稍后重试",
  inference_error: "推理服务暂时不可用",
  internal_error: "服务内部错误",
};

// 页面错误横幅的统一文案：优先服务端中文 message，映射表兜底；
// 未知错误码或无 message 时附 request_id 便于对照调用日志（方案 §11.2）。
export function errorBannerMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  const mapped = error.code ? errorCodeLabels[error.code] : undefined;
  const message = error.message || mapped || fallback;
  const known = Boolean(mapped) && Boolean(error.message);
  if (!known && error.requestId) return `${message}（请求 ID：${error.requestId}）`;
  return message;
}
