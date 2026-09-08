"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { submitAccountRequest } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Bracket } from "@/lib/bracket";
import { LoginForm } from "@/lib/login-form";

const HIGHLIGHTS: [string, string][] = [
  ["slow burn pacing", "randomized delays and idle browsing between actions — it looks like a person scrolling, not a script firing."],
  ["one browser per account", "every account gets its own isolated profile: separate cookies, session, and fingerprint."],
  ["runs on your schedule", "pick the days, the hours, and a daily cap. it works while you don't."],
  ["mix your actions", "chain likes, follows, and unfollows from feeds, topics, suggestions, or any account's followers."],
  ["see everything", "live client status, session-by-session logs, follow-back rates, and a full csv export."],
  ["built-in guardrails", "skips private and sponsored accounts, honors a global ignore list, and backs off when instagram pushes back."],
];

const inputCls =
  "flex-1 min-w-0 bg-transparent border-b border-base03 text-base05 placeholder-base04 outline-none focus:border-base0e py-0.5 font-mono transition-colors";
const textareaCls =
  "w-full bg-transparent border border-base03 text-base05 placeholder-base04 outline-none focus:border-base0e px-2 py-1 font-mono transition-colors resize-y";

function RequestForm() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [industry, setIndustry] = useState("");
  const [accountCount, setAccountCount] = useState("");
  const [handles, setHandles] = useState("");
  const [notes, setNotes] = useState("");
  const [website, setWebsite] = useState(""); // honeypot
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await submitAccountRequest({
        name: name.trim(),
        email: email.trim(),
        company: company.trim() || undefined,
        industry: industry.trim() || undefined,
        account_count: accountCount ? parseInt(accountCount, 10) : undefined,
        instagram_handles: handles.trim() || undefined,
        notes: notes.trim() || undefined,
        website: website || undefined,
      });
      setDone(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "request failed.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="px-4 py-6 space-y-1">
        <div className="text-status-ok">request received.</div>
        <div className="text-base04">we&apos;ll be in touch at {email.trim()}.</div>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="px-4 py-4 space-y-4">
      {error && <div className="text-status-bad">{error}</div>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-4">
        <div className="flex items-center gap-2">
          <span className="text-base04 shrink-0">name</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={200}
            autoComplete="name"
            className={inputCls}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-base04 shrink-0">email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            maxLength={320}
            autoComplete="email"
            className={inputCls}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-base04 shrink-0">company</span>
          <input
            type="text"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            maxLength={200}
            autoComplete="organization"
            className={inputCls}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-base04 shrink-0">industry</span>
          <input
            type="text"
            value={industry}
            onChange={(e) => setIndustry(e.target.value)}
            maxLength={200}
            placeholder="e.g. brewery, salon, photography"
            className={inputCls}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-base04 shrink-0">accounts</span>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={500}
            value={accountCount}
            onChange={(e) => setAccountCount(e.target.value.replace(/\D/g, ""))}
            placeholder="how many instagram accounts"
            className={inputCls}
          />
        </div>
      </div>
      <div className="space-y-1">
        <div className="text-base04">instagram handles</div>
        <textarea
          value={handles}
          onChange={(e) => setHandles(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="one per line"
          className={textareaCls}
        />
      </div>
      <div className="space-y-1">
        <div className="text-base04">anything else</div>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="what you're hoping to get out of it"
          className={textareaCls}
        />
      </div>
      {/* Honeypot: invisible to people, irresistible to form-filling bots. */}
      <div aria-hidden="true" className="absolute -left-[9999px] top-auto w-px h-px overflow-hidden">
        <label>
          website
          <input
            type="text"
            name="website"
            tabIndex={-1}
            autoComplete="off"
            value={website}
            onChange={(e) => setWebsite(e.target.value)}
          />
        </label>
      </div>
      <div className="flex items-center justify-between pt-1">
        <button type="submit" disabled={busy} className="group disabled:opacity-50 transition-colors">
          <Bracket className="text-base0e group-hover:text-base05">
            {busy ? "sending…" : "request account"}
          </Bracket>
        </button>
        <span className="text-base04 text-sm">invite-only while we grow</span>
      </div>
    </form>
  );
}

export function Landing() {
  const { user, loading } = useAuth();
  const router = useRouter();

  // Signed-in visitors land on the dashboard, as / always did. The marketing
  // copy renders meanwhile rather than a spinner — anonymous is the common case.
  useEffect(() => {
    if (!loading && user) router.push(user.is_superuser ? "/admin" : "/dashboard");
  }, [user, loading, router]);

  return (
    <div className="min-h-screen flex flex-col font-mono">
      <div className="flex-1 max-w-5xl mx-auto w-full sm:border-x border-base03">
        <header className="px-3 sm:px-6 py-3 flex items-center justify-between">
          <span className="font-semibold text-base0e">SlowBurnBot</span>
          <a href="#sign-in" className="group transition-colors">
            <Bracket className="text-base04 group-hover:text-base0e">sign in</Bracket>
          </a>
        </header>

        <main className="px-3 sm:px-6 py-6 space-y-8">
          <section className="space-y-3 max-w-3xl">
            <h1 className="text-2xl sm:text-3xl text-base05 leading-snug">
              grow your instagram presence —{" "}
              <span className="text-base0e">slowly, safely, and on autopilot.</span>
            </h1>
            <p className="text-base04 leading-relaxed">
              slowburnbot is a paced, safety-first instagram automation platform for people who
              manage more than one account. instead of hammering instagram with bursts of activity
              that get accounts flagged, it mimics natural human behavior — randomized timing,
              scheduled active hours, daily caps, and chained activities that look like a real
              person scrolling. configure it once in a clean web dashboard; it runs quietly in the
              background and reports back what it did.
            </p>
            <p className="text-base04 leading-relaxed">
              built for small businesses, agencies, and creators juggling several accounts who
              want consistent organic growth without the burnout — or the bans — of doing it by hand.
            </p>
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">
            <section className="lg:col-span-3 border border-base03">
              <div className="border-b border-base03 px-4 py-2 bg-base01">
                <span className="text-base05">what it does</span>
              </div>
              <ul className="divide-y divide-base03">
                {HIGHLIGHTS.map(([title, body]) => (
                  <li key={title} className="px-4 py-3 space-y-0.5">
                    <div className="text-base05">
                      <span className="text-base0e">*</span> {title}
                    </div>
                    <div className="text-base04 text-sm leading-relaxed">{body}</div>
                  </li>
                ))}
              </ul>
            </section>

            <section id="sign-in" className="lg:col-span-2 border border-base03 scroll-mt-4">
              <div className="border-b border-base03 px-4 py-2 bg-base01">
                <span className="text-base05">sign in</span>
              </div>
              <div className="px-4 py-4">
                <LoginForm showRegisterLink={false} />
              </div>
            </section>
          </div>

          <section id="request" className="border border-base03 relative scroll-mt-4">
            <div className="border-b border-base03 px-4 py-2 bg-base01 flex items-center justify-between">
              <span className="text-base05">request an account</span>
              <span className="text-base04 text-sm">tell us a bit about what you run</span>
            </div>
            <RequestForm />
          </section>
        </main>

        <footer className="px-3 sm:px-6 py-4 border-t border-base03 text-base04 text-sm">
          slowburnbot — patient growth for busy people.
        </footer>
      </div>
    </div>
  );
}
