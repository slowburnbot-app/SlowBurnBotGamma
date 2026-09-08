import type { Metadata } from "next";
import { Landing } from "./landing";

// Server component so this route can carry its own metadata; the page body
// is a client component because it reads auth state and hosts the forms.
export const metadata: Metadata = {
  title: "SlowBurnBot — slow, safe Instagram growth on autopilot",
  description:
    "Paced, safety-first Instagram automation for people who manage more than one account. Human-like timing, scheduled hours, daily caps, and a dashboard that shows you everything.",
};

export default function Home() {
  return <Landing />;
}
