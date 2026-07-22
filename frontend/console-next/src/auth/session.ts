import { computed, reactive } from "vue";

export type AuthMode = "api-key" | "jwt" | "oidc" | "local" | "anonymous";

interface StoredSession {
  tenantId: string;
  authMode: AuthMode;
  projectId: string;
  apiKey: string;
  bearer: string;
  authenticated: boolean;
  expiresAt: number | null;
}

const SESSION_KEY = "portraitHubConsoleSessionV2";
let expiryTimer: number | null = null;

function emptySession(): StoredSession {
  return {
    tenantId: "",
    authMode: "api-key",
    projectId: "default",
    apiKey: "",
    bearer: "",
    authenticated: false,
    expiresAt: null,
  };
}

function readStoredSession(): StoredSession {
  if (typeof window === "undefined") return emptySession();
  try {
    const parsed = JSON.parse(
      window.sessionStorage.getItem(SESSION_KEY) ?? "null",
    ) as Partial<StoredSession> | null;
    if (!parsed || typeof parsed !== "object") return emptySession();
    return {
      tenantId: typeof parsed.tenantId === "string" ? parsed.tenantId.trim() : "",
      authMode: parsed.authMode === "jwt" || parsed.authMode === "oidc" || parsed.authMode === "local" || parsed.authMode === "anonymous" ? parsed.authMode : "api-key",
      projectId: typeof parsed.projectId === "string" && parsed.projectId.trim() ? parsed.projectId.trim() : "default",
      apiKey: typeof parsed.apiKey === "string" ? parsed.apiKey : "",
      bearer: typeof parsed.bearer === "string" ? parsed.bearer : "",
      authenticated: parsed.authenticated === true,
      expiresAt: typeof parsed.expiresAt === "number" && Number.isFinite(parsed.expiresAt) ? parsed.expiresAt : null,
    };
  } catch {
    return emptySession();
  }
}

export const sessionState = reactive<StoredSession>(readStoredSession());
export const hasSession = computed(() => sessionState.authenticated);

function persistSession(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(sessionState));
}

function armExpiryTimer(): void {
  if (typeof window === "undefined") return;
  if (expiryTimer !== null) window.clearTimeout(expiryTimer);
  expiryTimer = null;
  if (!sessionState.authenticated || sessionState.expiresAt === null) return;
  const delay = Math.max(0, sessionState.expiresAt * 1000 - Date.now());
  expiryTimer = window.setTimeout(() => {
    if (!sessionState.authenticated) return;
    clearSession();
    window.dispatchEvent(new CustomEvent("portrait:session-expired"));
  }, delay);
}

export function beginSession(input: {
  tenantId: string;
  authMode: AuthMode;
  projectId?: string;
  apiKey?: string;
  bearer?: string;
}): void {
  sessionState.tenantId = input.tenantId.trim();
  sessionState.projectId = input.projectId?.trim() || "default";
  sessionState.authMode = input.authMode;
  sessionState.apiKey = input.authMode === "api-key" ? (input.apiKey ?? "").trim() : "";
  sessionState.bearer = input.authMode === "jwt" ? (input.bearer ?? "").trim() : "";
  sessionState.authenticated = false;
  sessionState.expiresAt = null;
  armExpiryTimer();
  persistSession();
}

export function setSessionTenant(tenantId: string): void {
  sessionState.tenantId = tenantId.trim();
  persistSession();
}

export function setSessionProject(projectId: string): void {
  sessionState.projectId = projectId.trim() || "default";
  persistSession();
}

export function setSessionExpiry(expiresAt: number | null | undefined): void {
  sessionState.expiresAt = typeof expiresAt === "number" && Number.isFinite(expiresAt) ? expiresAt : null;
  armExpiryTimer();
  persistSession();
}

export function markSessionAuthenticated(): void {
  sessionState.authenticated = true;
  armExpiryTimer();
  persistSession();
}

function cookieValue(name: string): string {
  if (typeof document === "undefined") return "";
  const prefix = encodeURIComponent(name) + "=";
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : "";
}

export function authHeaders(): HeadersInit {
  const headers: Record<string, string> = {};
  if (sessionState.tenantId) headers["X-Tenant-ID"] = sessionState.tenantId;
  if (sessionState.projectId && sessionState.projectId !== "default") {
    headers["X-Project-ID"] = sessionState.projectId;
  }
  if (sessionState.authMode === "api-key" && sessionState.apiKey) {
    headers["X-API-Key"] = sessionState.apiKey;
  }
  if (sessionState.authMode === "jwt" && sessionState.bearer) {
    headers.Authorization = `Bearer ${sessionState.bearer}`;
  }
  if (sessionState.authMode === "oidc" || sessionState.authMode === "local") {
    const csrf = cookieValue("portrait_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  return headers;
}

export function clearSession(): void {
  if (typeof window !== "undefined" && expiryTimer !== null) window.clearTimeout(expiryTimer);
  expiryTimer = null;
  Object.assign(sessionState, emptySession());
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(SESSION_KEY);
  }
}

armExpiryTimer();
