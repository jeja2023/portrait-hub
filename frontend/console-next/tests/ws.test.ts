import { afterEach, describe, expect, it, vi } from "vitest";

const { apiRequestMock } = vi.hoisted(() => ({
  apiRequestMock: vi.fn(),
}));

vi.mock("../src/api/client", () => ({
  apiRequest: apiRequestMock,
  jsonBody: (value: unknown) => JSON.stringify(value),
}));

import { openTicketWebSocket, type LiveConnectionState } from "../src/api/ws";

describe("ticket websocket fallback", () => {
  afterEach(() => {
    vi.useRealTimers();
    apiRequestMock.mockReset();
  });

  it("polls while degraded and stops polling when disposed", async () => {
    vi.useFakeTimers();
    apiRequestMock.mockRejectedValue(new Error("ticket unavailable"));
    const poll = vi.fn().mockResolvedValue(undefined);
    const states: LiveConnectionState[] = [];

    const stop = await openTicketWebSocket({
      resourceType: "job",
      resourceId: "job-1",
      onMessage: vi.fn(),
      onState: (state) => states.push(state),
      poll,
      pollIntervalMs: 1_000,
    });
    await Promise.resolve();
    await Promise.resolve();

    expect(states).toContain("degraded");
    expect(poll).toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1_100);
    expect(poll.mock.calls.length).toBeGreaterThanOrEqual(2);

    stop();
    const callsAfterStop = poll.mock.calls.length;
    await vi.advanceTimersByTimeAsync(2_000);
    expect(poll).toHaveBeenCalledTimes(callsAfterStop);
    expect(states.at(-1)).toBe("closed");
  });
});
