import { NextRequest, NextResponse } from "next/server";

// Dedicated handler for the public account-request form. A static segment
// wins over the catch-all in app/api/[...path]/route.ts, so this is the only
// path that reaches the backend's anonymous POST /public/account-request.
//
// Rate limiting lives here, not in FastAPI, because the catch-all proxy
// deliberately strips x-forwarded-* before forwarding — the backend only ever
// sees the proxy's own address. This handler runs at the deployment edge and
// can see the real client IP.
//
// Counters are per-process and reset on every redeploy. That's deliberate:
// for an invite-only product's contact form it's enough to blunt a burst.
// If volume ever justifies it, Turnstile or a DB-backed counter is the upgrade.

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8080";

const WINDOW_MS = 60 * 60 * 1000;
const PER_IP_LIMIT = 3;
const GLOBAL_LIMIT = 20;
const MAX_BODY_BYTES = 16 * 1024;

const perIp = new Map<string, number[]>();
let global: number[] = [];

function clientIp(req: NextRequest): string {
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) return fwd.split(",")[0].trim();
  return req.headers.get("x-real-ip") ?? "unknown";
}

function prune(stamps: number[], now: number): number[] {
  return stamps.filter((t) => now - t < WINDOW_MS);
}

/** Records the attempt and returns false when the caller is over a limit. */
function admit(ip: string): boolean {
  const now = Date.now();
  global = prune(global, now);
  const mine = prune(perIp.get(ip) ?? [], now);
  if (mine.length >= PER_IP_LIMIT || global.length >= GLOBAL_LIMIT) {
    perIp.set(ip, mine);
    return false;
  }
  mine.push(now);
  global.push(now);
  perIp.set(ip, mine);
  // Keep the map from growing with one-off visitors.
  if (perIp.size > 1000) {
    for (const [k, v] of perIp) if (prune(v, now).length === 0) perIp.delete(k);
  }
  return true;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const length = Number(req.headers.get("content-length") ?? 0);
  if (length > MAX_BODY_BYTES) {
    return NextResponse.json({ detail: "request too large." }, { status: 413 });
  }

  if (!admit(clientIp(req))) {
    return NextResponse.json(
      { detail: "too many requests — please try again later." },
      { status: 429 },
    );
  }

  const body = await req.text();
  const res = await fetch(`${BACKEND}/public/account-request`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
  });
  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
  });
}
