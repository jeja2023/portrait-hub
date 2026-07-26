import type { WebhookDeliveryAttempt } from "../api/contracts";

const DELIVERY_STATUS_LABELS: Record<string, string> = {
  pending: "待投递",
  delivering: "投递中",
  retrying: "等待重试",
  delivered: "已送达",
  failed: "投递失败",
  dead_letter: "死信",
};

const SIGNATURE_STATUS_LABELS: Record<string, string> = {
  pending: "等待签名",
  self_verified: "本地校验通过",
  verification_failed: "本地校验失败",
  unknown: "旧记录未记录",
};

const ATTEMPT_TRIGGER_LABELS: Record<string, string> = {
  initial: "首次",
  automatic_retry: "自动重试",
  manual_retry: "手动重试",
  legacy: "历史尝试",
};

export function webhookDeliveryStatusLabel(value: unknown): string {
  const key = String(value ?? "");
  return DELIVERY_STATUS_LABELS[key] ?? (key || "未知状态");
}

export function webhookSignatureStatusLabel(value: unknown): string {
  const key = String(value ?? "unknown");
  return SIGNATURE_STATUS_LABELS[key] ?? (key || "未知");
}

export function webhookAttemptTriggerLabel(value: unknown): string {
  const key = String(value ?? "legacy");
  return ATTEMPT_TRIGGER_LABELS[key] ?? (key || "历史尝试");
}

export function canRetryWebhookDelivery(status: unknown): boolean {
  return status === "failed" || status === "dead_letter";
}

export function webhookAttemptResponse(attempt: WebhookDeliveryAttempt): string {
  const bytes = Math.max(0, Number(attempt.response_bytes) || 0);
  if (attempt.status_code !== null && attempt.status_code !== undefined) {
    return `HTTP ${attempt.status_code} · ${bytes} B`;
  }
  return attempt.error_type ? `${attempt.error_type} · ${bytes} B` : `无 HTTP 响应 · ${bytes} B`;
}
