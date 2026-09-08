"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { submitAccountRequest } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Bracket } from "@/lib/bracket";
import { LoginForm } from "@/lib/login-form";

const HIGHLIGHTS: [string, string][] = [
  ["steady pacing", "consistent, measured activity that builds a following over time instead of chasing spikes."],
  ["every profile, its own space", "each profile is managed separately, with its own settings, schedule, and history."],
  ["on your schedule", "choose the days, the hours, and a daily limit that fit how you want to show up."],
  ["reach the right people", "connect with audiences that matter to you: people in your niche, fans of similar profiles, and communities around your topics."],
  ["see everything", "activity history, growth trends, and a full export, so you always know what's been done and what it's doing for you."],
  ["built-in good judgment", "sensible limits and a do-not-touch list keep your promotion tasteful and your reputation intact."],
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
            placeholder="how many profiles"
            className={inputCls}
          />
        </div>
      </div>
      <div className="space-y-1">
        <div className="text-base04">profile handles or links</div>
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
      <div className="flex-1 max-w-5xl mx-auto w-full bg-base00 sm:border-x border-base03">
        <header className="px-3 sm:px-6 py-3 flex items-center justify-between">
          <span className="font-semibold text-base0e">SlowBurnBot</span>
          <a href="#sign-in" className="group transition-colors">
            <Bracket className="text-base04 group-hover:text-base0e">sign in</Bracket>
          </a>
        </header>

        <main className="px-3 sm:px-6 py-6 space-y-8">
          <section className="space-y-3 max-w-3xl">
            <h1 className="text-2xl sm:text-3xl text-base05 leading-snug">
              grow your social presence —{" "}
              <span className="text-base0e">steadily, consistently, and without the daily grind.</span>
            </h1>
            <p className="text-base04 leading-relaxed">
              slowburnbot helps small businesses, agencies, and creators promote their social
              profiles at a steady, sustainable pace. set your goals and your schedule once in a
              clean web dashboard, then check back whenever you like to see how things are going.
            </p>
            <p className="text-base04 leading-relaxed">
              built for people juggling more than one profile who want consistent, organic growth
              without burning out — or burning through their audience&apos;s patience.
            </p>
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">
            <section className="lg:col-span-3 border border-base03 bg-base01">
              <div className="border-b border-base03 px-4 py-2 bg-base02">
                <span className="text-base05">what you get</span>
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

            <section id="sign-in" className="lg:col-span-2 border border-base03 bg-base01 scroll-mt-4">
              <div className="border-b border-base03 px-4 py-2 bg-base02">
                <span className="text-base05">sign in</span>
              </div>
              <div className="px-4 py-4">
                <LoginForm showRegisterLink={false} />
              </div>
            </section>
          </div>

          <section id="request" className="border border-base03 bg-base01 relative scroll-mt-4">
            <div className="border-b border-base03 px-4 py-2 bg-base02 flex items-center justify-between">
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
