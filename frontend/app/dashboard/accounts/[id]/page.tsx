"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  getAccounts,
  getAccountSettings,
  getAccountActionLimits,
  saveAccountSettings,
  updateAccount,
  deleteAccount,
  getFollowSeeds,
  addFollowSeed,
  updateFollowSeed,
  deleteFollowSeed,
  Account,
  AccountSettings,
  ActionBlock,
  ActionLimitEvent,
  FollowSeed,
} from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { fmtLimitUntil } from "@/lib/action-limit-badge";
import { BracketCheckbox } from "@/lib/bracket-checkbox";
import { formatTime } from "@/lib/format";
import { Dropdown } from "@/lib/dropdown";
import { NumberInput } from "@/lib/number-input";

// ── dropdown data ─────────────────────────────────────────────────────────────

const ACTION_TYPES = ["follow", "unfollow", "like"] as const;

const ACTION_TARGETS: Record<string, string[]> = {
  follow:    ["suggested users", "account list [likers]", "topics [likers]", "account list [followers]", "account list [following]", "account list [similar]"],
  unfollow:  ["previous follows"],
  like: ["posts [homepage]", "posts [topics]"],
};

const SCHEDULE_DAYS = ["daily", "weekdays", "weekends", "random 1/3", "random 2/3"];

// ── helpers ───────────────────────────────────────────────────────────────────

const ACTION_LABELS = ["1st", "2nd", "3rd", "4th"] as const;

const DEFAULT_ACTION: ActionBlock = {
  enabled: false,
  type: "",
  target: "",
  fixed_count: 0,
  variable_count: 0,
};

function pad4(actions: ActionBlock[] | null | undefined): ActionBlock[] {
  const base = actions ?? [];
  return [0, 1, 2, 3].map((i) => base[i] ?? { ...DEFAULT_ACTION });
}

/** Convert "10:00 PM" / "10:00PM" → "22:00"; pass through 24h as-is */
function parseTime(v: string): string | null {
  if (!v.trim()) return null;
  const m12 = v.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)$/i);
  if (m12) {
    let h = parseInt(m12[1], 10);
    const m = parseInt(m12[2], 10);
    if (m12[3].toUpperCase() === "PM" && h !== 12) h += 12;
    if (m12[3].toUpperCase() === "AM" && h === 12) h = 0;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }
  return v.slice(0, 5) || null;
}

/** Reformat a pasted/typed list (one per line, spaces, commas, "@"/"#" prefixes)
 *  into the bot's "a, b, c" form. Topics may contain spaces, so they only split
 *  on newlines and commas; handles never do, so they also split on whitespace. */
