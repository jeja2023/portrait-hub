import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest } from "../src/api/client";
import { beginSession, clearSession, markSessionAuthenticated } from "../src/auth/session";

describe("apiRequest", () => {
  beforeEach(() => {
    clearSession();
    beginSession({ tenantId: "tenant-a", authMode: "api-key", apiKey: "tab-secret" });
    markSessionAuthenticated();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("unwraps the API envelope and sends tab-scoped credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "success", data: { count: 2 } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest<{ count: number }>("/v1/test")).resolves.toEqual({ count: 2 });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);

    expect(headers.get("X-Tenant-ID")).toBe("tenant-a");
    expect(headers.get("X-API-Key")).toBe("tab-secret");
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
  });

  it("decodes structured errors and emits the unauthorized event", async () => {
    const unauthorized = vi.fn();
    window.addEventListener("portrait:unauthorized", unauthorized, { once: true });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: { code: "unauthorized", message: "凭证已过期", details: { source: "jwt" } },
            request_id: "req-401",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await apiRequest("/v1/test").catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 401,
      code: "unauthorized",
      message: "凭证已过期",
      requestId: "req-401",
    });
    expect(unauthorized).toHaveBeenCalledOnce();
  });

  it("turns an aborted timeout into a stable API error", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_path: string, init: RequestInit) => {
        return new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), {
            once: true,
          });
        });
      }),
    );

    const pending = apiRequest("/v1/slow", {}, 20);
    const assertion = expect(pending).rejects.toMatchObject({
      status: 0,
      message: "请求超时或已取消",
    });
    await vi.advanceTimersByTimeAsync(21);

    await assertion;
  });
});
