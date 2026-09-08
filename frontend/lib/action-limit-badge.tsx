import type { Account, ActionLimit } from "@/lib/api";
import { Bracket } from "@/lib/bracket";

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

/**
 * One "account status" table cell for a verb: [no limits] in the ok colour, or
 * [until 09/07 02:14PM] in the warning colour. The strike count and the reason
 * are in the tooltip and on the per-account status page, not in the table, so
 * the 4 status columns fit the accounts box without a horizontal scroll.
 */
export function LimitCell({ limits, verb }: { limits: ActionLimit[] | undefined; verb: string }) {
  const active = limits?.find((l) => l.action === verb);
  if (!active) return <Bracket className="text-status-ok">no limits</Bracket>;
  return (
    <span title={`${active.reason || active.tier} (strike ${active.strike})`}>
      <Bracket className="text-status-warning">{`until ${fmtLimitUntil(active.until)}`}</Bracket>
    </span>
  );
}

/** The "status page" table cell: [clean] 09/07 (full check time in the tooltip), or [------] when never read. */
export function StatusPageCell({ account }: { account: Account }) {
  if (!account.status_page_checked_at) return <Bracket className="text-base04">------</Bracket>;
  const checked = fmtLimitUntil(account.status_page_checked_at);
  return (
    <span title={`checked ${checked}`}>
      <Bracket className={account.status_page_result === "clean" ? "text-status-ok" : "text-status-warning"}>
        {account.status_page_result ?? "?"}
      </Bracket>
      <span className="text-base04">{` ${checked.slice(0, 5)}`}</span>
    </span>
  );
}
