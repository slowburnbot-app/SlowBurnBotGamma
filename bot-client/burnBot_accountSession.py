import time
import random
import builtins
from datetime import datetime
from burnBot_config import CONFIG
from burnBot_accountSession_setup import create_driver
from burnBot_login import handle_account_login
from burnBot_utils import check_schedule, process_exception, retry_on_connection_error
from burnBot_notifications import send_login_failure_alert, send_session_complete_notification
from burnBot_likePostsHome import do_like_posts_home
from burnBot_likePostsTopic import do_like_posts_topic
from burnBot_unfollowDatabase import do_unfollow_database
from burnBot_followSuggested import do_follow_suggested
from burnBot_followGroup import do_follow_group
from burnBot_randomActions import do_random_action
from burnBot_client_log import client_log_line, action_combo_slug, action_target_label, summarize_issue_log
from burnBot_run_log import set_session_context, clear_session_context, capture_failure_context, report_failure, flush_session_log, debug_line
import burnBot_status as status_store

# Global dictionary to store driver instances
drivers = {}


def _unpack_action_result(result, fn_name, account, slot_num, _print):
    """Normalize a (count, errs) or (count, errs, warns) return from any
    action module into (count, errs, warns).

    Action modules are not uniform in arity (some return 2-tuples, some 3 —
    see the file-by-file table in the design plan), and nothing enforces the
    contract. A mismatch here used to be a hard ValueError that killed the
    whole session (commit 5f46166 changed do_unfollow_database's arity
    without updating all of its return paths). Normalizing at this one
    boundary means a return-shape mistake in any module is reported as a
    loud error line instead of crashing the session.
    """
    if isinstance(result, (tuple, list)):
        if len(result) == 2:
            return result[0], result[1], ""
        if len(result) == 3:
            return result[0], result[1], result[2]
        bad_msg = f"action[{slot_num}]: bad return arity from {fn_name}: len={len(result)}"
    else:
        bad_msg = f"action[{slot_num}]: bad return arity from {fn_name}: {type(result).__name__}"
    _print(client_log_line(account, f"action[{slot_num}]", f"ERROR: {bad_msg}"))
    return 0, bad_msg + "\n", ""


def _parse_actions(settings):
    """Parse the actions list from API settings into a flat structure."""
    actions_list = settings.get("actions") or []
    slots = []
    for i in range(4):
        if i < len(actions_list) and actions_list[i]:
            a = actions_list[i]
            slots.append({
                'enabled': bool(a.get('enabled', False)),
                'type': str(a.get('type', '')).strip().lower(),
                'target': str(a.get('target', '')).strip().lower(),
                'fixed_count': int(a.get('fixed_count', 0) or 0),
                'variable_count': int(a.get('variable_count', 0) or 0),
            })
        else:
            slots.append({
                'enabled': False, 'type': '', 'target': '',
                'fixed_count': 0, 'variable_count': 0,
            })
    return slots


def accountSession(account, account_id, idx, threads_active, stop_flag, apiClient, permanent_idx=None, console=None):
    """
    Individual account session handler.
    Manages browser creation and bot script execution for a single account.

    Args:
        account: Account username/identifier
        account_id: UUID string from the API
        idx: Thread index in threads_active list
        threads_active: List of threading.Event objects for state control
        stop_flag: Threading event to signal shutdown
        apiClient: ApiClient instance for API access
        permanent_idx: Optional permanent index for consistent port assignment
        console: rich.Console instance for thread-safe output (optional)
    """
    global drivers
    _print = console.print if console else builtins.print

    try:
        _accountSession_inner(account, account_id, idx, threads_active, stop_flag, apiClient, permanent_idx, _print)
    except Exception as _thread_exc:
        import traceback
        _print(client_log_line(account, "error", f"FATAL in session thread: {_thread_exc}"))
        _print(client_log_line(account, "error", traceback.format_exc()))
        try:
            threads_active[idx].clear()
        except Exception:
            pass


