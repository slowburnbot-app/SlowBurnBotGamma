import fs from "node:fs";
import path from "node:path";
import YAML from "yaml";

export type Base24Variant = "dark" | "light";

export type Base24Theme = {
  system: "base24";
  name: string;
  slug: string;
  author?: string;
  variant: Base24Variant;
  palette: Record<string, string>;
};

export type LoadedTheme = Base24Theme & {
  /** CSS body for a :root { … } block — 24 declarations, no braces. */
  css: string;
};

const SLOT_IDS = [
  "base00", "base01", "base02", "base03", "base04", "base05", "base06", "base07",
  "base08", "base09", "base0A", "base0B", "base0C", "base0D", "base0E", "base0F",
  "base10", "base11", "base12", "base13", "base14", "base15", "base16", "base17",
] as const;

const HEX6 = /^#?[0-9a-fA-F]{6}$/;

/**
 * Loads a tinted-theming base24 YAML scheme from frontend/themes/.
 * Server-only: this is how the root layout injects the SSR default
 * (slowburnbot.yaml) before paint. The catalog users choose from lives in
 * the database (curated at /admin/themes, served by the backend /themes API);
 * the `slowburnbot` row there must match this file.
 * Throws if the file is missing, malformed, or any of the 24 slots is invalid.
 * No silent fallback — the YAML is the single source of truth for the default.
 */
export function loadTheme(slug: string): LoadedTheme {
  const filePath = path.join(process.cwd(), "themes", `${slug}.yaml`);
  const raw = fs.readFileSync(filePath, "utf8");
  const parsed = YAML.parse(raw) as Partial<Base24Theme>;

  if (parsed.system !== "base24") {
    throw new Error(
      `theme-loader: ${slug}.yaml has system="${parsed.system}", expected "base24"`,
    );
  }
  if (!parsed.palette || typeof parsed.palette !== "object") {
    throw new Error(`theme-loader: ${slug}.yaml missing palette object`);
  }

  const decls: string[] = [];
  for (const id of SLOT_IDS) {
    const v = parsed.palette[id];
    if (!v || !HEX6.test(v)) {
      throw new Error(
        `theme-loader: ${slug}.yaml palette.${id} is missing or not a 6-char hex (got: ${JSON.stringify(v)})`,
      );
    }
    const hex = v.replace(/^#/, "").toLowerCase();
    decls.push(`--${id.toLowerCase()}:#${hex};`);
  }

  return {
    ...(parsed as Base24Theme),
    css: decls.join(""),
  };
}
