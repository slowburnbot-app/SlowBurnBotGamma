"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { login } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Bracket } from "@/lib/bracket";

/**
 * The sign-in form shared by /login and the public landing page. Owns its
 * own submit + role-based routing so both callers behave identically.
 */
export function LoginForm({ showRegisterLink = true }: { showRegisterLink?: boolean }) {
  const router = useRouter();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
      const user = await refresh();
      router.push(user?.is_superuser ? "/admin" : "/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && <div className="text-status-bad">{error}</div>}
      <div className="flex items-center gap-2">
        <span className="font-mono text-base04 shrink-0">email</span>
        <input
          type="email"
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          className="flex-1 bg-transparent border-b border-base02 text-base05 placeholder-base04 outline-none focus:border-base0d py-0.5 font-mono transition-colors"
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="font-mono text-base04 shrink-0">password</span>
        <input
          type="password"
          placeholder="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
          className="flex-1 bg-transparent border-b border-base02 text-base05 placeholder-base04 outline-none focus:border-base0d py-0.5 font-mono transition-colors"
        />
      </div>
      <div className="flex items-center justify-between pt-1">
        <button
          type="submit"
          disabled={loading}
          className="group disabled:opacity-50 transition-colors"
        >
          <Bracket className="text-base0d group-hover:text-base05">
            {loading ? "signing in…" : "sign in"}
          </Bracket>
        </button>
        {showRegisterLink && (
          <Link href="/register" className="text-base04 hover:text-base0d transition-colors">
            register →
          </Link>
        )}
      </div>
    </form>
  );
}