def _accountSession_inner(account, account_id, idx, threads_active, stop_flag, apiClient, permanent_idx, _print):
    global drivers

    time.sleep(1)
    _print(client_log_line(account, "browser", "start thread"))

    # Fetch initial settings from API
    settings = apiClient.get_account_settings(account_id)
    if not settings:
        _print(client_log_line(account, "browser", "ERROR: Could not fetch settings from API"))
        threads_active[idx].clear()
        return

    # Parse schedule from settings
    scheduleDays = settings.get("schedule_days") or ""
    scheduleStart = settings.get("schedule_start") or ""
    scheduleEnd = settings.get("schedule_end") or ""
    scheduleMax = int(settings.get("max_runs_per_day", 0) or 0)

    # Parse actions
    action_slots = _parse_actions(settings)

    # Other settings
    unfollow_days = int(settings.get("unfollow_days", 30) or 30)
    action_topics = settings.get("topics") or ""

    # Account group / target accounts for follow[group] action
    account_list_tab = settings.get("account_group") or ""

    driver = None

    # Signal initial setup complete (main loop may already show "running"; do not
    # overwrite with "idle" here — that caused the TUI to drop to idle during Chrome/login.)
    threads_active[idx].clear()
    _print(client_log_line(account, "browser", "setup complete and idle"))

    # Main loop: Idle <-> Active
    while not stop_flag.is_set():
        if threads_active[idx].is_set():
            # ACTIVE STATE
            # ========================================
            status_store.update(account, status="initializing", last_action="browser")

            # Start the session transcript context up front — debug_line()
            # silently drops lines on threads without one, and browser
            # setup below emits debug detail before session_start_time is
            # stamped. Re-entering here after a skipped/failed session
            # simply resets the buffer.
            set_session_context(account, f"{account}#{datetime.now().strftime('%H%M%S')}")

            # Open browser for this session
            account_idx_for_port = permanent_idx if permanent_idx is not None else idx
            if driver is not None:
                try:
                    driver.current_url  # quick connectivity check
                    drivers[account] = driver
                    debug_line(client_log_line(account, "browser", "reusing existing browser session"))
                except Exception:
                    driver = None

            if driver is None:
                status_store.wait_vnc_ready()
                try:
                    driver = create_driver(account, account_idx=account_idx_for_port)
                    drivers[account] = driver
                    debug_line(client_log_line(account, "browser", "browser opened for session"))
                except Exception as driver_error:
                    _print(client_log_line(account, "error", f"Failed to create Chrome driver: {driver_error}"))
                    apiClient.log_error(account_id, f"Chrome driver creation failed: {driver_error}")
                    threads_active[idx].clear()
                    continue

            if driver is None:
                _print(client_log_line(account, "error", "create_driver returned None — skipping session"))
                apiClient.log_error(account_id, "Chrome driver creation returned None")
                threads_active[idx].clear()
                continue

            # Re-read settings from API on each session (cached, so fast if unchanged)
            try:
                apiClient.invalidate_settings_cache(account_id)
                fresh_settings = apiClient.get_account_settings(account_id)
                if fresh_settings:
                    settings = fresh_settings
                    scheduleDays = settings.get("schedule_days") or ""
                    scheduleStart = settings.get("schedule_start") or ""
                    scheduleEnd = settings.get("schedule_end") or ""
                    scheduleMax = int(settings.get("max_runs_per_day", 0) or 0)
                    action_slots = _parse_actions(settings)
                    unfollow_days = int(settings.get("unfollow_days", 30) or 30)
                    action_topics = settings.get("topics") or ""
                    account_list_tab = settings.get("account_group") or ""

                    debug_line(client_log_line(account, "browser", "Re-read settings from API"))
            except Exception as e:
                debug_line(client_log_line(account, "browser", f"Error re-reading settings, using cached: {e}"))

            # Use the scheduler's daily effective max (base max_runs_per_day + random
            # offset) so the session cap and the "Starting Session [n/max]" display match
            # the scheduler's "run [n/max]" trigger. Falls back to the base if unset.
            _eff_max = status_store.get_effective_max_runs(account)
            if _eff_max and _eff_max > 0:
                scheduleMax = _eff_max

            # Track session start time
            session_start_time = datetime.now().astimezone()

            # Initialize action counts
            action_counts = [0, 0, 0, 0]

            # Check schedule before attempting login
            if not check_schedule(scheduleDays, scheduleStart, scheduleEnd):
                _print(client_log_line(account, "summary", "skip — outside scheduled time/day"))
                session_end_time = datetime.now().astimezone()
                try:
                    run_seq = apiClient.get_run_count(account_id) + 1
                    apiClient.log_session_run(
                        account_id, session_start_time, session_end_time,
                        "", 0, "", 0, "", 0, "", 0, "", run_sequence=run_seq
                    )
                except Exception as e:
                    _print(client_log_line(account, "summary", f"Warning — failed to log skipped session: {e}"))
                threads_active[idx].clear()
                continue

            # Check max runs
            if scheduleMax > 0:
                try:
                    current_run_count = apiClient.get_run_count(account_id)
                except Exception as e:
                    _print(client_log_line(account, "error", f"Error reading run count, returning to IDLE: {e}"))
                    threads_active[idx].clear()
                    continue
                if current_run_count >= scheduleMax:
                    _print(client_log_line(account, "summary", f"skip — max runs per day reached ({current_run_count}/{scheduleMax})"))
                    threads_active[idx].clear()
                    continue

            session_already_handled = False
            actions_run = 0
            moduleErrorsLog = ""
            moduleWarningsLog = ""

            try:
                if stop_flag.is_set():
                    _print(client_log_line(account, "summary", "shutdown requested — skipping login and bot script"))
                    threads_active[idx].clear()
                    break

                # Get IG password from API
                accountPass = apiClient.get_ig_password(account_id) or ""

                status_store.update(account, status="running", last_action="—")

                # CHECK LOGIN / ACCOUNT STATUS
                login_success, current_user, loginFailureExit, login_attempts, verification_requested, loginDiag = handle_account_login(
                    driver, account, accountPass, apiClient
                )

                if loginFailureExit:
                    session_end_time = datetime.now().astimezone()
                    if verification_requested:
                        error_msg = "[login failure] - requesting code"
                    else:
                        error_msg = f"[login failure] - {login_attempts} attempt(s) failed"

                    log_msg = (error_msg + " " + loginDiag).strip() if loginDiag else error_msg

                    try:
                        run_seq = apiClient.get_run_count(account_id) + 1
                        apiClient.log_session_run(
                            account_id, session_start_time, session_end_time,
                            "", 0, "", 0, "", 0, "", 0,
                            log_msg, run_sequence=run_seq
                        )
                        run_count = run_seq
                        max_runs = scheduleMax
                    except Exception as e:
                        _print(client_log_line(account, "login", f"Warning — failed to log login failure session: {e}"))
                        run_count, max_runs = 1, scheduleMax

                    try:
                        send_login_failure_alert(account, error_msg, run_count, max_runs, apiClient=apiClient, account_id=account_id, _print=_print)
                    except Exception as notif_error:
                        _print(client_log_line(account, "notify", f"Warning — notification failed: {notif_error}"))

                    threads_active[idx].clear()
                    session_already_handled = True
                    run_info = f"[{run_count}/{max_runs}]" if max_runs > 0 else f"[{run_count}]"
                    _print(client_log_line(account, "login", f"returning to IDLE — login failure — run {run_info}"))
                    status_store.update(account, status="idle", last_action="session complete - error[login failure]")
                    continue

                if not loginFailureExit:
                    # RUN BOT SCRIPT
                    try:
                        main_window = driver.current_window_handle
                        debug_line(client_log_line(account, "summary", "ready to execute actions"))
                    except Exception:
                        main_window = None

                    try:
                        _next_run = apiClient.get_run_count(account_id) + 1
                        if scheduleMax > 0:
                            _print(client_log_line(account, "[account]", f"Starting Session [{_next_run}/{scheduleMax}]"))
                        else:
                            _print(client_log_line(account, "[account]", f"Starting Session [{_next_run}]"))
                    except Exception:
                        _print(client_log_line(account, "[account]", "Starting Session"))

                    # Process Actions
                    _action_slots_tuples = [
                        (i + 1, s['enabled'], s['type'], s['target'], s['fixed_count'], s['variable_count'])
                        for i, s in enumerate(action_slots)
                    ]

                    if settings.get("actions_random_order"):
                        random.shuffle(_action_slots_tuples)

                    for _slot_idx, (_slot_num, _enabled, _act_type, _act_target, _fixed, _variable) in enumerate(_action_slots_tuples):
                        if status_store.is_bot_paused():
                            break
                        _count = 0
                        _ran = False  # set True only once an action module actually ran to completion

                        if _enabled and _act_type:
                            _total = _fixed + (random.randint(1, _variable) if _variable > 0 else 0)
                            _act_label = action_target_label(_act_type, _act_target)
                            _act_scope = f"action[{_slot_num}]"
                            _print(client_log_line(account, _act_scope, f"{_act_label}-Attempting[{_total:02d}]"))
                            status_store.update(account, status="running", last_action=f"{_act_type} · {_act_target}")

                            try:
                                if _act_type == "like" and _act_target in ["home", "homepage posts", "post[homepage]", "posts [homepage]"]:
                                    _count, _errs, _warns = _unpack_action_result(
                                        do_like_posts_home(driver, account, _total, apiClient, account_id, _print=_print, log_scope=_act_scope, action_label=_act_label),
                                        "do_like_posts_home", account, _slot_num, _print)
                                    if _errs:
                                        moduleErrorsLog += _errs
                                    if _warns:
                                        moduleWarningsLog += _warns
                                    _ran = True

                                elif _act_type == "like" and _act_target in ["post[topics]", "posts [topics]"]:
                                    _topics = action_topics
                                    if _topics:
                                        _count, _errs, _warns = _unpack_action_result(
                                            do_like_posts_topic(driver, account, _total, apiClient, account_id, _topics, _print=_print, log_scope=_act_scope, action_label=_act_label),
                                            "do_like_posts_topic", account, _slot_num, _print)
                                        if _errs:
                                            moduleErrorsLog += _errs
                                        if _warns:
                                            moduleWarningsLog += _warns
                                        _ran = True
                                    else:
                                        _print(client_log_line(account, _act_scope, f"{_act_label} ERROR: No topics specified"))

                                elif _act_type == "follow" and _act_target in ["suggested", "home", "homepage", "suggested users"]:
                                    _count, _errs, _warns = _unpack_action_result(
                                        do_follow_suggested(driver, account, _total, apiClient, account_id, _print=_print, log_scope=_act_scope, action_label=_act_label),
                                        "do_follow_suggested", account, _slot_num, _print)
                                    if _errs:
                                        moduleErrorsLog += _errs
                                    if _warns:
                                        moduleWarningsLog += _warns
                                    _ran = True

                                elif _act_type == "follow" and _act_target in ["followers[group]", "following[group]", "account list [followers]", "account list [following]", "account list [similar]"]:
                                    _target_accounts = account_list_tab
                                    if _target_accounts:
                                        _count, _errs, _warns = _unpack_action_result(
                                            do_follow_group(
                                                driver, account, _total, apiClient, account_id,
                                                _act_target, _target_accounts, _print=_print,
                                                log_scope=_act_scope, action_label=_act_label,
                                            ),
                                            "do_follow_group", account, _slot_num, _print)
                                        if _errs:
                                            moduleErrorsLog += _errs
                                        if _warns:
                                            moduleWarningsLog += _warns
                                        _ran = True
                                    else:
                                        _print(client_log_line(account, _act_scope, f"{_act_label} ERROR: No target accounts specified"))

                                elif _act_type == "unfollow" and _act_target in ["database", "previous follows"]:
                                    _count, _errs, _warns = _unpack_action_result(
                                        do_unfollow_database(
                                            driver, account, _total, apiClient, account_id, unfollow_days,
                                            _print=_print,
                                            log_scope=_act_scope,
                                            action_label=_act_label,
                                        ),
                                        "do_unfollow_database", account, _slot_num, _print)
                                    if _errs:
                                        moduleErrorsLog += _errs
                                    if _warns:
                                        moduleWarningsLog += _warns
                                    _ran = True

                                else:
                                    debug_line(client_log_line(account, _act_scope, f"placeholder {_act_type}/{_act_target}"))
                                    _ran = True

                            except Exception as action_error:
                                error_msg = process_exception(True, f"Action {_slot_num} ({_act_label}) failed: {action_error}", True, False)
                                moduleErrorsLog += error_msg
                                try:
                                    apiClient.log_error(account_id, error_msg)
                                except Exception:
                                    pass
                                try:
                                    report_failure(
                                        account_id, f"action{_slot_num}-{_act_label}", "exception",
                                        {"exc": type(action_error).__name__, "msg": str(action_error)[:500],
                                         "diag": capture_failure_context(driver)},
                                    )
                                except Exception:
                                    pass
                                _count = 0

                        elif _enabled:
                            _print(client_log_line(account, f"action[{_slot_num}]", "enabled but no type"))
                        else:
                            _act_label = action_target_label(_act_type, _act_target) if _act_type else None
                            _msg = f"{_act_label}-disabled" if _act_label else "disabled"
                            _print(client_log_line(account, f"action[{_slot_num}]", _msg))

                        # Count completions, not attempts — a crashed action module
                        # (caught above) leaves _ran False and does not inflate the
                        # "N action(s) executed" summary.
                        if _ran:
                            actions_run += 1

                        # Index by the slot's original position (_slot_num), not the
                        # loop index — when actions_random_order shuffles the tuples,
                        # _slot_idx is the shuffled position and would misfile counts
                        # against the wrong action_slots entry.
                        action_counts[_slot_num - 1] = _count

                        # Random action between slots
                        _remaining = _action_slots_tuples[_slot_idx + 1:]
                        if any(_rem_en and _rem_ty for _, _rem_en, _rem_ty, _, _, _ in _remaining):
                            try:
                                do_random_action(driver, account, _print=_print)
                            except Exception:
                                pass

            except Exception as e:
                error_msg = f"ERROR in active state: {e}"
                _print(client_log_line(account, "error", error_msg))
                apiClient.log_error(account_id, error_msg)

            finally:
                if not session_already_handled:
                    session_end_time = datetime.now().astimezone()
                    run_count = 1
                    max_runs = scheduleMax
                    try:
                        run_seq = apiClient.get_run_count(account_id) + 1
                        apiClient.log_session_run(
                            account_id, session_start_time, session_end_time,
                            action_slots[0]['type'], action_counts[0],
                            action_slots[1]['type'], action_counts[1],
                            action_slots[2]['type'], action_counts[2],
                            action_slots[3]['type'], action_counts[3],
                            moduleErrorsLog.strip(),
                            warning_message=moduleWarningsLog.strip(),
                            run_sequence=run_seq
                        )
                        run_count = run_seq
                    except Exception as log_error:
                        _print(client_log_line(account, "error", f"ERROR in log_session_run: {log_error}"))
                        error_details = process_exception(printError=True, noteError="log_session_run failed", logError=True, debugError=False)
                        if error_details:
                            _print(client_log_line(account, "error", error_details.strip()))

                    try:
                        send_session_complete_notification(
                            account,
                            session_start_time,
                            session_end_time,
                            action_slots[0]['type'], action_counts[0], action_slots[0]['target'],
                            action_slots[1]['type'], action_counts[1], action_slots[1]['target'],
                            action_slots[2]['type'], action_counts[2], action_slots[2]['target'],
                            action_slots[3]['type'], action_counts[3], action_slots[3]['target'],
                            run_count,
                            max_runs,
                            moduleErrorsLog.strip(),
                            warning_log=moduleWarningsLog.strip(),
                            apiClient=apiClient,
                            account_id=account_id,
                            _print=_print,
                        )
                    except Exception as notif_error:
                        _print(client_log_line(account, "notify", f"Warning — session notification failed: {notif_error}"))
                        process_exception(printError=False, noteError="session notification failed", logError=False, debugError=False)

                    threads_active[idx].clear()
                    _run_label = f"{run_count}/{max_runs}" if max_runs > 0 else str(run_count)
                    _secs = int((session_end_time - session_start_time).total_seconds())
                    _mm, _ss = divmod(_secs, 60)
                    _hh, _mm = divmod(_mm, 60)
                    _dur = f"{_hh}h{_mm}m{_ss}s" if _hh else f"{_mm}m{_ss}s"
                    _print(client_log_line(account, "summary", f"Session[{_run_label}] - {actions_run} action(s) executed"))
                    _print(client_log_line(account, "summary", f"Session[{_run_label}] - DONE"))

                    _err_summary = summarize_issue_log(moduleErrorsLog)
                    _warn_summary = summarize_issue_log(moduleWarningsLog)
                    if _err_summary:
                        _session_last_action = f"session complete - error[{_err_summary}]"
                    elif _warn_summary:
                        _session_last_action = f"session complete - warning[{_warn_summary}]"
                    else:
                        _session_last_action = "session complete - no issues"

                    status_store.update(account, status="idle", last_action=_session_last_action, last_run=session_end_time.strftime("%m/%d %I:%M %p"))

                # Close or keep browser based on config
                close_browser_after_session = CONFIG.getboolean('browser-session', 'close_browser_after_session', fallback=True)
                if close_browser_after_session:
                    if driver is not None:
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        try:
                            from burnBot_accountSession_setup import build_user_data_dir, kill_chrome_processes_for_profile
                            chrome_user_data_dir = build_user_data_dir(account)
                            time.sleep(0.5)
                            kill_chrome_processes_for_profile(chrome_user_data_dir, account)
                            debug_line(client_log_line(account, "browser", "browser closed after session"))
                        except Exception:
                            pass
                    drivers.pop(account, None)
                    driver = None

                # Upload the debug-gated session transcript (no-op when bot_debug
                # was off), then drop this thread's session tag/buffer.
                flush_session_log(account_id)
                clear_session_context()

        else:
            # IDLE STATE
            time.sleep(5)

    # Cleanup on exit
    _print(client_log_line(account, "browser", "shutting down…"))

    close_browser_exit = True
    if CONFIG.has_section('browser-session'):
        close_browser_exit = CONFIG.getboolean('browser-session', 'close_browser_after_exit', fallback=True)

    if driver is not None:
        try:
            if close_browser_exit:
                driver_still_connected = False
                try:
                    window_handles = driver.window_handles
                    driver_still_connected = True
                    if window_handles:
                        try:
                            main_window = driver.current_window_handle
                        except Exception:
                            main_window = window_handles[0] if window_handles else None

                        extra_windows = [h for h in window_handles if h != main_window]
                        if extra_windows:
                            _print(client_log_line(account, "browser", f"Closing {len(extra_windows)} extra browser window(s)…"))
                            for handle in extra_windows:
                                try:
                                    driver.switch_to.window(handle)
                                    driver.close()
                                except Exception:
                                    pass
                            if main_window:
                                try:
                                    driver.switch_to.window(main_window)
                                except Exception:
                                    pass
                            time.sleep(0.5)
                except Exception:
                    driver_still_connected = False
                    _print(client_log_line(account, "browser", "Driver connection already lost, will kill Chrome processes directly"))

                if driver_still_connected:
                    try:
                        driver.quit()
                    except Exception:
                        pass

                try:
                    from burnBot_accountSession_setup import build_user_data_dir, kill_chrome_processes_for_profile
                    chrome_user_data_dir = build_user_data_dir(account)
                    time.sleep(0.5)
                    kill_chrome_processes_for_profile(chrome_user_data_dir, account)
                    _print(client_log_line(account, "browser", "thread exiting, browser closed."))
                except Exception as e:
                    _print(client_log_line(account, "error", f"Error killing Chrome processes: {e}"))
            else:
                try:
                    _print(client_log_line(account, "browser", "thread exiting, browser ready."))
                except Exception as e:
                    _print(client_log_line(account, "error", f"Error during disconnect: {e}"))
        except Exception as e:
            _print(client_log_line(account, "error", f"Error during cleanup: {e}"))
