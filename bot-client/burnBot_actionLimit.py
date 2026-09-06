# burnBot_actionLimit.py
"""Instagram action-limit detection and response (likes / follows / unfollows).

Instagram rarely *warns* before throttling an account. It either shows a hard
"Action Blocked / Try Again Later" dialog, or silently drops the action: the
heart paints red client-side and then reverts when the request is rejected;
the Follow button flips to "Following" and then snaps back. This module turns
those into two tiers of response, per action verb, per session thread:

  soft  — confirmed flip-then-revert count. At _SOFT_SLOW_AT every human-delay
          range on this thread doubles (burnBot_human.set_delay_scale); at
          _SOFT_STOP_AT the verb stops for the rest of the run. Run-local only.
  hard  — block dialog, or a 429 / rejected like-or-follow request seen in the
          page's resource timing. The verb stops for the run AND the backend
          starts an escalating cooldown (24h / 48h / 72h on repeats within a
          week) that the next run honours via GET /bot/settings.

Plus a once-a-day read of Instagram's own Account Status pages
(/settings/help/account_status/…), which the backend uses to shorten a
48h/72h cooldown back to 24h when the account reads clean.

State is thread-local (sessions are threads) in the same style as
burnBot_run_log. Nothing here may raise into an action module — every public
function is a safe no-op on failure. "Restricted" is deliberately not used in
this module's vocabulary: it already means age-restricted topic searches in
burnBot_likePostsTopic.
"""
import os
import threading
import time
from datetime import datetime, timezone

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By

from burnBot_client_log import client_log_line
from burnBot_human import hclick, hsleep, set_delay_scale
from burnBot_run_log import capture_failure_context, debug_line, report_failure

# 2026-09-05: thresholds are deliberate guesses (Instagram's limits vary by
# account age and history and cannot be provoked safely for testing). A revert
# is only counted when the UI *flipped then snapped back* — "never flipped" is
# a selector problem, not a limit — so 3 in one run is already unusual.
_SOFT_SLOW_AT = 3
_SOFT_STOP_AT = 6
_SOFT_SLOW_FACTOR = 2.0

# Re-check window after the optimistic UI flip. Instagram's rejection lands
# well under 2s; the callers' existing post-action pauses absorb this.
_REVERT_RECHECK_S = (2.5, 4.0)

# Phrases Instagram uses in its block dialogs. Matched only inside a visible
# [role=dialog] so help-page prose or a caption can't trip them.
_DIALOG_INDICATORS = (
    "try again later",
    "action blocked",
    "we limit how often",
    "we restrict certain activity",
    "misusing this feature",
    "to protect our community",
    "feedback_required",
)
_DIALOG_DISMISS_TEXTS = ("ok", "dismiss", "close", "not now", "got it")

# Instagram web multiplexes nearly everything through /api/graphql with HTTP
# 200 even on logical failures, so resource-timing status codes are only a
# bonus signal: any 429, or a rejected call on the few dedicated action paths.
_NETWORK_ACTION_PATHS = ("/api/v1/web/likes/", "/api/v1/web/friendships/")

# (name, url, page heading that proves the sub-page body rendered, clean sentence)
_STATUS_PAGES = (
    ("feature_limits",
     "https://www.instagram.com/settings/help/account_status/feature_limits",
     "features you can't use",
     "you can use all the features right now"),
    ("limits_to_your_reach",
     "https://www.instagram.com/settings/help/account_status/limits_to_your_reach",
     "limits to your reach",
     "you don't have limits to your account reach"),
)
_STATUS_PAGE_TEXT_CAP = 2000

_FOLLOW_PRE_STATES = ("follow", "follow back")

# Dev-only: BURNBOT_SIMULATE_ACTION_LIMIT="like:hard" | "like:revert" |
# "follow:revert" | "follow:hard" | "status:flagged" — honoured once per
# process and only while the remote bot_debug flag is on, so the whole
# report → cooldown → notify → next-run-skip pipeline can be exercised
# without provoking Instagram.
_SIM_ENV = "BURNBOT_SIMULATE_ACTION_LIMIT"
_sim_lock = threading.Lock()
_sim_consumed = False

_state = threading.local()


