# selenium 4
import time
import shutil
import sys
import os
import socket
import random
import traceback
import builtins
import threading


from burnBot_imports import *
from burnBot_config import CONFIG
from burnBot_client_log import client_log_line

INTERNET_HOLD_INITIAL_DELAY_SECONDS = 30
INTERNET_HOLD_MAX_DELAY_SECONDS = 300
INTERNET_RESTORE_GRACE_SECONDS = 12
_internet_state_lock = threading.Lock()
_internet_restored_event = threading.Event()
_internet_restored_event.set()
_internet_hold_owner = None
_internet_hold_active = False
_internet_recovery_notice = False
_shutdown_flag = None  # set by burnBot.py via register_shutdown_flag()


def register_shutdown_flag(event):
    """Let the offline-hold loop below notice a user-requested shutdown.

    wait_for_internet_restore() used to loop `while True` with no way to
    hear about Ctrl-C/stop — if the network was down when shutdown was
    requested, it kept retrying (up to 5-minute backoffs) instead of
    letting the owning thread join promptly.
    """
    global _shutdown_flag
    _shutdown_flag = event


def _shutdown_requested():
    return _shutdown_flag is not None and _shutdown_flag.is_set()


def _probe_internet_connection(host="8.8.8.8", port=53, timeout=8):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect((host, port))


def is_internet_hold_active():
    with _internet_state_lock:
        return _internet_hold_active


def consume_connectivity_recovery_notice():
    global _internet_recovery_notice
    with _internet_state_lock:
        notice = _internet_recovery_notice
        _internet_recovery_notice = False
        return notice


def wait_for_internet_restore(host="8.8.8.8", port=53, timeout=8):
    global _internet_hold_owner, _internet_hold_active, _internet_recovery_notice

    current_thread = threading.get_ident()
    current_owner = current_thread
    owner_event = None

    with _internet_state_lock:
        if not _internet_hold_active:
            _internet_hold_active = True
            _internet_hold_owner = current_thread
            _internet_recovery_notice = False
            _internet_restored_event.clear()
            builtins.print(client_log_line(None, "internet", "Offline hold active - checking every 30s up to 5 min."))
            builtins.print(client_log_line(None, "internet", "No new work will resume until connectivity is restored."))
        elif _internet_hold_owner != current_thread:
            owner_event = _internet_restored_event
            current_owner = _internet_hold_owner
        else:
            owner_event = None
            current_owner = current_thread

    if current_owner != current_thread:
        while not owner_event.wait(timeout=1):
            if _shutdown_requested():
                return False
        return True

    retry_delay = INTERNET_HOLD_INITIAL_DELAY_SECONDS
    while not _shutdown_requested():
        delay("offline hold - next connection check in ", retry_delay, retry_delay, "", scope="internet")

        try:
            _probe_internet_connection(host=host, port=port, timeout=timeout)
            builtins.print(client_log_line(None, "internet", f"Connection restored - waiting {INTERNET_RESTORE_GRACE_SECONDS}s for network to settle..."))
            time.sleep(INTERNET_RESTORE_GRACE_SECONDS)
            with _internet_state_lock:
                has_internet_connection.fail_count = 0
                _internet_hold_active = False
                _internet_hold_owner = None
                _internet_recovery_notice = True
                _internet_restored_event.set()
            builtins.print(client_log_line(None, "internet", "Connection restored - resuming bot operation."))
            return True
        except socket.error:
            retry_delay = min(retry_delay * 2, INTERNET_HOLD_MAX_DELAY_SECONDS)
            builtins.print(client_log_line(None, "internet", f"Still offline - next check in {retry_delay}s."))

    # Shutdown requested while still offline — release the hold (so any
    # other thread parked on owner_event.wait() above unblocks via its own
    # shutdown check) and let the caller unwind instead of looping forever.
    with _internet_state_lock:
        if _internet_hold_owner == current_thread:
            _internet_hold_active = False
            _internet_hold_owner = None
            _internet_restored_event.set()
    return False

