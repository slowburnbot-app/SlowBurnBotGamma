import time
from burnBot_human import hsleep, htype, hclick, hhover, hscroll
import random
import builtins as _builtins
from datetime import date
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from burnBot_utils import process_exception
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line
from burnBot_followSuggested import _find_home_follow_candidates
from burnBot_followFilter import load_known_handles, screen_candidate
from burnBot_seeds import load_seed_pool, pick_seed, finish_seed_use, maybe_discover_seeds
import burnBot_status as status_store

_p = _builtins.print  # set per-call by do_follow_group; safe because sessions run sequentially

# Target-saturation warning: fire only on a meaningful sample so a small dialog
# that happens to open on a run of already-followeds doesn't cry wolf.
_SATURATION_MIN_ENTRIES = 40
_SATURATION_WARN_RATIO = 0.80

# A profile page carries a "Suggested for you" carousel of accounts related to
# that profile. On profiles the viewing account does NOT follow it renders
# expanded below the header; on followed profiles it is collapsed behind the
# "Similar accounts" chevron in the header (verified 2026-08-25). The <svg> is
# SVG-namespaced, so a bare `//svg[...]` XPath never matches — locate it by
# CSS, then walk up to the nearest role=button ancestor (two levels up).
_SIMILAR_CHEVRON_CSS = "svg[aria-label='Similar accounts']"
_SIMILAR_NEXT_XPATH = "//button[@aria-label='Next'] | //*[@role='button'][@aria-label='Next']"


def _saturation_pct(skip_already, skip_private, followed_count):
    """% of processed entries that could never produce a follow from this pool
    (already known); None when nothing was processed."""
    processed = skip_already + skip_private + followed_count
    if processed <= 0:
        return None
    return round(skip_already / processed * 100)


def _saturation_warning(account, scope, lbl, action_label, target_account, skip_already, skip_private, followed_count):
    """Saturation check: how much of the target's pool was un-followable.
    Prints the warning/debug line and returns the text to append to the module warnings log."""
    processed = skip_already + skip_private + followed_count
    pct = _saturation_pct(skip_already, skip_private, followed_count)
    if pct is None:
        return ""
    saturation = pct / 100
    if processed >= _SATURATION_MIN_ENTRIES and saturation >= _SATURATION_WARN_RATIO:
        _p(client_log_line(account, scope, f"{lbl}Warning: [{target_account}] {pct}% saturated ({skip_already} of {processed} entries already followed) - consider rotating target accounts"))
        return f"{action_label or 'follow[group]'}: [{target_account}] {pct}% saturated ({skip_already}/{processed} already followed) - rotate targets\n"
    debug_line(client_log_line(account, scope, f"{lbl}debug target [{target_account}] saturation {pct}% ({skip_already} of {processed} entries already followed)"))
    return ""


def _find_similar_chevron(driver):
    """Return the clickable ancestor of the Similar-accounts chevron, or None."""
    try:
        svg = driver.find_element(By.CSS_SELECTOR, _SIMILAR_CHEVRON_CSS)
        return svg.find_element(By.XPATH, "./ancestor::*[@role='button' or self::button][1]")
    except Exception:
        return None


def _similar_panel_expanded(driver, target_account):
    """True when the suggestions carousel is on the page: a Next arrow plus at least one
    Follow candidate other than the target's own header button."""
    if not driver.find_elements(By.XPATH, _SIMILAR_NEXT_XPATH):
        return False
    return any(u.lower() != target_account.lower() for u, _b, _a in _find_home_follow_candidates(driver, max_candidates=5))


