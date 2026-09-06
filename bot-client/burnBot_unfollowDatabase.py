# burnBot_unfollowDatabase.py

import builtins as _builtins
from burnBot_imports import *
from burnBot_human import hsleep, htype, hclick, hhover, hscroll
from burnBot_utils import process_exception
from burnBot_client_log import client_log_line
import burnBot_actionLimit as limit
from datetime import date, datetime, timedelta
import random
import time

_p = _builtins.print  # set per-call by do_unfollow_database; safe because sessions run sequentially


def ensure_dialog_open(driver, dialog_type):
    """Ensure the correct dialog is open and ready for searching"""
    try:
        # Close any existing dialog first to ensure we open the right one
        try:
            close_button = driver.find_element(By.XPATH, "//div[@role='dialog']//button[contains(@aria-label, 'Close')]")
            close_button.click()
            time.sleep(0.5)
        except:
            pass

        # Open the requested dialog
        if 'following' in dialog_type.lower():
            link = driver.find_element(By.PARTIAL_LINK_TEXT, "following")
        else:
            link = driver.find_element(By.PARTIAL_LINK_TEXT, "followers")

        ActionChains(driver).move_to_element(link).click().perform()
        hsleep(2, 4)

        # Verify dialog opened
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
        )
        return True
    except Exception as e:
        return False


def search_for_profile(driver, username, target_username):
    """Helper function to check search box for profile"""
    try:
        # Wait for dialog to be present
        dialog = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//div[@role='dialog']"))
        )
        time.sleep(0.5)

        # Find search box - try multiple selectors
        searchBox = None
        try:
            searchBox = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@role='dialog']//input[@type='text']"))
            )
        except:
            try:
                searchBox = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, 'xhtitgo'))
                )
            except:
                pass

        if not searchBox:
            return False, None

        # Click to focus
        ActionChains(driver).move_to_element(searchBox).click().perform()
        time.sleep(0.5)

        # Clear any existing text
        searchBox.clear()
        time.sleep(0.5)

        # Type text
        search_text = username[:20]
        htype(searchBox, search_text)
        time.sleep(0.5)

        # Verify text was entered - if not, use JavaScript
        current_value = searchBox.get_attribute('value')
        if not current_value or len(current_value) == 0:
            driver.execute_script("arguments[0].value = arguments[1];", searchBox, search_text)
            driver.execute_script("""
                var event = new Event('input', { bubbles: true });
                arguments[0].dispatchEvent(event);
            """, searchBox)
            time.sleep(0.5)

        time.sleep(1)

        # Wait for results to load
        profile_link = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((
                By.XPATH,
                f"//div[@role='dialog']//a[@href='/{target_username}/']"
            ))
        )
        return True, profile_link

    except (NoSuchElementException, TimeoutException):
        return False, None
    except Exception:
        return False, None


