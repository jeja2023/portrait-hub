import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  authHeaders,
  beginSession,
  clearSession,
  markSessionAuthenticated,
  sessionState,
  setSessionExpiry,
} from "../src/auth/session";

describe("console session", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearSession();
  });

  it("keeps API credentials in tab-scoped storage only", () => {
    beginSession({
      tenantId: "tenant-a",
      authMode: "api-key",
      apiKey: "secret-api-key",
    });
    markSessionAuthenticated();

    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(1);
    expect(window.sessionStorage.getItem("portraitHubConsoleSessionV2")).toContain("secret-api-key");
    expect(authHeaders()).toEqual({
      "X-Tenant-ID": "tenant-a",
      "X-API-Key": "secret-api-key",
    });
  });

  it("lets a single-tenant credential determine the tenant", () => {
    beginSession({
      tenantId: "",
      authMode: "api-key",
      apiKey: "tenant-bound-key",
    });

    expect(authHeaders()).toEqual({ "X-API-Key": "tenant-bound-key" });
  });

  it("clears credentials and persisted state", () => {
    beginSession({ authMode: "jwt", tenantId: "tenant-b", bearer: "jwt-token" });
    markSessionAuthenticated();
    clearSession();

    expect(window.sessionStorage.length).toBe(0);
    expect(sessionState.authenticated).toBe(false);
    expect(sessionState.bearer).toBe("");
    expect(authHeaders()).toEqual({});
  });
  it("clears an authenticated session when the server expiry is reached", () => {
    vi.useFakeTimers();
    const expired = vi.fn();
    window.addEventListener("portrait:session-expired", expired, { once: true });
    try {
      beginSession({ authMode: "jwt", tenantId: "tenant-expiring", bearer: "jwt-token" });
      markSessionAuthenticated();
      setSessionExpiry(Date.now() / 1000 + 5);

      vi.advanceTimersByTime(5_100);

      expect(sessionState.authenticated).toBe(false);
      expect(sessionState.expiresAt).toBeNull();
      expect(expired).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });
});
