import type { Metadata } from "next";
import { Landing } from "./landing";

// Server component so this route can carry its own metadata; the page body
// is a client component because it reads auth state and hosts the forms.
export const metadata: Metadata = {
  title: "SlowBurnBot — steady, sustainable growth for your social profiles",
  description:
    "SlowBurnBot helps small businesses, agencies, and creators promote their social profiles at a steady, sustainable pace. Set it up once in a clean dashboard and watch your presence grow.",
};

export default function Home() {
  return <Landing />;
}
