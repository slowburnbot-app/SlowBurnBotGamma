"use client";

import { useEffect, useState } from "react";
import {
  adminListUsers,
  adminSyncSubscription,
  adminActivateSubscription,
  adminDeactivateSubscription,
  adminSetTier,
  AdminUser,
} from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { Dropdown } from "@/lib/dropdown";

const TIER_OPTIONS = [
  { value: "crawl", label: "crawl" },
  { value: "walk", label: "walk" },
  { value: "run", label: "run" },
];

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  async function loadUsers() {
    adminListUsers().then(setUsers).catch(() => {});
  }

  useEffect(() => {
    loadUsers();
  }, []);

  async function handleSync(userId: string) {
    setBusy(userId);
    setMsg("");
    try {
      const res = await adminSyncSubscription(userId) as { status: string };
      setMsg(`synced — status: ${res.status}`);
      await loadUsers();
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "sync failed.");
    } finally {
      setBusy(null);
    }
  }

  async function handleToggleSubscription(user: AdminUser) {
    setBusy(user.id);
    setMsg("");
    try {
      const action = user.subscription_status === "active"
        ? adminDeactivateSubscription
        : (id: string) => adminActivateSubscription(id);
      const res = await action(user.id);
      setMsg(`${user.email} — ${res.status} / ${res.plan_tier}`);
      await loadUsers();
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "toggle failed.");
    } finally {
      setBusy(null);
    }
  }

  async function handleGrantTrial(user: AdminUser) {
    setBusy(user.id);
    setMsg("");
    try {
      const res = await adminActivateSubscription(user.id, 30);
      const until = res.current_period_end ? new Date(res.current_period_end).toLocaleDateString() : "?";
      setMsg(`${user.email} — 30-day trial (${res.plan_tier}) until ${until}`);
      await loadUsers();
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "trial grant failed.");
    } finally {
      setBusy(null);
    }
  }

  async function handleSetTier(user: AdminUser, tier: string) {
    if (tier === user.plan_tier) return;
    setBusy(user.id);
    setMsg("");
    try {
      const res = await adminSetTier(user.id, tier);
      setMsg(`${user.email} — tier set to ${res.plan_tier}`);
      await loadUsers();
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "tier change failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-4 font-mono">
      <h1 className="font-semibold text-base05">admin — users</h1>
      {msg && <p className="text-status-ok">{msg}</p>}
      <div className="border border-base03">
        {users.length === 0 ? (
          <p className="px-4 py-6 text-base04">no users found.</p>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-base04 border-b border-base03 bg-base01">
                <th className="px-4 py-2 font-normal">email</th>
                <th className="px-4 py-2 font-normal">tier</th>
                <th className="px-4 py-2 font-normal">status</th>
                <th className="px-4 py-2 font-normal">joined</th>
                <th className="px-4 py-2 font-normal"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-base03">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-base02 transition-colors">
                  <td className="px-4 py-2 text-base05">{u.email}</td>
                  <td className="px-4 py-2">
                    <span className="inline-flex items-center gap-0">
                      <span className="text-base05">{"["}</span>
                      <Dropdown
                        value={u.plan_tier}
                        onChange={(v) => handleSetTier(u, v)}
                        options={TIER_OPTIONS}
                        disabled={busy === u.id}
                      />
                      <span className="text-base05">{"]"}</span>
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <Bracket className={u.subscription_status === "active" ? "text-status-ok" : "text-base04"}>
                      {u.subscription_status}
                    </Bracket>
                  </td>
                  <td className="px-4 py-2 text-base04">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2 text-right space-x-2">
                    <button
                      onClick={() => handleToggleSubscription(u)}
                      disabled={busy === u.id}
                      className="group disabled:opacity-50 transition-colors"
                    >
                      <Bracket className={u.subscription_status === "active" ? "text-base04 group-hover:text-base05" : "text-status-ok group-hover:text-base05"}>
                        {busy === u.id ? "..." : u.subscription_status === "active" ? "deactivate" : "activate"}
                      </Bracket>
                    </button>
                    <button
                      onClick={() => handleGrantTrial(u)}
                      disabled={busy === u.id}
                      className="group disabled:opacity-50 transition-colors"
                    >
                      <Bracket className="text-base0e group-hover:text-base05">
                        {busy === u.id ? "..." : "trial 30d"}
                      </Bracket>
                    </button>
                    <button
                      onClick={() => handleSync(u.id)}
                      disabled={busy === u.id}
                      className="group disabled:opacity-50 transition-colors"
                    >
                      <Bracket className="text-base0e group-hover:text-base05">
                        {busy === u.id ? "..." : "sync stripe"}
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
