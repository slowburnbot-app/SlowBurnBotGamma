# burnBot_theme.py — palette management for the TUI
#
# Three modes:
#   "default"  — built-in fixed palette (exact current look, no regression)
#   "tinty"    — loads the active tinty scheme (exact 24-color truecolor)
#   "terminal" — ANSI named colors for inline Rich Text; approximate hex for CSS
#
# Usage (from burnBot_app.py):
#   import burnBot_theme as _theme
#   _theme.register(app)           # call from on_mount after table columns are set up
#   _theme.apply_mode(app, "tinty")  # called by /tint picker

import os
import re
import subprocess

MODES = ["default", "tinty", "terminal"]
MODE_LABELS = {
    "default":  "Default (built-in)",
    "tinty":    "Follow tinty (exact)",
    "terminal": "Terminal (ANSI)",
}

# ------------------------------------------------------------------
# Palette role vocabulary
# ------------------------------------------------------------------
# surface      : panel / widget background
# surface_deep : header / deepest background
# surface_high : cursor / selection highlight
# heading      : titles, column headers
# text         : body text
# dim          : borders, scrollbars, meta / secondary text
# accent       : active, focus, "running" status
# brand        : app title highlight
# warn         : amber / warning / initializing
# wait         : yellow / scheduled, between runs
# orange       : orange / waiting (off-schedule)
# error        : red / error / disabled
# ghost        : autocomplete ghost text
# on_accent    : text drawn on accent backgrounds (cursor foreground)

# ------------------------------------------------------------------
# Default palette — exact current TUI hexes (zero visual regression)
# ------------------------------------------------------------------
DEFAULT_PALETTE = {
    "surface":      "#1a1a1a",
    "surface_deep": "#141413",
    "surface_high": "#2a2a2a",
    "heading":      "#f4f3ee",
    "text":         "#c9c7c0",
    "dim":          "#9A968B",
    "accent":       "#adcc00",
    "brand":        "#d97757",
    "warn":         "#E5C07B",
    "wait":         "#E8E06A",
    "orange":       "#E0904A",
    "error":        "#cf3b0a",
    "ghost":        "#4a4a45",
    "on_accent":    "#141413",
}

# ------------------------------------------------------------------
# Terminal mode — two dicts: inline (ANSI names) and CSS (hex approx)
# CSS can't express ANSI named colors, so they each serve their role.
# ------------------------------------------------------------------
TERMINAL_INLINE = {
    # background / structure roles — only used in CSS, kept as hex
    "surface":      "#1a1a1a",
    "surface_deep": "#141413",
    "surface_high": "#2a2a2a",
    # foreground roles — ANSI names so Rich renders them via terminal palette
    "heading":      "bright_white",
    "text":         "default",
    "dim":          "bright_black",
    "accent":       "green",
    "brand":        "magenta",
    "warn":         "yellow",
    "wait":         "bright_yellow",
    "orange":       "color(208)",
    "error":        "red",
    "ghost":        "bright_black",
    "on_accent":    "black",
}

TERMINAL_CSS = {
    "surface":      "#1a1a1a",
    "surface_deep": "#141413",
    "surface_high": "#2a2a2a",
    "heading":      "#ffffff",
    "text":         "#cccccc",
    "dim":          "#666666",
    "accent":       "#00cc00",    # approx ANSI green
    "brand":        "#cc00cc",    # approx ANSI magenta
    "warn":         "#cccc00",    # approx ANSI yellow
    "wait":         "#ffff55",    # approx ANSI bright yellow
    "orange":       "#ff8700",    # approx 256-color 208
    "error":        "#cc0000",    # approx ANSI red
    "ghost":        "#444444",
    "on_accent":    "#000000",
}

# ------------------------------------------------------------------
# base24 slot → palette role mapping (works for base16 too)
# All roles map to base00-base0E, present in both base16 and base24,
# except "wait" (base13, bright yellow) which falls back to the default
# palette on base16 schemes.
# ------------------------------------------------------------------
_BASE24_TO_ROLE = {
    "surface":      "base01",
    "surface_deep": "base00",
    "surface_high": "base02",
    "heading":      "base06",
    "text":         "base05",
    "dim":          "base04",
    "accent":       "base0B",   # green
    "brand":        "base0E",   # keywords/accent color
    "warn":         "base0A",   # yellow
    "wait":         "base13",   # bright yellow (base24 only)
    "orange":       "base09",   # orange
    "error":        "base08",   # red
    "ghost":        "base03",   # comments
    "on_accent":    "base00",   # darkest background (readable on accent)
}

# ------------------------------------------------------------------
# Account status → palette role
# ------------------------------------------------------------------
_STATUS_TO_ROLE = {
    "running":         "accent",
    "initializing":    "warn",
    "scheduled":       "wait",
    "paused":          "warn",
    "max runs":        "warn",
    "disabled":        "error",
    "system-disabled": "error",
    "no schedule":     "error",
    "idle":            "dim",
    "waiting":         "orange",
}


def status_color(palette: dict, status: str) -> str:
    """Return the resolved color string for an account status."""
    role = _STATUS_TO_ROLE.get(status, "dim")
    return palette.get(role, palette["dim"])


