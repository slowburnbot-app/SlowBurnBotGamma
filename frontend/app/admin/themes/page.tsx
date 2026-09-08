"use client";

import { useEffect, useMemo, useState } from "react";
import { adminCreateTheme, adminDeleteTheme, listThemes, Theme } from "@/lib/api";
import { Bracket } from "@/lib/bracket";
import { ACTIVE_THEME } from "@/lib/active-theme";
import {
  parseSchemeYaml,
  slugify,
  type Base24Palette,
  type ParsedScheme,
  type ThemeVariant,
} from "@/lib/base24";

// Same six chips the account-page picker shows, so previews match there.
const SWATCH_SLOTS = ["base00", "base08", "base0A", "base0B", "base0E", "base05"];

type GalleryEntry = {
  slug: string;
  name: string;
  author: string;
  variant: ThemeVariant;
  palette: Base24Palette;
};

type NewTheme = {
  name: string;
  slug: string;
  variant: ThemeVariant;
  author: string;
  palette: Base24Palette;
};

const PASTE_PLACEHOLDER = 'system: "base24"\nname: "My Theme"\nvariant: "dark"\npalette:\n  base00: "#1a1b26"\n  …';

function Swatches({ palette }: { palette: Record<string, string> }) {
  return (
    <div className="flex gap-1">
      {SWATCH_SLOTS.map((slot) => (
        <div
          key={slot}
          style={{
            width: "18px",
            height: "18px",
            backgroundColor: palette[slot] ?? "#000",
            borderRadius: "2px",
            flexShrink: 0,
          }}
        />
      ))}
    </div>
  );
}

