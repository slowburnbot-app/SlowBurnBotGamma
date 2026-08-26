# burnBot_followEngagers.py
#
# Follow people who recently LIKED posts — either posts found under the
# account's configured topics, or the latest posts of the seed accounts in the
# account list. Likers of a fresh post were active in the niche within the last
# day or two, which is the property the suggestions list and a competitor's
# followers dialog both lack, and new posts land every day so the source never
# runs dry.
#
# DOM facts verified live 2026-08-26 (headed Chrome, timeforashifty):
#   - a post page carries <a href="/p/<code>/liked_by/"> (two of them: the
#     avatar pile with empty text and the "N others"/"N likes" text link);
#     clicking opens a role=dialog titled "Likes" — the URL does not change
#   - each row: profile <a href="/<handle>/"> + a Follow/Following button, so
#     _find_home_follow_candidates works inside the dialog unchanged
#   - the list scrolls inside a plain overflow-y:auto <div> with no stable
#     class (scrollHeight 1310 / clientHeight 356 on a 20-liker post) — found
#     by measuring, not by selector
#   - hovering a row's username opens the same profile hover card the other
#     follow flows read (burnBot_followFilter.read_hover_card)

import builtins as _builtins
import random
import time
from datetime import date

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from burnBot_human import hsleep, hclick
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line
from burnBot_utils import process_exception
from burnBot_followSuggested import _find_home_follow_candidates
import burnBot_followGroup as _fg
from burnBot_followGroup import _saturation_warning, _saturation_pct
from burnBot_followFilter import load_known_handles, screen_candidate
from burnBot_seeds import load_seed_pool, order_seeds, finish_seed_use, maybe_discover_seeds
import burnBot_likePostsTopic as _lpt
from burnBot_likePostsTopic import _open_topic_search_results
import burnBot_status as status_store

_p = _builtins.print  # set per-call by do_follow_engagers; safe because sessions run sequentially

_POSTS_PER_TOPIC = 12        # grid tiles harvested per topic search
_POSTS_PER_ACCOUNT = 6       # latest grid tiles harvested per seed account
_MAX_FOLLOWS_PER_POST = 8    # spread the day's follows across posts (and look less mechanical)
_POST_BUDGET_S = 150         # per-post wall clock incl. ~15s human-paced follows
_MAX_STALL_SCROLLS = 3       # dialog scrolls with no new rows before treating the list as exhausted
_DIALOG_XPATH = "//div[@role='dialog']"

# Scroll the likers list: the scroll container is the first dialog descendant that
# actually overflows. Returns false when nothing scrollable was found.
_SCROLL_DIALOG_JS = """
const d = document.querySelector("div[role='dialog']");
if (!d) return false;
const s = Array.from(d.querySelectorAll('div')).find(el =>
  el.scrollHeight > el.clientHeight + 20 &&
  ['auto', 'scroll'].includes(getComputedStyle(el).overflowY));
if (!s) return false;
s.scrollTop += arguments[0];
return true;
"""


def _wait_ready(driver, timeout=15):
    WebDriverWait(driver, timeout).until(lambda d: d.execute_script("return document.readyState") == "complete")


def _collect_post_links(driver, limit, include_reels):
    """Unique post URLs from whatever grid is on the page, in DOM order."""
    sel = "a[href*='/p/'], a[href*='/reel/']" if include_reels else "a[href*='/p/']"
    links = []
    seen = set()
    for a in driver.find_elements(By.CSS_SELECTOR, sel):
        try:
            href = a.get_attribute("href") or ""
        except Exception:
            continue
        if not href or href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= limit:
            break
    return links


def _open_likers_dialog(driver):
    """Click the post's likes link. Returns True once the Likes dialog is present."""
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/liked_by/']")
    if not links:
        return False
    # Prefer the text link ("20 others" / "1,234 likes") over the avatar pile.
    target = None
    for a in links:
        try:
            if (a.text or "").strip():
                target = a
                break
        except Exception:
            continue
    target = target or links[0]
    try:
        hclick(driver, target)
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", target)
        except Exception:
            return False
    try:
        WebDriverWait(driver, 8).until(lambda d: d.find_elements(By.XPATH, _DIALOG_XPATH))
    except Exception:
        return False
    hsleep(2, 3)
    return True


def _dialog_root(driver):
    dialogs = driver.find_elements(By.XPATH, _DIALOG_XPATH)
    return dialogs[0] if dialogs else None