def get_css_vars(palette: dict) -> dict[str, str]:
    """Convert a palette dict to Textual CSS variable names (bb-* prefix, kebab-case)."""
    return {f"bb-{k.replace('_', '-')}": v for k, v in palette.items()}


# ------------------------------------------------------------------
# Tinty scheme loader
# ------------------------------------------------------------------
_tinty_slug_cache:    str  | None = None
_tinty_palette_cache: dict | None = None


def load_tinty_palette() -> dict | None:
    """
    Query tinty for its active scheme, parse the YAML, and map base24 slots
    to palette roles.  Returns a palette dict, or None on any failure
    (caller should fall back to DEFAULT_PALETTE).
    """
    global _tinty_slug_cache, _tinty_palette_cache
    try:
        result = subprocess.run(
            ["tinty", "current", "slug"],
            capture_output=True, text=True, timeout=3,
        )
        slug = (result.stdout or "").strip()
        if not slug or result.returncode != 0:
            return None

        # Return cached result if slug hasn't changed since last call
        if slug == _tinty_slug_cache and _tinty_palette_cache is not None:
            return _tinty_palette_cache

        # Resolve YAML path from slug e.g. "base24-tokyo-night-dark"
        first_dash = slug.index("-")
        system = slug[:first_dash]        # "base24"
        name   = slug[first_dash + 1:]   # "tokyo-night-dark"

        xdg_data = os.environ.get(
            "XDG_DATA_HOME",
            os.path.join(os.path.expanduser("~"), ".local", "share"),
        )
        yaml_path = os.path.join(
            xdg_data, "tinted-theming", "tinty", "repos",
            "schemes", system, f"{name}.yaml",
        )
        if not os.path.isfile(yaml_path):
            return None

        with open(yaml_path, "r", encoding="utf-8") as fh:
            raw = fh.read()

        # Parse palette lines — handles both "#hex" (tinty) and bare "hex" (repo) formats
        slots: dict[str, str] = {}
        for m in re.finditer(r'\bbase([0-9A-Fa-f]{2})\s*:\s*["\']?#?([0-9a-fA-F]{6})', raw):
            key = "base" + m.group(1).upper()
            slots[key] = "#" + m.group(2).lower()

        if len(slots) < 16:
            return None  # Incomplete parse; bail out

        # Map base24 slots → roles, falling back to default for any missing slot
        palette = {
            role: slots.get(slot, DEFAULT_PALETTE[role])
            for role, slot in _BASE24_TO_ROLE.items()
        }
        _tinty_slug_cache    = slug
        _tinty_palette_cache = palette
        return palette

    except Exception:
        return None


# ------------------------------------------------------------------
# Mode application
# ------------------------------------------------------------------

def _build_palettes(mode: str) -> tuple[dict, dict]:
    """Return (css_palette, inline_palette) for the given mode."""
    if mode == "tinty":
        p = load_tinty_palette()
        if p is None:
            p = DEFAULT_PALETTE
        return p, p
    if mode == "terminal":
        return TERMINAL_CSS, TERMINAL_INLINE
    # "default"
    return DEFAULT_PALETTE, DEFAULT_PALETTE


def register(app) -> None:
    """
    Initialise theme state on the app.
    Call from on_mount() *after* all DataTable columns have been added.
    """
    from burnBot_config import CONFIG
    mode = CONFIG.get("client_ui", "tint", fallback="default")
    if mode not in MODES:
        mode = "default"
    apply_mode(app, mode, persist=False)


def apply_mode(app, mode: str, persist: bool = True) -> None:
    """
    Apply a theme mode to the app.

    Updates app._tint_mode, app._palette (inline styles), app._css_palette
    (CSS variables), then refreshes CSS and every inline-rendered widget.
    """
    css_p, inline_p = _build_palettes(mode)
    app._tint_mode   = mode
    app._palette     = inline_p
    app._css_palette = css_p

    # Allow ANSI color passthrough for terminal mode
    try:
        app.ansi_color = (mode == "terminal")
    except Exception:
        pass

    # Re-apply CSS with new variable values
    try:
        app.refresh_css()
    except Exception:
        pass

    if persist:
        _persist_mode(mode)

    # Refresh every widget that uses inline palette colors
    for fn_name in (
        "_refresh_header",
        "_refresh_settings_rows",
        "_refresh_tint_rows",
        "_refresh_help_rows",
        "_refresh_hint_widgets",
        "_refresh_all_account_rows",
    ):
        try:
            getattr(app, fn_name)()
        except Exception:
            pass


def _persist_mode(mode: str) -> None:
    """Write the tint mode to [client_ui] tint in the INI file."""
    from burnBot_config import CONFIG, CONFIG_FILE_PATH
    if not CONFIG_FILE_PATH:
        return
    if not CONFIG.has_section("client_ui"):
        CONFIG.add_section("client_ui")
    CONFIG.set("client_ui", "tint", mode)
    try:
        with open(CONFIG_FILE_PATH, "w") as fh:
            CONFIG.write(fh)
    except OSError:
        pass