class _Session:
    def __init__(self, account, account_id, apiClient, _print):
        self.account = account
        self.account_id = account_id
        self.apiClient = apiClient
        self._print = _print
        self.cooldowns = {}      # verb -> {"until": datetime|None, "strike": int, "reason": str}
        self.reverts = {}        # verb -> int
        self.stopped = {}        # verb -> reason
        self.warnings = []       # lines drained into the session warning log
        self.slowed = False


def _sess():
    return getattr(_state, "session", None)


def _print_for(s):
    if s is not None and s._print is not None:
        return s._print
    import builtins
    return builtins.print


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def _parse_until(value):
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _fmt_until(dt):
    if dt is None:
        return "----"
    try:
        return dt.astimezone().strftime("%m/%d %I:%M %p")
    except Exception:
        return str(dt)


def begin_session(account, account_id, apiClient, action_limits=None, _print=None) -> None:
    """Start per-thread tracking for one session. `action_limits` is the list
    the server returns with the account settings: [{action, tier, reason,
    strike, until}, …]; only entries whose `until` is still ahead count."""
    try:
        s = _Session(account, account_id, apiClient, _print)
        now = datetime.now(timezone.utc)
        for item in action_limits or []:
            try:
                verb = str(item.get("action") or "").strip().lower()
                until = _parse_until(item.get("until"))
                if not verb or until is None or until <= now:
                    continue
                s.cooldowns[verb] = {
                    "until": until,
                    "strike": int(item.get("strike") or 1),
                    "reason": str(item.get("reason") or ""),
                }
            except Exception:
                continue
        _state.session = s
        set_delay_scale(1.0)
        for verb, cd in s.cooldowns.items():
            _print_for(s)(client_log_line(
                account, "limit",
                f"{verb} on cooldown until {_fmt_until(cd['until'])} (strike {cd['strike']}) — will skip",
            ))
    except Exception:
        _state.session = None


def end_session() -> None:
    try:
        set_delay_scale(1.0)
    except Exception:
        pass
    _state.session = None


def is_action_blocked(verb) -> tuple:
    """(blocked, why) for an action verb ("like" / "follow" / "unfollow")."""
    s = _sess()
    if s is None:
        return False, ""
    try:
        verb = str(verb or "").strip().lower()
        if verb in s.stopped:
            return True, s.stopped[verb]
        cd = s.cooldowns.get(verb)
        if cd and cd["until"] and cd["until"] > datetime.now(timezone.utc):
            return True, f"cooldown until {_fmt_until(cd['until'])}"
    except Exception:
        pass
    return False, ""


def drain_warnings() -> str:
    """Return (and clear) the warning lines recorded this session, one per
    line, newline-terminated — the shape moduleWarningsLog expects."""
    s = _sess()
    if s is None or not s.warnings:
        return ""
    lines, s.warnings = s.warnings, []
    return "".join(f"{ln}\n" for ln in lines)


# ---------------------------------------------------------------------------
# Simulation (dev only)
# ---------------------------------------------------------------------------

def _take_simulation(expect_verb=None, expect_kind=None):
    """Consume the simulation env var once if it matches (verb, kind)."""
    global _sim_consumed
    try:
        raw = os.environ.get(_SIM_ENV, "")
        if not raw:
            return False
        from burnBot_accountSession_setup import is_bot_debug_enabled
        if not is_bot_debug_enabled():
            return False
        verb, _, kind = raw.strip().lower().partition(":")
        if expect_verb is not None and verb != expect_verb:
            return False
        if expect_kind is not None and kind != expect_kind:
            return False
        with _sim_lock:
            if _sim_consumed:
                return False
            _sim_consumed = True
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Per-click checks
# ---------------------------------------------------------------------------

def mark(driver):
    """Resource-timing marker to take just before a click (see after_click).

    Also clears the resource-timing buffer: its default 250-entry cap fills
    within seconds of a feed load, after which nothing new is recorded and
    the network scan would be blind. Clearing is a plain web API call, not a
    page-context patch (honest-Chrome rule)."""
    try:
        return float(driver.execute_script(
            "try { performance.clearResourceTimings(); } catch (e) {} return performance.now();"
        ))
    except Exception:
        return None


_DIALOG_TEXT_JS = """
const out = [];
for (const d of document.querySelectorAll('[role="dialog"]')) {
  const r = d.getBoundingClientRect();
  if (r.width > 0 && r.height > 0) out.push((d.innerText || '').slice(0, 1500));
}
return out;
"""

