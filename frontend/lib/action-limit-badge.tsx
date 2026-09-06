import type { ActionLimit } from "@/lib/api";

/** "09/07 02:14PM" in the viewer's local time for an ISO UTC timestamp. */
export function fmtLimitUntil(iso: string | null | undefined): string {
  if (!iso) return "------";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "------";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const h = d.getHours();
  const period = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${mm}/${dd} ${String(h12).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}${period}`;
}

/**
 * Inline [like·limited] badges for an account's active Instagram action-limit
 * cooldowns. Renders nothing when there are none, so it can sit next to the
 * account name in any table without reserving space.
 */
export function ActionLimitBadges({ limits }: { limits: ActionLimit[] | undefined }) {
  if (!limits || limits.length === 0) return null;
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      {limits.map((l) => (
        <span
          key={l.action}
          className="text-base05"
          title={`${l.action} limited until ${fmtLimitUntil(l.until)} — ${l.reason || l.tier} (strike ${l.strike})`}
        >
          [<span className="text-status-warning">{l.action}·limited</span>]
        </span>
      ))}
    </span>
  );
}
