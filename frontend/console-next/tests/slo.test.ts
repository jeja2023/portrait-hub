import { describe, expect, it } from "vitest";

import {
  deviceQueueDepths,
  histogramPercentile,
  summarizeSloCallLogs,
  summarizeSloMetrics,
} from "../src/utils/slo";

const metrics = [
  "gpu_worker_requests_total 200",
  'gpu_worker_requests_total{status="200",status_class="2xx"} 198',
  'gpu_worker_requests_total{status="400",status_class="4xx"} 1',
  'gpu_worker_requests_total{status="500",status_class="5xx"} 1',
  'gpu_worker_queue_seconds_bucket{le="0.1"} 80',
  'gpu_worker_queue_seconds_bucket{le="0.5"} 99',
  'gpu_worker_queue_seconds_bucket{le="+Inf"} 100',
  "gpu_worker_queue_seconds_count 100",
  'gpu_worker_gpu_device_queue_depth{device="0"} 2',
  'gpu_worker_gpu_device_queue_depth{device="1"} 3',
].join("\n");

describe("SLO calculations", () => {
  it("uses call log outcomes for availability and burn rate", () => {
    const summary = summarizeSloCallLogs([
      { status: "success" },
      { status: "success" },
      { status: "error" },
    ]);

    expect(summary.request_count).toBe(3);
    expect(summary.error_count).toBe(1);
    expect(summary.success_rate).toBeCloseTo(2 / 3);
    expect(summary.error_budget_remaining).toBe(0);
    expect(summary.error_budget_burn_rate).toBeGreaterThan(60);
  });

  it("falls back to Prometheus status classes and exposes queue signals", () => {
    const summary = summarizeSloMetrics(metrics);

    expect(summary.request_count).toBe(200);
    expect(summary.error_count).toBe(2);
    expect(summary.success_rate).toBe(0.99);
    expect(histogramPercentile(metrics, "gpu_worker_queue_seconds", 0.95)).toBe(0.5);
    expect(deviceQueueDepths(metrics)).toEqual({ "0": 2, "1": 3 });
  });
});