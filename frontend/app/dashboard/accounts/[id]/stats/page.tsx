"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { getAccounts, getAccountSourceStats, Account, SourceStat } from "@/lib/api";
import { Dropdown } from "@/lib/dropdown";

type SortKey = "source" | "complete" | "followed_back" | "rate" | "daily";
type SortDir = "asc" | "desc";
type Period = "day" | "week" | "month" | "all";

function fmtPct(v: number | null): string {
  if (v == null) return "----";
  return `${Math.round(v * 100)}%`;
}

const periodOptions = [
  { value: "day", label: "today" },
  { value: "week", label: "last 7 days" },
  { value: "month", label: "last 30 days" },
  { value: "all", label: "all time" },
];

export default function AccountStatsPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [items, setItems] = useState<SourceStat[]>([]);
  const [days, setDays] = useState(1);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState<Period>("week");
  const [sortKey, setSortKey] = useState<SortKey>("complete");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function SortTh({ label, field, className = "" }: { label: string; field: SortKey; className?: string }) {
    const active = sortKey === field;
    const arrow = active ? (sortDir === "asc" ? "\u2191" : "\u2193") : "\u00a0";
    return (
      <th
        className={`px-4 py-2 font-normal cursor-pointer select-none transition-colors hover:text-base05 ${active ? "text-base0d" : ""} ${className}`}
        onClick={() => toggleSort(field)}
      >
        <span className="whitespace-nowrap">{label}<span className="inline-block w-[1em] text-center">{arrow}</span></span>
      </th>
    );
  }

  const daily = (s: SourceStat) => s.followed_back / days;

  const sorted = useMemo(() => {
    const list = [...items];
    const dir = sortDir === "asc" ? 1 : -1;
    list.sort((a, b) => {
      const val = (s: SourceStat) =>
        sortKey === "source" ? (s.source ?? "").toLowerCase()
        : sortKey === "daily" ? daily(s)
        : (s[sortKey as keyof SourceStat] ?? -1);
      const av = val(a);
      const bv = val(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
    return list;
  }, [items, sortKey, sortDir, days]);

  const totals = useMemo(() => {
    const t = { total: 0, complete: 0, followed_back: 0 };
    for (const s of items) {
      t.total += s.total;
      t.complete += s.complete;
      t.followed_back += s.followed_back;
    }
    return { ...t, rate: t.complete ? t.followed_back / t.complete : null, daily: t.followed_back / days };
  }, [items, days]);

  useEffect(() => {
    getAccounts()
      .then((all) => {
        const found = all.find((a) => a.id === id);
        if (!found) router.push("/dashboard/accounts");
        else setAccount(found);
      })
      .catch(() => router.push("/dashboard/accounts"));
  }, [id, router]);

  useEffect(() => {
    setLoading(true);
    getAccountSourceStats(id, period)
      .then((res) => { setItems(res.items); setDays(res.days ?? 1); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id, period]);

  if (!account) return null;

  return (
    <div className="space-y-4 font-mono">
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <Link href="/dashboard/accounts" className="text-base04 hover:text-base05 transition-colors">accounts</Link>
        <span className="text-base03">-</span>
        <Link href="/dashboard/accounts?tab=stats" className="text-base04 hover:text-base05 transition-colors">stats</Link>
        <span className="text-base03">-</span>
        <span className="text-base05">{account.name}</span>
      </div>

      <div className="border border-base02 bg-base01">
        <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-base04 border-b border-base02 bg-base02">
              <SortTh label="source" field="source" />
              <SortTh label="Complete" field="complete" />
              <SortTh label="Followed Back" field="followed_back" />
              <SortTh label="Success Rate" field="rate" />
              <SortTh label="Daily" field="daily" />
              <th className="px-2 py-2 font-normal w-full text-right whitespace-nowrap">
                <span className="inline-flex items-center gap-0">
                  <span className="text-base04">{"results:\u00a0 "}</span>
                  <span className="text-base05">{"["}</span>
                  <Dropdown
                    value={period}
                    onChange={(v) => setPeriod(v as Period)}
                    options={periodOptions}
                  />
                  <span className="text-base05">{"]"}</span>
                </span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-base02">
            {loading ? (
              <tr><td colSpan={6} className="px-4 py-6 text-base04">loading&hellip;</td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-6 text-base04">no follow data yet.</td></tr>
            ) : (
              sorted.map((s, i) => (
                <tr key={i} className="hover:bg-base02/60 transition-colors">
                  <td className="px-4 py-1.5 text-base05">{s.source ?? "—"}</td>
                  <td className="px-4 py-1.5 text-base04">{s.complete.toLocaleString()}</td>
                  <td className="px-4 py-1.5 text-base04">{s.followed_back.toLocaleString()}</td>
                  <td className="px-4 py-1.5 text-base04">{fmtPct(s.rate)}</td>
                  <td className="px-4 py-1.5 text-base04">{daily(s).toFixed(1)}</td>
                  <td></td>
                </tr>
              ))
            )}
          </tbody>
          {items.length > 0 && (
            <tfoot>
              <tr className="text-base04 border-t border-base02 bg-base02">
                <td className="px-4 py-2 text-base05">total</td>
                <td className="px-4 py-2">{totals.complete.toLocaleString()}</td>
                <td className="px-4 py-2">{totals.followed_back.toLocaleString()}</td>
                <td className="px-4 py-2">{fmtPct(totals.rate)}</td>
                <td className="px-4 py-2">{totals.daily.toFixed(1)}</td>
                <td></td>
              </tr>
            </tfoot>
          )}
        </table>
        </div>
      </div>
    </div>
  );
}