function normalizeList(raw: string | null | undefined, kind: "accounts" | "topics"): string | null {
  const text = raw ?? "";
  const parts = kind === "accounts" ? text.split(/[\s,]+/) : text.split(/[\n\r,]+/);
  const prefix = kind === "accounts" ? /^@+/ : /^#+/;
  const seen = new Set<string>();
  const out: string[] = [];
  for (const p of parts) {
    const item = p.trim().replace(prefix, "").trim();
    if (!item) continue;
    const key = item.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out.length ? out.join(", ") : null;
}

// ── styles ────────────────────────────────────────────────────────────────────

const sectionCls = "border border-base02 bg-base01";

// ── component ─────────────────────────────────────────────────────────────────

export default function AccountDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(null);
  const [settings, setSettings] = useState<Partial<AccountSettings>>({});
  const [actions, setActions] = useState<ActionBlock[]>(pad4(null));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [editingStart, setEditingStart] = useState<string | null>(null);
  const [editingEnd, setEditingEnd] = useState<string | null>(null);
  const [editingPw, setEditingPw] = useState(false);
  const [pwValue, setPwValue] = useState("");
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [seeds, setSeeds] = useState<FollowSeed[]>([]);
  const [newSeed, setNewSeed] = useState("");
  const [seedMsg, setSeedMsg] = useState("");
  const [limitEvents, setLimitEvents] = useState<ActionLimitEvent[]>([]);

  const refreshSeeds = useCallback(() => {
    getFollowSeeds(id).then((r) => setSeeds(r.items)).catch(() => {});
  }, [id]);

  async function handleAddSeed() {
    const handle = newSeed.trim();
    if (!handle) return;
    setSeedMsg("");
    try {
      await addFollowSeed(id, handle);
      setNewSeed("");
      refreshSeeds();
    } catch (err: unknown) {
      setSeedMsg(err instanceof Error ? err.message : "add failed.");
    }
  }

  async function handleToggleSeed(seed: FollowSeed) {
    const updated = await updateFollowSeed(id, seed.id, { active: !seed.active }).catch(() => null);
    if (updated) setSeeds((prev) => prev.map((s) => (s.id === seed.id ? updated : s)));
  }

  async function handleDeleteSeed(seed: FollowSeed) {
    if (!confirm(`Remove seed "${seed.handle}"?`)) return;
    await deleteFollowSeed(id, seed.id).catch(() => {});
    setSeeds((prev) => prev.filter((s) => s.id !== seed.id));
  }

  useEffect(() => {
    refreshSeeds();
  }, [refreshSeeds]);

  // account group + topics: grow to show all text, and keep both boxes the same
  // height (the taller one wins) whenever either value changes, including first load.
  const groupRef = useRef<HTMLTextAreaElement>(null);
  const topicsRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const a = groupRef.current;
    const b = topicsRef.current;
    if (!a || !b) return;
    a.style.height = "auto";
    b.style.height = "auto";
    const h = Math.max(a.scrollHeight, b.scrollHeight) + 2; // + top/bottom border
    a.style.height = `${h}px`;
    b.style.height = `${h}px`;
  }, [account, settings.account_group, settings.topics]); // `account` gates the textareas' first render

  useEffect(() => {
    getAccounts().then((list) => {
      const found = list.find((a) => a.id === id) ?? null;
      if (!found) { router.push("/dashboard/accounts"); return; }
      setAccount(found);
    });
    getAccountSettings(id)
      .then((s) => { setSettings(s); setActions(pad4(s.actions)); setSettingsLoaded(true); })
      .catch((err: unknown) => {
        setLoadError(err instanceof Error ? err.message : "failed to load settings.");
      });
    getAccountActionLimits(id, 10).then(setLimitEvents).catch(() => {});
  }, [id, router]);

  async function handleSaveSettings(e: React.FormEvent) {
    e.preventDefault();
    if (!settingsLoaded) {
      // Block save if the initial fetch failed — the local state holds defaults,
      // not the user's real settings, and saving would overwrite them.
      setMsg("cannot save — settings failed to load. reload the page first.");
      return;
    }
    setSaving(true);
    setMsg("");
    try {
      const cleaned = {
        ...settings,
        account_group: normalizeList(settings.account_group, "accounts"),
        topics: normalizeList(settings.topics, "topics"),
      };
      setSettings(cleaned);
      await saveAccountSettings(id, { ...cleaned, actions });
      setMsg("saved.");
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "save failed.");
    } finally {
      setSaving(false);
    }
  }

  function updateAction(index: number, patch: Partial<ActionBlock>) {
    setActions((prev) => prev.map((a, i) => {
      if (i !== index) return a;
      const updated = { ...a, ...patch };
      // reset target when type changes
      if (patch.type !== undefined && patch.type !== a.type) updated.target = "";
      return updated;
    }));
  }

  async function handleAccountField(patch: Partial<Account>) {
    const updated = await updateAccount(id, patch).catch(() => null);
    if (updated) setAccount(updated);
  }

  async function handleDelete() {
    if (!account) return;
    if (!confirm(`Delete account "${account.name}"?`)) return;
    await deleteAccount(id).catch(() => {});
    router.push("/dashboard/accounts");
  }

  if (!account) return null;

  const groupDisplay = account.group_number != null ? String(account.group_number) : "";
  const poolMode = settings.account_group_mode === "pool";

  return (
    <div className="space-y-6 font-mono">

      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        <Link href="/dashboard/accounts" className="text-base04 hover:text-base05 transition-colors">accounts</Link>
        <span className="text-base03">-</span>
        <Link href="/dashboard/accounts?tab=settings" className="text-base04 hover:text-base05 transition-colors">settings</Link>
        <span className="text-base03">-</span>
        <span className="text-base05">{account.name}</span>
      </div>

      <form onSubmit={handleSaveSettings} className="space-y-4">

        {/* Configuration */}
        <div className={sectionCls}>
          <div className="px-4 py-2 border-b border-base02 text-base04 bg-base02">configuration</div>
          <div className="px-4 py-3 flex items-center gap-x-5 gap-y-2 flex-wrap">

            <BracketCheckbox
              label="enabled"
              checked={account.enabled}
              onChange={(v) => handleAccountField({ enabled: v })}
            />

            <span className="inline-flex items-center gap-0">
              <span className="text-base04">{"client: "}</span>
              <span className="text-base05">{"["}</span>
              <input
                type="text"
                inputMode="numeric"
                maxLength={2}
                placeholder="--"
                value={groupDisplay}
                onChange={(e) => {
                  const val = e.target.value.replace(/\D/g, "").slice(0, 2);
                  setAccount((a) => a && { ...a, group_number: val ? +val : null });
                }}
                onBlur={() => handleAccountField({ group_number: account.group_number })}
                className="w-5 bg-transparent border-b border-base02 text-base05 outline-none focus:border-base0d font-mono transition-colors placeholder-base04 text-center"
              />
              <span className="text-base05">{"]"}</span>
            </span>

            <span className="inline-flex items-center gap-1">
              <span className="text-base04">password:</span>
              {editingPw ? (
                <span className="inline-flex items-center gap-0">
                  <span className="text-base05">{"["}</span>
                  <input
                    type="password"
                    autoFocus
                    value={pwValue}
                    onChange={(e) => setPwValue(e.target.value)}
                    onBlur={() => {
                      if (pwValue) handleAccountField({ ig_password: pwValue } as Partial<Account>);
                      setEditingPw(false);
                      setPwValue("");
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); }
                      if (e.key === "Escape") { setEditingPw(false); setPwValue(""); }
                    }}
                    style={{ width: "6ch", paddingLeft: "1ch", paddingRight: "1ch", boxSizing: "content-box" }}
                    className="bg-transparent text-base05 outline-none font-mono"
                  />
                  <span className="text-base05">{"]"}</span>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => setEditingPw(true)}
                  className="group cursor-pointer transition-colors inline-flex items-center gap-0"
                >
                  <span className="text-base05">[</span>
                  <span style={{ paddingLeft: "1ch", paddingRight: "1ch" }} className={account.has_password ? "text-status-ok group-hover:text-base0d" : "text-base04 group-hover:text-base0d"}>
                    {account.has_password ? "******" : "------"}
                  </span>
                  <span className="text-base05">]</span>
                </button>
              )}
            </span>

          </div>
        </div>

        {/* Action limits — read-only: Instagram throttling detected by the bot.
            Not part of the form's save semantics (no inputs). */}
        <div className={sectionCls}>
          <div className="px-4 py-2 border-b border-base02 text-base04 bg-base02">action limits</div>
          <div className="px-4 py-3 space-y-2">
            <div className="grid gap-x-3 gap-y-1" style={{ gridTemplateColumns: "9ch auto" }}>
              {(["like", "follow", "unfollow"] as const).map((verb) => {
                const active = account.action_limits?.find((l) => l.action === verb);
                return (
                  <span key={verb} className="contents">
                    <span className="text-base04">{verb}:</span>
                    {active ? (
                      <span>
                        <span className="text-base05">{"["}</span>
                        <span className="text-status-warning">{`limited until ${fmtLimitUntil(active.until)}`}</span>
                        <span className="text-base05">{"]"}</span>
                        <span className="text-base04">{` strike ${active.strike} — ${active.reason || active.tier}`}</span>
                      </span>
                    ) : (
                      <Bracket className="text-base04">------</Bracket>
                    )}
                  </span>
                );
              })}
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
            {limitEvents.length > 0 && (
              <div className="pt-2 border-t border-base02 space-y-0.5">
                <div className="text-base04">recent events</div>
                {limitEvents.map((ev) => (
                  <div key={ev.id} className="text-base04 whitespace-nowrap overflow-hidden text-ellipsis">
                    <span className="text-base05">{fmtLimitUntil(ev.created_at)}</span>
                    {` ${ev.action} ${ev.tier}`}
                    {ev.until ? ` → until ${fmtLimitUntil(ev.until)} (strike ${ev.strike})` : ""}
                    {ev.reason ? ` — ${ev.reason}` : ""}
                    {ev.cleared_reason ? ` [${ev.cleared_reason}]` : ""}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Schedule */}
        <div className={sectionCls}>
          <div className="px-4 py-2 border-b border-base02 text-base04 bg-base02">schedule</div>
          <div className="px-4 py-3 space-y-2">

            <div className="flex items-center gap-x-5 gap-y-2 flex-wrap">
              <span className="inline-flex items-center gap-0">
                <span className="text-base04">{"sessions/day - fixed: "}</span>
                <span className="text-base05">{"["}</span>
                <NumberInput
                  value={settings.max_runs_per_day}
                  onChange={(n) => setSettings((s) => ({ ...s, max_runs_per_day: n || 1 }))}
                  placeholder="1"
                />
                <span className="text-base05">{"]"}</span>
                <span className="text-base04 leading-none">{" + random: "}</span>
                <span className="text-base05">{"["}</span>
                <NumberInput
                  value={settings.max_runs_random_per_day}
                  onChange={(n) => setSettings((s) => ({ ...s, max_runs_random_per_day: n }))}
                  placeholder="0"
                />
                <span className="text-base05">{"]"}</span>
              </span>
            </div>

            <div className="flex items-center gap-x-5 gap-y-2 flex-wrap">
              <span className="inline-flex items-center gap-0">
                <span className="text-base04">{"days: "}</span>
                <span className="text-base05">{"["}</span>
                <Dropdown
                  value={settings.schedule_days ?? ""}
                  onChange={(v) => setSettings((s) => ({ ...s, schedule_days: v || null }))}
                  placeholder="----"
                  options={[
                    { value: "", label: "----" },
                    ...SCHEDULE_DAYS.map((d) => ({ value: d, label: d })),
                  ]}
                />
                <span className="text-base05">{"]"}</span>
              </span>

              <span className="inline-flex items-center gap-0">
                <span className="text-base04">{"start: "}</span>
                <span className="text-base05">{"["}</span>
                <input
                  type="text"
                  value={editingStart ?? (settings.schedule_start ? formatTime(settings.schedule_start) : "")}
                  onFocus={() => setEditingStart(settings.schedule_start ? formatTime(settings.schedule_start) : "")}
                  onChange={(e) => setEditingStart(e.target.value)}
                  onBlur={() => { setSettings((s) => ({ ...s, schedule_start: parseTime(editingStart ?? "") })); setEditingStart(null); }}
                  placeholder="10:00 AM"
                  style={{ width: "8ch", paddingLeft: "1ch", paddingRight: "0", boxSizing: "content-box" }}
                  className="bg-transparent text-base05 outline-none font-mono min-w-0"
                />
                <span className="text-base05">{"]"}</span>
              </span>

              <span className="inline-flex items-center gap-0">
                <span className="text-base04">{"end: "}</span>
                <span className="text-base05">{"["}</span>
                <input
                  type="text"
                  value={editingEnd ?? (settings.schedule_end ? formatTime(settings.schedule_end) : "")}
                  onFocus={() => setEditingEnd(settings.schedule_end ? formatTime(settings.schedule_end) : "")}
                  onChange={(e) => setEditingEnd(e.target.value)}
                  onBlur={() => { setSettings((s) => ({ ...s, schedule_end: parseTime(editingEnd ?? "") })); setEditingEnd(null); }}
                  placeholder="10:00 PM"
                  style={{ width: "8ch", paddingLeft: "1ch", paddingRight: "0", boxSizing: "content-box" }}
                  className="bg-transparent text-base05 outline-none font-mono min-w-0"
                />
                <span className="text-base05">{"]"}</span>
              </span>
            </div>

            <div className="flex items-center gap-x-5 gap-y-2 flex-wrap">
              <span className="inline-flex items-center gap-0">
                <span className="text-base04">{"delay - fixed: "}</span>
                <span className="text-base05">{"["}</span>
                <NumberInput
                  value={settings.delay_base_minutes}
                  onChange={(n) => setSettings((s) => ({ ...s, delay_base_minutes: n }))}
                  placeholder="60"
                />
                <span className="text-base05">{"]"}</span>
                <span className="text-base04 leading-none">{" + random: "}</span>
                <span className="text-base05">{"["}</span>
                <NumberInput
                  value={settings.delay_random_minutes}
                  onChange={(n) => setSettings((s) => ({ ...s, delay_random_minutes: n }))}
                  placeholder="0"
                />
                <span className="text-base05">{"]"}</span>
                <span className="text-base04">{" - minutes before and between each session"}</span>
              </span>
            </div>

          </div>
        </div>

        {/* Actions */}
        <div className={sectionCls}>
          <div className="px-4 py-2 border-b border-base02 text-base04 bg-base02">session actions</div>
          <div className="overflow-x-auto">
          <table className="w-full font-mono">
            <thead>
              <tr className="text-left text-base04 border-b border-base02 bg-base02">
                <th className="px-4 py-2 font-normal w-10"></th>
                <th className="px-4 py-2 font-normal w-10">on</th>
                <th className="px-4 py-2 font-normal">type</th>
                <th className="px-4 py-2 font-normal">target</th>
                <th className="px-4 py-2 font-normal w-28">fixed</th>
                <th className="px-4 py-2 font-normal w-28">random</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-base02">
              {actions.map((action, i) => {
                const targets = action.type ? (ACTION_TARGETS[action.type] ?? []) : [];
                return (
                  <tr key={i}>
                    <td className="px-4 py-2 text-base04">{ACTION_LABELS[i]}</td>
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        onClick={() => updateAction(i, { enabled: !action.enabled })}
                        className="group text-left cursor-pointer transition-colors"
                      >
                        <Bracket className={action.enabled ? "text-status-ok group-hover:text-status-bad" : "text-base04 group-hover:text-status-ok"}>
                          {action.enabled ? "x" : "\u00a0"}
                        </Bracket>
                      </button>
                    </td>
                    <td className="px-4 py-2">
                      <Dropdown
                        value={action.type}
                        onChange={(v) => updateAction(i, { type: v })}
                        placeholder="----"
                        options={[
                          { value: "", label: "----" },
                          ...ACTION_TYPES.map((t) => ({ value: t, label: t })),
                        ]}
                      />
                    </td>
                    <td className="px-4 py-2">
                      <Dropdown
                        value={action.target}
                        onChange={(v) => updateAction(i, { target: v })}
                        placeholder="----"
                        disabled={targets.length === 0}
                        options={[
                          { value: "", label: "----" },
                          ...targets.map((t) => ({ value: t, label: t })),
                        ]}
                      />
                    </td>
                    <td className="px-4 py-2">
                      <span className="inline-flex items-center gap-0">
                        <span className="text-base05">{"["}</span>
                        <NumberInput
                          value={action.fixed_count}
                          onChange={(n) => updateAction(i, { fixed_count: n })}
                          placeholder="0"
                        />
                        <span className="text-base05">{"]"}</span>
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <span className="inline-flex items-center gap-0">
                        <span className="text-base05">{"["}</span>
                        <NumberInput
                          value={action.variable_count}
                          onChange={(n) => updateAction(i, { variable_count: n })}
                          placeholder="0"
                        />
                        <span className="text-base05">{"]"}</span>
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          <div className="px-4 py-3 border-t border-base02">
            <BracketCheckbox
              label="run session actions in random order"
              checked={settings.actions_random_order ?? false}
              onChange={(v) => setSettings((s) => ({ ...s, actions_random_order: v }))}
            />
          </div>
        </div>

        {/* Follow Settings */}
        <div className={sectionCls}>
          <div className="px-4 py-2 border-b border-base02 text-base04 bg-base02">follow settings</div>
          <div className="px-4 py-3 flex items-center gap-x-5 gap-y-2 flex-wrap border-b border-base02">

            <span className="inline-flex items-center gap-0">
              <span className="text-base04">{"unfollow after: "}</span>
              <span className="text-base05">{"["}</span>
              <NumberInput
                value={settings.unfollow_days}
                onChange={(n) => setSettings((s) => ({ ...s, unfollow_days: n || 30 }))}
                placeholder="30"
              />
              <span className="text-base05">{"]"}</span>
              <span className="text-base04">{"\u00A0days"}</span>
            </span>

          </div>
          <div className="px-4 py-3 flex items-center gap-x-5 gap-y-2 flex-wrap border-b border-base02">
            <span className="text-base04">follow filters (0 = off):</span>
            <span className="inline-flex items-center gap-0">
              <span className="text-base04">{"max followers: "}</span>
              <span className="text-base05">{"["}</span>
              <NumberInput
                value={settings.max_followers}
                onChange={(n) => setSettings((s) => ({ ...s, max_followers: n }))}
                placeholder="0" max={9999999} maxLength={7}
              />
              <span className="text-base05">{"]"}</span>
            </span>
            <span className="inline-flex items-center gap-0">
              <span className="text-base04">{"min following/followers %: "}</span>
              <span className="text-base05">{"["}</span>
              <NumberInput
                value={settings.min_follow_ratio_pct}
                onChange={(n) => setSettings((s) => ({ ...s, min_follow_ratio_pct: n }))}
                placeholder="0" max={999} maxLength={3}
              />
              <span className="text-base05">{"]"}</span>
            </span>
            <span className="inline-flex items-center gap-0">
              <span className="text-base04">{"min posts: "}</span>
              <span className="text-base05">{"["}</span>
              <NumberInput
                value={settings.min_posts}
                onChange={(n) => setSettings((s) => ({ ...s, min_posts: n }))}
                placeholder="0" max={999} maxLength={3}
              />
              <span className="text-base05">{"]"}</span>
            </span>
          </div>
          <div className="px-4 py-3 grid grid-cols-2 gap-x-6 gap-y-4">
            <div>
              {/* account group: exactly one of the two sections is active; the other dims */}
              <div className="mb-1">
                <BracketCheckbox
                  label="account group - manual list"
                  checked={!poolMode}
                  onChange={(v) => { if (v) setSettings((s) => ({ ...s, account_group_mode: "manual" })); }}
                />
              </div>
              <div className={poolMode ? "opacity-40 transition-opacity" : "transition-opacity"}>
                <textarea ref={groupRef} placeholder="one per line or comma-separated" rows={5}
                  value={settings.account_group ?? ""}
                  onChange={(e) => setSettings((s) => ({ ...s, account_group: e.target.value || null }))}
                  onBlur={() => setSettings((s) => ({ ...s, account_group: normalizeList(s.account_group, "accounts") }))}
                  className="w-full bg-transparent border border-base02 text-base05 placeholder-base04 outline-none focus:border-base0d p-2 font-mono transition-colors resize-y break-words whitespace-pre-wrap"
                />
              </div>

              <div className="mt-3 mb-1">
                <BracketCheckbox
                  label="account group - dynamic seed pool"
                  checked={poolMode}
                  onChange={(v) => { if (v) setSettings((s) => ({ ...s, account_group_mode: "pool" })); }}
                />
                <span className="text-base03 ml-2">{`(${seeds.filter((s) => s.active).length} active - grows from "similar accounts" when under 10)`}</span>
              </div>
              <div className={poolMode ? "transition-opacity" : "opacity-40 transition-opacity"}>
              <div className="border border-base02 p-2 space-y-1 max-h-64 overflow-y-auto">
                {seeds.length === 0 && <div className="text-base04">----</div>}
                {seeds.map((s) => (
                  <div key={s.id} className="flex items-center gap-2 flex-wrap">
                    <button
                      type="button"
                      onClick={() => handleToggleSeed(s)}
                      title={s.active ? "active - click to disable" : (s.retire_reason ?? "disabled") + " - click to enable"}
                      className="group cursor-pointer inline-flex items-center gap-0"
                    >
                      <span className="text-base05">[</span>
                      <span className={s.active ? "text-status-ok group-hover:text-base0d" : "text-base04 group-hover:text-base0d"}>{s.active ? "x" : " "}</span>
                      <span className="text-base05">]</span>
                    </button>
                    <span className={s.active ? "text-base05" : "text-base04 line-through"}>{s.handle}</span>
                    <span className="text-base04">
                      {s.complete > 0 ? `${s.followed_back}/${s.complete} (${Math.round((s.rate ?? 0) * 100)}%)` : "----"}
                    </span>
                    {s.last_saturation != null && <span className="text-base04">{`sat ${s.last_saturation}%`}</span>}
                    {s.origin !== "manual" && <span className="text-base03">{s.origin}</span>}
                    {!s.active && s.retire_reason && <span className="text-base03">{s.retire_reason}</span>}
                    <button type="button" onClick={() => handleDeleteSeed(s)} className="text-base04 hover:text-status-error transition-colors">[del]</button>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <span className="text-base05">[</span>
                <input
                  type="text"
                  value={newSeed}
                  onChange={(e) => setNewSeed(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); handleAddSeed(); } }}
                  placeholder="handle"
                  className="bg-transparent text-base05 placeholder-base04 outline-none font-mono w-40"
                />
                <span className="text-base05">]</span>
                <button type="button" onClick={handleAddSeed} className="group cursor-pointer">
                  <Bracket className="text-base0d group-hover:text-base05">add</Bracket>
                </button>
                {seedMsg && <span className="text-status-error">{seedMsg}</span>}
              </div>
              </div>
            </div>
            <div>
              <div className="text-base04 mb-1">instagram topics</div>
              <textarea ref={topicsRef} placeholder="one per line or comma-separated" rows={5}
                value={settings.topics ?? ""}
                onChange={(e) => setSettings((s) => ({ ...s, topics: e.target.value || null }))}
                onBlur={() => setSettings((s) => ({ ...s, topics: normalizeList(s.topics, "topics") }))}
                className="w-full bg-transparent border border-base02 text-base05 placeholder-base04 outline-none focus:border-base0d p-2 font-mono transition-colors resize-y break-words whitespace-pre-wrap"
              />
            </div>
          </div>
        </div>

        {/* Save / Delete */}
        <div className="flex items-center gap-6">
          <button
            type="submit"
            disabled={saving || !settingsLoaded}
            className="group disabled:opacity-50 transition-colors bg-base11 border border-base02 px-2 py-0.5"
          >
            <Bracket className="text-base0d group-hover:text-base05">
              {saving ? "saving…" : "save settings"}
            </Bracket>
          </button>
          {loadError && <span className="text-status-bad">{loadError}</span>}
          {msg && <span className="text-status-ok">{msg}</span>}
          <button type="button" onClick={handleDelete} className="group transition-colors ml-auto bg-base11 border border-base02 px-2 py-0.5">
            <Bracket className="text-base04 group-hover:text-status-bad">delete account</Bracket>
          </button>
        </div>

      </form>
    </div>
  );
}
