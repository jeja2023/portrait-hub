const sensitiveMarkers = [
  "access_key",
  "api_key",
  "authorization",
  "credential",
  "embedding",
  "password",
  "private_key",
  "secret",
  "stack",
  "stream_url",
  "token",
  "vector",
];

function isSensitiveKey(key: string): boolean {
  const normalized = key.toLowerCase();
  return sensitiveMarkers.some((marker) => normalized === marker || normalized.includes(marker));
}

// 值级兜底（方案 §8.4）：key 名不规范时仍拦截典型敏感值形态。
// 凭证前缀 / JWT / Bearer / 带凭证或 query 的流地址 / 内网地址。
const sensitiveValuePatterns = [
  /^phk_[A-Za-z0-9]/,
  /^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\./,
  /^Bearer\s+\S+/i,
  /^(rtsp|rtmp|srt):\/\/\S*[@?]/i,
  /^[a-z][a-z0-9+.-]*:\/\/[^/@\s]*:[^/@\s]*@/i,
  /\b(?:10|127|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?::\d+)?\b/,
];

function isSensitiveStringValue(value: string): boolean {
  return sensitiveValuePatterns.some((pattern) => pattern.test(value));
}

// 长数值数组按生物特征向量处理：即便字段名不含 embedding/vector 也不外显。
function isLikelyFeatureVector(value: unknown[]): boolean {
  return value.length >= 32 && value.every((item) => typeof item === "number");
}

export function redactForDisplay(value: unknown, key = ""): unknown {
  if (key && isSensitiveKey(key)) return "<redacted>";
  if (typeof value === "string" && isSensitiveStringValue(value)) return "<redacted>";
  if (Array.isArray(value)) {
    if (isLikelyFeatureVector(value)) return `<redacted:${value.length} 维向量>`;
    return value.map((item) => redactForDisplay(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([entryKey, entryValue]) => [
        entryKey,
        redactForDisplay(entryValue, entryKey),
      ]),
    );
  }
  return value;
}
