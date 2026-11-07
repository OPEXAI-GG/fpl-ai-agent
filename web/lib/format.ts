// Formatting helpers. Money is stored in tenths of £m, as FPL does.

export const money = (tenths: number | null | undefined) =>
  tenths == null ? "—" : `£${(tenths / 10).toFixed(1)}m`;

export const int = (n: number | null | undefined) =>
  n == null ? "—" : new Intl.NumberFormat("en-GB").format(n);

export const pts = (n: number | null | undefined, digits = 1) =>
  n == null ? "—" : n.toFixed(digits);

export const signed = (n: number | null | undefined, digits = 1) =>
  n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(digits)}`;

export const pct = (n: number | null | undefined, digits = 0) =>
  n == null ? "—" : `${(n * 100).toFixed(digits)}%`;

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function dateTime(iso: string | number | null | undefined, tz?: string | null) {
  if (iso == null) return "—";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: tz || undefined,
    timeZoneName: "short",
  }).format(d);
}

export function ago(seconds: number | null | undefined) {
  if (seconds == null) return "unknown";
  const s = Math.max(0, seconds);
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export function agoFrom(unix: number | null | undefined) {
  return unix == null ? "unknown" : ago(Date.now() / 1000 - unix);
}

export function until(iso: string | null | undefined) {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return "passed";
  const h = Math.floor(ms / 3600000);
  const d = Math.floor(h / 24);
  if (d >= 2) return `in ${d} days`;
  if (h >= 1) return `in ${h} h ${Math.floor((ms % 3600000) / 60000)} min`;
  return `in ${Math.max(1, Math.floor(ms / 60000))} min`;
}

export const CHIP_NAMES: Record<string, string> = {
  wildcard: "Wildcard",
  freehit: "Free Hit",
  bboost: "Bench Boost",
  "3xc": "Triple Captain",
};

export function actionText(action: string, chip: string | null) {
  if (chip) return `Play ${CHIP_NAMES[chip] ?? chip}`;
  if (action === "roll the free transfer") return "Roll your transfer";
  if (action === "make transfers") return "Make transfers";
  return action;
}
