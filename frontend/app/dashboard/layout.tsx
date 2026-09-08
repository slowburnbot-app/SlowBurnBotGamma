"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { logout } from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { APP_VERSION } from "@/lib/version";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !user) router.push("/login");
  }, [user, loading, router]);

  if (loading || !user) {
    return <div className="min-h-screen flex items-center justify-center font-mono text-base04">loading…</div>;
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const navItems = [
    { href: "/dashboard", label: "overview" },
    { href: "/dashboard/accounts", label: "accounts" },
    { href: "/dashboard/clients", label: "clients" },
    { href: "/dashboard/config", label: "config" },
    ...(user.is_superuser ? [{ href: "/admin", label: "admin" }] : []),
  ];

  function isNavActive(href: string) {
    if (href === "/dashboard") return pathname === "/dashboard";
    return pathname === href || pathname.startsWith(`${href}/`);
  }

  return (
    <div className="min-h-screen flex flex-col font-mono">
      <div className="flex-1 max-w-5xl mx-auto w-full sm:border-x border-base02">
        <header className="px-3 sm:px-6 pt-5 pb-3 flex flex-col gap-y-1">
          <div className="flex items-center justify-end gap-3 sm:gap-4">
            <button onClick={() => window.location.reload()} className="text-base03 hover:text-base04 cursor-pointer transition-colors" title="Click to reload">v{APP_VERSION}</button>
            <span className="text-base0b truncate max-w-[12rem] sm:max-w-none">{user.email}</span>
            <div className="flex gap-1">
              <Link href="/dashboard/account" className={`group transition-colors ${pathname.startsWith("/dashboard/account") ? "text-base0d" : ""}`}>
                <Bracket className="text-base04 group-hover:text-base0d">account</Bracket>
              </Link>
              <button onClick={handleLogout} className="group transition-colors">
                <Bracket className="text-base04 group-hover:text-base0d">log out</Bracket>
              </button>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold text-base0d">SlowBurnBot <span className="text-base03 font-normal">--</span></span>
            <nav className="flex flex-wrap gap-1">
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`transition-colors ${
                    isNavActive(item.href)
                      ? "text-base0d"
                      : "text-base04 hover:text-white"
                  }`}
                >
                  <span className="text-base05">[</span>{item.label}<span className="text-base05">]</span>
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="px-3 sm:px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
