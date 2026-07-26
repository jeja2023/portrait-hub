import { describe, expect, it } from "vitest";

import {
  canRetryWebhookDelivery,
  webhookAttemptResponse,
  webhookAttemptTriggerLabel,
  webhookDeliveryStatusLabel,
  webhookSignatureStatusLabel,
} from "../src/utils/webhookDeliveries";

describe("webhook delivery presentation", () => {
  it("labels delivery, signature, and attempt states", () => {
    expect(webhookDeliveryStatusLabel("retrying")).toBe("等待重试");
    expect(webhookDeliveryStatusLabel("dead_letter")).toBe("死信");
    expect(webhookSignatureStatusLabel("self_verified")).toBe("本地校验通过");
    expect(webhookSignatureStatusLabel(undefined)).toBe("旧记录未记录");
    expect(webhookAttemptTriggerLabel("manual_retry")).toBe("手动重试");
  });

  it("only offers manual retry for terminal failures", () => {
    expect(canRetryWebhookDelivery("failed")).toBe(true);
    expect(canRetryWebhookDelivery("dead_letter")).toBe(true);
    expect(canRetryWebhookDelivery("retrying")).toBe(false);
    expect(canRetryWebhookDelivery("delivered")).toBe(false);
  });

  it("summarizes HTTP and transport responses without exposing bodies", () => {
    expect(
      webhookAttemptResponse({
        attempt: 1,
        started_at: 1,
        finished_at: 2,
        status_code: 503,
        success: false,
        error_type: "HTTPError",
        response_bytes: 17,
      }),
    ).toBe("HTTP 503 · 17 B");
    expect(
      webhookAttemptResponse({
        attempt: 2,
        started_at: 2,
        finished_at: 3,
        status_code: null,
        success: false,
        error_type: "TimeoutError",
        response_bytes: 0,
      }),
    ).toBe("TimeoutError · 0 B");
  });
});