function PastePanel({
  onAdd,
  busy,
  onDirtyChange,
}: {
  /** resolves true when the theme was added — the form clears itself then */
  onAdd: (data: NewTheme) => Promise<boolean>;
  busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [slug, setSlug] = useState("");

  useEffect(() => {
    onDirtyChange(text.trim() !== "");
  }, [text, onDirtyChange]);

  const parsed = useMemo<{ scheme?: ParsedScheme; error?: string }>(() => {
    if (!text.trim()) return {};
    try {
      return { scheme: parseSchemeYaml(text) };
    } catch (err) {
      return { error: err instanceof Error ? err.message : "could not parse the scheme" };
    }
  }, [text]);

  const effectiveSlug = slug || (parsed.scheme ? slugify(parsed.scheme.name) : "");

  async function handleAdd() {
    if (!parsed.scheme) return;
    const ok = await onAdd({
      name: parsed.scheme.name,
      slug: effectiveSlug,
      variant: parsed.scheme.variant,
      author: parsed.scheme.author ?? "",
      palette: parsed.scheme.palette,
    });
    if (ok) {
      setText("");
      setSlug("");
    }
  }

  return (
    <div className="border border-base02 bg-base01">
      <div className="border-b border-base02 px-4 py-2 bg-base02 text-base04">
        paste a base24 scheme (yaml)
      </div>
      <div className="px-4 py-3 space-y-3">
        <textarea
          rows={7}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={PASTE_PLACEHOLDER}
          spellCheck={false}
          className="w-full bg-base00 border border-base02 focus:border-base0d text-base05 placeholder-base03 outline-none font-mono text-sm p-2 transition-colors"
        />
        {parsed.error && <p className="text-status-bad text-sm">{parsed.error}</p>}
        {parsed.scheme && (
          <div className="flex items-center gap-x-5 gap-y-2 flex-wrap text-sm">
            <Swatches palette={parsed.scheme.palette} />
            <span className="text-base05">{parsed.scheme.name}</span>
            <span className="text-base04">{parsed.scheme.variant}</span>
            <span className="inline-flex items-center gap-0">
              <span className="text-base04">{"slug: "}</span>
              <span className="text-base05">{"["}</span>
              <input
                type="text"
                value={effectiveSlug}
                onChange={(e) => setSlug(slugify(e.target.value))}
                style={{ width: "24ch" }}
                className="bg-transparent text-base05 outline-none font-mono min-w-0 px-0"
              />
              <span className="text-base05">{"]"}</span>
            </span>
            <button
              type="button"
              onClick={handleAdd}
              disabled={busy || !effectiveSlug}
              className="group disabled:opacity-50 transition-colors"
            >
              <Bracket className="text-base0d group-hover:text-base05">add theme</Bracket>
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function GalleryPanel({
  gallery,
  installed,
  onAdd,
  busy,
}: {
  gallery: GalleryEntry[] | null;
  installed: Set<string>;
  onAdd: (data: NewTheme) => Promise<boolean>;
  busy: boolean;
}) {
  const [search, setSearch] = useState("");

  const filtered = useMemo(() => {
    if (!gallery) return [];
    const q = search.trim().toLowerCase();
    if (!q) return gallery;
    return gallery.filter(
      (g) =>
        g.name.toLowerCase().includes(q) ||
        g.slug.includes(q) ||
        g.author.toLowerCase().includes(q),
    );
  }, [gallery, search]);

  return (
    <div className="border border-base02 bg-base01">
      <div className="border-b border-base02 px-4 py-2 bg-base02 flex items-center gap-4 flex-wrap text-sm">
        <span className="text-base04">gallery</span>
        <span className="inline-flex items-center gap-0">
          <span className="text-base04">{"search: "}</span>
          <span className="text-base05">{"["}</span>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="name, slug, or author"
            style={{ width: "28ch" }}
            className="bg-transparent text-base05 placeholder-base03 outline-none font-mono min-w-0 px-0"
          />
          <span className="text-base05">{"]"}</span>
        </span>
        {gallery && (
          <span className="text-base03">
            {filtered.length} of {gallery.length} base24 schemes
          </span>
        )}
      </div>
      {!gallery ? (
        <div className="px-4 py-3 text-base04">loading…</div>
      ) : (
        <div className="max-h-96 overflow-y-auto">
          <table className="w-full text-sm">
            <tbody className="divide-y divide-base02">
              {filtered.map((g) => {
                const added = installed.has(g.slug);
                return (
                  <tr key={g.slug} className="hover:bg-base02/60 transition-colors">
                    <td className="px-[6px] py-2 w-32">
                      <Swatches palette={g.palette} />
                    </td>
                    <td className="px-[6px] py-2 text-base05">{g.name}</td>
                    <td className="px-[6px] py-2 text-base04">{g.variant}</td>
                    <td className="px-[6px] py-2 text-base04 max-w-xs truncate">{g.author}</td>
                    <td className="px-[6px] py-2 text-right whitespace-nowrap">
                      {added ? (
                        <span className="text-base03">added</span>
                      ) : (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => onAdd({ ...g })}
                          className="group disabled:opacity-50 transition-colors"
                        >
                          <Bracket className="text-base04 group-hover:text-base05">add</Bracket>
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-[6px] py-3 text-base04">
                    no schemes match “{search}”.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function AdminThemesPage() {
  const [themes, setThemes] = useState<Theme[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [showGallery, setShowGallery] = useState(false);
  const [showPaste, setShowPaste] = useState(false);
  const [pasteDirty, setPasteDirty] = useState(false);
  const [gallery, setGallery] = useState<GalleryEntry[] | null>(null);

  async function load() {
    try {
      setThemes(await listThemes());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "failed to load themes.");
    }
  }

  useEffect(() => {
    listThemes()
      .then(setThemes)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "failed to load themes."));
  }, []);

  // The bundled gallery is 190 tinted-theming/schemes (spec-0.11) base24
  // files, pre-parsed by Portal's scripts/fetch-base24-gallery.ts and copied
  // to lib/base24-gallery.json. Loaded on first open so it never ships to
  // other routes.
  useEffect(() => {
    if (!showGallery || gallery) return;
    import("@/lib/base24-gallery.json")
      .then((m) => setGallery(m.default as GalleryEntry[]))
      .catch(() => setError("failed to load the gallery."));
  }, [showGallery, gallery]);

  const installed = useMemo(() => new Set((themes ?? []).map((t) => t.slug)), [themes]);

  async function add(data: NewTheme): Promise<boolean> {
    setBusy(true);
    setMsg("");
    setError("");
    try {
      await adminCreateTheme({
        name: data.name,
        slug: data.slug,
        variant: data.variant,
        author: data.author || null,
        palette: data.palette,
      });
      setMsg(`added "${data.name}".`);
      await load();
      return true;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "failed to add theme.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function remove(t: Theme) {
    if (!confirm(`Delete "${t.name}"? Visitors who picked it fall back to the default theme.`)) return;
    setBusy(true);
    setMsg("");
    setError("");
    try {
      await adminDeleteTheme(t.id);
      setMsg(`deleted "${t.name}".`);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "failed to delete theme.");
    } finally {
      setBusy(false);
    }
  }

  function togglePaste() {
    if (showPaste && pasteDirty && !confirm("Discard the pasted scheme?")) return;
    setShowPaste((v) => !v);
  }

  if (!themes) {
    return <div className="font-mono text-base04">{error || "loading…"}</div>;
  }

  return (
    <div className="space-y-4 font-mono">
      <div className="flex items-center gap-4 flex-wrap">
        <h1 className="font-semibold text-base05">admin — Themes</h1>
        <button type="button" onClick={() => setShowGallery((v) => !v)} className="group transition-colors">
          <Bracket className={showGallery ? "text-base0d" : "text-base04 group-hover:text-base05"}>
            browse gallery
          </Bracket>
        </button>
        <button type="button" onClick={togglePaste} className="group transition-colors">
          <Bracket className={showPaste ? "text-base0d" : "text-base04 group-hover:text-base05"}>
            paste yaml
          </Bracket>
        </button>
      </div>
      {msg && <p className="text-status-ok">{msg}</p>}
      {error && <p className="text-status-bad">{error}</p>}

      {showPaste && <PastePanel onAdd={add} busy={busy} onDirtyChange={setPasteDirty} />}
      {showGallery && <GalleryPanel gallery={gallery} installed={installed} onAdd={add} busy={busy} />}

      <div className="border border-base02 bg-base01">
        <table className="w-full">
          <thead>
            <tr className="text-left text-base04 border-b border-base02 bg-base02">
              <th className="px-[6px] py-2 font-normal">preview</th>
              <th className="px-[6px] py-2 font-normal">name</th>
              <th className="px-[6px] py-2 font-normal">variant</th>
              <th className="px-[6px] py-2 font-normal">author</th>
              <th className="px-[6px] py-2 font-normal text-right"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-base02">
            {themes.map((t) => {
              const isDefault = t.slug === ACTIVE_THEME;
              return (
                <tr key={t.id} className="hover:bg-base02/60 transition-colors">
                  <td className="px-[6px] py-2 w-32">
                    <Swatches palette={t.palette} />
                  </td>
                  <td className="px-[6px] py-2">
                    <span className="text-base05">{t.name}</span>
                    {isDefault && <span className="text-base03 ml-2 text-xs">default</span>}
                    <span className="text-base03 ml-2 text-xs">{t.slug}</span>
                  </td>
                  <td className="px-[6px] py-2 text-base04">{t.variant}</td>
                  <td className="px-[6px] py-2 text-base04 max-w-xs truncate">{t.author || "----"}</td>
                  <td className="px-[6px] py-2 text-right whitespace-nowrap">
                    {!isDefault && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => remove(t)}
                        className="group disabled:opacity-50 transition-colors"
                      >
                        <Bracket className="text-base04 group-hover:text-status-bad-hover">delete</Bracket>
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
