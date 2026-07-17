import { describe, expect, it } from "vitest";

import { redactForDisplay } from "../src/utils/redact";

describe("redactForDisplay", () => {
  it("redacts sensitive values recursively without mutating safe fields", () => {
    const value = {
      id: "result-1",
      nested: {
        authorization: "Bearer secret",
        face_embedding: [0.1, 0.2],
        label: "person-a",
      },
      rows: [{ stream_url: "rtsp://user:pass@example.test/live", score: 0.98 }],
    };

    expect(redactForDisplay(value)).toEqual({
      id: "result-1",
      nested: {
        authorization: "<redacted>",
        face_embedding: "<redacted>",
        label: "person-a",
      },
      rows: [{ stream_url: "<redacted>", score: 0.98 }],
    });
    expect(value.nested.authorization).toBe("Bearer secret");
  });

  it("redacts a sensitive root key", () => {
    expect(redactForDisplay("secret", "api_key")).toBe("<redacted>");
  });
});
