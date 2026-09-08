"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { adminListAccounts, adminDeleteAccount, AdminAccount } from "@/lib/api";
import { Bracket } from "@/lib/bracket";

export default function AdminAccountsPage() {
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    adminListAccounts().then(setAccounts).catch(() => {});
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDelete(account: AdminAccount) {
    if (!confirm(`Delete account "${account.name}" (${account.user_email})? This cannot be undone.`)) return;
    setBusy(account.id);
    setError(null);
    try {
      await adminDeleteAccount(account.id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "delete failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4 font-mono">
      <h1 className="font-semibold text-base05">
        admin — accounts{" "}
        <span className="text-base04 font-normal">
          [{String(accounts.length).padStart(2, "0")}]
        </span>
      </h1>

      {error && <p className="text-status-bad text-sm">{error}</p>}

      <div className="border border-base03 bg-base01">
        {accounts.length === 0 ? (
          <p className="px-4 py-6 text-base04">no accounts found.</p>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-base04 border-b border-base03 bg-base02">
                <th className="px-4 py-2 font-normal">user</th>
                <th className="px-4 py-2 font-normal">account</th>
                <th className="px-4 py-2 font-normal">on</th>
                <th className="px-4 py-2 font-normal">grp</th>
                <th className="px-4 py-2 font-normal"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-base03">
              {accounts.map((a) => (
                <tr key={a.id} className="hover:bg-base02 transition-colors">
                  <td className="px-4 py-2 text-base04 text-sm">{a.user_email}</td>
                  <td className="px-4 py-2 text-base05">{a.name}</td>
                  <td className="px-4 py-2">
                    {a.system_disabled ? (
                      <Bracket className="text-base03">-</Bracket>
                    ) : (
                      <>
                        <span className="text-base04">[</span>
                        <span className={a.enabled ? "text-status-ok" : "text-status-bad"}>
                          {a.enabled ? "x" : "\u00a0"}
                        </span>
                        <span className="text-base04">]</span>
                      </>
                    )}
                  </td>
                  <td className="px-4 py-2 text-base04">
                    {a.group_number != null ? String(a.group_number).padStart(2, "0") : "—"}
                  </td>
                  <td className="px-4 py-2 text-right space-x-2">
                    <Link
                      href={`/admin/accounts/${a.id}/follow-targets`}
                      className="group transition-colors"
                    >
                      <Bracket className="text-base04 group-hover:text-base0e">
                        follow-targets
                      </Bracket>
                    </Link>
                    <button
                      onClick={() => handleDelete(a)}
                      disabled={busy === a.id}
                      className="group disabled:opacity-50 transition-colors bg-base11 border border-base03 px-2 py-0.5"
                    >
                      <Bracket className="text-status-bad group-hover:text-base05">
                        {busy === a.id ? "..." : "delete"}
                      </Bracket>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
    </div>
  );
}