_DIALOG_DISMISS_JS = """
const wanted = arguments[0];
for (const d of document.querySelectorAll('[role="dialog"]')) {
  for (const b of d.querySelectorAll('button, [role="button"]')) {
    const t = (b.innerText || '').trim().toLowerCase();
    if (wanted.includes(t)) { b.click(); return t; }
  }
}
return null;
"""

_NETWORK_SCAN_JS = """
const m = arguments[0]; const paths = arguments[1]; const out = [];
for (const e of performance.getEntriesByType('resource')) {
  if (m !== null && m !== undefined && e.startTime < m) continue;
  const s = e.responseStatus; if (!s) continue;
  const n = e.name || '';
  const onActionPath = paths.some(p => n.includes(p));
  if (s === 429 || ((s === 400 || s === 403) && onActionPath)) {
    out.push([s, n.replace(/\\?.*$/, '').slice(0, 160)]);
  }
}
return out;
"""


def _scan_block_dialog(driver):
    """Return the matched indicator phrase if a visible block dialog is up."""
    try:
        texts = driver.execute_script(_DIALOG_TEXT_JS) or []
    except Exception:
        return None
    for text in texts:
        low = (text or "").lower()
        for phrase in _DIALOG_INDICATORS:
            if phrase in low:
                return phrase
    return None


def _dismiss_block_dialog(driver):
    try:
        return driver.execute_script(_DIALOG_DISMISS_JS, list(_DIALOG_DISMISS_TEXTS))
    except Exception:
        return None


def _scan_network(driver, marker):
    try:
        hits = driver.execute_script(_NETWORK_SCAN_JS, marker, list(_NETWORK_ACTION_PATHS)) or []
    except Exception:
        return None
    if not hits:
        return None
    status, name = hits[0]
    return f"http {status} on {name}"


def after_click(driver, verb, marker=None, context=None) -> bool:
    """Run the cheap post-click checks (block dialog, rejected request).
    Returns True when a hard signal was found — the verb is now stopped for
    this run and the caller should stop attempting it."""
    s = _sess()
    if s is None:
        return False
    try:
        verb = str(verb or "").strip().lower()
        if verb in s.stopped:
            return True
        if _take_simulation(verb, "hard"):
            _record_hard(driver, verb, "simulated block dialog", {"context": context, "simulated": True})
            return True
        phrase = _scan_block_dialog(driver)
        if phrase:
            details = {"context": context, "signal": "dialog", "phrase": phrase}
            details["dismissed"] = _dismiss_block_dialog(driver)
            _record_hard(driver, verb, phrase, details)
            return True
        net = _scan_network(driver, marker)
        if net:
            _record_hard(driver, verb, net, {"context": context, "signal": "network"})
            return True
    except Exception:
        pass
    return False


def record_revert(verb, context=None) -> None:
    """A confirmed flip-then-revert on `verb` (soft signal)."""
    s = _sess()
    if s is None:
        return
    try:
        verb = str(verb or "").strip().lower()
        n = s.reverts.get(verb, 0) + 1
        s.reverts[verb] = n
        debug_line(client_log_line(s.account, "limit", f"{verb} reverted ({n}) ctx={context}"))
        if n == _SOFT_SLOW_AT and not s.slowed:
            s.slowed = True
            set_delay_scale(_SOFT_SLOW_FACTOR)
            line = f"action limit: {verb} — soft ({n} reverted) — delays x{_SOFT_SLOW_FACTOR:g} for this run"
            _print_for(s)(client_log_line(s.account, "limit", line))
            s.warnings.append(line)
            _safe_report_failure(s, verb, "slowed", {"reverts": n, "context": context})
        elif n >= _SOFT_STOP_AT and verb not in s.stopped:
            reason = f"{n} reverted"
            s.stopped[verb] = reason
            line = f"action limit: {verb} — soft ({reason}) — stopped for this run"
            _print_for(s)(client_log_line(s.account, "limit", line))
            s.warnings.append(line)
            _safe_report_failure(s, verb, "soft-stop", {"reverts": n, "context": context})
            _safe_report_limit(s, verb, "soft", reason, {"reverts": n, "context": context})
            _safe_alert(s, verb, "soft", reason, None, None)
    except Exception:
        pass