def _harvest_post_likers(driver, account, remaining, apiClient, account_id, source, lbl,
                         known, follow_date, scope, exclude):
    """Follow likers from the open dialog. Returns (followed, skip_known, skip_filtered, errors)."""
    followed = 0
    skip_known = 0
    skip_filtered = 0
    errors = ""
    seen = set()
    stalls = 0
    t0 = time.monotonic()
    cap = min(remaining, _MAX_FOLLOWS_PER_POST)

    while followed < cap:
        if status_store.is_bot_paused():
            break
        if time.monotonic() - t0 > _POST_BUDGET_S:
            debug_line(client_log_line(account, scope, f"{lbl}post budget reached after {followed} follow(s)"))
            break

        root = _dialog_root(driver)
        if root is None:
            break
        candidates = [
            c for c in _find_home_follow_candidates(driver, max_candidates=60, root=root)
            if c[0].lower() not in seen and c[0].lower() not in exclude
        ]
        if not candidates:
            stalls += 1
            if stalls >= _MAX_STALL_SCROLLS:
                break
            try:
                if not driver.execute_script(_SCROLL_DIALOG_JS, 700):
                    break
            except Exception:
                break
            hsleep(2, 3)
            continue
        stalls = 0

        for user_name, follow_button, anchor in candidates:
            if status_store.is_bot_paused() or followed >= cap:
                break
            if time.monotonic() - t0 > _POST_BUDGET_S:
                break
            seen.add(user_name.lower())
            try:
                if user_name in known:
                    if known.is_skipped(user_name):
                        skip_filtered += 1   # filtered on an earlier run — config choice, not exhaustion
                    else:
                        skip_known += 1
                    # Likers dialogs are dense with known handles; no visible line, no sleep,
                    # or a 60-row batch would eat the whole per-post budget before any follow.
                    debug_line(client_log_line(account, scope, f"{lbl}[-skip] - [{user_name}] - [in database]"))
                    continue

                if not screen_candidate(
                    driver, apiClient, account_id, account, scope, lbl, source,
                    user_name, anchor, known, follow_date, _p,
                ):
                    skip_filtered += 1
                    continue

                try:
                    hclick(driver, follow_button)
                except Exception:
                    driver.execute_script("arguments[0].click();", follow_button)

                followed += 1
                known.add(user_name)
                try:
                    apiClient.create_follow_target(
                        account_id, user_name, source=source, status="following", follow_date=follow_date,
                    )
                except Exception:
                    pass
                _p(client_log_line(account, scope, f"{lbl}[+] - [{user_name}]"))
                hsleep(10, 20)
            except Exception as e:
                errors += process_exception(True, f"follow user failed: {e}", True, False)
                continue

        if followed < cap:
            try:
                driver.execute_script(_SCROLL_DIALOG_JS, 700)
            except Exception:
                pass
            hsleep(2, 3)

    return followed, skip_known, skip_filtered, errors


