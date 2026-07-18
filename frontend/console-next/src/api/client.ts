import { authHeaders } from "../auth/session";
import type { PortraitEnvelope, PortraitErrorBody } from "./contracts";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly requestId: string | null;
  readonly details: unknown;

  constructor(input: {
    status: number;
    message: string;
    code?: string | null;
    requestId?: string | null;
    details?: unknown;
  }) {
    super(input.message);
    this.name = "ApiError";
    this.status = input.status;
    this.code = input.code ?? null;
    this.requestId = input.requestId ?? null;
    this.details = input.details;
  }
}

export interface ApiRawResponse<T> {
  body: T;
  httpStatus: number;
  requestId: string | null;
}

function requestHeaders(initial?: HeadersInit, body?: BodyInit | null): Headers {
  const headers = new Headers(initial);
  for (const [key, value] of Object.entries(authHeaders())) headers.set(key, value);
  if (body && !(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");
  return headers;
}

async function decodeError(response: Response): Promise<ApiError> {
  let body: PortraitErrorBody;
  try {
    body = (await response.json()) as PortraitErrorBody;
  } catch {
    body = {};
  }
  const detailMessage =
    typeof body.detail === "string"
      ? body.detail
      : body.detail && typeof body.detail === "object" && "status" in body.detail
        ? String((body.detail as { status: unknown }).status)
        : null;
  const message = body.error?.message ?? detailMessage ?? "请求失败（HTTP " + response.status + "）";
  const requestId = body.request_id ?? response.headers.get("x-request-id");
  return new ApiError({
    status: response.status,
    message,
    code: body.error?.code,
    requestId,
    details: body.error?.details ?? body.detail,
  });
}

export async function apiRaw<T>(path: string, init: RequestInit = {}, timeoutMs = 30_000): Promise<ApiRawResponse<T>> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  const upstreamSignal = init.signal;
  const abortFromUpstream = () => controller.abort();
  upstreamSignal?.addEventListener("abort", abortFromUpstream, { once: true });
  try {
    const response = await fetch(path, {
      ...init,
      headers: requestHeaders(init.headers, init.body),
      signal: controller.signal,
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      const error = await decodeError(response);
      if (response.status === 401) window.dispatchEvent(new CustomEvent("portrait:unauthorized"));
      throw error;
    }
    const body = response.status === 204 ? undefined : await response.json();
    return {
      body: body as T,
      httpStatus: response.status,
      requestId:
        response.headers.get("x-request-id") ??
        (body && typeof body === "object" && "request_id" in body
          ? String((body as { request_id?: unknown }).request_id ?? "")
          : null),
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError({ status: 0, message: "请求超时或已取消" });
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    upstreamSignal?.removeEventListener("abort", abortFromUpstream);
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}, timeoutMs = 30_000): Promise<T> {
  const response = await apiRaw<PortraitEnvelope<T> | T>(path, init, timeoutMs);
  const body = response.body;
  if (body && typeof body === "object" && "status" in body && "data" in body) {
    return (body as PortraitEnvelope<T>).data;
  }
  return body as T;
}

export async function apiText(path: string, timeoutMs = 15_000): Promise<string> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      headers: requestHeaders(),
      signal: controller.signal,
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) throw await decodeError(response);
    return await response.text();
  } finally {
    window.clearTimeout(timeout);
  }
}

export function jsonBody(value: unknown): string {
  return JSON.stringify(value);
}