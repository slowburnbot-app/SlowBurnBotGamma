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
import re
import time
from datetime import date

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException

from burnBot_human import hsleep, hclick
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line
from burnBot_utils import process_exception
from burnBot_followSuggested import _find_home_follow_candidates
import burnBot_followGroup as _fg
from burnBot_followGroup import _saturation_warning, _saturation_pct, _ZERO_YIELD_SATURATION
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


_REEL_PATH_RE = re.compile(r"/reel/([^/?#]+)/?")


def _collect_post_links(driver, limit, include_reels):
    """Unique post URLs from whatever grid is on the page, in DOM order.

    Reel links are rewritten to /p/<code>/ — IG serves the same media there in
    the post layout, which carries the likes link the /reel/ layout lacks
    (verified 2026-08-28: /p/DcKENiBJL7U/ shows "3 others"; the /reel/ URL never
    does). Some grids are reel-only (buckscountyviews 12/12 that day), so
    dropping reels outright starved otherwise-productive seeds."""
    sel = "a[href*='/p/'], a[href*='/reel/']" if include_reels else "a[href*='/p/']"
    links = []
    seen = set()
    for a in driver.find_elements(By.CSS_SELECTOR, sel):
        try:
            href = a.get_attribute("href") or ""
        except Exception:
            continue
        href = _REEL_PATH_RE.sub(r"/p/\1/", href)
        if not href or href in seen:
            continue
        seen.add(href)
        links.append(href)
        if len(links) >= limit:
            break
    return links


# Likes links come in two layouts (verified live 2026-08-26 on usbgphilly posts):
#   - <a href="/p/<code>/liked_by/">…44 others</a>   (plus a text-less avatar-pile twin)
#   - <a href="#"><span>3 others</span></a>          (low like counts — no liked_by href)
# Both open the same "Likes" dialog. The "Liked by …" line also renders late on some
# loads, so the link is awaited rather than assumed present after the page settles.
# Two cases never match (verified live 2026-08-27):
#   - /reel/ pages: the like count is a bare number in the side rail, no anchor and no
#     "likes"/"others" text — so reel links are opened as /p/<code>/ instead
#     (_collect_post_links), never at their /reel/ URL.
#   - posts with hidden like counts: no "Liked by"/"N likes" line at all for a viewer who
#     follows none of the likers; an account that hides on every post is a dead seed.
_LIKES_LINK_XPATH = (
    "//a[contains(@href,'/liked_by/')]"
    " | //main//a[contains(normalize-space(),' others') or contains(normalize-space(),' likes')]"
)


def _find_likes_link(driver):
    links = driver.find_elements(By.XPATH, _LIKES_LINK_XPATH)
    # Prefer a link with visible text ("44 others" / "1,234 likes") over the avatar pile.
    for a in links:
        try:
            if (a.text or "").strip():
                return a
        except Exception:
            continue
    return links[0] if links else None


def _open_likers_dialog(driver):
    """Click the post's likes link. Returns True once the Likes dialog is present."""
    try:
        WebDriverWait(driver, 8).until(lambda d: d.find_elements(By.XPATH, _LIKES_LINK_XPATH))
    except Exception:
        return False
    for attempt in range(2):
        target = _find_likes_link(driver)
        if target is None:
            return False
        try:
            if attempt == 0:
                hclick(driver, target)
            else:
                driver.execute_script("arguments[0].click();", target)
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", target)
            except Exception:
                return False
        try:
            WebDriverWait(driver, 8).until(lambda d: d.find_elements(By.XPATH, _DIALOG_XPATH))
            hsleep(2, 3)
            return True
        except Exception:
            hsleep(1, 2)
    return False


def _dialog_root(driver):
    dialogs = driver.find_elements(By.XPATH, _DIALOG_XPATH)
    return dialogs[0] if dialogs else None


def _harvest_post_likers(driver, account, remaining, apiClient, account_id, source, lbl,
                         known, follow_date, scope, exclude, already=0, target_total=None):
    """Follow likers from the open dialog. Returns (followed, skip_known, skip_filtered, errors).

    already / target_total: the action-wide running count, so per-follow log lines
    read "<source>-[NN/NN] - [handle]" like every other follow action."""
    if target_total is None:
        target_total = already + remaining
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

        stale_batch = False
        for user_name, follow_button, anchor in candidates:
            if status_store.is_bot_paused() or followed >= cap:
                break
            if time.monotonic() - t0 > _POST_BUDGET_S:
                break
            seen.add(user_name.lower())
            # The likers list is virtualized: Instagram recycles its rows after a follow or
            # a scroll, which detaches every element harvested in this batch. One stale
            # button means the rest are stale too — un-mark this handle and rescan.
            try:
                follow_button.is_displayed()
            except StaleElementReferenceException:
                seen.discard(user_name.lower())
                stale_batch = True
                break
            try:
                if user_name in known:
                    if known.is_skipped(user_name):
                        skip_filtered += 1   # filtered on an earlier run — config choice, not exhaustion
                    else:
                        skip_known += 1
                    # Likers dialogs are dense with known handles; no visible line, no sleep,
                    # or a 60-row batch would eat the whole per-post budget before any follow.
                    debug_line(client_log_line(account, scope, f"{source}-[-skip] - [{user_name}] - [in database]"))
                    continue

                if not screen_candidate(
                    driver, apiClient, account_id, account, scope, f"{source}-", source,
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
                _p(client_log_line(account, scope, f"{source}-[{already + followed:02d}/{target_total:02d}] - [{user_name}]"))
                hsleep(10, 20)
            except StaleElementReferenceException:
                seen.discard(user_name.lower())
                stale_batch = True
                break
            except Exception as e:
                errors += process_exception(True, f"follow user failed: {e}", True, False)
                continue

        if stale_batch:
            debug_line(client_log_line(account, scope, f"{lbl}likers list re-rendered - rescanning"))
            hsleep(1, 2)
            continue   # fresh candidates from the current viewport; no scroll

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
            seed_dialogs = 0
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
                    seed_dialogs += 1

                    f, k, s, errs = _harvest_post_likers(
                        driver, account, target_count - followed_count, apiClient, account_id,
                        source, _lbl, known, follow_date, _scope, exclude,
                        already=followed_count, target_total=target_count,
                    )
                    followed_count += f
                    seed_followed += f
                    seed_known += k
                    seed_filtered += s
                    module_errors_log += errs
                    _p(client_log_line(account, _scope, f"{_lbl}post done: +{f} follow(s), {k} known, {s} filtered [{followed_count:02d}/{target_count:02d}]"))
                except Exception as e:
                    module_errors_log += process_exception(True, f"post {post_url} failed: {e}", True, False)
                    continue

            if seed_dialogs == 0 and not status_store.is_bot_paused():
                # Every post refused a likers dialog — the seed hides like counts (or the
                # pages failed). Same treatment as follow[group]'s zero-yield target: mark it
                # 100% saturated so the pool damps it now and retires it on the next dry use.
                msg = f"[{seed}] no likes link on {len(post_links)} post(s) - like counts hidden?"
                _p(client_log_line(account, _scope, f"{_lbl}Warning: {msg} - trying next seed"))
                module_warnings_log += f"{action_label or 'follow[engagers]'}: {msg}\n"
                if mode != "topics":
                    finish_seed_use(apiClient, seed_entry, _ZERO_YIELD_SATURATION, account_rate, account, _scope, _lbl, _p)
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