def do_follow_engagers(driver, account, target_count, apiClient, account_id, mode, seeds,
                       group_mode="manual", _print=None, log_scope=None, action_label=None):
    """Follow recent likers of posts.

    mode:  "topics"   — seeds is the account's comma-separated topics list;
                        posts come from the topic search results grid
           "accounts" — seeds is the comma-separated account group;
                        posts are each target's latest grid tiles
    group_mode (accounts only): "manual" — targets from the account group at random
                                "pool"   — targets from the follow_seeds pool

    Returns: (followed_count, error_log, warning_log)
    """
    global _p
    _p = _print if _print is not None else _builtins.print
    # Borrowed helpers (_saturation_warning, _open_similar_panel, _open_topic_search_results)
    # log through their own module's _p — point those at this action's log pane too.
    _fg._p = _p
    _lpt._p = _p
    _scope = log_scope or "follow-engagers"
    _lbl = f"{action_label}-" if action_label else ""
    _done_lbl = (action_label[0].upper() + action_label[1:]) if action_label else "Done"

    followed_count = 0
    module_errors_log = ""
    module_warnings_log = ""

    try:
        follow_date = date.today()
        known = load_known_handles(apiClient, account_id, account, _scope, _lbl, _p)

        account_rate = None
        if mode == "topics":
            seed_list = [{"handle": s.strip().lstrip("#"), "id": None} for s in (seeds or "").split(",") if s.strip()]
            random.shuffle(seed_list)
        else:
            # Manual account group (uniform) or the follow-back-weighted seed pool
            # that grows itself when it runs low.
            seed_pool, account_rate, all_seeds, group_handles = load_seed_pool(apiClient, account_id, seeds, group_mode)
            if group_mode == "pool":
                maybe_discover_seeds(driver, apiClient, account_id, account, seed_pool, all_seeds, group_handles, _scope, _lbl, _p)
            seed_list = order_seeds(seed_pool)
        if not seed_list:
            what = "topics" if mode == "topics" else "target accounts"
            msg = f"[error] no {what} configured"
            _p(client_log_line(account, _scope, f"{_lbl}{msg}"))
            module_errors_log += f"{action_label or 'follow[engagers]'}: {msg}\n"
            return 0, module_errors_log, module_warnings_log

        for seed_entry in seed_list:
            if status_store.is_bot_paused() or followed_count >= target_count:
                break
            seed = seed_entry["handle"]

            exclude = {account.lower(), seed.lower()}
            if mode == "topics":
                source = f"#{seed}[likers]"
                _p(client_log_line(account, _scope, f"{_lbl}searching topic [{seed}]"))
                result = _open_topic_search_results(driver, account, seed, account_id=account_id)
                if result == "restricted":
                    module_warnings_log += f"{action_label or 'follow[engagers]'}: [warning] topic [{seed}] hidden by Instagram (age-restricted search)\n"
                    continue
                if not result:
                    module_errors_log += f"{action_label or 'follow[engagers]'}: [error] could not open search results for [{seed}]\n"
                    continue
                post_links = _collect_post_links(driver, _POSTS_PER_TOPIC, include_reels=False)
            else:
                source = f"{seed}[likers]"
                _p(client_log_line(account, _scope, f"{_lbl}selected target: {seed}"))
                driver.get(f"https://www.instagram.com/{seed}/")
                _wait_ready(driver)
                hsleep(3, 5)
                if driver.find_elements(By.XPATH, "//*[contains(text(), \"Sorry, this page isn't available.\")]"):
                    msg = f"Target account '{seed}' not found"
                    _p(client_log_line(account, _scope, f"{_lbl}ERROR: {msg}"))
                    module_errors_log += f"{action_label or 'follow[engagers]'}: {msg}\n"
                    finish_seed_use(apiClient, seed_entry, None, account_rate, account, _scope, _lbl, _p, retire_reason="not found")
                    continue
                post_links = _collect_post_links(driver, _POSTS_PER_ACCOUNT, include_reels=True)

            if not post_links:
                _p(client_log_line(account, _scope, f"{_lbl}Warning: no posts found for [{seed}]"))
                module_warnings_log += f"{action_label or 'follow[engagers]'}: [warning] no posts found for [{seed}]\n"
                continue
            _p(client_log_line(account, _scope, f"{_lbl}[{seed}] {len(post_links)} post(s) to mine"))

            seed_followed = 0
            seed_known = 0
            seed_filtered = 0
            for post_url in post_links:
                if status_store.is_bot_paused() or followed_count >= target_count:
                    break
                try:
                    driver.get(post_url)
                    _wait_ready(driver)
                    hsleep(3, 5)
                    if not _open_likers_dialog(driver):
                        debug_line(client_log_line(account, _scope, f"{_lbl}no likes dialog for {post_url}"))
                        continue

                    f, k, s, errs = _harvest_post_likers(
                        driver, account, target_count - followed_count, apiClient, account_id,
                        source, _lbl, known, follow_date, _scope, exclude,
                    )
                    followed_count += f
                    seed_followed += f
                    seed_known += k
                    seed_filtered += s
                    module_errors_log += errs
                    _p(client_log_line(account, _scope, f"{_lbl}[{followed_count:02d}/{target_count:02d}] post done: +{f} follow(s), {k} known, {s} filtered"))
                except Exception as e:
                    module_errors_log += process_exception(True, f"post {post_url} failed: {e}", True, False)
                    continue

            module_warnings_log += _saturation_warning(
                account, _scope, _lbl, action_label, seed, seed_known, seed_filtered, seed_followed,
            )
            if mode != "topics":
                finish_seed_use(
                    apiClient, seed_entry, _saturation_pct(seed_known, seed_filtered, seed_followed),
                    account_rate, account, _scope, _lbl, _p,
                )

        if followed_count < target_count:
            _p(client_log_line(account, _scope, f"{_lbl}Incomplete[{followed_count}/{target_count}]"))
            if followed_count == 0:
                module_errors_log += f"{action_label or 'follow[engagers]'}: [error] no likers followed (0/{target_count})\n"
            else:
                module_warnings_log += f"{action_label or 'follow[engagers]'}: [warning] limited likers found ({followed_count}/{target_count})\n"
        else:
            _p(client_log_line(account, _scope, f"{_done_lbl}-Completed[{followed_count}/{target_count}]"))

    except Exception as e:
        module_errors_log += process_exception(True, f"follow engagers failed: {e}", True, True)

    return followed_count, module_errors_log, module_warnings_log
