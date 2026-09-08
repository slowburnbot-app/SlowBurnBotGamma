"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { logout } from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { APP_VERSION } from "@/lib/version";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  const navItems = [
    { href: "/admin", label: "[users]" },
    { href: "/admin/accounts", label: "[accounts]" },
    { href: "/admin/invites", label: "[invites]" },
    { href: "/admin/requests", label: "[requests]" },
  ];

  useEffect(() => {
    if (!loading && (!user || !user.is_superuser)) router.push("/dashboard");
  }, [user, loading, router]);

  if (loading || !user?.is_superuser) return null;

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const headerAdminActive =
    pathname !== "/admin/config" && (pathname === "/admin" || pathname.startsWith("/admin/"));
  const headerConfigActive = pathname === "/admin/config";

  function subNavUsersActive() {
    return pathname === "/admin";
  }

  function subNavAccountsActive() {
    return pathname === "/admin/accounts" || pathname.startsWith("/admin/accounts/");
  }

  return (
    <div className="min-h-screen flex flex-col font-mono">
      <div className="flex-1 max-w-5xl mx-auto w-full sm:border-x border-base02">
        <header className="px-3 sm:px-6 py-3 flex flex-col-reverse gap-y-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-x-3">
          <div className="flex flex-wrap items-center gap-x-3 sm:gap-x-6 gap-y-1">
            <span className="font-semibold text-base0d">SlowBurnBot</span>
            <span className="hidden sm:inline text-base03">--</span>
            <nav className="flex flex-wrap gap-1">
              <Link
                href="/admin"
                className={`transition-colors ${
                  headerAdminActive ? "text-base0d" : "text-base04 hover:text-white"
                }`}
              >
                <span className="text-base05">[</span>admin<span className="text-base05">]</span>
              </Link>
              <Link
                href="/admin/config"
                className={`transition-colors ${
                  headerConfigActive ? "text-base0d" : "text-base04 hover:text-white"
                }`}
              >
                <span className="text-base05">[</span>config<span className="text-base05">]</span>
              </Link>
            </nav>
          </div>
          <div className="flex flex-wrap items-center gap-3 sm:gap-4">
            <button onClick={() => window.location.reload()} className="text-base03 hover:text-base04 cursor-pointer transition-colors" title="Click to reload">v{APP_VERSION}</button>
            <span className="text-base0a truncate max-w-[12rem] sm:max-w-none">{user.email}</span>
            <button onClick={handleLogout} className="group transition-colors">
              <Bracket className="text-base04 group-hover:text-base0d">sign out</Bracket>
            </button>
          </div>
        </header>
        <main className="px-3 sm:px-6 py-6 space-y-4">
          <nav className="flex flex-wrap gap-1 text-sm border-b border-base02 pb-3 items-center">
            <span className="text-base04 mr-2">admin:</span>
            {navItems.map((item) => {
              const active =
                item.href === "/admin"
                  ? subNavUsersActive()
                  : pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`transition-colors ${
                    active ? "text-base0d" : "text-base04 hover:text-white"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          {children}
        </main>
      </div>
    </div>
  );
}