def close_windows(driver):
    """
    Close all other tabs/windows in the current driver session, preserving the main window.
    This only affects windows in the current driver instance, not other Chrome sessions.
    """
    try:
        StartWindow = driver.current_window_handle
        all_windows = driver.window_handles
        
        # Only close windows that are NOT the main window
        extra_windows = [h for h in all_windows if h != StartWindow]
        
        if extra_windows:
            for handle in extra_windows:
                try:
                    driver.switch_to.window(handle)
                    driver.close()
                    time.sleep(random.randint(2, 3))
                except Exception:
                    # Window might already be closed or invalid, continue
                    pass

        # Switch back to main window
        try:
            driver.switch_to.window(StartWindow)
        except Exception:
            # If main window is gone, try to switch to any remaining window
            remaining = driver.window_handles
            if remaining:
                driver.switch_to.window(remaining[0])
    except Exception:
        # Driver connection might be lost, that's okay
        pass

def has_internet_connection(host="8.8.8.8", port=53, timeout=8, max_fails=3):
    if not hasattr(has_internet_connection, "fail_count"):
        has_internet_connection.fail_count = 0
    if is_internet_hold_active():
        return wait_for_internet_restore(host=host, port=port, timeout=timeout)

    for attempt in range(1, max_fails + 1):
        try:
            _probe_internet_connection(host=host, port=port, timeout=timeout)
            has_internet_connection.fail_count = 0
            return True
        except socket.error:
            has_internet_connection.fail_count = attempt

            if attempt >= max_fails:
                builtins.print(client_log_line(None, "internet", f"check failed [{attempt}/{max_fails}]"))
                return wait_for_internet_restore(host=host, port=port, timeout=timeout)

            # Progressive delay (capped): 10s, 20s, 30s ... up to 60s
            delay_seconds = min(attempt * 10, 60)
            delay(f"check failed [{attempt}/{max_fails}] - retry in ", delay_seconds, delay_seconds, "", scope="internet")
            builtins.print("")

    return False

def process_exception(printError, noteError, logError, debugError):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    frame = exc_traceback.tb_frame
    line_number = exc_traceback.tb_lineno
    file_short = os.path.basename(exc_traceback.tb_frame.f_code.co_filename)
    file_long = frame.f_code.co_filename
    full_error = traceback.format_exc()
    errorReturn = ""

    if not has_internet_connection():
        errorReturn += "[ConnectionError] Internet is offline.\n"

    if printError:
        builtins.print(client_log_line(None, "error", f"[{exc_type.__name__}][<{file_short}>|<{line_number}>] {noteError}"))
    if debugError:
        builtins.print(f"\n{full_error}\n")
    if logError:
        errorReturn += f"--[{exc_type.__name__}][<{file_short}>|<{line_number}>][{noteError}]\n"

    return errorReturn

def delay(pre, t1, t2, post, account=None, scope=None):
    tx = random.randint(t1, t2)
    width = shutil.get_terminal_size().columns
    og_mins, og_secs = divmod(tx, 60)
    og_timer = f"{og_mins}:{og_secs:02d}" if tx > 60 else f"{tx:02d}"

    for counter in range(tx + 1):
        mins, secs = divmod(counter, 60)
        timer = f"{mins}:{secs:02d}" if tx > 60 else f"{counter:02d}"
        body = f"{pre}[{timer}/{og_timer}]{post}"
        message = client_log_line(account, scope, body) if scope is not None else body
        builtins.print('\r' + message.ljust(width), end='', flush=True)
        time.sleep(1)

    builtins.print()  # move to new line after delay ends
    
def retry_on_connection_error(func, max_retries=3, *args, **kwargs):
    """
    Retry a function if it fails due to connection error
    
    Args:
        func: Function to retry
        max_retries: Maximum number of retry attempts
        *args, **kwargs: Arguments to pass to the function
    
    Returns:
        Result of the function call, or None if all retries fail
    """
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < max_retries - 1:
                # Check internet and wait before retry
                if not has_internet_connection():
                    builtins.print(client_log_line(None, "internet", f"operation failed, checking internet... (attempt {attempt + 1}/{max_retries})"))
                    # has_internet_connection already waits and retries
                else:
                    # Internet is fine, but operation failed for another reason
                    builtins.print(client_log_line(None, "internet", f"operation failed: {e} (attempt {attempt + 1}/{max_retries})"))
                    time.sleep(3)  # Brief wait before retry
            else:
                # Final attempt failed
                raise e
    return None

