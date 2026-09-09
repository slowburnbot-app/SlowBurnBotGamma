/**
 * Format a time string (e.g. "09:00", "21:30:00") as "09:00AM" / "09:30PM"
 */
export function formatTime(t: string | null | undefined): string {
  if (!t) return "?";
  const match = t.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return t;
  const h = parseInt(match[1], 10);
  const m = parseInt(match[2], 10);
  const period = h >= 12 ? "PM" : "AM";
  const hour12 = h % 12 || 12;
  return `${String(hour12).padStart(2, "0")}:${String(m).padStart(2, "0")}${period}`;
}

/**
 * Compact form for table cells: "9AM" when the minutes are zero, else "9:30PM".
 * The settings page inputs keep formatTime (their parser expects "HH:MMAM").
 */
export function formatTimeShort(t: string | null | undefined): string {
  const full = formatTime(t);
  const match = full.match(/^0?(\d{1,2}):(\d{2})(AM|PM)$/);
  if (!match) return full;
  return match[2] === "00" ? `${match[1]}${match[3]}` : `${match[1]}:${match[2]}${match[3]}`;
}

/**
 * Build a condensed schedule label: "daily 9AM-10PM"
 */
export function scheduleLabel(s: {
  schedule_days?: string | null;
  schedule_start?: string | null;
  schedule_end?: string | null;
  max_runs_per_day?: number | null;
} | undefined): string {
  if (!s) return "—";
  const parts: string[] = [];
  if (s.schedule_days) parts.push(s.schedule_days);
  if (s.schedule_start || s.schedule_end) {
    parts.push(`${formatTimeShort(s.schedule_start)}-${formatTimeShort(s.schedule_end)}`);
  }
  return parts.length ? parts.join(" ") : "—";
}

/** True when stored type is a placeholder or a mis-mapped number (bad runlog import), not a real action label. */
export function isInvalidSessionActionType(type: string | null | undefined): boolean {
  if (type == null || type.trim() === "") return true;
  const t = type.trim();
  if (t === "—" || t === "--" || t === "-") return true;
  const normalized = t.replace(/,/g, "");
  return /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/.test(normalized);
}

/**
 * Display one session-log action slot. Hides junk types so stats columns are not shown as fake actions.
 */
export function formatSessionAction(type: string | null | undefined, count: number): string {
  if (isInvalidSessionActionType(type)) return "—";
  return `${type!.trim()} (${count})`;
}
