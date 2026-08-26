# burnBot_followFilter.py
#
# Shared candidate screening for every follow action (suggested, account list,
# post engagers). One place for:
#   - the known-handle set (previous follow targets + universal ignore list),
#     lowercased on both sides so the dedupe actually matches DOM handles;
#   - reading the profile hover card that opens when a username is hovered
#     (posts / followers / following / private), verified live 2026-08-26:
#     the card is a positioned <div>, NOT a role=dialog, whose innerText reads
#       "<handle>\n<Full Name>\n92\nposts\n1,424\nfollowers\n1,651\nfollowing\n…"
#   - the user-configurable quality rules (max_followers, min_follow_ratio_pct,
#     min_posts, skip_private) and the skip bookkeeping that goes with them.

import re

from selenium.webdriver.common.action_chains import ActionChains

from burnBot_human import hsleep
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line

_PRIVATE_MARKERS = ("the account is private", "this account is private")

# "1,424" / "12.5K" / "1.2M" / "3"
_NUM_RE = re.compile(r"^([\d][\d,]*(?:\.\d+)?)\s*([KkMm]?)$")

# Walk up from every "followers" label on the page and return the innerText of the
# first ancestor that also carries "posts"/"following" AND the hovered username —
# the username check is what separates the hover card from the profile header
# stats that share the page when the bot is inside a followers/following dialog.
_HOVER_CARD_JS = """
const uname = (arguments[0] || '').toLowerCase();
const snap = document.evaluate(
  "//span[starts-with(normalize-space(text()),'follower')]",
  document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
for (let i = 0; i < snap.snapshotLength; i++) {
  let el = snap.snapshotItem(i);
  for (let depth = 0; depth < 10 && el; depth++, el = el.parentElement) {
    const t = el.innerText || '';
    if (t.length > 600) break;
    // Exact-line match on the handle: a substring test would let a short handle
    // ("nike") match the profile header of the account being mined ("nikestore").
    if (/following/i.test(t) && /posts?/i.test(t) &&
        t.split('\\n').some(l => l.trim().toLowerCase() === uname)) {
      return t;
    }
  }
}
return null;
"""


def parse_count(raw):
    """'1,424' -> 1424, '12.5K' -> 12500, '1.2M' -> 1200000. None when unparseable."""
    if raw is None:
        return None
    m = _NUM_RE.match(str(raw).strip())
    if not m:
        return None
    num, suffix = m.group(1).replace(",", ""), m.group(2).upper()
    try:
        value = float(num)
    except ValueError:
        return None
    if suffix == "K":
        value *= 1_000
    elif suffix == "M":
        value *= 1_000_000
    return int(value)