def do_unfollow_database(driver, account, target_count, apiClient, account_id, unfollow_days=30, _print=None, log_scope="unfollow-database", action_label=None):
    """Unfollow accounts from follow targets database via API.

    log_scope: TUI scope token, e.g. action[N] from the session layer.
    action_label: verb[target] label, e.g. unfollow[database].
    """
    global _p
    _p = _print if _print is not None else _builtins.print
    _log_scope = (log_scope or "unfollow-database").strip() or "unfollow-database"
    _lbl = f"{action_label}-" if action_label else ""
    _done_lbl = (action_label[0].upper() + action_label[1:]) if action_label else "Done"
    unfollows_performed = 0
    moduleErrorsLog = ""
    moduleWarningsLog = ""

    _blocked, _why = limit.is_action_blocked("unfollow")
    if _blocked:
        _p(client_log_line(account, _log_scope, f"{_lbl}skipped - action limit ({_why})"))
        return 0, moduleErrorsLog, moduleWarningsLog

    try:
        today = date.today()
        unfollow_date = today

        _p(client_log_line(account, _log_scope, f"{_lbl}loading account data…"))

        # Get eligible follow targets from API (status=following, older than unfollow_days)
        try:
            targets = apiClient.get_follow_targets(
                account_id, status="following", older_than_days=unfollow_days,
                page_size=target_count
            )
        except Exception as e:
            _p(client_log_line(account, _log_scope, f"{_lbl}ERROR: Could not load follow targets: {e}"))
            return 0, f"Could not load follow targets: {e}", ""

        if not targets:
            _p(client_log_line(account, _log_scope, f"{_lbl}no eligible accounts found (all too recent, done, or private)"))
            return 0, "", ""

        _p(client_log_line(account, _log_scope, f"{_lbl}found {len(targets)} eligible account(s)"))

        # Navigate to account profile
        _p(client_log_line(account, _log_scope, f"{_lbl}loading profile page…"))
        active_account_page = f"https://www.instagram.com/{account}/"
        driver.get(active_account_page)
        WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        hsleep(2, 4)

        # Save the main window handle
        main_window = driver.current_window_handle

        # Open following in new tab
        _p(client_log_line(account, _log_scope, f"{_lbl}opening following tab…"))
        driver.switch_to.new_window('tab')
        driver.get(active_account_page)
        WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        hsleep(2, 4)

        following_window = driver.current_window_handle

        # Click following link to open dialog
        try:
            following_link = driver.find_element(By.PARTIAL_LINK_TEXT, "following")
            ActionChains(driver).move_to_element(following_link).click().perform()
            hsleep(2, 4)
        except Exception as e:
            _p(client_log_line(account, _log_scope, f"{_lbl}ERROR: Could not open following dialog"))
            driver.close()
            driver.switch_to.window(main_window)
            return 0, f"Could not open following dialog: {e}", ""

        # Open followers in new tab
        _p(client_log_line(account, _log_scope, f"{_lbl}opening followers tab…"))
        driver.switch_to.new_window('tab')
        driver.get(active_account_page)
        WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        hsleep(2, 4)

        followers_window = driver.current_window_handle

        # Click followers link to open dialog
        try:
            followers_link = driver.find_element(By.PARTIAL_LINK_TEXT, "followers")
            ActionChains(driver).move_to_element(followers_link).click().perform()
            hsleep(2, 4)
        except Exception as e:
            _p(client_log_line(account, _log_scope, f"{_lbl}ERROR: Could not open followers dialog"))
            driver.close()
            driver.switch_to.window(following_window)
            driver.close()
            driver.switch_to.window(main_window)
            return 0, f"Could not open followers dialog: {e}", ""

        # Process each target. Both tabs are open at this point, so cleanup
        # for them runs in `finally` below — a per-target exception used to
        # skip straight past the tab-closing code that used to sit at the end
        # of this block into the outer except (which does no cleanup at all),
        # leaking the followers_window/following_window tabs whenever
        # close_browser_after_session is False.
        target_formatted = f"{target_count:02d}"

        try:
            for attempt_num, target in enumerate(targets, start=1):
                if unfollows_performed >= target_count:
                    break

                loop_username = target.get("target_handle", "")
                target_id = target.get("id", "")

                if not loop_username:
                    continue

                # count_formatted tracks attempt/loop position for the progress
                # log line — unfollows_performed (the real success count used
                # for the cap and the final summary) is only incremented once
                # an unfollow actually goes through, below. Incrementing it
                # here unconditionally used to overcount: a target that
                # errored out or was already gone from the following list
                # still bumped the counter, so a run could report "[10/10]
                # Completed" while only a handful of accounts were genuinely
                # unfollowed.
                count_formatted = f"{attempt_num:02d}"

                # Create username variations to try (reversed order)
                loop_username_clean = loop_username.replace(".", "").replace("_", "")
                loop_username_short = loop_username[:20]
                loop_username_clean_short = loop_username_clean[:20]

                usernames_to_try = []
                for username in [loop_username_clean_short, loop_username_short, loop_username_clean, loop_username]:
                    if username not in usernames_to_try:
                        usernames_to_try.append(username)

                # Check if they follow back (check followers tab first)
                follow_back = False

                # Switch to followers tab
                driver.switch_to.window(followers_window)
                time.sleep(1)

                # Search in followers list
                found_in_followers = False
                for username in usernames_to_try:
                    found_in_followers, _ = search_for_profile(driver, username, loop_username)
                    if found_in_followers:
                        break
                    time.sleep(0.3)

                if found_in_followers:
                    follow_back = True

                follow_back_display = "yes" if follow_back else "no"

                # Switch to following tab for unfollowing
                driver.switch_to.window(following_window)
                time.sleep(1)

                # Search for profile in following list
                found = False
                profile_link = None
                for username in usernames_to_try:
                    found, profile_link = search_for_profile(driver, username, loop_username)
                    if found:
                        break
                    time.sleep(0.3)

                if found and profile_link:
                    try:
                        # Find the account box and unfollow button
                        account_box = profile_link.find_element(By.XPATH,
                                                               "ancestor::div[contains(@class, 'x1uhb9sk') or contains(@class, 'x1n2onr6')]")
                        follow_button = account_box.find_element(By.XPATH, ".//button[.//div[contains(text(), 'ollow')]]")

                        # Click to trigger unfollow modal
                        _marker = limit.mark(driver)
                        hclick(driver, follow_button)
                        hsleep(3, 5)

                        # Click unfollow confirmation
                        unfollow_confirm = WebDriverWait(driver, 6).until(
                            EC.element_to_be_clickable((By.CLASS_NAME, "_a9-_"))
                        )
                        hclick(driver, unfollow_confirm)
                        hsleep(1.5, 2.5)

                        # Block dialog / rejected request after the confirm —
                        # a hard action limit stops unfollowing for this run.
                        if limit.after_click(driver, "unfollow", _marker, context=f"unfollow {loop_username}"):
                            return unfollows_performed, moduleErrorsLog, moduleWarningsLog

                        # The confirm click landed — this is a genuine unfollow,
                        # not just an attempt. Count it here, not per-loop-iteration.
                        unfollows_performed += 1

                        _p(client_log_line(
                            account, _log_scope,
                            f"{_lbl}[{unfollows_performed:02d}/{target_formatted}] - [{loop_username}] - [fb:{follow_back_display}]",
                        ))

                        # Update follow target via API
                        try:
                            apiClient.update_follow_target(
                                target_id,
                                status="done",
                                unfollow_date=unfollow_date,
                                follow_back=follow_back
                            )
                        except Exception as update_error:
                            moduleErrorsLog += f"Could not update target for {loop_username}: {update_error}\n"

                        hsleep(4, 6)

                    except Exception as e:
                        _p(client_log_line(account, _log_scope, f"{_lbl}{count_formatted}/{target_formatted} @{loop_username} error"))
                        moduleErrorsLog += f"Error unfollowing {loop_username}: {e}\n"
                else:
                    # Account not found in following list (already unfollowed manually or by another process)
                    # Still mark as done in database to avoid checking again
                    _p(client_log_line(
                        account, _log_scope,
                        f"{_lbl}[-skip] - [{loop_username}] - [not in following]",
                    ))

                    # Update follow target via API
                    try:
                        apiClient.update_follow_target(
                            target_id,
                            status="done",
                            unfollow_date=unfollow_date,
                            follow_back=follow_back
                        )
                    except Exception as update_error:
                        moduleErrorsLog += f"Could not update target for {loop_username}: {update_error}\n"

                    moduleWarningsLog += f"Not found in following list (already unfollowed): {loop_username}\n"

            # Summary message
            if unfollows_performed < target_count:
                _p(client_log_line(account, _log_scope, f"{_lbl}Incomplete[{unfollows_performed}/{target_count}]"))
            else:
                _p(client_log_line(account, _log_scope, f"{_done_lbl}-Completed[{unfollows_performed}/{target_count}]"))
        finally:
            # Close extra tabs and return to main window — always, even if a
            # per-target exception unwound past the loop above.
            for window in (followers_window, following_window):
                try:
                    if window in driver.window_handles:
                        driver.switch_to.window(window)
                        driver.close()
                except Exception:
                    pass
            try:
                if main_window in driver.window_handles:
                    driver.switch_to.window(main_window)
            except Exception:
                pass

    except Exception as error:
        noteError = "do_unfollow_database catch all"
        printError = True
        logError = True
        debugError = False
        moduleErrorsLog += process_exception(printError, noteError, logError, debugError)

    return unfollows_performed, moduleErrorsLog, moduleWarningsLog
