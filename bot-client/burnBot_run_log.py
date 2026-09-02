# burnBot_run_log.py
#
# Developer-facing remote diagnostics for the bot client. Nothing here writes
# to the customer's disk — the customer never modifies code, so local debug
# files help nobody (the developer can't reach them; the customer has the live
# TUI, /log-save, and the dashboard). Everything diagnostic goes to the
# backend instead, where the developer can actually read it:
#
#   - report_failure(): a compact, structured failure record (stage, timing,
#     exception, inline page-text diagnostics) POSTed fire-and-forget to
#     POST /bot/activity-log with kind="failure" — always on.
#   - buffer_line() / flush_session_log(): when the remote bot_debug flag is
#     ON (dashboard-flippable, see is_bot_debug_enabled), every TUI log line
#     of a session is buffered in memory and uploaded as one kind="run_log"
#     record at session end — the full transcript, on demand, per customer.
#
# Retrieval: GET /accounts/{account_id}/activity-log?kind=failure|run_log.
#
# Deliberately separate from burnBot_client_log.py, which must stay a pure
# zero-I/O string formatter — it is imported at module-load time by nearly
# every file, before config is loaded. Nothing in this module may raise into
# a caller; every public function is a safe no-op on failure, because
# diagnostics must never break a bot run.

import atexit
import json
import queue
import threading
import uuid
from collections import deque
from datetime import datetime

try:
    from burnBot_version import BOT_VERSION
except Exception:
    BOT_VERSION = "?"


RUN_ID = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"

_context = threading.local()

_SESSION_BUFFER_MAX_LINES = 1500
_SESSION_LOG_MAX_BYTES = 64_000   # upload cap; head-truncated (session end kept)
_DIAG_MAX_CHARS = 4000            # keeps the whole failure record under the 8000-char details cap


# ---------------------------------------------------------------------------
# Session correlation
# ---------------------------------------------------------------------------

def set_session_context(account, session_id) -> None:
    """Tag this thread's uploaded records (and start its line buffer) with
    account/session for the duration of one accountSession run. Call from the
    session thread only — the tag is thread-local."""
    _context.account = account
    _context.session_id = session_id
    _context.lines = deque(maxlen=_SESSION_BUFFER_MAX_LINES)


def clear_session_context() -> None:
    _context.account = None
    _context.session_id = None
    _context.lines = None


def _current_session_tag() -> str:
    return getattr(_context, "session_id", None) or "-"


# ---------------------------------------------------------------------------
# Debug-gated session line buffer (feeds the kind="run_log" upload)
# ---------------------------------------------------------------------------

def buffer_line(raw_line: str) -> None:
    """Called from the burnBot_status.add_log tap for every TUI log line.
    Buffers the line for this thread's active session — only while the
    remote bot_debug flag is on, and only on threads that have a session
    context (scheduler-thread lines are skipped). Never raises."""
    try:
        lines = getattr(_context, "lines", None)
        if lines is None:
            return
        from burnBot_accountSession_setup import is_bot_debug_enabled
        if not is_bot_debug_enabled():
            return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        lines.append(f"{ts} {raw_line}")
    except Exception:
        pass


def debug_line(line) -> None:
    """Route a debug-detail line into the uploaded session transcript ONLY —
    never the customer-facing TUI. Same gating as buffer_line (Debug Logging
    flag on + session context present). Replaces the old pattern of
    `if is_bot_debug_enabled(): print(...)`, which spammed the customer's
    TUI with internals whenever the flag was flipped for remote diagnosis.
    Never raises."""
    try:
        lines = getattr(_context, "lines", None)
        if lines is None:
            return
        from burnBot_accountSession_setup import is_bot_debug_enabled
        if not is_bot_debug_enabled():
            return
        text = str(line)
        # Many legacy payloads already carry their own "-- DEBUG:" prefix.
        if not text.lstrip().startswith("-- DEBUG"):
            text = f"-- DEBUG: {text}"
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        lines.append(f"{ts} {text}")
    except Exception:
        pass


