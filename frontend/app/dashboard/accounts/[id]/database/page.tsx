"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { getAccounts, getAccountDatabase, downloadAccountDatabaseCsv, Account, FollowTarget } from "@/lib/api";

const PAGE_SIZE = 100;

type SortKey = "handle" | "source" | "status" | "followed" | "unfollowed" | "fb";
type SortDir = "asc" | "desc";

function statusCls(status: string): string {
  if (status === "done") return "text-status-ok";
  if (status === "skipped") return "text-base04";
  return "text-base05";
}

export default function AccountDatabasePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [items, setItems] = useState<FollowTarget[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState<SortKey>("followed");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [exporting, setExporting] = useState(false);

  async function handleExport() {
    if (!account) return;
    setExporting(true);
    try {
      await downloadAccountDatabaseCsv(id, account.name);
    } catch {
      // swallow — link will simply not download
    } finally {
      setExporting(false);
    }
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
    setPage(1);
  }

  function SortTh({ label, field, className = "" }: { label: string; field: SortKey; className?: string }) {
    const active = sortKey === field;
    const arrow = active ? (sortDir === "asc" ? "↑" : "↓") : "\u00a0";
    return (
      <th
        className={`px-4 py-2 font-normal cursor-pointer select-none transition-colors hover:text-base05 ${active ? "text-base0e" : ""} ${className}`}
        onClick={() => toggleSort(field)}
      >
        <span className="whitespace-nowrap">{label}<span className="inline-block w-[1em] text-center">{arrow}</span></span>
      </th>
    );
  }

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
    getAccountDatabase(id, page, PAGE_SIZE, sortKey, sortDir)
      .then((res) => {
        setItems(res.items);
        setTotal(res.total);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id, page, sortKey, sortDir]);

  const totalPages = Math.ceil(total / PAGE_SIZE);

  if (!account) return null;

  return (
    <div className="space-y-4 font-mono">
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <Link href="/dashboard/accounts" className="text-base04 hover:text-base05 transition-colors">accounts</Link>
        <span className="text-base03">-</span>
        <Link href="/dashboard/accounts?tab=database" className="text-base04 hover:text-base05 transition-colors">database</Link>
        <span className="text-base03">-</span>
        <span className="text-base05">{account.name}</span>
        <span className="text-base04 ml-auto">[{total.toLocaleString()} records]</span>
      </div>

      <div className="border border-base03 bg-base01">
        {loading ? (
          <p className="px-4 py-6 text-base04">loading…</p>
        ) : items.length === 0 ? (
          <p className="px-4 py-6 text-base04">no records found.</p>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-base04 border-b border-base03 bg-base02">
                <SortTh label="handle" field="handle" />
                <SortTh label="source" field="source" />
                <SortTh label="status" field="status" />
                <SortTh label="followed" field="followed" />
                <SortTh label="unfollowed" field="unfollowed" />
                <SortTh label="fb" field="fb" />
              </tr>
            </thead>
            <tbody className="divide-y divide-base03">
              {items.map((t) => (
                <tr key={t.id} className="hover:bg-base02 transition-colors">
                  <td className="px-4 py-1.5 text-base05">{t.target_handle}</td>
                  <td className="px-4 py-1.5 text-base04 text-xs">{t.source ?? "—"}</td>
                  <td className={`px-4 py-1.5 ${statusCls(t.status)}`}>{t.status}</td>
                  <td className="px-4 py-1.5 text-base04">{t.follow_date ?? "—"}</td>
                  <td className="px-4 py-1.5 text-base04">{t.unfollow_date ?? "—"}</td>
                  <td className={`px-4 py-1.5 ${t.follow_back === true ? "text-status-ok" : t.follow_back === false ? "text-status-bad" : "text-base04"}`}>
                    {t.follow_back === true ? "yes" : t.follow_back === false ? "no" : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>

      <div className="flex items-center gap-4 text-sm flex-wrap">
        {totalPages > 1 && (
          <>
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              className="disabled:opacity-30 text-base04 hover:text-base05 transition-colors"
            >
              [prev]
            </button>
            <span className="text-base04">
              page {page} / {totalPages}
            </span>
            <button
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              className="disabled:opacity-30 text-base04 hover:text-base05 transition-colors"
            >
              [next]
            </button>
          </>
        )}
        {total > 0 && (
          <button
            onClick={handleExport}
            disabled={exporting}
            className="ml-auto disabled:opacity-30 text-base04 hover:text-base05 transition-colors"
          >
            {exporting ? "[exporting…]" : "[download csv]"}
          </button>
        )}
      </div>
    </div>
  );
}
