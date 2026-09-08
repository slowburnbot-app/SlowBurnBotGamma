"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { getAccounts, getAccountActionLimits, Account, ActionLimitEvent } from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { fmtLimitUntil } from "@/lib/action-limit-badge";

const sectionCls = "border border-base02 bg-base01";

// Read-only: Instagram throttling detected by the bot for this account
// (active cooldowns per verb, the last Account Status page read, and the
// event history). Nothing here is editable.
export default function AccountStatusPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [events, setEvents] = useState<ActionLimitEvent[]>([]);

  useEffect(() => {
    getAccounts()
      .then((all) => {
        const found = all.find((a) => a.id === id);
        if (!found) router.push("/dashboard/accounts");
        else setAccount(found);
      })
      .catch(() => router.push("/dashboard/accounts"));
    getAccountActionLimits(id, 25).then(setEvents).catch(() => {});
  }, [id, router]);

  if (!account) return null;

  return (
    <div className="space-y-4 font-mono">
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <Link href="/dashboard/accounts" className="text-base04 hover:text-base05 transition-colors">accounts</Link>
        <span className="text-base03">-</span>
        <Link href="/dashboard/accounts?tab=status" className="text-base04 hover:text-base05 transition-colors">account status</Link>
        <span className="text-base03">-</span>
        <span className="text-base05">{account.name}</span>
      </div>

      <div className={sectionCls}>
        <div className="px-4 py-2 border-b border-base02 text-base04 bg-base02">account status and limits</div>
        <div className="px-4 py-3 space-y-2">
          <div className="grid gap-x-3 gap-y-1" style={{ gridTemplateColumns: "12ch auto" }}>
            <span className="text-base04">actions:</span>
            <span className="flex items-center gap-x-5 gap-y-1 flex-wrap">
              {(["like", "follow", "unfollow"] as const).map((verb) => {
                const active = account.action_limits?.find((l) => l.action === verb);
                return (
                  <span key={verb} className="inline-flex items-center gap-0">
                    <span className="text-base04">{`${verb}: `}</span>
                    {active ? (
                      <span>
                        <span className="text-base05">{"["}</span>
                        <span className="text-status-warning">{`limited until ${fmtLimitUntil(active.until)}`}</span>
                        <span className="text-base05">{"]"}</span>
                        <span className="text-base04">{` strike ${active.strike} — ${active.reason || active.tier}`}</span>
                      </span>
                    ) : (
                      <Bracket className="text-status-ok">no limits</Bracket>
                    )}
                  </span>
                );
              })}
            </span>
            <span className="text-base04">status page:</span>
            <span>
              {account.status_page_checked_at ? (
                <>
                  <Bracket className={account.status_page_result === "clean" ? "text-status-ok" : "text-status-warning"}>
                    {account.status_page_result ?? "?"}
                  </Bracket>
                  <span className="text-base04">{` checked ${fmtLimitUntil(account.status_page_checked_at)}`}</span>
                </>
              ) : (
                <Bracket className="text-base04">------</Bracket>
              )}
            </span>
          </div>
          <div className="pt-2 border-t border-base02 space-y-0.5">
            <div className="text-base04">recent events</div>
            {events.length === 0 ? (
              <div className="text-base04">no limit events yet.</div>
            ) : (
              events.map((ev) => (
                <div key={ev.id} className="text-base04 whitespace-nowrap overflow-hidden text-ellipsis">
                  <span className="text-base05">{fmtLimitUntil(ev.created_at)}</span>
                  {` ${ev.action} ${ev.tier}`}
                  {ev.until ? ` → until ${fmtLimitUntil(ev.until)} (strike ${ev.strike})` : ""}
                  {ev.reason ? ` — ${ev.reason}` : ""}
                  {ev.cleared_reason ? ` [${ev.cleared_reason}]` : ""}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
