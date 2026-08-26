import time
from burnBot_human import hsleep, htype, hclick, hhover, hscroll
import random
import builtins as _builtins
from datetime import date
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
import burnBot_status as status_store
from burnBot_client_log import client_log_line
from burnBot_followFilter import load_known_handles, screen_candidate

_p = _builtins.print  # set per-call by do_follow_suggested; safe because sessions run sequentially


def _extract_username_from_profile_href(href: str) -> str | None:
    """
    Extract an Instagram username from a profile URL.

    Accepts absolute or relative hrefs like:
    - https://www.instagram.com/someuser/
    - /someuser/

    Returns username or None if not a profile link.
    """
    if not href:
        return None

    try:
        # Normalize relative href to a parseable URL
        if href.startswith("/"):
            href = f"https://www.instagram.com{href}"

        path = urlparse(href).path or ""
        # Expected profile path: "/<username>/"
        parts = [p for p in path.split("/") if p]
        if len(parts) != 1:
            return None

        username = parts[0].strip()
        if not username:
            return None

        # Exclude non-profile routes
        reserved = {
            "accounts", "explore", "reels", "direct", "p", "tv", "stories",
            "about", "developer", "legal", "privacy", "terms",
        }
        if username.lower() in reserved:
            return None

        return username
    except Exception:
        return None


def _find_home_follow_candidates(driver, max_candidates: int = 50, root=None):
    """
    Find follow candidates on Instagram home page by locating Follow buttons and
    extracting the associated username from nearby profile links.

    root: optional WebElement to search within (e.g. an open likers dialog)
          instead of the whole document.

    Returns: list[tuple[str, WebElement, WebElement|None]]
      - (username, follow_button_element, username_anchor_element_or_None)
    """
    # Instagram UI varies: buttons can be <button> or <div role="button">
    prefix = ".//" if root is not None else "//"
    follow_buttons = (root if root is not None else driver).find_elements(
        By.XPATH,
        (
            f"{prefix}button[normalize-space()='Follow' or normalize-space()='Follow back']"
            f" | {prefix}*[@role='button'][normalize-space()='Follow' or normalize-space()='Follow back']"
        ),
    )

    candidates = []
    seen = set()

    for btn in follow_buttons:
        if len(candidates) >= max_candidates:
            break

        try:
            # Find nearest ancestor container that has at least one link
            container = btn.find_element(By.XPATH, "./ancestor::div[.//a[@href]][1]")
            anchors = container.find_elements(By.XPATH, ".//a[@href]")

            username = None
            username_anchor = None
            for a in anchors:
                href = a.get_attribute("href") or ""
                u = _extract_username_from_profile_href(href)
                if u:
                    username = u
                    username_anchor = a
                    break

            if not username or username in seen:
                continue

            seen.add(username)
            candidates.append((username, btn, username_anchor))
        except (NoSuchElementException, StaleElementReferenceException):
            continue
        except Exception:
            continue

    return candidates


def _find_explore_people_candidates(driver, max_candidates: int = 50):
    """
    Find follow candidates on Instagram's explore/people page.

    Implementation intentionally reuses the same resilient heuristic as home:
    locate Follow/Follow back buttons and infer the username from nearby profile links.
    """
    return _find_home_follow_candidates(driver, max_candidates=max_candidates)


def _create_follow_entry(apiClient, account_id, user_name: str, source: str, status: str, follow_date):
    """
    Create a follow target entry via the API.
    """
    try:
        apiClient.create_follow_target(
            account_id, user_name, source=source,
            status=status, follow_date=follow_date
        )
    except Exception:
        # Logging failures shouldn't crash the bot action
        pass


