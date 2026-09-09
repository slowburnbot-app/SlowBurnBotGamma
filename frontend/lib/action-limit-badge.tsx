import type { Account, ActionLimit } from "@/lib/api";

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
 * One "account status" table cell for a verb. Same conventions as the other
 * views of the accounts table: "ok" for no active limit, plain text in the
 * row's own colour, no brackets (on this dashboard [ ] marks a
 * link or an input). Only an active limit is coloured, in the warning colour,
 * like the badge next to the account name. The strike count and the reason
 * are in the tooltip and on the per-account status page.
 */
export function LimitCell({ limits, verb }: { limits: ActionLimit[] | undefined; verb: string }) {
  const active = limits?.find((l) => l.action === verb);
  if (!active) return <>ok</>;
  return (
    <span className="text-status-warning" title={`${active.reason || active.tier} (strike ${active.strike})`}>
      {`until ${fmtLimitUntil(active.until)}`}
    </span>
  );
}

/** The "status page" table cell: "clean 09/07" in the row colour, a flagged result in the warning colour, "----" when never read. */
export function StatusPageCell({ account }: { account: Account }) {
  if (!account.status_page_checked_at) return <>----</>;
  const checked = fmtLimitUntil(account.status_page_checked_at);
  const clean = account.status_page_result === "clean";
  return (
    <span title={`checked ${checked}`}>
      <span className={clean ? "" : "text-status-warning"}>{account.status_page_result ?? "?"}</span>
      {` ${checked.slice(0, 5)}`}
    </span>
  );
}
