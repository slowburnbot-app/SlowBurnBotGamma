# burnBot_client_log.py — unified TUI / client log line shape
from __future__ import annotations

from datetime import datetime
from typing import Optional

ACCOUNT_COL = 18
PREFIX_COL = 32


def log_sanitize(text: str) -> str:
    if not text:
        return ""
    return text.replace("\x00", "")


def mask_email(email: str) -> str:
    s = (email or "").strip()
    if not s or "@" not in s:
        return s or "—"
    local, _, domain = s.partition("@")
    if len(local) <= 2:
        return f"{local[0]}***@{domain}" if local else f"***@{domain}"
    return f"{local[:2]}***@{domain}"


def mask_phone(phone: str) -> str:
    s = "".join(c for c in (phone or "") if c.isdigit())
    if len(s) < 4:
        return "***" if phone and phone.strip() else "—"
    return f"***{s[-4:]}"


def _truncate_account(name: str, width: int) -> str:
    n = log_sanitize(name)
    if len(n) <= width:
        return n
    if width <= 1:
        return "…"
    return n[: width - 1] + "…"


def client_log_line(account: Optional[str], scope: str, message: str = "") -> str:
    """One log line: HH:MM:SS [account]-scope: <aligned message>"""
    ts = datetime.now().strftime("%H:%M:%S")
    sc = log_sanitize((scope or "").strip())
    msg = log_sanitize(str(message))
    acct_raw = (account or "").strip()
    if acct_raw:
        acct = _truncate_account(acct_raw, ACCOUNT_COL)
        prefix = f"[{acct}]-{sc}:"
    else:
        prefix = f"[{sc}]:"
    return f"{ts} {prefix:<{PREFIX_COL}} {msg}".rstrip()


def summarize_issue_log(log_text: str, max_len: int = 60) -> Optional[str]:
    """Collapse a newline-joined error/warning log into a short one-line summary."""
    lines = [l.strip() for l in (log_text or "").strip().splitlines() if l.strip()]
    if not lines:
        return None
    summary = lines[0]
    if len(lines) > 1:
        summary += f" (+{len(lines) - 1} more)"
    if len(summary) > max_len:
        summary = summary[: max_len - 1] + "…"
    return summary


def action_combo_slug(act_type: str, act_target: str) -> Optional[str]:
    """Hyphenated action-target for dashboard/API type+target pairs (single source of truth)."""
    t = (act_type or "").strip().lower()
    g = (act_target or "").strip().lower()
    if t == "like":
        if g in ("home", "homepage posts", "post[homepage]", "posts [homepage]"):
            return "like-home"
        if g in ("post[topics]", "posts [topics]"):
            return "like-topics"
    if t == "follow":
        if g in ("suggested", "home", "homepage", "suggested users"):
            return "follow-suggested"
        if g in (
            "followers[group]",
            "following[group]",
            "account list [followers]",
            "account list [following]",
            "account list [similar]",
        ):
            return "follow-group"
        if g in ("post engagers [topics]", "post engagers [account list]"):
            return "follow-engagers"
    if t == "unfollow":
        if g == "database":
            return "unfollow-database"
        if g == "previous follows":
            return "unfollow-previous"
    return None


def action_target_label(act_type: str, act_target: str) -> str:
    """verb[target] slug for the log taxonomy (e.g. 'like-post[homepage]', 'follow[suggested]')."""
    t = (act_type or "").strip().lower()
    g = (act_target or "").strip().lower()
    if t == "like":
        if g in ("home", "homepage posts", "post[homepage]", "posts [homepage]"):
            return "like-post[homepage]"
        if g in ("post[topics]", "posts [topics]"):
            return "like-post[topics]"
    if t == "follow":
        if g in ("suggested", "home", "homepage", "suggested users"):
            return "follow[suggested]"
        if g == "account list [similar]":
            return "follow[similar]"
        if g == "post engagers [topics]":
            return "follow[likers-topics]"
        if g == "post engagers [account list]":
            return "follow[likers-accounts]"
        if "follower" in g:
            return "follow[followers]"
        if "following" in g:
            return "follow[following]"
    if t == "unfollow":
        if g == "database":
            return "unfollow[database]"
        if g == "previous follows":
            return "unfollow[previous]"
    if t and g:
        return f"{t}[{g}]"
    return t or g or "action"