def _open_similar_panel(driver, account, target_account, scope, lbl):
    """Make sure the target's suggestions carousel is expanded. Returns (opened: bool, warning: str)."""
    if _similar_panel_expanded(driver, target_account):
        _p(client_log_line(account, scope, f"{lbl}Suggested for you strip already expanded for {target_account}"))
        return True, ""

    chevron = _find_similar_chevron(driver)
    if chevron is None:
        msg = f"[{target_account}] no Similar accounts panel found - skipping"
        _p(client_log_line(account, scope, f"{lbl}Warning: {msg}"))
        return False, f"follow[similar]: {msg}\n"

    try:
        actions = ActionChains(driver)
        actions.move_to_element(chevron)
        actions.perform()
        hsleep(1, 2)
        actions = ActionChains(driver)
        actions.click(chevron)
        actions.perform()
        hsleep(3, 5)
    except Exception as e:
        msg = f"[{target_account}] failed to open Similar accounts panel: {str(e).splitlines()[0][:80]}"
        _p(client_log_line(account, scope, f"{lbl}Warning: {msg}"))
        return False, f"follow[similar]: {msg}\n"

    _p(client_log_line(account, scope, f"{lbl}Similar accounts panel opened for {target_account}"))
    return True, ""


def _harvest_similar_accounts(driver, account, target_count, apiClient, account_id, target_account, database_names, follow_date, scope, lbl):
    """Follow accounts from the (already opened) Similar-accounts carousel, paging with its
    Next arrow. Returns (followed_count, skip_already, skip_private, errors_log)."""
    action_type = "similar"
    followed_count = 0
    skip_already = 0
    skip_private = 0
    module_errors_log = ""
    seen = set()
    stall_pages = 0
    max_stall_pages = 3  # consecutive pages with no unseen handles before treating as end of carousel

    def _advance():
        try:
            nxt = driver.find_element(By.XPATH, _SIMILAR_NEXT_XPATH)
        except Exception:
            return False
        try:
            driver.execute_script("arguments[0].click();", nxt)
            hsleep(2, 4)
            return True
        except Exception:
            return False

    while followed_count < target_count:
        if status_store.is_bot_paused():
            break

        # The target's own header Follow button resolves to the target's handle under the
        # candidate heuristic — never treat it as a suggestion.
        candidates = [
            c for c in _find_home_follow_candidates(driver, max_candidates=60)
            if c[0] not in seen and c[0].lower() != target_account.lower()
        ]
        if not candidates:
            stall_pages += 1
            if stall_pages >= max_stall_pages or not _advance():
                _p(client_log_line(account, scope, f"{lbl}Warning: reached end of Similar accounts [{followed_count}/{target_count}]"))
                break
            continue
        stall_pages = 0

        for user_name, follow_button, user_name_anchor in candidates:
            if status_store.is_bot_paused() or followed_count >= target_count:
                break
            seen.add(user_name)

            try:
                if user_name in database_names:
                    if database_names.is_skipped(user_name):
                        skip_private += 1   # filtered on an earlier run — a config choice, not exhaustion
                    else:
                        skip_already += 1
                    _p(client_log_line(account, scope, f"{target_account}[{action_type}]-[-skip] - [{user_name}] - [in database]"))
                    hsleep(1, 1)
                    continue

                # Hover + hover-card filters (private / too big / low ratio / no posts)
                if not screen_candidate(
                    driver, apiClient, account_id, account, scope, f"{target_account}[{action_type}]-",
                    f"{target_account}[{action_type}]", user_name, user_name_anchor,
                    database_names, follow_date, _p,
                ):
                    skip_private += 1
                    continue

                try:
                    hclick(driver, follow_button)
                except Exception:
                    driver.execute_script("arguments[0].click();", follow_button)

                followed_count += 1
                database_names.add(user_name)
                try:
                    apiClient.create_follow_target(
                        account_id, user_name, source=f"{target_account}[{action_type}]",
                        status="following", follow_date=follow_date,
                    )
                except Exception:
                    pass
                _p(client_log_line(account, scope, f"{target_account}[{action_type}]-[{followed_count:02d}/{target_count:02d}] - [{user_name}]"))
                hsleep(10, 20)

            except StaleElementReferenceException:
                continue
            except Exception as e:
                error_msg = process_exception(True, f"follow user failed: {e}", True, False)
                module_errors_log += error_msg
                continue

        if followed_count < target_count and not _advance():
            _p(client_log_line(account, scope, f"{lbl}Warning: reached end of Similar accounts [{followed_count}/{target_count}]"))
            break

    return followed_count, skip_already, skip_private, module_errors_log


