"use client";

import { useEffect, useState } from "react";
import { createCheckoutSession, createPortalSession, getSubscriptionInfo, SubscriptionInfo } from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { ACTIVE_THEME } from "@/lib/active-theme";
import { getStoredTheme, setStoredTheme, applyThemeCss } from "@/lib/theme-store";

type ThemeEntry = {
  slug: string;
  name: string;
  preview: Record<string, string>;
};

const SWATCH_SLOTS = ["base00", "base08", "base0A", "base0B", "base0E", "base05"];

function ThemeSelector() {
  const [themes, setThemes] = useState<ThemeEntry[]>([]);
  const [applied, setApplied] = useState<string | null>(null);

  useEffect(() => {
    setApplied(getStoredTheme() ?? ACTIVE_THEME);
    fetch("/api/themes")
      .then((r) => r.json())
      .then(setThemes)
      .catch(() => {});
  }, []);

  function apply(slug: string) {
    fetch(`/api/themes/${encodeURIComponent(slug)}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.css) {
          applyThemeCss(data.css);
          setStoredTheme(slug);
          setApplied(slug);
        }
      })
      .catch(() => {});
  }

  function reset() {
    fetch(`/api/themes/${encodeURIComponent(ACTIVE_THEME)}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.css) {
          applyThemeCss(data.css);
          import("@/lib/theme-store").then(({ clearStoredTheme }) => clearStoredTheme());
          setApplied(ACTIVE_THEME);
        }
      })
      .catch(() => {});
  }

  const storedIsOverride = applied !== null && applied !== ACTIVE_THEME;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-4">
        <h2 className="font-semibold text-base05">theme</h2>
        {storedIsOverride && (
          <button onClick={reset} className="group cursor-pointer transition-colors">
            <Bracket className="text-base04 group-hover:text-base05">reset to default</Bracket>
          </button>
        )}
      </div>
      <div className="border border-base03">
        <table className="w-full">
          <thead>
            <tr className="text-left text-base04 border-b border-base03 bg-base01">
              <th className="px-[6px] py-2 font-normal">name</th>
              <th className="px-[6px] py-2 font-normal">preview</th>
              <th className="px-[6px] py-2 font-normal text-right"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-base03">
            {themes.map((t) => {
              const isCurrent = t.slug === applied;
              const isDefault = t.slug === ACTIVE_THEME;
              return (
                <tr key={t.slug} className={isCurrent ? "" : "hover:bg-base02 transition-colors"}>
                  <td className="px-[6px] py-2">
                    <span className={isCurrent ? "text-base0e font-semibold" : "text-base04"}>
                      {t.name}
                    </span>
                    {isDefault && (
                      <span className="text-base03 ml-2 text-xs">default</span>
                    )}
                  </td>
                  <td className="px-[6px] py-2">
                    <div className="flex gap-1">
                      {SWATCH_SLOTS.map((slot) => (
                        <div
                          key={slot}
                          style={{
                            width: "18px",
                            height: "18px",
                            backgroundColor: t.preview[slot] ?? "#000",
                            borderRadius: "2px",
                            flexShrink: 0,
                          }}
                        />
                      ))}
                    </div>
                  </td>
                  <td className="px-[6px] py-2 text-right">
                    {isCurrent ? (
                      <Bracket className="text-base0e">active</Bracket>
                    ) : (
                      <button onClick={() => apply(t.slug)} className="group cursor-pointer transition-colors">
                        <Bracket className="text-base04 group-hover:text-base05">apply</Bracket>
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function AccountPage() {
  const [info, setInfo] = useState<SubscriptionInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    getSubscriptionInfo().then(setInfo).catch(() => {});
  }, []);

  const statusOk = info?.status === "active" || info?.status === "trialing";
  // Whether this account has a real Stripe customer behind it — an
  // admin-activated or invite-trial account can be "active"/"trialing"
  // with no Stripe link at all, so status alone can't tell us this. Plan
  // changes for a real Stripe customer go through the Customer Portal;
  // everyone else (including a demo/trial account adding payment for the
  // first time) goes through Checkout.
  const hasBillableSubscription = info?.has_stripe_customer ?? false;

  async function handlePlanAction(tier: string) {
    setBusy(tier);
    setMsg("");
    try {
      const { url } = hasBillableSubscription
        ? await createPortalSession()
        : await createCheckoutSession(tier);
      window.location.href = url;
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "Something went wrong.");
      setBusy(null);
    }
  }

  async function handleManageBilling() {
    setBusy("portal");
    setMsg("");
    try {
      const { url } = await createPortalSession();
      window.location.href = url;
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "Something went wrong.");
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6 font-mono">
      <h1 className="font-semibold text-base05">Account</h1>

      {/* Plan */}
      <div className="space-y-2">
        <h2 className="font-semibold text-base05">plan</h2>
        {info ? (
          <>
            <div className="border border-base03">
              <div className="border-b border-base03 px-[6px] py-2 bg-base01 text-base04 flex items-center justify-between">
                <span>current plan</span>
                {hasBillableSubscription && (
                  <button onClick={handleManageBilling} disabled={busy !== null} className="group cursor-pointer transition-colors disabled:opacity-50">
                    <Bracket className="text-base04 group-hover:text-base05">
                      {busy === "portal" ? "..." : "manage billing"}
                    </Bracket>
                  </button>
                )}
              </div>
              <div className="px-[6px] py-2 flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="text-base05 font-semibold capitalize">{info.plan_tier}</span>
                <span className="text-base03">—</span>
                <span className={statusOk ? "text-status-ok" : "text-status-bad"}>{info.status}</span>
                <span className="text-base03">—</span>
                <span><span className="text-base04">accounts </span><span className="text-base05">{info.current_accounts}/{info.max_accounts}</span></span>
                <span className="text-base03">—</span>
                <span><span className="text-base04">clients </span><span className="text-base05">{info.current_clients}/{info.max_clients}</span></span>
                {info.current_period_end && (
                  <>
                    <span className="text-base03">—</span>
                    <span><span className="text-base04">renewal </span><span className="text-base05">{new Date(info.current_period_end).toLocaleDateString()}</span></span>
                  </>
                )}
              </div>
            </div>
            {msg && <p className="text-status-bad text-sm">{msg}</p>}

            <h2 className="font-semibold text-base05">available plans</h2>
            <div className="border border-base03">
              <table className="w-full">
                <thead>
                  <tr className="text-left text-base04 border-b border-base03 bg-base01">
                    <th className="px-[6px] py-2 font-normal">tier</th>
                    <th className="px-[6px] py-2 font-normal">price</th>
                    <th className="px-[6px] py-2 font-normal">accounts</th>
                    <th className="px-[6px] py-2 font-normal">clients</th>
                    <th className="px-[6px] py-2 font-normal text-right"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-base03">
                  {info.tiers.map((tier) => {
                    const isCurrent = tier.name === info.plan_tier;
                    const isUpgrade = tier.max_accounts > info.max_accounts;
                    return (
                      <tr key={tier.name} className={isCurrent ? "" : "hover:bg-base02 transition-colors"}>
                        <td className="px-[6px] py-2">
                          <span className={`capitalize font-semibold ${isCurrent ? "text-base0e" : "text-base04"}`}>
                            {tier.name}
                          </span>
                        </td>
                        <td className={`px-[6px] py-2 ${isCurrent ? "text-base05" : "text-base04"}`}>${tier.price}/mo</td>
                        <td className={`px-[6px] py-2 ${isCurrent ? "text-base05" : "text-base04"}`}>{tier.max_accounts}</td>
                        <td className={`px-[6px] py-2 ${isCurrent ? "text-base05" : "text-base04"}`}>{tier.max_clients}</td>
                        <td className="px-[6px] py-2 text-right">
                          {isCurrent && hasBillableSubscription ? (
                            <Bracket className="text-base0e">current plan</Bracket>
                          ) : isCurrent ? (
                            // Current tier, but no Stripe customer yet (demo
                            // / admin-activated / invite-trial account) —
                            // offer to start paying for the plan it's
                            // already on, not just switching tiers.
                            <button
                              onClick={() => handlePlanAction(tier.name)}
                              disabled={busy !== null}
                              className="group cursor-pointer transition-colors disabled:opacity-50"
                            >
                              <Bracket className="text-base04 group-hover:text-base05">
                                {busy === tier.name ? "..." : "add payment"}
                              </Bracket>
                            </button>
                          ) : (
                            <button
                              onClick={() => handlePlanAction(tier.name)}
                              disabled={busy !== null}
                              className="group cursor-pointer transition-colors disabled:opacity-50"
                            >
                              <Bracket className="text-base04 group-hover:text-base05">
                                {busy === tier.name ? "..." : isUpgrade ? "upgrade" : "downgrade"}
                              </Bracket>
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="text-base04">loading...</p>
        )}
      </div>

      {/* Theme */}
      <ThemeSelector />
    </div>
  );
}
