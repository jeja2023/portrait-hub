import { sessionState } from "../auth/session";
import { apiRequest, jsonBody } from "./client";
import type { WebSocketTicketResponse } from "./contracts";

export type LiveConnectionState = "connecting" | "open" | "degraded" | "closed";

export async function openTicketWebSocket(input: {
  resourceType: "job" | "stream";
  resourceId: string;
  onMessage: (payload: unknown) => void;
  onState: (state: LiveConnectionState) => void;
}): Promise<() => void> {
  let stopped = false;
  let socket: WebSocket | null = null;
  let retry = 0;
  let retryTimer: number | null = null;

  const connect = async (): Promise<void> => {
    if (stopped) return;
    input.onState("connecting");
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
        input.onState("open");
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
        input.onState(retry >= 3 ? "degraded" : "connecting");
        retryTimer = window.setTimeout(() => void connect(), Math.min(30_000, 1000 * 2 ** retry));
      };
    } catch {
      if (stopped) return;
      retry += 1;
      input.onState("degraded");
      retryTimer = window.setTimeout(() => void connect(), Math.min(30_000, 1000 * 2 ** retry));
    }
  };

  void connect();
  return () => {
    stopped = true;
    if (retryTimer !== null) window.clearTimeout(retryTimer);
    socket?.close();
    input.onState("closed");
  };
}