def do_follow_group(driver, account, target_count, apiClient, account_id, group_type, target_accounts, group_mode="manual", _print=None, log_scope=None, action_label=None):
    global _p
    _p = _print if _print is not None else _builtins.print
    _scope = log_scope or "follow-group"
    _lbl = f"{action_label}-" if action_label else ""
    _done_lbl = (action_label[0].upper() + action_label[1:]) if action_label else "Done"
    """
    Follow accounts from a target account's followers or following list

    Args:
        driver: Selenium WebDriver instance
        account: Account username (self account)
        target_count: Number of accounts to follow
        apiClient: ApiClient instance for API access
        account_id: Account UUID
        group_type: "followers[group]" or "following[group]"
        target_accounts: Comma-separated list of target account usernames (the account group)
        group_mode: "manual" — pick from target_accounts at random (original behaviour)
                    "pool"   — pick from the follow_seeds pool (weighted, self-managing)

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

        # Target pool: the manual account-group list, or the self-managing seed pool
        seed_pool, account_rate, all_seeds, group_handles = load_seed_pool(apiClient, account_id, target_accounts, group_mode)
        if group_mode == "pool":
            maybe_discover_seeds(driver, apiClient, account_id, account, seed_pool, all_seeds, group_handles, _scope, _lbl, _p)
        seed = pick_seed(seed_pool)
        if seed is None:
            _p(client_log_line(account, _scope, f"{_lbl}ERROR: No target accounts provided"))
            return 0, "No target accounts provided", ""

        target_account = seed["handle"]
        _p(client_log_line(account, _scope, f"{_lbl}selected target: {target_account}"))
        
        # Navigate to target account page
        target_account_page = f"https://www.instagram.com/{target_account}/"
        driver.get(target_account_page)
        WebDriverWait(driver, 10).until(lambda d: d.execute_script('return document.readyState') == 'complete')
        hsleep(3, 5)
        
        # Check if target account exists
        if driver.find_elements(By.XPATH, "//*[contains(text(), \"Sorry, this page isn't available.\")]"):
            error_msg = f"Target account '{target_account}' not found"
            _p(client_log_line(account, _scope, f"{_lbl}ERROR: {error_msg}"))
            finish_seed_use(apiClient, seed, None, account_rate, account, _scope, _lbl, _p, retire_reason="not found")
            return 0, error_msg, ""
        
        # Similar accounts: no followers/following dialog — a carousel off the profile header.
        if group_type == "account list [similar]":
            opened, _warn = _open_similar_panel(driver, account, target_account, _scope, _lbl)
            module_warnings_log += _warn
            if not opened:
                return 0, module_errors_log, module_warnings_log
            followed_count, skip_already, skip_private, _errs = _harvest_similar_accounts(
                driver, account, target_count, apiClient, account_id, target_account,
                database_names, follow_date, _scope, _lbl,
            )
            module_errors_log += _errs
            _p(client_log_line(account, _scope, f"{_done_lbl}-Completed[{followed_count}/{target_count}]"))
            module_warnings_log += _saturation_warning(
                account, _scope, _lbl, action_label, target_account, skip_already, skip_private, followed_count,
            )
            finish_seed_use(
                apiClient, seed, _saturation_pct(skip_already, skip_private, followed_count),
                account_rate, account, _scope, _lbl, _p,
            )
            return followed_count, module_errors_log, module_warnings_log

        # Determine which link to click (followers or following)
        if group_type in ("followers[group]", "account list [followers]"):
            link_text = 'followers'
            action_type = "followers"
        elif group_type in ("following[group]", "account list [following]"):
            link_text = 'following'
            action_type = "following"
        else:
            _p(client_log_line(account, _scope, f"{_lbl}ERROR: Invalid group type"))
            return 0, f"Invalid group type: {group_type}", ""
        
        # Find and click the followers/following link — try multiple strategies since
        # Instagram changes whether these are <a href=…>, <button>, or role="link" elements.
        _strategies = [
            # Instagram now uses <a href="#" role="link"> where text is split across child spans
            # and a text node " following"/" followers" — match by role + contains on full text.
            ("role-link-text",  By.XPATH, f"//a[@role='link'][contains(., ' {link_text}')]"),
            ("href-slash",      By.XPATH, f"//a[contains(@href, '/{link_text}/')]"),
            ("href-noslash",    By.XPATH, f"//a[contains(@href, '/{link_text}')]"),
            ("button-text",     By.XPATH, f"//button[.//*[normalize-space()='{link_text}']]"),
            ("role-link-exact", By.XPATH, f"//*[@role='link'][.//*[normalize-space()='{link_text}']]"),
            ("header-text",     By.XPATH, f"//header//*[normalize-space()='{link_text}']"),
        ]
        target_link = None
        matched_strategy = None
        for _strat_name, _by, _sel in _strategies:
            try:
                target_link = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((_by, _sel))
                )
                matched_strategy = _strat_name
                break
            except Exception:
                continue

        if target_link is None:
            error_msg = f"Failed to open {link_text} dialog: element not found with any selector strategy"
            _p(client_log_line(account, _scope, f"{_lbl}ERROR: {error_msg}"))
            return 0, error_msg, ""

        _p(client_log_line(account, _scope, f"{_lbl}located {link_text} via [{matched_strategy}]"))
        try:
            actions = ActionChains(driver)
            actions.move_to_element(target_link)
            actions.perform()
            hsleep(2, 4)

            actions = ActionChains(driver)
            actions.click(target_link)
            actions.perform()
            hsleep(2, 4)
        except Exception as e:
            error_msg = f"Failed to open {link_text} dialog: {e}"
            _p(client_log_line(account, _scope, f"{_lbl}ERROR: {error_msg}"))
            return 0, error_msg, ""
        
        _p(client_log_line(account, _scope, f"{_lbl}dialog opened for {target_account}"))
        
        # Main follow loop
        user_boxes_done = []
        stall_scrolls = 0
        max_stall_scrolls = 5  # consecutive no-new-boxes scrolls before treating as end of list
        scan_entries_seen = 0
        skip_already = 0   # entries that can never produce a follow from this pool
        skip_private = 0   # config choice, not pool exhaustion — excluded from saturation
        scan_heartbeat_at = time.time() + 60

        def _scan_heartbeat():
            # Periodic progress line so long dialog scans (private-heavy lists,
            # silent skips) are diagnosable from the run log instead of going dark.
            nonlocal scan_heartbeat_at
            if time.time() >= scan_heartbeat_at:
                _p(client_log_line(account, _scope, f"{_lbl}scanning dialog… {scan_entries_seen} entries seen, {followed_count}/{target_count} followed"))
                scan_heartbeat_at = time.time() + 60

        while followed_count < target_count:
            if status_store.is_bot_paused():
                finish_seed_use(
                    apiClient, seed, _saturation_pct(skip_already, skip_private, followed_count),
                    account_rate, account, _scope, _lbl, _p,
                )
                return followed_count, module_errors_log, module_warnings_log
            _scan_heartbeat()
            try:
                # Find all user boxes in the dialog
                user_boxes_found = driver.find_elements(By.CLASS_NAME, "xozqiw3")
                user_boxes_new = [item for item in user_boxes_found if item not in user_boxes_done]

                if not user_boxes_new:
                    stall_scrolls += 1
                    if stall_scrolls >= max_stall_scrolls:
                        _p(client_log_line(account, _scope, f"{_lbl}Warning: reached end of list [{followed_count}/{target_count}]"))
                        break
                    # No new boxes, try scrolling
                    try:
                        window = driver.find_element(By.CLASS_NAME, 'xz65tgg')
                        window.send_keys(Keys.PAGE_DOWN)
                        hsleep(2, 4)
                        continue
                    except Exception:
                        # Can't scroll anymore, we've reached the end
                        _p(client_log_line(account, _scope, f"{_lbl}Warning: reached end of list [{followed_count}/{target_count}]"))
                        break
                else:
                    stall_scrolls = 0
                    scan_entries_seen += len(user_boxes_new)

            except Exception as e:
                # Error loading user boxes, try scrolling
                try:
                    window = driver.find_element(By.CLASS_NAME, 'xz65tgg')
                    window.send_keys(Keys.PAGE_DOWN)
                    hsleep(1, 3)
                    continue
                except Exception:
                    break
            
            # Process each new user box
            for user_box in user_boxes_new:
                if status_store.is_bot_paused() or followed_count >= target_count:
                    break
                _scan_heartbeat()

                try:
                    user_name_element = user_box.find_element(By.CLASS_NAME, "_aad7")
                    user_status_element = user_box.find_element(By.CLASS_NAME, "_aad6")
                    user_name = user_name_element.text
                    user_status = user_status_element.text
                except Exception:
                    # Skip if we can't get username/status
                    continue
                
                try:
                    # Check if already in database
                    if user_name in database_names:
                        if database_names.is_skipped(user_name):
                            skip_private += 1   # filtered on an earlier run — a config choice, not exhaustion
                        else:
                            skip_already += 1
                        _p(client_log_line(account, _scope, f"{target_account}[{action_type}]-[-skip] - [{user_name}] - [in database]"))
                        hsleep(1, 1)
                        continue

                    # Check if already following
                    if user_status != "Follow":
                        skip_already += 1
                        _p(client_log_line(account, _scope, f"{target_account}[{action_type}]-[-skip] - [{user_name}] - [{user_status.lower()}]"))
                        hsleep(1, 1)
                        continue
                    
                    # Hover + hover-card filters (private / too big / low ratio / no posts)
                    target_source = f"{target_account}[{action_type}]"
                    if not screen_candidate(
                        driver, apiClient, account_id, account, _scope, f"{target_source}-",
                        target_source, user_name, user_name_element,
                        database_names, follow_date, _p,
                    ):
                        skip_private += 1
                        continue

                    # Check for stale element
                    if not user_name_element.text:
                        continue

                    # Follow the account
                    followed_count += 1

                    hclick(driver, user_status_element)

                    # Log followed account via API
                    try:
                        apiClient.create_follow_target(
                            account_id, user_name, source=target_source,
                            status="following", follow_date=follow_date
                        )
                    except Exception:
                        pass
                    database_names.add(user_name)

                    _p(client_log_line(account, _scope, f"{target_source}-[{followed_count:02d}/{target_count:02d}] - [{user_name}]"))

                    # Delay between follows
                    hsleep(10, 20)
                
                except StaleElementReferenceException:
                    continue
                
                except Exception as e:
                    error_msg = process_exception(True, f"follow user failed: {e}", True, False)
                    module_errors_log += error_msg
                    continue
            
            # Mark these boxes as done
            user_boxes_done.extend(user_boxes_found)
            
            # Scroll down to load more users
            if followed_count < target_count:
                try:
                    window = driver.find_element(By.CLASS_NAME, 'xz65tgg')
                    window.send_keys(Keys.PAGE_DOWN)
                    hsleep(2, 4)
                    
                    # Scroll again for good measure
                    window = driver.find_element(By.CLASS_NAME, 'xz65tgg')
                    window.send_keys(Keys.PAGE_DOWN)
                    hsleep(2, 4)
                
                except StaleElementReferenceException:
                    try:
                        window = driver.find_element(By.CLASS_NAME, 'xz65tgg')
                        window.send_keys(Keys.PAGE_DOWN)
                        hsleep(2, 4)
                    except Exception:
                        pass
                except Exception:
                    pass
        
        _p(client_log_line(account, _scope, f"{_done_lbl}-Completed[{followed_count}/{target_count}]"))

        module_warnings_log += _saturation_warning(
            account, _scope, _lbl, action_label, target_account, skip_already, skip_private, followed_count,
        )
        finish_seed_use(
            apiClient, seed, _saturation_pct(skip_already, skip_private, followed_count),
            account_rate, account, _scope, _lbl, _p,
        )

    except Exception as e:
        error_msg = process_exception(True, f"follow group failed: {e}", True, True)
        module_errors_log += error_msg

    return followed_count, module_errors_log, module_warnings_log


