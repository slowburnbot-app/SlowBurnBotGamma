"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { register, login } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Bracket } from "@/lib/bracket";

function RegisterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [inviteCode, setInviteCode] = useState(searchParams.get("code") ?? "");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    if (!inviteCode.trim()) {
      setError("Registration code is required.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      await register(email, password, undefined, inviteCode.trim());
      await login(email, password);
      await refresh();
      router.push("/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
    <div className="text-base04 mb-4">create account</div>
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && <div className="text-status-bad">{error}</div>}
      <div className="flex items-center gap-2">
        <span className="font-mono text-base04 shrink-0">code</span>
        <input
          type="text"
          placeholder="registration code"
          value={inviteCode}
          onChange={(e) => setInviteCode(e.target.value)}
          required
          className="flex-1 bg-transparent border-b border-base03 text-base05 placeholder-base04 outline-none focus:border-base0e py-0.5 font-mono transition-colors"
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="font-mono text-base04 shrink-0">email</span>
        <input
          type="email"
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          className="flex-1 bg-transparent border-b border-base03 text-base05 placeholder-base04 outline-none focus:border-base0e py-0.5 font-mono transition-colors"
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
          minLength={8}
          className="flex-1 bg-transparent border-b border-base03 text-base05 placeholder-base04 outline-none focus:border-base0e py-0.5 font-mono transition-colors"
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="font-mono text-base04 shrink-0">confirm</span>
        <input
          type="password"
          placeholder="confirm password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          required
          minLength={8}
          className="flex-1 bg-transparent border-b border-base03 text-base05 placeholder-base04 outline-none focus:border-base0e py-0.5 font-mono transition-colors"
        />
      </div>
      <div className="flex items-center justify-between pt-1">
        <button
          type="submit"
          disabled={loading}
          className="group disabled:opacity-50 transition-colors"
        >
          <Bracket className="text-base0e group-hover:text-base05">
            {loading ? "creating..." : "create account"}
          </Bracket>
        </button>
        <Link href="/login" className="text-base04 hover:text-base0e transition-colors">
          ← sign in
        </Link>
      </div>
    </form>
    </>
  );
}

export default function RegisterPage() {
  return (
    <div className="min-h-screen flex flex-col font-mono">
      <div className="flex-1 max-w-5xl mx-auto w-full bg-base00 sm:border-x border-base03">
        <header className="px-3 sm:px-6 py-3">
          <span className="font-semibold text-base0e">SlowBurnBot</span>
        </header>
        <main className="px-3 sm:px-6 py-6">
          <Suspense fallback={<div className="text-base04">loading...</div>}>
            <RegisterForm />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
