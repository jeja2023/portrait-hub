export const SLO_WINDOW_SECONDS = 30 * 24 * 60 * 60;
export const SLO_AVAILABILITY_TARGET = 0.995;

export interface MetricSample {
  labels: string;
  value: number;
}

export interface SloCallLog {
  status?: string;
  http_status?: number;
  status_code?: number;
  created_at?: number;
}

export interface AvailabilitySummary {
  request_count: number;
  success_count: number;
  error_count: number;
  success_rate: number;
  error_budget_remaining: number;
  error_budget_burn_rate: number;
}

export function metricSamples(rawMetrics: string, name: string): MetricSample[] {
  const prefix = name + " ";
  const labeledPrefix = name + "{";
  return rawMetrics.split("\n").flatMap((line) => {
    if (!line.startsWith(prefix) && !line.startsWith(labeledPrefix)) return [];
    const separator = line.lastIndexOf(" ");
    const value = Number(line.slice(separator + 1));
    if (!Number.isFinite(value)) return [];
    const labels = line.startsWith(labeledPrefix) ? line.slice(name.length + 1, line.indexOf("}")) : "";
    return [{ labels, value }];
  });
}

export function metricValue(rawMetrics: string, name: string): number {
  return metricSamples(rawMetrics, name).find((sample) => sample.labels === "")?.value ?? 0;
}

export function histogramPercentile(rawMetrics: string, name: string, percentile: number): number {
  const buckets = metricSamples(rawMetrics, name + "_bucket")
    .map((sample) => {
      const match = /(?:^|,)le="([^"]+)"/.exec(sample.labels);
      return {
        boundary: match?.[1] === "+Inf" ? Number.POSITIVE_INFINITY : Number(match?.[1]),
        count: sample.value,
      };
    })
    .filter((bucket) => Number.isFinite(bucket.boundary) || bucket.boundary === Number.POSITIVE_INFINITY)
    .sort((left, right) => left.boundary - right.boundary);
  const total = metricValue(rawMetrics, name + "_count");
  if (total <= 0) return 0;
  const target = total * Math.min(1, Math.max(0, percentile));
  return buckets.find((bucket) => bucket.count >= target)?.boundary ?? 0;
}

export function metricLabel(labels: string, key: string): string | null {
  const match = new RegExp("(?:^|,)" + key + "=\"([^\"]+)\"").exec(labels);
  return match?.[1] ?? null;
}

export function deviceQueueDepths(rawMetrics: string): Record<string, number> {
  return Object.fromEntries(
    metricSamples(rawMetrics, "gpu_worker_gpu_device_queue_depth").flatMap((sample) => {
      const device = metricLabel(sample.labels, "device");
      return device === null ? [] : [[device, sample.value]];
    }),
  );
}

function availabilitySummary(requestCount: number, errorCount: number): AvailabilitySummary {
  const boundedRequests = Math.max(0, requestCount);
  const boundedErrors = Math.min(boundedRequests, Math.max(0, errorCount));
  const successCount = boundedRequests - boundedErrors;
  const successRate = boundedRequests > 0 ? successCount / boundedRequests : 1;
  const observedErrorRate = boundedRequests > 0 ? boundedErrors / boundedRequests : 0;
  const allowedErrorRate = 1 - SLO_AVAILABILITY_TARGET;
  return {
    request_count: boundedRequests,
    success_count: successCount,
    error_count: boundedErrors,
    success_rate: successRate,
    error_budget_remaining: Math.min(1, Math.max(0, (allowedErrorRate - observedErrorRate) / allowedErrorRate)),
    error_budget_burn_rate: observedErrorRate / allowedErrorRate,
  };
}

export function summarizeSloCallLogs(logs: SloCallLog[]): AvailabilitySummary {
  const errors = logs.filter((log) => {
    if (log.status) return log.status !== "success";
    const status = Number(log.http_status ?? log.status_code ?? 0);
    return status >= 400;
  }).length;
  return availabilitySummary(logs.length, errors);
}

export function summarizeSloMetrics(rawMetrics: string): AvailabilitySummary {
  const requests = metricValue(rawMetrics, "gpu_worker_requests_total");
  const errors = metricSamples(rawMetrics, "gpu_worker_requests_total")
    .filter((sample) => /status_class="[45]xx"/.test(sample.labels))
    .reduce((sum, sample) => sum + sample.value, 0);
  return availabilitySummary(requests, errors);
}