def _record_hard(driver, verb, reason, details) -> None:
    s = _sess()
    if s is None or verb in s.stopped:
        return
    s.stopped[verb] = reason
    payload = dict(details or {})
    payload["diag"] = capture_failure_context(driver)
    resp = _safe_report_limit(s, verb, "hard", reason, payload)
    until = _parse_until(resp.get("until")) if isinstance(resp, dict) else None
    strike = int(resp.get("strike") or 1) if isinstance(resp, dict) else None
    if until is not None:
        s.cooldowns[verb] = {"until": until, "strike": strike or 1, "reason": reason}
    tail = f" — skipped until {_fmt_until(until)}" if until else " — stopped for this run"
    if strike:
        tail += f" (strike {strike})"
    line = f"action limit: {verb} — hard ({reason}){tail}"
    _print_for(s)(client_log_line(s.account, "limit", line))
    s.warnings.append(line)
    _safe_report_failure(s, verb, "hard", {"reason": reason, **payload})
    _safe_alert(s, verb, "hard", reason, until, strike)


def _safe_report_failure(s, verb, status, payload):
    try:
        report_failure(s.account_id, f"action-limit/{verb}", status, payload)
    except Exception:
        pass


def _safe_report_limit(s, verb, tier, reason, details):
    try:
        if s.apiClient is None:
            return None
        return s.apiClient.report_action_limit(s.account_id, verb, tier, reason, details)
    except Exception:
        return None