def check_schedule(scheduleDays, scheduleStart, scheduleEnd):
    """
    Check if current time/day matches the schedule settings.
    
    Args:
        scheduleDays: String - "daily", "weekdays", or "weekend"
        scheduleStart: String - Start time in "9:00 AM" format
        scheduleEnd: String - End time in "5:00 PM" format
    
    Returns:
        bool: True if current time/day matches schedule, False otherwise
    """
    from datetime import datetime
    
    # If no schedule is set, allow execution (backward compatibility)
    if not scheduleDays and not scheduleStart and not scheduleEnd:
        return True
    
    now = datetime.now()
    current_weekday = now.weekday()  # 0=Monday, 6=Sunday
    current_time = now.strftime("%I:%M %p")  # 12-hour format with AM/PM (e.g., "09:00 AM")
    current_time_24 = now.strftime("%H:%M")  # 24-hour format for comparison
    
    # Check day match
    if scheduleDays:
        scheduleDays_lower = scheduleDays.lower().strip()
        
        if scheduleDays_lower == "daily":
            # All days are allowed
            day_match = True
        elif scheduleDays_lower == "weekdays":
            # Monday (0) through Friday (4)
            day_match = current_weekday < 5
        elif scheduleDays_lower == "weekend":
            # Saturday (5) and Sunday (6)
            day_match = current_weekday >= 5
        elif scheduleDays_lower in ("random 1/3", "random 2/3"):
            # Deterministic daily dice roll — same result for all checks on the same calendar day
            import hashlib, random as _random
            from datetime import date as _date
            seed = int(hashlib.md5(_date.today().isoformat().encode()).hexdigest(), 16) % (2 ** 32)
            rng = _random.Random(seed)
            threshold = 1/3 if scheduleDays_lower == "random 1/3" else 2/3
            day_match = rng.random() < threshold
        else:
            # Unknown day format - allow execution (fail-safe)
            print(f"[schedule]: Warning - Unknown day format: {scheduleDays}, allowing execution")
            day_match = True
        
        if not day_match:
            return False
    
    # Check time range
    if scheduleStart or scheduleEnd:
        try:
            # Parse start time
            start_time_24 = None
            if scheduleStart:
                start_time_str = scheduleStart.strip()
                # Try parsing as "9:00 AM" format
                try:
                    start_dt = datetime.strptime(start_time_str, "%I:%M %p")
                    start_time_24 = start_dt.strftime("%H:%M")
                except ValueError:
                    # Try "9:00AM" (no space)
                    try:
                        start_dt = datetime.strptime(start_time_str, "%I:%M%p")
                        start_time_24 = start_dt.strftime("%H:%M")
                    except ValueError:
                        # Try 24-hour format with seconds (HH:MM:SS)
                        try:
                            start_dt = datetime.strptime(start_time_str, "%H:%M:%S")
                            start_time_24 = start_dt.strftime("%H:%M")
                        except ValueError:
                            # Try 24-hour format without seconds (HH:MM)
                            try:
                                start_dt = datetime.strptime(start_time_str, "%H:%M")
                                start_time_24 = start_dt.strftime("%H:%M")
                            except ValueError:
                                print(f"[schedule]: Warning - Could not parse start time: {scheduleStart}")
            
            # Parse end time
            end_time_24 = None
            if scheduleEnd:
                end_time_str = scheduleEnd.strip()
                # Try parsing as "5:00 PM" format
                try:
                    end_dt = datetime.strptime(end_time_str, "%I:%M %p")
                    end_time_24 = end_dt.strftime("%H:%M")
                except ValueError:
                    # Try "5:00PM" (no space)
                    try:
                        end_dt = datetime.strptime(end_time_str, "%I:%M%p")
                        end_time_24 = end_dt.strftime("%H:%M")
                    except ValueError:
                        # Try 24-hour format with seconds (HH:MM:SS)
                        try:
                            end_dt = datetime.strptime(end_time_str, "%H:%M:%S")
                            end_time_24 = end_dt.strftime("%H:%M")
                        except ValueError:
                            # Try 24-hour format without seconds (HH:MM)
                            try:
                                end_dt = datetime.strptime(end_time_str, "%H:%M")
                                end_time_24 = end_dt.strftime("%H:%M")
                            except ValueError:
                                print(f"[schedule]: Warning - Could not parse end time: {scheduleEnd}")
            
            # Check if current time is within range
            if start_time_24 and end_time_24:
                if start_time_24 <= end_time_24:
                    # Normal range (e.g., 9:00 AM to 5:00 PM)
                    if not (start_time_24 <= current_time_24 <= end_time_24):
                        return False
                else:
                    # Overnight range (e.g., 10:00 PM to 6:00 AM)
                    if not (current_time_24 >= start_time_24 or current_time_24 <= end_time_24):
                        return False
            elif start_time_24:
                # Only start time specified
                if current_time_24 < start_time_24:
                    return False
            elif end_time_24:
                # Only end time specified
                if current_time_24 > end_time_24:
                    return False
                    
        except Exception as e:
            print(f"[schedule]: Error parsing time - {e}")
            # If time parsing fails, allow execution (fail-safe)
            return True

    return True


