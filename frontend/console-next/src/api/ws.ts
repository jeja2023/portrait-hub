import { sessionState } from "../auth/session";
import { apiRequest, jsonBody } from "./client";
import type { WebSocketTicketResponse } from "./contracts";

export type LiveConnectionState = "connecting" | "open" | "degraded" | "closed";

export async function openTicketWebSocket(input: {
  resourceType: "job" | "stream";
  resourceId: string;
  onMessage: (payload: unknown) => void;
  onState: (state: LiveConnectionState) => void;
  poll?: () => Promise<void> | void;
  pollIntervalMs?: number;
}): Promise<() => void> {
  let stopped = false;
  let socket: WebSocket | null = null;
  let retry = 0;
  let retryTimer: number | null = null;
  let pollTimer: number | null = null;
  let pollInFlight = false;

  const stopPolling = (): void => {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const pollOnce = async (): Promise<void> => {
    if (stopped || pollInFlight || !input.poll || document.visibilityState !== "visible") return;
    pollInFlight = true;
    try {
      await input.poll();
    } catch {
      input.onState("degraded");
    } finally {
      pollInFlight = false;
    }
  };

  const startPolling = (): void => {
    if (pollTimer !== null || !input.poll) return;
    void pollOnce();
    pollTimer = window.setInterval(() => void pollOnce(), input.pollIntervalMs ?? 5_000);
  };

  const setState = (state: LiveConnectionState): void => {
    input.onState(state);
    if (state === "degraded") startPolling();
    if (state === "open") stopPolling();
  };

  const connect = async (): Promise<void> => {
    if (stopped) return;
    setState("connecting");
    try {
      const issued = await apiRequest<WebSocketTicketResponse>("/v1/console/ws-ticket", {
        method: "POST",
        body: jsonBody({ resource_type: input.resourceType, resource_id: input.resourceId }),
      });
      const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
      const query = new URLSearchParams({ tenant_id: sessionState.tenantId, ticket: issued.ticket });
      socket = new WebSocket(`${scheme}//${window.location.host}${issued.websocket_path}?${query}`);
      socket.onopen = () => {
        retry = 0;
        setState("open");
      };
      socket.onmessage = (event) => {
        try {
          input.onMessage(JSON.parse(String(event.data)) as unknown);
        } catch {
          input.onMessage({ status: "invalid_payload" });
        }
      };
      socket.onclose = () => {
        if (stopped) return;
        retry += 1;
        setState(retry >= 3 ? "degraded" : "connecting");
        retryTimer = window.setTimeout(() => void connect(), Math.min(30_000, 1000 * 2 ** retry));
      };
    } catch {
      if (stopped) return;
      retry += 1;
      setState("degraded");
      retryTimer = window.setTimeout(() => void connect(), Math.min(30_000, 1000 * 2 ** retry));
    }
  };

  void connect();
  return () => {
    stopped = true;
    stopPolling();
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    socket?.close();
    setState("closed");
  };
}