def _safe_alert(s, verb, tier, reason, until, strike):
    try:
        from burnBot_notifications import send_action_limit_alert
        send_action_limit_alert(
            s.account, verb, tier, reason, _fmt_until(until) if until else None, strike,
            apiClient=s.apiClient, account_id=s.account_id, _print=s._print,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Follow verification
# ---------------------------------------------------------------------------

def follow_row(button):
    """The nearest container of a Follow button that holds a profile link —
    taken BEFORE the click so a re-rendered button can be re-found."""
    try:
        return button.find_element(By.XPATH, "./ancestor::div[.//a[@href]][1]")
    except Exception:
        return None


def _button_text(el):
    return (el.text or "").strip()


def _read_follow_state(button, row):
    try:
        return _button_text(button)
    except StaleElementReferenceException:
        if row is None:
            raise
        for el in row.find_elements(By.XPATH, ".//button | .//*[@role='button']"):
            try:
                t = _button_text(el)
            except StaleElementReferenceException:
                continue
            if t.lower() in _FOLLOW_PRE_STATES or t.lower() in ("following", "requested"):
                return t
        raise


def verify_follow_click(driver, button, row=None, timeout=6) -> str:
    """After clicking Follow: "following" (flipped and stayed — Following or
    Requested), "reverted" (flipped, then back to Follow), "unchanged" (never
    flipped), "unknown" (could not read). Never raises."""
    try:
        if _take_simulation("follow", "revert"):
            hsleep(*_REVERT_RECHECK_S)
            return "reverted"
        end = time.monotonic() + timeout
        flipped = False
        while time.monotonic() < end:
            try:
                t = _read_follow_state(button, row)
            except Exception:
                return "unknown"
            if t and t.lower() not in _FOLLOW_PRE_STATES:
                flipped = True
                break
            time.sleep(0.4)
        if not flipped:
            return "unchanged"
        hsleep(*_REVERT_RECHECK_S)
        try:
            t = _read_follow_state(button, row)
        except Exception:
            # Virtualised lists recycle rows after a follow; a vanished
            # button after a confirmed flip is not evidence of a revert.
            return "following"
        if t and t.lower() in _FOLLOW_PRE_STATES:
            return "reverted"
        return "following"
    except Exception:
        return "unknown"


def follow_and_verify(driver, button, context=None) -> str:
    """Click a Follow button and verify the result. Returns one of
    "following" | "reverted" | "unchanged" | "unknown" (from
    verify_follow_click), "blocked" (a hard limit fired on this click) or
    "click_failed". Callers count and record the follow only on "following"."""
    row = follow_row(button)
    marker = mark(driver)
    try:
        hclick(driver, button)
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", button)
        except Exception:
            return "click_failed"
    # Verify first: the block dialog lands with the API response, a beat
    # after the click, so the verification wait doubles as the settle time.
    outcome = verify_follow_click(driver, button, row=row)
    if after_click(driver, "follow", marker, context=context):
        return "blocked"
    if outcome == "unknown":
        # The click was sent and nothing says it failed; Instagram re-rendered
        # the row before we could read it. Treat as followed so the follow is
        # recorded (an unrecorded follow would never be unfollowed later).
        return "following"
    return outcome


def like_reverted(article, unlike_xpath) -> bool:
    """After the heart flipped to Unlike: wait the re-check window, then
    report whether it snapped back. Never raises (unreadable == not reverted)."""
    try:
        if _take_simulation("like", "revert"):
            hsleep(*_REVERT_RECHECK_S)
            return True
        hsleep(*_REVERT_RECHECK_S)
        return len(article.find_elements(By.XPATH, unlike_xpath)) == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Account Status page (once a day, from the session loop)
# ---------------------------------------------------------------------------

def status_check_due(checked_at) -> bool:
    """True when the server's last status-page timestamp is missing or >24h old."""
    dt = _parse_until(checked_at)
    if dt is None:
        return True
    return (datetime.now(timezone.utc) - dt).total_seconds() >= 24 * 3600


def check_status_page(driver, account, account_id, apiClient, _print=None):
    """Read Instagram's Account Status sub-pages and report the result to the
    backend. Returns True (clean), False (flagged) or None (unreadable).
    Leaves the browser on the home feed."""
    _p = _print or _print_for(None)
    s = _sess()
    texts = {}
    verdicts = {}
    try:
        for name, url, heading, clean_phrase in _STATUS_PAGES:
            try:
                driver.get(url)
                hsleep(3, 5)
                text = driver.execute_script(
                    "const m = document.querySelector('main') || document.body;"
                    "return (m && m.innerText) ? m.innerText : '';"
                ) or ""
            except Exception:
                text = ""
            texts[name] = text[:_STATUS_PAGE_TEXT_CAP]
            low = text.lower()
            # The settings nav (which always says "Account Status") renders
            # before the sub-page body, so only the sub-page's own heading
            # proves the body loaded; without it the read is inconclusive.
            if clean_phrase in low:
                verdicts[name] = True
            elif heading in low:
                verdicts[name] = False   # body rendered, clean sentence absent
            else:
                verdicts[name] = None    # not rendered / not readable
        if _take_simulation("status", "flagged"):
            verdicts = {k: False for k in verdicts}
        readable = [v for v in verdicts.values() if v is not None]
        if not readable:
            clean = None
        else:
            clean = all(readable)
        summary = ", ".join(f"{k}={'clean' if v else ('flagged' if v is False else '?')}" for k, v in verdicts.items())
        # Always report — an unreadable read still stamps the account so the
        # daily check does not re-run every session.
        resp = None
        try:
            if apiClient is not None:
                resp = apiClient.report_status_page_check(
                    account_id, clean, "\n\n".join(f"[{k}]\n{v}" for k, v in texts.items()),
                )
        except Exception:
            resp = None
        if clean is None:
            debug_line(client_log_line(account, "limit", f"account status page unreadable ({summary})"))
        elif clean:
            _p(client_log_line(account, "limit", f"account status: clean ({summary})"))
            # Server replies released=[{action, until}] for cooldowns it shortened.
            released = resp.get("released") if isinstance(resp, dict) else None
            for item in released or []:
                try:
                    verb = str(item.get("action") or "").lower()
                    until = _parse_until(item.get("until"))
                    _p(client_log_line(account, "limit", f"{verb} cooldown shortened — now until {_fmt_until(until)}"))
                    if s is not None and verb in s.cooldowns:
                        s.cooldowns[verb]["until"] = until
                except Exception:
                    continue
        else:
            line = f"action limit: account status page flagged ({summary})"
            _p(client_log_line(account, "limit", line))
            if s is not None:
                s.warnings.append(line)
            try:
                report_failure(account_id, "action-limit/status-page", "flagged",
                               {"verdicts": verdicts, "texts": texts})
            except Exception:
                pass
            try:
                if apiClient is not None:
                    apiClient.report_action_limit(account_id, "all", "status_page", "account status page flagged",
                                                  {"verdicts": verdicts})
            except Exception:
                pass
            if s is not None:
                _safe_alert(s, "all", "status_page", "account status page flagged", None, None)
        return clean
    except Exception:
        return None
    finally:
        try:
            driver.get("https://www.instagram.com/")
            hsleep(2, 4)
        except Exception:
            pass
