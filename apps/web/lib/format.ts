const tokenFormatter = new Intl.NumberFormat("zh-CN");

/** Format a duration as a compact, human-readable value with stable units. */
export function formatDurationMs(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const milliseconds = Math.max(0, value);
  if (milliseconds < 1_000) return `${Math.round(milliseconds)} 毫秒`;
  if (milliseconds < 60_000) {
    const seconds = milliseconds / 1_000;
    return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} 秒`;
  }
  const totalSeconds = Math.round(milliseconds / 1_000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) {
    return `${minutes} 分${seconds > 0 ? ` ${String(seconds).padStart(2, "0")} 秒` : ""}`;
  }
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return `${hours} 小时${remainingMinutes > 0 ? ` ${remainingMinutes} 分` : ""}`;
}

/** Keep token counts scannable without changing their stored integer value. */
export function formatTokenCount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return tokenFormatter.format(Math.max(0, Math.round(value)));
}

/** Format an optional provider cost without inventing a currency or precision. */
export function formatCost(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (numeric === 0) return "0";
  return numeric.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}