def parse_hover_card(text):
    """Pull posts/followers/following/private out of hover-card text.

    Returns a dict (missing counts are None) or None when the text is empty.
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    card = {"posts": None, "followers": None, "following": None, "private": False}
    lowered = text.lower()
    card["private"] = any(marker in lowered for marker in _PRIVATE_MARKERS)
    for idx, line in enumerate(lines):
        label = line.lower()
        key = None
        if label in ("post", "posts"):
            key = "posts"
        elif label in ("follower", "followers"):
            key = "followers"
        elif label == "following":
            key = "following"
        if key and idx > 0 and card[key] is None:
            card[key] = parse_count(lines[idx - 1])
    return card


def fmt_count(n):
    if n is None:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def evaluate_candidate(card, user_config, page_private=False):
    """Apply the user's follow filters to a parsed hover card.

    Returns (verdict, detail) where verdict is one of
      ok | private | too_big | low_ratio | no_posts
    A missing card never blocks a follow ("ok", "no-card") — the filters are a
    quality lever, not a gate, and IG sometimes just doesn't render the card.
    """
    cfg = user_config or {}
    skip_private = bool(cfg.get("skip_private", False))
    is_private = page_private or bool(card and card.get("private"))
    if is_private and skip_private:
        return "private", ""
    if not card:
        return "ok", "no-card"

    followers, following, posts = card.get("followers"), card.get("following"), card.get("posts")

    max_followers = int(cfg.get("max_followers") or 0)
    if max_followers > 0 and followers is not None and followers > max_followers:
        return "too_big", f"{fmt_count(followers)} followers"

    min_ratio_pct = int(cfg.get("min_follow_ratio_pct") or 0)
    if min_ratio_pct > 0 and followers and following is not None:
        ratio_pct = following * 100 // followers
        if ratio_pct < min_ratio_pct:
            return "low_ratio", f"{fmt_count(following)}/{fmt_count(followers)} = {ratio_pct}%"

    min_posts = int(cfg.get("min_posts") or 0)
    if min_posts > 0 and posts is not None and posts < min_posts:
        return "no_posts", f"{posts} posts"

    return "ok", ""


class KnownHandles:
    """Case-insensitive set of handles the bot must not follow again.

    Handles the bot chose NOT to follow (status skipped/private) are tracked
    separately so a seed's saturation math can tell "already followed" (pool
    exhausted) from "filtered last time" (a config choice)."""

    def __init__(self, handles=()):
        self._set = {h.lower() for h in handles if h}
        self._skipped = set()

    def __contains__(self, handle):
        return bool(handle) and handle.lower() in self._set

    def add(self, handle, skipped=False):
        if handle:
            self._set.add(handle.lower())
            if skipped:
                self._skipped.add(handle.lower())

    def is_skipped(self, handle):
        return bool(handle) and handle.lower() in self._skipped

    def __len__(self):
        return len(self._set)


_SKIP_STATUSES = ("skipped", "private")
_PAGE_SIZE = 5000


def load_known_handles(apiClient, account_id, account, scope, lbl, _p):
    """Previous follow targets (any status) + the universal ignore list."""
    known = KnownHandles()
    try:
        page = 1
        while True:
            targets = apiClient.get_follow_targets(account_id, page=page, page_size=_PAGE_SIZE)
            if not targets:
                break
            for t in targets:
                known.add(t.get("target_handle"), skipped=t.get("status") in _SKIP_STATUSES)
            if len(targets) < _PAGE_SIZE:
                break
            page += 1
    except Exception as e:
        _p(client_log_line(account, scope, f"{lbl}Warning: Could not load follow targets: {e}"))
    try:
        for h in apiClient.get_ignore_handles():
            known.add(h)
    except Exception as e:
        _p(client_log_line(account, scope, f"{lbl}Warning: Could not load ignore list: {e}"))
    _p(client_log_line(account, scope, f"{lbl}loaded {len(known)} existing entries"))
    return known


def read_hover_card(driver, user_name):
    try:
        text = driver.execute_script(_HOVER_CARD_JS, user_name)
    except Exception:
        return None
    return parse_hover_card(text)


def screen_candidate(driver, apiClient, account_id, account, scope, lbl, source,
                     user_name, anchor, known, follow_date, _p):
    """Hover the candidate, read its card, apply the filters.

    Returns True when the caller should go ahead and click Follow. On a skip the
    follow-target row is written (status 'private' or 'skipped') so the handle is
    never re-evaluated, the handle is added to `known`, and the skip line is printed.
    """
    if anchor is not None:
        try:
            ActionChains(driver).move_to_element(anchor).perform()
            hsleep(1, 2)
        except Exception:
            pass

    card = read_hover_card(driver, user_name)
    page_private = False
    if card is None:
        # No card keyed to this username — fall back to the old whole-page scan.
        page = (driver.page_source or "").lower()
        page_private = any(marker in page for marker in _PRIVATE_MARKERS)

    user_config = apiClient.get_user_config() if apiClient else None
    verdict, detail = evaluate_candidate(card, user_config, page_private=page_private)

    if card:
        debug_line(client_log_line(
            account, scope,
            f"{lbl}card [{user_name}] posts={card['posts']} followers={card['followers']} "
            f"following={card['following']} private={card['private']} -> {verdict}",
        ))

    if verdict == "ok":
        if (page_private or (card and card.get("private"))):
            _p(client_log_line(account, scope, f"{lbl}private @{user_name}"))
        return True

    status = "private" if verdict == "private" else "skipped"
    try:
        apiClient.create_follow_target(
            account_id, user_name, source=source, status=status, follow_date=follow_date,
        )
    except Exception:
        pass
    known.add(user_name, skipped=True)
    reason = verdict if not detail else f"{verdict} {detail}"
    _p(client_log_line(account, scope, f"{lbl}[-skip] - [{user_name}] - [{reason}]"))
    return False
