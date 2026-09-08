"use client";

import { useEffect, useState } from "react";
import {
  adminListInvites,
  adminCreateInvite,
  adminDeleteInvite,
  InviteCode,
} from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { Dropdown } from "@/lib/dropdown";

const TIER_OPTIONS = [
  { value: "crawl", label: "crawl" },
  { value: "walk", label: "walk" },
  { value: "run", label: "run" },
];

export default function AdminInvitesPage() {
  const [invites, setInvites] = useState<InviteCode[]>([]);
  const [email, setEmail] = useState("");
  const [planTier, setPlanTier] = useState("crawl");
  const [freeTrial, setFreeTrial] = useState(false);
  const [sendEmail, setSendEmail] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function load() {
    adminListInvites().then(setInvites).catch(() => {});
  }

  useEffect(() => {
    load();
  }, []);

  function getStatus(inv: InviteCode): string {
    if (inv.used_at) return "used";
    if (inv.expires_at && new Date(inv.expires_at) < new Date()) return "expired";
    return "available";
  }

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg("");
    try {
      const result = await adminCreateInvite({
        email: email.trim() || undefined,
        plan_tier: planTier,
        free_trial_days: freeTrial ? 30 : undefined,
        send_email: sendEmail && !!email.trim(),
      });
      if ("email_error" in result && result.email_error) {
        setMsg(`code: ${result.code} (email failed: ${result.email_error})`);
      } else {
        setMsg(`code: ${result.code}`);
      }
      setEmail("");
      setFreeTrial(false);
      setSendEmail(false);
      await load();
    } catch (err: unknown) {
      setMsg(err instanceof Error ? err.message : "failed to create invite.");
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(inv: InviteCode) {
    try {
      await adminDeleteInvite(inv.id);
      await load();
    } catch {
      // silently fail
    }
  }

  return (
    <div className="space-y-4 font-mono">
      <h1 className="font-semibold text-base05">admin — invites</h1>
      {msg && <p className="text-status-ok">{msg}</p>}

      <div className="border border-base03 bg-base01">
        <div className="border-b border-base03 px-4 py-2 bg-base02">
          <span className="text-base05">generate invite</span>
        </div>
        <form onSubmit={handleGenerate} className="px-4 py-3 space-y-3">
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex items-center gap-2">
              <span className="text-base04">email</span>
              <input
                type="email"
                placeholder="optional"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="bg-transparent border-b border-base03 text-base05 placeholder-base04 outline-none focus:border-base0e py-0.5 font-mono transition-colors w-56"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-base04">tier</span>
              <span className="text-base05">{"["}</span>
              <Dropdown value={planTier} onChange={setPlanTier} options={TIER_OPTIONS} />
              <span className="text-base05">{"]"}</span>
            </div>
          </div>
          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 cursor-pointer">
              <button
                type="button"
                onClick={() => setFreeTrial(!freeTrial)}
                className="group transition-colors"
              >
                <Bracket className="text-base04 group-hover:text-base05">
                  {freeTrial ? "x" : "\u00a0"}
                </Bracket>
              </button>
              <span className="text-base04">30-day free trial</span>
            </label>
            {email.trim() && (
              <label className="flex items-center gap-2 cursor-pointer">
                <button
                  type="button"
                  onClick={() => setSendEmail(!sendEmail)}
                  className="group transition-colors"
                >
                  <Bracket className="text-base04 group-hover:text-base05">
                    {sendEmail ? "x" : "\u00a0"}
                  </Bracket>
                </button>
                <span className="text-base04">send email</span>
              </label>
            )}
          </div>
          <div>
            <button
              type="submit"
              disabled={busy}
              className="group disabled:opacity-50 transition-colors"
            >
              <Bracket className="text-base0e group-hover:text-base05">
                {busy ? "..." : "generate"}
              </Bracket>
            </button>
          </div>
        </form>
      </div>

      <div className="border border-base03 bg-base01">
        <div className="border-b border-base03 px-4 py-2 bg-base02">
          <span className="text-base05">invite codes</span>
          <span className="text-base04 ml-2">[{invites.length}]</span>
        </div>
        {invites.length === 0 ? (
          <p className="px-4 py-6 text-base04">no invites yet.</p>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-base04 border-b border-base03 bg-base02">
                <th className="px-4 py-2 font-normal">code</th>
                <th className="px-4 py-2 font-normal">email</th>
                <th className="px-4 py-2 font-normal">tier</th>
                <th className="px-4 py-2 font-normal">trial</th>
                <th className="px-4 py-2 font-normal">status</th>
                <th className="px-4 py-2 font-normal">created</th>
                <th className="px-4 py-2 font-normal"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-base03">
              {invites.map((inv) => {
                const invStatus = getStatus(inv);
                return (
                  <tr key={inv.id} className="hover:bg-base02 transition-colors">
                    <td className="px-4 py-2 text-base05 font-semibold">{inv.code}</td>
                    <td className="px-4 py-2 text-base04">{inv.email || "----"}</td>
                    <td className="px-4 py-2 text-base04">{inv.plan_tier}</td>
                    <td className="px-4 py-2 text-base04">
                      {inv.free_trial_days ? `${inv.free_trial_days}d` : "----"}
                    </td>
                    <td className="px-4 py-2">
                      <Bracket
                        className={
                          invStatus === "available"
                            ? "text-status-ok"
                            : invStatus === "used"
                            ? "text-base04"
                            : "text-status-bad"
                        }
                      >
                        {invStatus}
                      </Bracket>
                    </td>
                    <td className="px-4 py-2 text-base04">
                      {new Date(inv.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {invStatus === "available" && (
                        <button
                          onClick={() => handleRevoke(inv)}
                          className="group transition-colors"
                        >
                          <Bracket className="text-status-bad group-hover:text-base05">revoke</Bracket>
                        </button>
                      )}
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
  );
}
