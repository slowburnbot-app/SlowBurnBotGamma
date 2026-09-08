"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import {
  getAccounts,
  getAccountSettings,
  getAccountStats,
  getEntitlement,
  getLogSummary,
  getFollowbackSummary,
  getClientStatus,
  getRecentSessionLog,
  getSubscriptionInfo,
  updateAccount,
  Account,
  AccountSettings,
  AccountStats,
  ClientStatus,
  Entitlement,
  LogSummaryEntry,
  FollowbackSummaryEntry,
  RecentSessionLogEntry,
  SubscriptionInfo,
  PLAN_LIMITS,
} from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { Dropdown } from "@/lib/dropdown";
import { ActionLimitBadges } from "@/lib/action-limit-badge";
import { scheduleLabel, formatSessionAction } from "@/lib/format";

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const h = d.getHours();
  const m = d.getMinutes();
  const period = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${String(h12).padStart(2, "0")}:${String(m).padStart(2, "0")}${period}`;
}

function fmtGroup(n: number | null | undefined): string {
  if (n == null) return "—";
  return String(n).padStart(2, "0");
}

function actionSlots(settings: AccountSettings | undefined, account: Account) {
  const dim = account.system_disabled ? "text-base03" : account.enabled ? "text-base05" : "text-base04";
  return (
    <span className={`whitespace-nowrap ${dim}`}>
      {[0, 1, 2, 3].map(i => {
        const slot = settings?.actions?.[i];
        const on = slot?.enabled ?? false;
        const label = on ? `${slot!.fixed_count}+${slot!.variable_count}` : "---";
        return (
          <span key={i}>
            {i > 0 && <span className="text-base03">|</span>}
            <span className={on ? dim : "text-base03"}>{label}</span>
          </span>
        );
      })}
    </span>
  );
}

function fmtNum(v: number | null | undefined): string {
  if (v == null) return "----";
  return v.toLocaleString();
}

function fmtPct(v: number | null): string {
  if (v == null) return "----";
  return `${Math.round(v * 100)}%`;
}

type Tab = "settings" | "activity" | "stats" | "database";
type SortKey = "name" | "enabled" | "group" | "following" | "unfollow_ready" | "complete" | "ignored" | "total" | "success" | "last_25" | "all_time" | "sessions" | "likes" | "follows" | "unfollows" | "fb_complete" | "followed_back" | "fb_rate" | "fb_daily";
type SortDir = "asc" | "desc";
type Period = "day" | "week" | "month";

export default function DashboardPage() {
  const { user } = useAuth();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [settingsMap, setSettingsMap] = useState<Record<string, AccountSettings>>({});
  const [statsMap, setStatsMap] = useState<Record<string, AccountStats>>({});
  const [logMap, setLogMap] = useState<Record<string, LogSummaryEntry>>({});
  const [fbMap, setFbMap] = useState<Record<string, FollowbackSummaryEntry>>({});
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null);
  const [clientStatus, setClientStatus] = useState<ClientStatus[]>([]);
  const [clientStatusError, setClientStatusError] = useState(false);
  const [subInfo, setSubInfo] = useState<SubscriptionInfo | null>(null);
  const [recentLog, setRecentLog] = useState<RecentSessionLogEntry[]>([]);
  const [expandedErrors, setExpandedErrors] = useState<Set<string>>(new Set());
  const [expandedWarnings, setExpandedWarnings] = useState<Set<string>>(new Set());

  const toggleError = useCallback((id: string) => {
    setExpandedErrors((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleWarning = useCallback((id: string) => {
    setExpandedWarnings((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const [planTier, setPlanTier] = useState<string>("free");
  const [tab, setTab] = useState<Tab>("settings");
  const [activityPeriod, setActivityPeriod] = useState<Period>("week");
  const [statsPeriod, setStatsPeriod] = useState<Period>("week");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const maxAccounts = subInfo?.max_accounts ?? PLAN_LIMITS[planTier] ?? 0;

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function getVal(account: Account, key: SortKey): number | string {
    switch (key) {
      case "name": return account.name.toLowerCase();
      case "enabled": return account.enabled ? 1 : 0;
      case "group": return account.group_number ?? -1;
      case "following": return statsMap[account.id]?.following ?? -1;
      case "unfollow_ready": return statsMap[account.id]?.unfollow_ready ?? -1;
      case "complete": return statsMap[account.id]?.complete ?? -1;
      case "ignored": return statsMap[account.id]?.ignored ?? -1;
      case "total": return statsMap[account.id]?.total ?? -1;
      case "success": return statsMap[account.id]?.success ?? -1;
      case "last_25": return statsMap[account.id]?.last_25 ?? -1;
      case "all_time": return statsMap[account.id]?.all_time ?? -1;
      case "sessions": return logMap[account.id]?.sessions ?? -1;
      case "likes": return logMap[account.id]?.likes ?? -1;
      case "follows": return logMap[account.id]?.follows ?? -1;
      case "unfollows": return logMap[account.id]?.unfollows ?? -1;
      case "fb_complete": return fbMap[account.id]?.complete ?? -1;
      case "followed_back": return fbMap[account.id]?.followed_back ?? -1;
      case "fb_rate": return fbMap[account.id]?.rate ?? -1;
      case "fb_daily": { const fb = fbMap[account.id]; return fb ? fb.followed_back / (fb.days || 1) : -1; }
    }
  }

  const sortedAccounts = useMemo(() => {
    const list = [...accounts];
    const dir = sortDir === "asc" ? 1 : -1;
    list.sort((a, b) => {
      const av = getVal(a, sortKey);
      const bv = getVal(b, sortKey);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
    return list;
  }, [accounts, statsMap, logMap, fbMap, sortKey, sortDir]);

  function SortTh({ label, field, className = "" }: { label: string; field: SortKey; className?: string }) {
    const active = sortKey === field;
    const arrow = active ? (sortDir === "asc" ? "↑" : "↓") : "\u00a0";
    return (
      <th
        className={`px-[6px] py-2 font-normal cursor-pointer select-none transition-colors hover:text-base05 ${active ? "text-base0d" : ""} ${className}`}
        onClick={() => toggleSort(field)}
      >
        <span className="whitespace-nowrap">{label}<span className="inline-block w-[1em] text-center">{arrow}</span></span>
      </th>
    );
  }

  async function load() {
    getEntitlement().then((e) => setPlanTier(e.plan_tier)).catch(() => {});
    const data = await getAccounts().catch(() => []);
    setAccounts(data);
    const settingsEntries = await Promise.all(
      data.map((a) =>
        getAccountSettings(a.id)
          .then((s) => [a.id, s] as const)
          .catch(() => null)
      )
    );
    setSettingsMap(
      Object.fromEntries(settingsEntries.filter((e): e is [string, AccountSettings] => e !== null))
    );
    const statsEntries = await Promise.all(
      data.map((a) =>
        getAccountStats(a.id)
          .then((s) => [a.id, s] as const)
          .catch(() => null)
      )
    );
    setStatsMap(
      Object.fromEntries(statsEntries.filter((e): e is [string, AccountStats] => e !== null))
    );
  }

  useEffect(() => {
    load();
    getEntitlement().then(setEntitlement).catch(() => {});
    getRecentSessionLog(15).then((r) => setRecentLog(r.items)).catch(() => {});
    const refreshClientStatus = () =>
      getClientStatus()
        .then((cs) => {
          setClientStatus(cs);
          setClientStatusError(false);
        })
        .catch(() => setClientStatusError(true));
    refreshClientStatus();
    getSubscriptionInfo().then(setSubInfo).catch(() => {});
    const interval = setInterval(refreshClientStatus, 60_000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (tab === "activity") {
      getLogSummary(activityPeriod).then(setLogMap).catch(() => {});
    }
  }, [tab, activityPeriod]);

  useEffect(() => {
    if (tab === "stats") {
      getFollowbackSummary(statsPeriod).then(setFbMap).catch(() => {});
      getLogSummary(statsPeriod).then(setLogMap).catch(() => {});
    }
  }, [tab, statsPeriod]);

  async function handleToggleEnabled(account: Account) {
    const updated = await updateAccount(account.id, { enabled: !account.enabled }).catch(() => null);
    if (updated) setAccounts((prev) => prev.map((a) => (a.id === account.id ? updated : a)));
  }

  const activeAccounts = accounts.filter((a) => !a.system_disabled);
  const enabledCount = activeAccounts.filter((a) => a.enabled).length;
  const displayPlanTier = entitlement?.plan_tier ?? user?.plan_tier ?? "free";

  const summaryPeriodOptions = [
    { value: "day", label: "today" },
    { value: "week", label: "last 7 days" },
    { value: "month", label: "last 30 days" },
  ];

  const tabs: { key: Tab; label: string }[] = [
    { key: "settings", label: "settings" },
    { key: "activity", label: "activity" },
    { key: "stats", label: "stats" },
    { key: "database", label: "database" },
  ];

  return (
    <div className="space-y-6 font-mono">
      <h1 className="font-semibold text-base05">
        Overview{user?.display_name ? ` — ${user.display_name}` : ""}
      </h1>

      <div className="space-y-2">
      <h2 className="font-semibold text-base05">
        Clients <span className="text-base0a font-normal">[{String(subInfo?.current_clients ?? 0).padStart(2, "0")}/{subInfo?.max_clients ? String(subInfo.max_clients).padStart(2, "0") : "--"}]</span>
      </h2>

      <div className="border border-base02 bg-base01">
        {clientStatus.length === 0 ? (
          clientStatusError ? (
            <p className="px-4 py-6 text-status-bad">client status unavailable — could not reach the server.</p>
          ) : (
            <p className="px-4 py-6 text-base04">no clients reporting yet.</p>
          )
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="text-left text-base04 border-b border-base02 bg-base02">
                <th className="px-2.5 py-2 font-normal">Client</th>
                <th className="px-2.5 py-2 font-normal">Name</th>
                <th className="px-2.5 py-2 font-normal">OS</th>
                <th className="px-2.5 py-2 font-normal">Status</th>
                <th className="px-2.5 py-2 font-normal">State</th>
                <th className="px-2.5 py-2 font-normal whitespace-nowrap">Current Action</th>
                <th className="px-2.5 py-2 font-normal whitespace-nowrap">Last Session</th>
                <th className="w-full"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-base02">
              {clientStatus.map((cs) => (
                <tr key={cs.client_id} className="hover:bg-base02/60 transition-colors">
                  <td className="px-2.5 py-2 text-base05 whitespace-nowrap">{String(cs.client_id).padStart(2, "0")}</td>
                  <td className="px-2.5 py-2 text-base04 whitespace-nowrap">{cs.client_name || "----"}</td>
                  <td className="px-2.5 py-2 text-base04 whitespace-nowrap">{cs.system_type || "----"}</td>
                  <td className="px-2.5 py-2 whitespace-nowrap">
                    {cs.connected ? (
                      <span className="text-status-ok">connected</span>
                    ) : (
                      <span className="text-base04">
                        offline{cs.last_heartbeat ? ` - ${fmtTime(cs.last_heartbeat)}` : ""}
                      </span>
                    )}
                  </td>
                  <td className="px-2.5 py-2 whitespace-nowrap">
                    {!cs.connected
                      ? <span className="text-base04">------</span>
                      : cs.status === "running"
                      ? <span className="text-status-ok">active</span>
                      : cs.status === "paused"
                      ? <span className="text-base0a">paused</span>
                      : cs.status === "delay"
                      ? <span className="text-base04">delay</span>
                      : <span className="text-base04">{cs.status || "----"}</span>
                    }
                  </td>
                  <td className="px-2.5 py-2 text-base04 whitespace-nowrap">
                    {!cs.connected
                      ? "------"
                      : cs.status === "running" && cs.current_account
                      ? <span className="text-base05">running {cs.current_account}</span>
                      : cs.status === "delay"
                      ? "session delay"
                      : "------"}
                  </td>
                  <td className="px-2.5 py-2 text-base04 whitespace-nowrap">
                    {cs.last_session_account || "------"}
                  </td>
                  <td></td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
      </div>

      <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 className="font-semibold text-base05">
          Accounts <span className="text-base0a font-normal">[{String(subInfo?.current_accounts ?? accounts.length).padStart(2, "0")}/{String(maxAccounts).padStart(2, "0")}]</span>
        </h2>
        <span className="text-base04">--</span>
        <div className="basis-full sm:basis-auto flex flex-wrap items-center gap-2">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="group cursor-pointer transition-colors"
            >
              <Bracket className={tab === t.key ? "text-base0d" : "text-base04 group-hover:text-white"}>{t.label}</Bracket>
            </button>
          ))}
        </div>
      </div>

      <div className="border border-base02 bg-base01">
        {accounts.length === 0 ? (
          <p className="px-4 py-6 font-mono text-base04">No accounts yet.</p>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full font-mono">
            <thead>
              <tr className="text-left text-base04 border-b border-base02 bg-base02">
                <SortTh label="On" field="enabled" />
                <SortTh label="Account" field="name" />
                {tab === "settings" && (
                  <>
                    <SortTh label="Client" field="group" />
                    <th className="px-[6px] py-2 font-normal">Schedule</th>
                    <th className="px-[6px] py-2 font-normal whitespace-nowrap">Actions</th>
                    <th className="px-[6px] py-2 font-normal">Delay</th>
                    <th className="px-[6px] py-2 font-normal">Runs/Day</th>
                  </>
                )}
                {tab === "activity" && (
                  <>
                    <SortTh label="Sessions" field="sessions" className="whitespace-nowrap" />
                    <SortTh label="Likes" field="likes" className="whitespace-nowrap" />
                    <SortTh label="Follows" field="follows" className="whitespace-nowrap" />
                    <SortTh label="Unfollows" field="unfollows" className="whitespace-nowrap" />
                  </>
                )}
                {tab === "stats" && (
                  <>
                    <SortTh label="Complete" field="fb_complete" className="whitespace-nowrap" />
                    <SortTh label="Followed Back" field="followed_back" className="whitespace-nowrap" />
                    <SortTh label="Success Rate" field="fb_rate" className="whitespace-nowrap" />
                    <SortTh label="Daily" field="fb_daily" className="whitespace-nowrap" />
                  </>
                )}
                {tab === "database" && (
                  <>
                    <SortTh label="Following" field="following" className="whitespace-nowrap" />
                    <SortTh label="Pend." field="unfollow_ready" className="whitespace-nowrap" />
                    <SortTh label="Compl." field="complete" className="whitespace-nowrap" />
                    <SortTh label="Ignored" field="ignored" className="whitespace-nowrap" />
                    <SortTh label="Total" field="total" className="whitespace-nowrap" />
                    <SortTh label="Success" field="success" className="whitespace-nowrap" />
                  </>
                )}
                <th className="px-[6px] py-2 font-normal w-full text-right whitespace-nowrap">
                  {tab === "activity" && (
                    <span className="inline-flex items-center gap-0">
                      <span className="text-base04">{"activity:\u00a0 "}</span>
                      <span className="text-base05">{"["}</span>
                      <Dropdown
                        value={activityPeriod}
                        onChange={(v) => setActivityPeriod(v as Period)}
                        options={summaryPeriodOptions}
                      />
                      <span className="text-base05">{"]"}</span>
                    </span>
                  )}
                  {tab === "stats" && (
                    <span className="inline-flex items-center gap-0">
                      <span className="text-base04">{"results:\u00a0 "}</span>
                      <span className="text-base05">{"["}</span>
                      <Dropdown
                        value={statsPeriod}
                        onChange={(v) => setStatsPeriod(v as Period)}
                        options={summaryPeriodOptions}
                      />
                      <span className="text-base05">{"]"}</span>
                    </span>
                  )}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-base02">
              {sortedAccounts.map((account) => {
                const stats = statsMap[account.id];
                const log = logMap[account.id];
                const fb = fbMap[account.id];
                return (
                  <tr key={account.id} className={`hover:bg-base02/60 transition-colors ${account.system_disabled ? "text-base03" : account.enabled ? "text-base05" : "text-base04"}`}>
                    <td className="px-[6px] py-2 whitespace-nowrap">
                      {account.system_disabled ? (
                        <Bracket className="text-base03">-</Bracket>
                      ) : (
                        <button
                          onClick={() => handleToggleEnabled(account)}
                          className="group cursor-pointer transition-colors"
                        >
                          <Bracket className={account.enabled ? "text-status-ok group-hover:text-status-bad" : "text-base04 group-hover:text-status-ok"}>
                            {account.enabled ? "x" : " "}
                          </Bracket>
                        </button>
                      )}
                    </td>
                    <td className="px-2 pr-4 py-2 whitespace-nowrap overflow-hidden text-ellipsis" style={{ maxWidth: "20ch" }}>
                      {account.name}
                      {account.action_limits?.length > 0 && <> <ActionLimitBadges limits={account.action_limits} /></>}
                    </td>
                    {tab === "settings" && (
                      <>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtGroup(account.group_number)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">
                          {scheduleLabel(settingsMap[account.id])}
                        </td>
                        <td className="px-[6px] py-2 whitespace-nowrap">
                          {actionSlots(settingsMap[account.id], account)}
                        </td>
                        <td className="px-[6px] py-2 whitespace-nowrap">
                          {settingsMap[account.id] ? `${settingsMap[account.id].delay_base_minutes ?? 0}+${settingsMap[account.id].delay_random_minutes ?? 0}` : "—"}
                        </td>
                        <td className="px-[6px] py-2 whitespace-nowrap">
                          {settingsMap[account.id] ? `${settingsMap[account.id].max_runs_per_day ?? 0}+${settingsMap[account.id].max_runs_random_per_day ?? 0}` : "—"}
                        </td>
                      </>
                    )}
                    {tab === "activity" && (
                      <>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(log?.sessions)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(log?.likes)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(log?.follows)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(log?.unfollows)}</td>
                      </>
                    )}
                    {tab === "stats" && (
                      <>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(fb?.complete)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(fb?.followed_back)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtPct(fb?.rate ?? null)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fb ? (fb.followed_back / (fb.days || 1)).toFixed(1) : "----"}</td>
                      </>
                    )}
                    {tab === "database" && (
                      <>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(stats?.following)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(stats?.unfollow_ready)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(stats?.complete)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(stats?.ignored)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(stats?.total)}</td>
                        <td className="px-[6px] py-2 whitespace-nowrap">{fmtNum(stats?.success)}</td>
                      </>
                    )}
                    <td className="px-[6px] py-2 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {tab === "settings" && (
                          <Link href={`/dashboard/accounts/${account.id}`} className="group font-mono transition-colors">
                            <Bracket className="text-base04 group-hover:text-base0d">settings</Bracket>
                          </Link>
                        )}
                        {tab === "activity" && (
                          <Link href={`/dashboard/accounts/${account.id}/log`} className="group font-mono transition-colors">
                            <Bracket className="text-base04 group-hover:text-base0d">log</Bracket>
                          </Link>
                        )}
                        {tab === "stats" && (
                          <Link href={`/dashboard/accounts/${account.id}/stats`} className="group font-mono transition-colors">
                            <Bracket className="text-base04 group-hover:text-base0d">stats</Bracket>
                          </Link>
                        )}
                        {tab === "database" && (
                          <Link href={`/dashboard/accounts/${account.id}/database`} className="group font-mono transition-colors">
                            <Bracket className="text-base04 group-hover:text-base0d">data</Bracket>
                          </Link>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        )}

      </div>
      </div>

      <div className="space-y-2">
      <div className="flex items-center gap-4">
        <h2 className="font-semibold text-base05">Recent Activity</h2>
        <span className="text-base04">--</span>
        <Link href="/dashboard/accounts?tab=activity" className="text-base0d hover:text-base05 transition-colors">
          by account →
        </Link>
      </div>

      <div className="border border-base02 bg-base01">
        {recentLog.length === 0 ? (
          <div className="px-4 py-6 text-base04">no session log entries yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-base04 border-b border-base02 bg-base02">
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">account</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">date</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">run</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">start</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">end</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">action 1</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">action 2</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">action 3</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">action 4</th>
                  <th className="px-[6px] py-2 font-normal whitespace-nowrap">errors</th>
                  <th className="w-full"></th>
                </tr>
              </thead>
              <tbody>
                {(() => {
                  const dayGroups = new Map<string, number>();
                  let gi = 0;
                  for (const e of recentLog) {
                    const k = e.run_date ?? "";
                    if (!dayGroups.has(k)) dayGroups.set(k, gi++);
                  }
                  return recentLog.map((entry) => {
                  const altDay = (dayGroups.get(entry.run_date ?? "") ?? 0) % 2 === 1;
                  const rowBg = altDay ? "bg-base02" : "";
                  return (
                  <>
                  <tr key={entry.id} className={`hover:bg-base02/60 transition-colors border-t border-base02 ${rowBg}`}>
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <Link
                        href={`/dashboard/accounts/${entry.account_id}/log`}
                        className="text-base0d hover:text-base05 transition-colors"
                      >
                        {entry.account_name}
                      </Link>
                    </td>
                    <td className="px-2 py-1.5 text-base05 whitespace-nowrap">{entry.run_date ?? "—"}</td>
                    <td className="px-2 py-1.5 text-base04 whitespace-nowrap">{entry.run_sequence}</td>
                    <td className="px-2 py-1.5 text-base04 whitespace-nowrap">{fmtTime(entry.start_time)}</td>
                    <td className="px-2 py-1.5 text-base04 whitespace-nowrap">{fmtTime(entry.end_time)}</td>
                    <td className="px-2 py-1.5 text-base04 whitespace-nowrap">
                      {formatSessionAction(entry.action_1_type, entry.action_1_count)}
                    </td>
                    <td className="px-2 py-1.5 text-base04 whitespace-nowrap">
                      {formatSessionAction(entry.action_2_type, entry.action_2_count)}
                    </td>
                    <td className="px-2 py-1.5 text-base04 whitespace-nowrap">
                      {formatSessionAction(entry.action_3_type, entry.action_3_count)}
                    </td>
                    <td className="px-2 py-1.5 text-base04 whitespace-nowrap">
                      {formatSessionAction(entry.action_4_type, entry.action_4_count)}
                    </td>
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      {entry.error_message || entry.warning_message ? (
                        <span className="flex gap-2">
                          {entry.error_message && (
                            <button
                              onClick={() => toggleError(entry.id)}
                              className="text-status-bad hover:text-status-bad-hover cursor-pointer transition-colors text-xs"
                            >
                              {expandedErrors.has(entry.id) ? "▾ error" : "▸ error"}
                            </button>
                          )}
                          {entry.warning_message && (
                            <button
                              onClick={() => toggleWarning(entry.id)}
                              className="text-status-warning hover:text-status-warning-hover cursor-pointer transition-colors text-xs"
                            >
                              {expandedWarnings.has(entry.id) ? "▾ warning" : "▸ warning"}
                            </button>
                          )}
                        </span>
                      ) : (
                        <span className="text-base03 text-xs">none</span>
                      )}
                    </td>
                    <td></td>
                  </tr>
                  {entry.error_message && expandedErrors.has(entry.id) && (
                    <tr key={`${entry.id}-err`} className="bg-base02">
                      <td colSpan={11} className="px-2 py-1 text-base04 text-xs whitespace-pre-wrap">
                        {entry.error_message.split("\n").map((line) => `- ${line}`).join("\n")}
                      </td>
                    </tr>
                  )}
                  {entry.warning_message && expandedWarnings.has(entry.id) && (
                    <tr key={`${entry.id}-warn`} className="bg-base02">
                      <td colSpan={11} className="px-2 py-1 text-base04 text-xs whitespace-pre-wrap">
                        {entry.warning_message.split("\n").map((line) => `- ${line}`).join("\n")}
                      </td>
                    </tr>
                  )}
                  </>
                  );
                  });
                })()}
              </tbody>
            </table>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
