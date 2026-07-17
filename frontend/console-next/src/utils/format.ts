const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function formatTimestamp(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "--";
  const numeric = typeof value === "number" ? value : Number(value);
  const date = Number.isFinite(numeric)
    ? new Date(numeric < 10_000_000_000 ? numeric * 1000 : numeric)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? "--" : dateTimeFormatter.format(date);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "--";
  return new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 }).format(value);
}

export const statusLabels: Record<string, string> = {
  queued: "等待中",
  running: "处理中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  active: "运行中",
  stopped: "已停止",
};