def flush_session_log(account_id) -> None:
    """Upload this thread's buffered session lines as one kind="run_log"
    activity record, then clear the buffer. No-op when the buffer is empty
    (bot_debug was off for the whole session). Never raises."""
    try:
        lines = getattr(_context, "lines", None)
        if not lines or not account_id:
            return
        header = f"run={RUN_ID} ses={_current_session_tag()} ver={BOT_VERSION} lines={len(lines)}"
        text = header + "\n" + "\n".join(lines)
        if len(text) > _SESSION_LOG_MAX_BYTES:
            # Head-truncate: failures cluster at the end of a session, so the
            # end of the transcript is the part worth keeping.
            text = header + "\n…(truncated)…\n" + text[-_SESSION_LOG_MAX_BYTES:]
        lines.clear()
        _ensure_uploader_started()
        _failure_queue.put_nowait((account_id, "session", "complete", {"_raw": text}, "run_log"))
    except queue.Full:
        pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Text-only failure diagnostics
# ---------------------------------------------------------------------------

def capture_page_diag(driver):
    """
    Capture page URL, visible button texts, role-button/link texts, iframe
    count, body text, and a page source snippet for element-not-found
    diagnostics. Returns (log_str, print_str) — log_str is compact (URL +
    element counts); print_str also includes body/source.
    """
    try:
        from burnBot_imports import By

        page_url = driver.current_url
        buttons = driver.find_elements(By.TAG_NAME, "button")
        btn_texts = [b.text.strip() for b in buttons if b.text.strip()]
        role_buttons = driver.find_elements(By.XPATH, "//*[@role='button']")
        role_btn_texts = [b.text.strip() for b in role_buttons if b.text.strip()][:8]
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        iframe_count = len(iframes)
        body_text = driver.execute_script(
            "return (document.body && document.body.innerText) "
            "? document.body.innerText.slice(0, 1500) : '(no body)'"
        ).replace("\n", " ")
        src_snippet = (driver.page_source or "")[:2000].replace("\n", " ")
        log_str = (
            f"url={page_url} | buttons={btn_texts} | role_buttons={role_btn_texts}"
            f" | iframes={iframe_count}"
        )
        print_str = f"[login-diag] {log_str} | body={body_text} | src={src_snippet}"
        return log_str, print_str
    except Exception:
        return "", ""


def capture_failure_context(driver) -> str:
    """Text-only snapshot of the page for a failure record's `diag` field:
    the capture_page_diag print form, capped to keep the whole uploaded
    record within the details-size budget. Returns "" on any error —
    diagnostics must never break a run."""
    if driver is None:
        return ""
    try:
        _log_str, print_str = capture_page_diag(driver)
        return (print_str or _log_str)[:_DIAG_MAX_CHARS]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Backend upload — fire-and-forget, never blocks the automation loop
# ---------------------------------------------------------------------------

_failure_queue: "queue.Queue" = queue.Queue(maxsize=200)
_uploader_started = False
_uploader_lock = threading.Lock()


def _ensure_uploader_started() -> None:
    global _uploader_started
    with _uploader_lock:
        if _uploader_started:
            return
        t = threading.Thread(target=_uploader_loop, daemon=True, name="failure-uploader")
        t.start()
        atexit.register(_drain_on_exit)
        _uploader_started = True


def _uploader_loop() -> None:
    while True:
        item = _failure_queue.get()
        if item is None:
            return
        account_id, stage, status, payload, kind = item
        try:
            from burnBot_apiClient import get_shared_client
            client = get_shared_client()
            if client is not None and account_id:
                if isinstance(payload, dict) and "_raw" in payload:
                    details = payload["_raw"][:66_000]
                else:
                    details = json.dumps(payload, default=str)[:8000]
                client.log_activity(account_id, stage, status, details, kind=kind)
        except Exception:
            pass  # diagnostics must never throw


def _drain_on_exit() -> None:
    try:
        _failure_queue.put_nowait(None)
    except Exception:
        pass


def report_failure(account_id, stage, status, payload: dict) -> None:
    """
    Enqueue a structured failure record for background upload to the
    backend (kind="failure"). Never blocks — a full queue drops the record
    silently rather than stalling the automation thread on a slow/offline
    API call.
    """
    try:
        _ensure_uploader_started()
        record = dict(payload or {})
        record.setdefault("run", RUN_ID)
        record.setdefault("ses", _current_session_tag())
        record.setdefault("ver", BOT_VERSION)
        record.setdefault("stage", stage)
        _failure_queue.put_nowait((account_id, stage, status, record, "failure"))
    except queue.Full:
        pass
    except Exception:
        pass
