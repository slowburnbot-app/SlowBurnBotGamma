import { LoginForm } from "@/lib/login-form";

export default function LoginPage() {
  return (
    <div className="min-h-screen flex flex-col font-mono">
      <div className="flex-1 max-w-5xl mx-auto w-full sm:border-x border-base03">
        <header className="px-3 sm:px-6 py-3">
          <span className="font-semibold text-base0e">SlowBurnBot</span>
        </header>
        <main className="px-3 sm:px-6 py-6">
          <div className="text-base04 mb-4">sign in</div>
          <LoginForm />
        </main>
      </div>
    </div>
  );
}
