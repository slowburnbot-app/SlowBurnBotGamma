/**
 * base24 color scheme helpers (https://github.com/tinted-theming/home).
 * Dependency-free and client-safe — used by the admin theme manager (paste
 * parsing, previews) and by the client-side theme apply path.
 *
 * The server-side loader for the SSR default lives in theme-loader.ts; the
 * catalog users pick from is in the database (see /admin/themes).
 */

export const BASE24_KEYS = [
  "base00", "base01", "base02", "base03", "base04", "base05", "base06", "base07",
  "base08", "base09", "base0A", "base0B", "base0C", "base0D", "base0E", "base0F",
  "base10", "base11", "base12", "base13", "base14", "base15", "base16", "base17",
] as const;

export type Base24Key = (typeof BASE24_KEYS)[number];
export type Base24Palette = Record<Base24Key, string>;
export type ThemeVariant = "dark" | "light";

export const HEX = /^#[0-9a-f]{6}$/i;

export function isBase24Palette(v: unknown): v is Base24Palette {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  return BASE24_KEYS.every((k) => typeof o[k] === "string" && HEX.test(o[k] as string));
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Fallback when a pasted scheme doesn't declare its variant. */
export function variantOf(palette: Base24Palette): ThemeVariant {
  return luminance(palette.base00) < 0.5 ? "dark" : "light";
}

/**
 * The same `--base00:#……;` declaration string theme-loader.ts emits for the
 * SSR default (lowercase slot names, no braces), so theme-store's
 * applyThemeCss can consume a database palette unchanged. Every value is
 * re-checked against HEX because the result lands in the document's style —
 * nothing non-hex may pass.
 */
export function paletteCss(palette: Record<string, string>): string {
  return BASE24_KEYS.filter((k) => HEX.test(palette[k] ?? ""))
    .map((k) => `--${k.toLowerCase()}:${palette[k].toLowerCase()};`)
    .join("");
}

export type ParsedScheme = {
  name: string;
  author?: string;
  variant: ThemeVariant;
  palette: Base24Palette;
};

/**
 * Parse a tinted-theming scheme YAML file. The spec's files are flat
 * (scalars + one `palette:` map), so a line-based parse suffices — no yaml
 * dependency on the client. Accepts quoted/unquoted values, hex with/without
 * '#', trailing `# comments`, and lowercase key casing (base0a → base0A).
 * Throws readable errors for base16-only or malformed input.
 */
export function parseSchemeYaml(src: string): ParsedScheme {
  const scalar = (key: string) => {
    const raw = src.match(new RegExp(`^${key}:[ \\t]*(.*)$`, "m"))?.[1]?.trim();
    if (!raw) return undefined;
    const quoted = raw.match(/^["'](.*?)["']/);
    if (quoted) return quoted[1];
    return raw.replace(/\s+#.*$/, "").trim() || undefined;
  };

  const canonical = new Map(BASE24_KEYS.map((k) => [k.toLowerCase(), k]));
  const palette: Partial<Record<Base24Key, string>> = {};
  for (const m of src.matchAll(
    /^[ \t]+(base[01][0-9a-fA-F]):\s*["']?#?([0-9a-fA-F]{6})["']?\s*(?:#.*)?$/gm,
  )) {
    const key = canonical.get(m[1].toLowerCase());
    if (key) palette[key] = "#" + m[2].toLowerCase();
  }

  const system = scalar("system") ?? "base24";
  if (system !== "base24") {
    throw new Error(`Only base24 schemes are supported (this one declares system: "${system}")`);
  }
  const missing = BASE24_KEYS.filter((k) => !palette[k]);
  if (missing.length) {
    throw new Error(`Palette is missing ${missing.join(", ")} — is this a base24 scheme?`);
  }
  const name = scalar("name");
  if (!name) throw new Error("Scheme has no name");

  const full = palette as Base24Palette;
  const declared = scalar("variant");
  return {
    name,
    author: scalar("author"),
    variant: declared === "light" || declared === "dark" ? declared : variantOf(full),
    palette: full,
  };
}

export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