def extract_username_from_href(href):
    """Extract an Instagram username from a profile-style href."""
    if not href:
        return ""

    cleaned_href = href.strip()
    if "instagram.com" in cleaned_href:
        cleaned_href = cleaned_href.split("instagram.com", 1)[1]

    cleaned_href = cleaned_href.split("?", 1)[0].split("#", 1)[0]
    if not cleaned_href.startswith("/"):
        cleaned_href = f"/{cleaned_href}"

    path_parts = [part for part in cleaned_href.split("/") if part]
    if len(path_parts) != 1:
        return ""

    username = path_parts[0].strip()
    if not username:
        return ""

    reserved_paths = {
        "about",
        "accounts",
        "ads",
        "api",
        "challenge",
        "developer",
        "direct",
        "explore",
        "graphql",
        "p",
        "reel",
        "reels",
        "stories",
        "tags",
    }
    if username.lower() in reserved_paths:
        return ""

    allowed_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._"
    if any(ch not in allowed_chars for ch in username):
        return ""

    return username


def get_post_author_username(article):
    """Find the post author's username from profile links inside a post or
    feed article. Returns "unknown" when no profile-style link parses — on
    the home feed that means the article is an ad or an unrecognized layout
    (every legitimate post has a header profile link)."""
    candidate_locators = [
        ".//header//a[@href]",
        ".//a[@href]",
    ]

    for locator in candidate_locators:
        try:
            anchor_elements = article.find_elements(By.XPATH, locator)
        except Exception:
            continue

        for anchor in anchor_elements:
            try:
                href = anchor.get_attribute("href") or ""
                username = extract_username_from_href(href)
                if not username:
                    continue

                anchor_text = " ".join((anchor.text or "").split())
                if locator == ".//header//a[@href]":
                    return username

                if anchor_text and username.lower() in anchor_text.lower():
                    return username

                has_profile_image = len(anchor.find_elements(By.XPATH, ".//img")) > 0
                if has_profile_image:
                    return username
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

    # Alt-text fallback: some feed article layouts render the header profile
    # link as a <span role="link"> with no <a href> anywhere in the article,
    # but the avatar <img> alt still names the author
    # ("<username>'s profile picture"). Header-scoped first so a liked-by or
    # comment avatar can't win over the author's.
    for img_locator in (
        ".//header//img[contains(@alt, \"'s profile picture\")]",
        ".//img[contains(@alt, \"'s profile picture\")]",
    ):
        try:
            images = article.find_elements(By.XPATH, img_locator)
        except Exception:
            continue
        for image in images:
            try:
                alt_text = image.get_attribute("alt") or ""
                candidate = alt_text.split("'s profile picture", 1)[0].strip()
                username = extract_username_from_href(candidate)
                if username:
                    return username
            except StaleElementReferenceException:
                continue
            except Exception:
                continue

    return "unknown"

