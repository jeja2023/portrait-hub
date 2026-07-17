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

export function redactForDisplay(value: unknown, key = ""): unknown {
  if (key && isSensitiveKey(key)) return "<redacted>";
  if (Array.isArray(value)) return value.map((item) => redactForDisplay(item));
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
