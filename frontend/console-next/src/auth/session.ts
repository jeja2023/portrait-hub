import { computed, reactive } from "vue";

export type AuthMode = "api-key" | "jwt" | "anonymous";

interface StoredSession {
  tenantId: string;
  authMode: AuthMode;
  apiKey: string;
  bearer: string;
  authenticated: boolean;
}

const SESSION_KEY = "portraitHubConsoleSessionV2";

function emptySession(): StoredSession {
  return {
    tenantId: "",
    authMode: "api-key",
    apiKey: "",
    bearer: "",
    authenticated: false,
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
      authMode: parsed.authMode === "jwt" || parsed.authMode === "anonymous" ? parsed.authMode : "api-key",
      apiKey: typeof parsed.apiKey === "string" ? parsed.apiKey : "",
      bearer: typeof parsed.bearer === "string" ? parsed.bearer : "",
      authenticated: parsed.authenticated === true,
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

export function beginSession(input: {
  tenantId: string;
  authMode: AuthMode;
  apiKey?: string;
  bearer?: string;
}): void {
  sessionState.tenantId = input.tenantId.trim();
  sessionState.authMode = input.authMode;
  sessionState.apiKey = input.authMode === "api-key" ? (input.apiKey ?? "").trim() : "";
  sessionState.bearer = input.authMode === "jwt" ? (input.bearer ?? "").trim() : "";
  sessionState.authenticated = false;
  persistSession();
}

export function markSessionAuthenticated(): void {
  sessionState.authenticated = true;
  persistSession();
}

export function authHeaders(): HeadersInit {
  const headers: Record<string, string> = {};
  if (sessionState.tenantId) headers["X-Tenant-ID"] = sessionState.tenantId;
  if (sessionState.authMode === "api-key" && sessionState.apiKey) {
    headers["X-API-Key"] = sessionState.apiKey;
  }
  if (sessionState.authMode === "jwt" && sessionState.bearer) {
    headers.Authorization = `Bearer ${sessionState.bearer}`;
  }
  return headers;
}

export function clearSession(): void {
  Object.assign(sessionState, emptySession());
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(SESSION_KEY);
  }
}