def do_follow_suggested(driver, account, target_count, apiClient, account_id, _print=None, log_scope=None, action_label=None):
    global _p
    _p = _print if _print is not None else _builtins.print
    _scope = log_scope or "follow-suggested"
    _lbl = f"{action_label}-" if action_label else ""
    _done_lbl = (action_label[0].upper() + action_label[1:]) if action_label else "Done"
    """
    Follow suggested accounts from Instagram's explore/people page

    Args:
        driver: Selenium WebDriver instance
        account: Account username
        target_count: Number of accounts to follow
        apiClient: ApiClient instance for API access
        account_id: Account UUID

    Returns:
        tuple: (followed_count, error_log_string, warning_log_string)
    """
    module_errors_log = ""
    module_warnings_log = ""
    followed_count = 0

    try:
        today = date.today()
        follow_date = today

        # Previously followed / skipped handles + universal ignore list (case-insensitive)
        database_names = load_known_handles(apiClient, account_id, account, _scope, _lbl, _p)

        # ------------------------------------------------------------------
        # Phase A (primary): Explore People
        # ------------------------------------------------------------------
        try:
            driver.get("https://www.instagram.com/explore/people/")
            WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
            hsleep(4, 6)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e).split("\n")[0]
            module_errors_log += f"{error_type}: explore navigation failed: {error_msg}\n"

        explore_candidates = []
        if followed_count < target_count:
            # Failure signal: no candidates after wait + 1–2 scroll rescans
            for attempt in range(3):
                if followed_count >= target_count:
                    break

                remaining = max(1, target_count - followed_count)
                explore_candidates = _find_explore_people_candidates(driver, max_candidates=max(50, remaining * 4))
                if explore_candidates:
                    break

                # Scroll and rescan
                try:
                    hscroll(driver, 900)
                except Exception:
                    pass
                hsleep(2, 4)

        if explore_candidates:
            _p(client_log_line(account, _scope, f"{_lbl}explore found {len(explore_candidates)} candidate(s)"))

            for user_name, follow_button, user_name_anchor in explore_candidates:
                if followed_count >= target_count:
                    break

                try:
                    if user_name in database_names:
                        _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{user_name}] - [in database]"))
                        continue

                    # Hover + hover-card filters (private / too big / low ratio / no posts)
                    if not screen_candidate(
                        driver, apiClient, account_id, account, _scope, _lbl, "suggested[accounts]",
                        user_name, user_name_anchor, database_names, follow_date, _p,
                    ):
                        continue

                    # Click follow
                    click_success = False
                    try:
                        hclick(driver, follow_button)
                        click_success = True
                    except Exception:
                        try:
                            follow_button.click()
                            click_success = True
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", follow_button)
                                click_success = True
                            except Exception:
                                click_success = False

                    if not click_success:
                        continue

                    followed_count += 1
                    database_names.add(user_name)

                    _create_follow_entry(
                        apiClient, account_id,
                        user_name=user_name,
                        source="suggested[accounts]",
                        status="following",
                        follow_date=follow_date,
                    )
                    _p(client_log_line(account, _scope, f"{_lbl}[{followed_count:02d}/{target_count:02d}] - [{user_name}]"))
                    hsleep(10, 20)

                except StaleElementReferenceException:
                    continue
                except Exception as e:
                    error_type = type(e).__name__
                    error_msg = str(e).split("\n")[0]
                    _p(client_log_line(account, _scope, f"{_lbl}error {error_type}: {error_msg[:80]}"))
                    module_errors_log += f"{error_type}: {error_msg}\n"
                    continue
        else:
            if followed_count < target_count:
                _p(client_log_line(account, _scope, f"{_lbl}explore returned no users, falling back to home"))

        # ------------------------------------------------------------------
        # Phase B (fallback/top-up): Home page Suggested for you
        # ------------------------------------------------------------------
        home_cycles = 0
        max_home_cycles = 2  # one pass + one reload if partial progress was made

        while followed_count < target_count and home_cycles < max_home_cycles:
            if status_store.is_bot_paused():
                return followed_count, module_errors_log, module_warnings_log
            home_cycles += 1
            start_count = followed_count

            driver.get("https://www.instagram.com/")
            WebDriverWait(driver, 15).until(lambda d: d.execute_script('return document.readyState') == 'complete')
            hsleep(4, 6)

            # ------------------------------------------------------------------
            # Primary: existing selector logic (keep as-is)
            # ------------------------------------------------------------------
            user_boxes = driver.find_elements(
                By.XPATH,
                "//div[@data-visualcompletion='loading-state']//ancestor::div[contains(@class, 'x1qnrgzn')]",
            )

            if user_boxes and len(user_boxes) > 0:
                _p(client_log_line(account, _scope, f"{_lbl}found {len(user_boxes)} suggested user(s)"))

                for box_index, user_box in enumerate(user_boxes):
                    if status_store.is_bot_paused() or followed_count >= target_count:
                        break

                    try:
                        # Get username and follow button status
                        try:
                            user_name_element = user_box.find_element(By.CLASS_NAME, "_aad7")
                            user_status_element = user_box.find_element(By.CLASS_NAME, "_aad6")
                            user_name = user_name_element.text
                            user_status = user_status_element.text

                            if not user_name:
                                continue

                        except Exception:
                            continue

                        # Check if already in database
                        if user_name in database_names:
                            _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{user_name}] - [in database]"))
                            continue

                        # Check if already following
                        if user_status != "Follow":
                            _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{user_name}] - [{user_status.lower()}]"))
                            continue

                        # Hover + hover-card filters (private / too big / low ratio / no posts)
                        if not screen_candidate(
                            driver, apiClient, account_id, account, _scope, _lbl, "suggested[accounts]",
                            user_name, user_name_element, database_names, follow_date, _p,
                        ):
                            continue

                        # Check for stale element
                        if not user_name_element.text:
                            continue

                        # Follow the account
                        followed_count += 1

                        hclick(driver, user_status_element)

                        # Log followed account via API
                        _create_follow_entry(
                            apiClient, account_id,
                            user_name=user_name,
                            source="suggested[accounts]",
                            status="following",
                            follow_date=follow_date,
                        )
                        database_names.add(user_name)

                        _p(client_log_line(account, _scope, f"{_lbl}[{followed_count:02d}/{target_count:02d}] - [{user_name}]"))

                        # Delay between follows
                        hsleep(10, 20)

                    except StaleElementReferenceException:
                        continue

                    except Exception as e:
                        error_type = type(e).__name__
                        error_msg = str(e).split("\n")[0]
                        _p(client_log_line(account, _scope, f"{_lbl}error {error_type}: {error_msg[:80]}"))
                        module_errors_log += f"{error_type}: {error_msg}\n"
                        continue

            else:
                # ------------------------------------------------------------------
                # Fallback: more resilient candidate finder (only if primary found none)
                # ------------------------------------------------------------------
                remaining = max(1, target_count - followed_count)
                candidates = _find_home_follow_candidates(driver, max_candidates=max(50, remaining * 4))

                if candidates:
                    _p(client_log_line(account, _scope, f"{_lbl}fallback found {len(candidates)} follow candidate(s)"))
                else:
                    msg = "[error] no suggested users found"
                    _p(client_log_line(account, _scope, f"{_lbl}{msg}"))
                    module_errors_log += f"follow[suggested]: {msg}\n"
                    return followed_count, module_errors_log, module_warnings_log

                for user_name, follow_button, user_name_anchor in candidates:
                    if followed_count >= target_count:
                        break

                    try:
                        if user_name in database_names:
                            _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{user_name}] - [in database]"))
                            continue

                        # Hover + hover-card filters (private / too big / low ratio / no posts)
                        if not screen_candidate(
                            driver, apiClient, account_id, account, _scope, _lbl, "suggested[accounts]",
                            user_name, user_name_anchor, database_names, follow_date, _p,
                        ):
                            continue

                        followed_count += 1

                        try:
                            hclick(driver, follow_button)
                        except Exception:
                            try:
                                follow_button.click()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", follow_button)
                                except Exception:
                                    followed_count -= 1
                                    continue

                        _create_follow_entry(
                            apiClient, account_id,
                            user_name=user_name,
                            source="suggested[accounts]",
                            status="following",
                            follow_date=follow_date,
                        )
                        database_names.add(user_name)
                        _p(client_log_line(account, _scope, f"{_lbl}[{followed_count:02d}/{target_count:02d}] - [{user_name}]"))
                        hsleep(10, 20)

                    except StaleElementReferenceException:
                        continue

                    except Exception as e:
                        error_type = type(e).__name__
                        error_msg = str(e).split("\n")[0]
                        _p(client_log_line(account, _scope, f"{_lbl}error {error_type}: {error_msg[:80]}"))
                        module_errors_log += f"{error_type}: {error_msg}\n"
                        continue

            # Loop will reload home and continue if still under target
        
        if followed_count < target_count:
            if followed_count == 0:
                msg = "[error] no suggested users found"
                _p(client_log_line(account, _scope, f"{_lbl}Incomplete[{followed_count}/{target_count}]"))
                module_errors_log += f"follow[suggested]: {msg} ({followed_count}/{target_count})\n"
            else:
                msg = "[warning] limited suggested users found"
                _p(client_log_line(account, _scope, f"{_lbl}Incomplete[{followed_count}/{target_count}]"))
                module_warnings_log += f"follow[suggested]: {msg} ({followed_count}/{target_count})\n"
        else:
            _p(client_log_line(account, _scope, f"{_done_lbl}-Completed[{followed_count}/{target_count}]"))

    except Exception as e:
        # Simplified error output
        error_type = type(e).__name__
        error_msg = str(e).split('\n')[0]
        _p(client_log_line(account, _scope, f"{_lbl}FATAL {error_type}: {error_msg[:100]}"))
        module_errors_log += f"{error_type}: {error_msg}\n"

    return followed_count, module_errors_log, module_warnings_log


