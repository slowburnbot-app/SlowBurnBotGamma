# burnBot_likePostsTopic.py
# Likes posts from Instagram topic/keyword search results.
# Topics are read from the account settings row on Google Drive (Column AO, index 40),
# supplied as a comma-separated list of hashtags or topic names (e.g. "beer, craft beer, brewing").
# Each topic is opened via Instagram's in-app Search UI, then posts are liked until the target is reached.

import builtins as _builtins
from burnBot_imports import *
from burnBot_human import hsleep, htype, hclick, hhover, hscroll
from burnBot_utils import process_exception, get_post_author_username
from burnBot_accountSession_setup import is_bot_debug_enabled
from burnBot_client_log import client_log_line
from burnBot_run_log import capture_failure_context, report_failure, debug_line
import burnBot_actionLimit as limit
import random
import time
from urllib.parse import quote
import burnBot_status as status_store

# Fail-fast guards for the topic search loop (see do_like_posts_topic) — a
# dead topic used to burn its full ~60s wait budget before moving on, and a
# search UI that has genuinely changed would burn that for every remaining
# topic in the list.
_TOPIC_BUDGET_S = 90
_MAX_CONSEC_SEARCH_FAILURES = 2

# Topic-saturation warning (mirrors the follow-group target-saturation check):
# fire only on a meaningful per-topic sample so a topic that happens to open on
# a couple of already-liked posts doesn't cry wolf.
_TOPIC_SATURATION_MIN_POSTS = 8
_TOPIC_SATURATION_WARN_RATIO = 0.75

# 2026-08-13: a full topics action failed 0/37 with every post reading
# author=unknown and no Unlike flip after the click, while the same flow
# worked from a fresh session — the bot's session is being served a post
# modal whose content doesn't render/react normally. These failures only
# left bare debug lines, so cap a couple of full-page diagnostic uploads
# per action to capture the DOM the bot actually sees.
_MAX_LIKE_DIAG_REPORTS = 2

# Labelled locator lists (name, By, xpath) — the name lets the log say which
# strategy actually matched (or was tried and failed), matching the pattern
# already used in burnBot_followGroup.py's follower/following dialog lookup.
_SEARCH_ENTRY_LOCATORS = [
    ("nav-explore-search",   By.XPATH, "//a[contains(@href,'/explore/search')]"),
    ("nav-explore-aria",     By.XPATH, "//a[@href='/explore/' and (.//*[normalize-space()='Search'] or @aria-label='Search')]"),
    ("span-search-ancestor", By.XPATH, "//span[normalize-space()='Search']/ancestor::*[self::a or self::button or @role='link' or @tabindex][1]"),
    ("aria-search",          By.XPATH, "//*[self::a or self::div or self::button][@aria-label='Search']"),
    ("role-link-search",     By.XPATH, "//*[@role='link' and @aria-label='Search']"),
    ("text-search-fallback", By.XPATH, "//*[self::a or self::div or self::button][.//*[normalize-space()='Search'] or normalize-space()='Search']"),
]

_SEARCH_INPUT_LOCATORS = [
    ("aria-search-input",    By.XPATH, "//input[@aria-label='Search input']"),
    ("placeholder-search",   By.XPATH, "//input[@placeholder='Search']"),
    ("aria-contains-search", By.XPATH, "//input[contains(@aria-label,'Search')]"),
    ("text-input",           By.XPATH, "//input[@type='text']"),
    ("textarea-search",      By.XPATH, "//textarea[contains(@aria-label,'Search') or @placeholder='Search']"),
    ("role-searchbox",       By.XPATH, "//*[@role='searchbox']"),
    ("role-textbox",         By.XPATH, "//*[@role='textbox']"),
    ("contenteditable",      By.XPATH, "//*[@contenteditable='true']"),
    ("aria-search-textbox",  By.XPATH, "//*[contains(@aria-label,'Search') and (@role='textbox' or @contenteditable='true')]"),
]


# Instagram hides keyword search results for age-restricted terms (alcohol
# etc.) — the page says "We've hidden most results for your search…" and
# shows no post grid. That's a per-topic content policy, not a bot failure:
# the customer fixes it by changing topics or age-verifying the account.
# Deliberately apostrophe-free: IG may render "We've" with either an ASCII
# or typographic apostrophe, and .lower() normalizes neither.
_RESTRICTED_MARKERS = (
    "hidden most results",
)


def _detect_restricted_search(driver):
    """True if the current page is Instagram's hidden/age-restricted search
    results notice. Never raises."""
    try:
        body = (driver.execute_script(
            "return (document.body && document.body.innerText) || ''"
        ) or "").lower()
        return any(marker in body for marker in _RESTRICTED_MARKERS)
    except Exception:
        return False


def _find_visible(driver, locators):
    """Return (element, name) for the first visible match across a labelled
    locator list, or (None, None). No wait — caller decides whether/how to poll."""
    for name, by, xpath in locators:
        try:
            visible = [e for e in driver.find_elements(by, xpath) if e.is_displayed()]
            if visible:
                return visible[0], name
        except Exception:
            continue
    return None, None


def _wait_first_match(driver, timeout, locators):
    """Wait up to `timeout` for ANY locator in a labelled list to yield visible
    elements, returning (name, elements) as soon as one does. Raises
    TimeoutException if none match within the budget.

    Replaces the old pattern of giving each locator its own fresh
    WebDriverWait in series: `until()` was fed a lambda returning a *list*,
    and an empty list is falsy — so a locator with no match burned its FULL
    timeout before falling through to the next one (two 15s locators = 30s
    silently spent with no log line). This checks every locator on each
    poll tick and returns the moment any one matches.
    """
    def _predicate(d):
        for name, by, xpath in locators:
            try:
                elems = [e for e in d.find_elements(by, xpath) if e.is_displayed()]
            except Exception:
                continue
            if elems:
                return name, elems
        return False
    return WebDriverWait(driver, timeout).until(_predicate)

_p = _builtins.print  # set per-call by do_like_posts_topic; safe because sessions run sequentially


def _normalize_post_path(post_url):
    """Return a stable /p/... path for matching result links."""
    if not post_url:
        return ""

    cleaned_url = post_url.strip()
    if "instagram.com" in cleaned_url:
        cleaned_url = cleaned_url.split("instagram.com", 1)[1]

    cleaned_url = cleaned_url.split("?", 1)[0].split("#", 1)[0]
    if not cleaned_url.startswith("/"):
        cleaned_url = f"/{cleaned_url}"

    if cleaned_url.startswith("/p/") and not cleaned_url.endswith("/"):
        cleaned_url = f"{cleaned_url}/"

    return cleaned_url


def _open_post_from_results(driver, account, post_url):
    """
    Open a result tile with a real click so Instagram keeps the user in the
    search-results flow instead of forcing a direct page load.
    """
    post_path = _normalize_post_path(post_url)
    if not post_path:
        return False

    result_locator = (
        By.XPATH,
        f"//a[contains(@href, '{post_path}') and not(contains(@href, '/liked_by'))]"
    )

    try:
        result_link = WebDriverWait(driver, 10).until(
            lambda d: next(
                (elem for elem in d.find_elements(*result_locator) if elem.is_displayed()),
                None
            )
        )
    except Exception:
        debug_line(client_log_line(account, "like-topics", f"debug could not find result tile for [{post_path}]"))
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", result_link)
        hsleep(1, 2)
    except Exception:
        pass

    clicked = False
    try:
        hclick(driver, result_link)
        clicked = True
    except Exception:
        pass

    if not clicked:
        try:
            driver.execute_script("arguments[0].click();", result_link)
            clicked = True
        except Exception:
            pass

    if not clicked:
        debug_line(client_log_line(account, "like-topics", f"debug result tile click failed for [{post_path}]"))
        return False

    try:
        WebDriverWait(driver, 12).until(
            lambda d: (
                post_path in ((d.current_url or "").split("instagram.com")[-1].split("?", 1)[0])
                and len(d.find_elements(By.TAG_NAME, "article")) > 0
            )
        )
        hsleep(2, 3)
        return True
    except Exception:
        debug_line(client_log_line(account, "like-topics", f"debug post did not finish opening for [{post_path}]"))
        return False


def _close_open_post(driver, account, results_url):
    """Close an opened result and return to the search grid."""
    try:
        body = driver.find_element(By.TAG_NAME, "body")
    except Exception:
        body = None

    for _ in range(2):
        try:
            if body:
                body.send_keys(Keys.ESCAPE)
            else:
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        except Exception:
            pass

        try:
            WebDriverWait(driver, 6).until(
                lambda d: (
                    "/explore/search/keyword/" in (d.current_url or "")
                    and len(d.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")) > 0
                )
            )
            hsleep(1, 2)
            return True
        except Exception:
            pass

    close_button_locators = [
        (By.XPATH, "//div[@role='dialog']//*[@aria-label='Close']"),
        (By.XPATH, "//div[@role='dialog']//*[@role='button'][.//*[local-name()='svg' and @aria-label='Close']]"),
    ]

    for by, locator in close_button_locators:
        try:
            close_button = WebDriverWait(driver, 4).until(
                lambda d: next(
                    (elem for elem in d.find_elements(by, locator) if elem.is_displayed()),
                    None
                )
            )
            driver.execute_script("arguments[0].click();", close_button)
            WebDriverWait(driver, 6).until(
                lambda d: (
                    "/explore/search/keyword/" in (d.current_url or "")
                    and len(d.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")) > 0
                )
            )
            hsleep(1, 2)
            return True
        except Exception:
            continue

    try:
        if "/p/" in (driver.current_url or "") and results_url:
            driver.back()
            WebDriverWait(driver, 10).until(
                lambda d: (
                    results_url in (d.current_url or "")
                    or (
                        "/explore/search/keyword/" in (d.current_url or "")
                        and len(d.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")) > 0
                    )
                )
            )
            hsleep(1, 2)
            return True
    except Exception:
        pass

    debug_line(client_log_line(account, "like-topics", "debug: could not return to results grid"))
    return False


def _open_topic_search_results(driver, account, topic, account_id=None):
    """
    Open Instagram search UI, search for a topic, and land on the
    keyword results page. Direct URL loads have proven unreliable for this flow.

    Broken into named stages so a failure log says *where* the time went
    instead of a bare `TimeoutException: Message:` (lambda-based
    WebDriverWait timeouts carry no message of their own). Stage failures
    always log and capture a diagnostic artifact — never gated on debug —
    since a non-debug run still needs to explain a ~60s topic.

    Returns:
        True          — landed on the keyword results page
        "restricted"  — Instagram hid the results for this term (age/content
                        policy); a customer-settings issue, not a bot failure
        False         — search genuinely failed
    """
    search_query = topic.strip()
    if not search_query:
        return False

    _stage = {"name": "init", "t0": time.monotonic()}

    def _enter(name):
        _stage["name"] = name
        _stage["t0"] = time.monotonic()

    def _elapsed():
        return time.monotonic() - _stage["t0"]

    def _ok(detail=""):
        debug_line(client_log_line(account, "like-topics", f"stage={_stage['name']} outcome=ok{(' ' + detail) if detail else ''}"))

    def _fail(detail=""):
        _p(client_log_line(
            account, "like-topics",
            f"stage={_stage['name']} outcome=timeout elapsed={_elapsed():.1f}s topic=[{topic}]{(' ' + detail) if detail else ''}"
        ))
        try:
            report_failure(
                account_id, f"topic-search/{_stage['name']}", "timeout",
                {"topic": topic, "elapsed_ms": int(_elapsed() * 1000), "detail": detail,
                 "diag": capture_failure_context(driver)},
            )
        except Exception:
            pass

    def _restricted():
        """Log + report Instagram's hidden-results notice for this topic."""
        _p(client_log_line(
            account, "like-topics",
            f"topic [{topic}] hidden by Instagram (age-restricted search)"
        ))
        try:
            report_failure(
                account_id, "topic-search/restricted", "restricted",
                {"topic": topic, "elapsed_ms": int(_elapsed() * 1000),
                 "diag": capture_failure_context(driver)},
            )
        except Exception:
            pass
        return "restricted"

    try:
        _enter("home-load")
        driver.get("https://www.instagram.com/")
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass  # pageLoadStrategy is already 'normal' — driver.get() already blocked on this
        hsleep(2, 4)

        _enter("search-open")
        search_clicked = False
        for name, by, locator in _SEARCH_ENTRY_LOCATORS:
            try:
                candidates = driver.find_elements(by, locator)
                for candidate in candidates:
                    if candidate.is_displayed():
                        driver.execute_script("arguments[0].click();", candidate)
                        search_clicked = True
                        break
                if search_clicked:
                    _ok(f"via [{name}]")
                    break
            except Exception:
                continue

        if not search_clicked:
            try:
                search_clicked = driver.execute_script("""
                    const searchSpan = Array.from(document.querySelectorAll('span'))
                        .find(el => (el.textContent || '').trim() === 'Search');
                    if (searchSpan) {
                        const clickable = searchSpan.closest('a, button, [role="link"], [tabindex]');
                        if (clickable) {
                            clickable.click();
                            return true;
                        }
                    }
                    return false;
                """)
                if search_clicked:
                    _ok("via [js-span-search]")
            except Exception:
                search_clicked = False

        if not search_clicked:
            _enter("search-fallback")
            try:
                driver.get("https://www.instagram.com/explore/search/")
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
                hsleep(2, 4)
                search_clicked = True
                debug_line(client_log_line(account, "like-topics", f"using direct search page fallback for [{topic}]"))
            except Exception:
                _fail("could not open search UI or its direct-URL fallback")
                return False

        _enter("search-input")
        search_input, _input_name = _find_visible(driver, _SEARCH_INPUT_LOCATORS)
        if not search_input:
            try:
                search_input = WebDriverWait(driver, 8).until(
                    EC.visibility_of_element_located(_SEARCH_INPUT_LOCATORS[0][1:])
                )
                _input_name = _SEARCH_INPUT_LOCATORS[0][0]
            except Exception:
                search_input = None

        if not search_input:
            try:
                search_input = driver.execute_script("""
                    const selectors = [
                        "input[aria-label='Search input']",
                        "input[placeholder='Search']",
                        "input[aria-label*='Search']",
                        "textarea[aria-label*='Search']",
                        "[role='searchbox']",
                        "[role='textbox']",
                        "[contenteditable='true']",
                        "[aria-label*='Search']"
                    ];
                    for (const selector of selectors) {
                        const elements = Array.from(document.querySelectorAll(selector));
                        const visible = elements.find(el => {
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                        });
                        if (visible) {
                            return visible;
                        }
                    }
                    return null;
                """)
                _input_name = "js-fallback"
            except Exception:
                search_input = None

        if not search_input:
            _fail("search box not found")
            return False
        _ok(f"via [{_input_name}]")

        try:
            search_input.click()
        except Exception:
            pass

        try:
            active_role = (search_input.get_attribute("role") or "").lower()
            is_editable = (search_input.get_attribute("contenteditable") or "").lower() == "true"

            if is_editable or active_role in ["textbox", "searchbox"]:
                search_input.send_keys(Keys.CONTROL, "a")
                search_input.send_keys(Keys.BACKSPACE)
            else:
                search_input.send_keys(Keys.CONTROL, "a")
                search_input.send_keys(Keys.DELETE)
        except Exception:
            try:
                search_input.clear()
            except Exception:
                pass

        htype(search_input, search_query)
        hsleep(3, 5)

        _enter("keyword-result")
        normalized_query = " ".join(search_query.lower().split())
        keyword_result_locators = [
            ("exact-span", By.XPATH,
             f"//a[contains(@href, '/explore/search/keyword/')][.//span[normalize-space()=\"{search_query}\"]]"),
            ("any-keyword-link", By.XPATH, "//a[contains(@href, '/explore/search/keyword/')]"),
        ]

        keyword_clicked = False
        try:
            matched_name, keyword_candidates = _wait_first_match(driver, 8, keyword_result_locators)
        except Exception:
            matched_name, keyword_candidates = None, []
            if _detect_restricted_search(driver):
                return _restricted()
            # No keyword row in the dropdown — expected for short/generic
            # terms (e.g. "rtd"); the direct-URL fallback below handles it.
            # Transcript-only: not a failure, so no visible line and no
            # failure record for the end user.
            debug_line(client_log_line(
                account, "like-topics",
                f"stage={_stage['name']} no keyword row in dropdown after {_elapsed():.1f}s topic=[{topic}] — using direct URL"
            ))

        if keyword_candidates:
            if len(keyword_candidates) > 1:
                for candidate in keyword_candidates:
                    try:
                        candidate_text = " ".join((candidate.text or "").lower().split())
                        if candidate_text and normalized_query in candidate_text:
                            driver.execute_script("arguments[0].click();", candidate)
                            keyword_clicked = True
                            break
                    except Exception:
                        continue
            if not keyword_clicked:
                driver.execute_script("arguments[0].click();", keyword_candidates[0])
                keyword_clicked = True
            if keyword_clicked:
                _ok(f"via [{matched_name}]")

        if not keyword_clicked:
            # Short/generic terms (e.g. "rtd") get only account rows in the
            # search dropdown — no keyword link to click — but the keyword
            # results URL still works when loaded directly. The previous
            # ENTER-the-search-box fallback left the browser on /explore/
            # with the search panel open, where the results-page check
            # false-positived on the grid behind the panel and every tile
            # click then failed as not-visible.
            _enter("keyword-url-fallback")
            try:
                driver.get(f"https://www.instagram.com/explore/search/keyword/?q={quote(search_query)}")
                WebDriverWait(driver, 10).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
                debug_line(client_log_line(account, "like-topics", f"using direct keyword URL for [{topic}]"))
            except Exception:
                _fail("direct keyword URL fallback failed")
                return False

        _enter("results-page")
        try:
            WebDriverWait(driver, 12).until(
                lambda d: (
                    "/explore/search/keyword/" in (d.current_url or "")
                    or len(d.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")) > 0
                )
            )
        except Exception:
            if _detect_restricted_search(driver):
                return _restricted()
            _fail("no keyword URL and no /p/ links after search submit")
            return False
        _ok()
        hsleep(4, 6)
        return True

    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e).split("\n")[0].strip() or "(no message — lambda predicate timeout)"
        _p(client_log_line(
            account, "like-topics",
            f"error search failed for [{topic}] at stage={_stage['name']} after {_elapsed():.1f}s - {error_type}: {error_msg[:80]}"
        ))
        try:
            report_failure(
                account_id, f"topic-search/{_stage['name']}", "exception",
                {"topic": topic, "exc": error_type, "msg": error_msg[:200],
                 "diag": capture_failure_context(driver)},
            )
        except Exception:
            pass
        return False


def do_like_posts_topic(driver, account, target_count, apiClient=None, account_id=None, topics=None, _print=None, log_scope=None, action_label=None):
    global _p
    _p = _print if _print is not None else _builtins.print
    _scope = log_scope or "like-topics"
    _lbl = f"{action_label}-" if action_label else ""
    _done_lbl = (action_label[0].upper() + action_label[1:]) if action_label else "Done"
    """
    Like posts from Instagram topic/hashtag pages.

    Args:
        driver:       Selenium WebDriver instance
        account:      Account name (for logging)
        target_count: Number of posts to like
        apiClient:    Optional ApiClient instance for ignore list access
        account_id:   Account UUID (unused here, kept for consistent interface)
        topics:       Comma-separated string of hashtags/topics (e.g. "beer, craft beer").
                      Leading '#' characters are stripped automatically.

    Returns:
        tuple: (likes_performed, errors_log, warnings_log)
    """
    likes_performed = 0
    moduleErrorsLog = ""
    moduleWarningsLog = ""
    restricted_topics = []

    # Parse topic list
    topic_list = []
    if topics:
        for t in topics.split(","):
            t = t.strip().lstrip("#")
            if t:
                topic_list.append(t)

    if not topic_list:
        msg = "[error] no topics configured for post[topics] action"
        _p(client_log_line(account, _scope, f"{_lbl}{msg}"))
        moduleErrorsLog += f"like[topics]: {msg}\n"
        return 0, moduleErrorsLog, moduleWarningsLog

    _blocked, _why = limit.is_action_blocked("like")
    if _blocked:
        _p(client_log_line(account, _scope, f"{_lbl}skipped - action limit ({_why})"))
        return 0, moduleErrorsLog, moduleWarningsLog

    # Load like_sponsored setting from API (suggested posts don't appear on hashtag pages)
    _user_cfg = apiClient.get_user_config() if apiClient else {}
    like_sponsored = _user_cfg.get('like_sponsored', True)

    # Load ignore list if available
    ignore_list = []
    if apiClient:
        try:
            ignore_list = apiClient.get_ignore_handles()
            if ignore_list:
                debug_line(client_log_line(account, _scope, f"loaded {len(ignore_list)} ignored account(s)"))
        except Exception as e:
            _p(client_log_line(account, _scope, f"Warning: Could not load ignore list: {e}"))

    target_formatted = f"{target_count:02d}"
    processed_urls = set()  # Track post URLs to avoid double-liking across topics
    like_diag_reports = 0  # cap on full-page diagnostics uploaded per action

    consec_search_failures = 0

    try:
        for topic in topic_list:
            if status_store.is_bot_paused():
                return likes_performed, moduleErrorsLog, moduleWarningsLog
            if likes_performed >= target_count:
                break

            _p(client_log_line(account, _scope, f"searching topic [{topic}]"))
            topic_t0 = time.monotonic()
            topic_likes_at_start = likes_performed
            topic_already_liked = 0

            _search_result = _open_topic_search_results(driver, account, topic, account_id=account_id)
            if _search_result == "restricted":
                # Instagram hid the results for this term (age/content policy).
                # A customer-settings issue, not a bot failure: warn, don't
                # error, and don't count it toward the consecutive-failure
                # breaker — the search UI demonstrably worked.
                restricted_topics.append(topic)
                moduleWarningsLog += (
                    f"like[topics]: [warning] topic [{topic}] hidden by Instagram "
                    f"(age-restricted search)\n"
                )
                consec_search_failures = 0
                continue
            if not _search_result:
                moduleErrorsLog += f"like[topics]: [error] could not open search results for [{topic}]\n"
                consec_search_failures += 1
                if consec_search_failures >= _MAX_CONSEC_SEARCH_FAILURES:
                    moduleErrorsLog += (
                        f"like[topics]: [error] search unavailable — aborted after "
                        f"{_MAX_CONSEC_SEARCH_FAILURES} consecutive topic failures\n"
                    )
                    break
                continue
            consec_search_failures = 0

            max_posts_per_topic = max(target_count * 3, 30)  # scan more than target to find unliked posts
            posts_scanned = 0
            scrolls = 0
            max_scrolls = 10

            while likes_performed < target_count and scrolls < max_scrolls:
                if time.monotonic() - topic_t0 > _TOPIC_BUDGET_S:
                    topic_likes = likes_performed - topic_likes_at_start
                    if topic_likes == 0:
                        # Dead topic: burned its whole budget without a single
                        # like — that's the case the budget exists for, and
                        # the only one worth telling the customer about.
                        moduleWarningsLog += f"like[topics]: [warning] topic [{topic}] produced no likes within {_TOPIC_BUDGET_S}s budget - consider topic replacement\n"
                    else:
                        # Healthy topic that simply used its time slice —
                        # ~15s per human-paced like means ~5-6 likes fill the
                        # budget. Normal pacing, transcript-only.
                        debug_line(client_log_line(
                            account, _scope,
                            f"topic [{topic}] budget reached after {topic_likes} like(s), moving to next topic"
                        ))
                    break
                if status_store.is_bot_paused():
                    return likes_performed, moduleErrorsLog, moduleWarningsLog
                if scrolls == 0:
                    try:
                        WebDriverWait(driver, 12).until(
                            lambda d: (
                                len(d.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")) > 0
                                or len(d.find_elements(By.XPATH, "//*[contains(normalize-space(),'See more')]")) > 0
                            )
                        )
                    except Exception:
                        pass

                # Find post thumbnail links on the topic page (grid layout) and extract
                # hrefs into a plain list now. The live DOM can still change after each
                # modal open/close cycle, so we avoid holding thumbnail elements directly.
                post_links = driver.find_elements(
                    By.XPATH,
                    "//a[contains(@href, '/p/') and not(contains(@href, '/p/liked_by'))]"
                )

                new_link_urls = []
                for lnk in post_links:
                    try:
                        href = lnk.get_attribute("href")
                        if href and href not in processed_urls:
                            new_link_urls.append(href)
                    except StaleElementReferenceException:
                        continue

                if not new_link_urls:
                    if scrolls == 0:
                        # A restricted term can still reach the keyword results
                        # page (URL matches) while showing the hidden-results
                        # notice instead of a post grid — classify it here too,
                        # not just in the search-stage timeout branches.
                        if _detect_restricted_search(driver):
                            _p(client_log_line(account, _scope, f"topic [{topic}] hidden by Instagram (age-restricted search)"))
                            restricted_topics.append(topic)
                            moduleWarningsLog += (
                                f"like[topics]: [warning] topic [{topic}] hidden by Instagram "
                                f"(age-restricted search)\n"
                            )
                            break
                        _p(client_log_line(account, _scope, f"no posts found for topic [{topic}]"))
                    break

                for post_url in new_link_urls:
                    if time.monotonic() - topic_t0 > _TOPIC_BUDGET_S:
                        break
                    if likes_performed >= target_count or posts_scanned >= max_posts_per_topic:
                        break

                    if post_url in processed_urls:
                        continue

                    processed_urls.add(post_url)
                    posts_scanned += 1

                    results_url = driver.current_url
                    post_opened = False

                    try:
                        post_opened = _open_post_from_results(driver, account, post_url)
                        if not post_opened:
                            debug_line(client_log_line(account, _scope, f"skip could not open [{post_url}] from results"))
                            continue

                        article = WebDriverWait(driver, 8).until(
                            EC.presence_of_element_located((By.TAG_NAME, "article"))
                        )
                        article_account = get_post_author_username(article)

                        if article_account == "unknown" and like_diag_reports < _MAX_LIKE_DIAG_REPORTS:
                            like_diag_reports += 1
                            try:
                                report_failure(
                                    account_id, "topic-search/post-author", "unknown",
                                    {"topic": topic, "post": post_url,
                                     "article_html": (article.get_attribute("outerHTML") or "")[:2000],
                                     "diag": capture_failure_context(driver)},
                                )
                            except Exception:
                                pass

                        # Check ignore list
                        if article_account in ignore_list:
                            display_name = article_account[:15] if len(article_account) > 15 else article_account
                            _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{display_name}] - [ignored]"))
                            continue

                        # Skip sponsored posts if like_sponsored is disabled
                        if not like_sponsored:
                            try:
                                page_inner_text = driver.execute_script("return document.body.innerText || ''")
                                is_ad = 'Sponsored' in page_inner_text
                                if not is_ad:
                                    is_ad = bool(driver.find_elements(
                                        By.XPATH, "//*[contains(@href,'/ads/about')]"
                                    ))
                                if is_ad:
                                    display_name = article_account[:15] if len(article_account) > 15 else article_account
                                    _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{display_name}] - [sponsored]"))
                                    continue
                            except Exception:
                                pass

                        # Find like button — scoped to the action-bar <section>.
                        # Comment hearts are also role=button wrapping an
                        # svg[aria-label='Like'], so an article-wide match on an
                        # already-liked post (main button reads 'Unlike') used to
                        # pick the first comment heart, like a random COMMENT, and
                        # count it as a post like because verification passed off
                        # the already-lit main button.
                        try:
                            if article.find_elements(
                                By.XPATH,
                                ".//section//*[@role='button'][.//*[local-name()='svg' and @aria-label='Unlike']]"
                            ):
                                display_name = article_account[:15] if len(article_account) > 15 else article_account
                                topic_already_liked += 1
                                _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{display_name}] - [already liked]"))
                                continue

                            like_button = WebDriverWait(article, 8).until(
                                EC.presence_of_element_located((
                                    By.XPATH,
                                    ".//section//*[@role='button'][.//*[local-name()='svg' and @aria-label='Like']]"
                                ))
                            )

                            like_status = ""
                            try:
                                like_icon = like_button.find_element(By.XPATH, ".//*[local-name()='svg' and @aria-label='Like']")
                                like_status = like_icon.get_attribute("aria-label") or ""
                            except Exception:
                                like_status = ""

                            if like_status == "Like":
                                display_name = article_account[:15] if len(article_account) > 15 else article_account
                                try:
                                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", like_button)
                                    time.sleep(1)
                                except Exception:
                                    pass

                                clicked = False
                                _marker = limit.mark(driver)
                                try:
                                    hclick(driver, like_button)
                                    clicked = True
                                except Exception:
                                    pass

                                if not clicked:
                                    try:
                                        driver.execute_script("arguments[0].click();", like_button)
                                        clicked = True
                                    except Exception:
                                        pass

                                if clicked:
                                    _unlike_xpath = ".//section//*[@role='button'][.//*[local-name()='svg' and @aria-label='Unlike']]"
                                    try:
                                        WebDriverWait(article, 6).until(
                                            lambda a: len(a.find_elements(By.XPATH, _unlike_xpath)) > 0
                                        )
                                        flipped = True
                                    except Exception:
                                        flipped = False

                                    # Block dialog / rejected request (checked after
                                    # the flip wait — the dialog lands with the API
                                    # response). A hard action limit stops likes for
                                    # the run.
                                    if limit.after_click(driver, "like", _marker, context=f"topic {topic} {post_url}"):
                                        return likes_performed, moduleErrorsLog, moduleWarningsLog

                                    if flipped and limit.like_reverted(article, _unlike_xpath):
                                        # Flipped then snapped back: Instagram rejected
                                        # the like (soft action-limit signal).
                                        debug_line(client_log_line(account, _scope, f"skip @{display_name} reason=like_reverted"))
                                        limit.record_revert("like", context=f"topic {topic} {post_url}")
                                        if limit.is_action_blocked("like")[0]:
                                            return likes_performed, moduleErrorsLog, moduleWarningsLog
                                        hsleep(3, 4)
                                    elif flipped:
                                        likes_performed += 1
                                        count_formatted = f"{likes_performed:02d}"
                                        _p(client_log_line(account, _scope, f"{_lbl}[{count_formatted}/{target_formatted}] - [{display_name}]"))
                                        hsleep(3, 4)   # remainder of the old 6-8s pause (re-check took the rest)
                                    else:
                                        debug_line(client_log_line(account, _scope, f"skip @{display_name} reason=like_state_unchanged"))
                                        if like_diag_reports < _MAX_LIKE_DIAG_REPORTS:
                                            like_diag_reports += 1
                                            try:
                                                unlike_anywhere = len(driver.find_elements(
                                                    By.XPATH, "//*[local-name()='svg' and @aria-label='Unlike']"
                                                ))
                                                report_failure(
                                                    account_id, "topic-search/like-verify", "unchanged",
                                                    {"topic": topic, "post": post_url,
                                                     "unlike_svgs_on_page": unlike_anywhere,
                                                     "article_html": (article.get_attribute("outerHTML") or "")[:2000],
                                                     "diag": capture_failure_context(driver)},
                                                )
                                            except Exception:
                                                pass
                                else:
                                    debug_line(client_log_line(account, _scope, f"skip @{display_name} reason=like_click_failed"))
                            else:
                                if is_bot_debug_enabled():
                                    display_name = article_account[:15] if len(article_account) > 15 else article_account
                                    state_label = like_status if like_status else "no like control"
                                    debug_line(client_log_line(account, _scope, f"already handled @{display_name} topic={topic} state={state_label}"))

                        except (NoSuchElementException, TimeoutException):
                            pass
                        except StaleElementReferenceException:
                            pass

                    except Exception as e:
                        error_type = type(e).__name__
                        msg = str(e).split('\n')[0]
                        debug_line(client_log_line(account, _scope, f"error {error_type}: {msg[:80]}"))
                        moduleErrorsLog += f"like[topics]: {error_type}: {msg}\n"
                        continue
                    finally:
                        if post_opened:
                            _close_open_post(driver, account, results_url)

                # Re-open the search results page and scroll to load more posts if still needed.
                # _close_open_post above already asserts we're back on the keyword grid, so
                # re-running the full search flow here is the rare recovery path, not the norm —
                # doing it unconditionally on every scroll (up to max_scrolls times per topic)
                # used to multiply a single dead topic's failure cost by up to 11x.
                if likes_performed < target_count:
                    _on_results = (
                        "/explore/search/keyword/" in (driver.current_url or "")
                        and len(driver.find_elements(By.XPATH, "//a[contains(@href, '/p/')]")) > 0
                    )
                    if not _on_results:
                        # Anything other than a clean True ends this topic — note
                        # "restricted" is a truthy string, so an `if not` check
                        # here would mistake a mid-topic restriction for success.
                        _refresh = _open_topic_search_results(driver, account, topic, account_id=account_id)
                        if _refresh is not True:
                            if _refresh == "restricted":
                                restricted_topics.append(topic)
                                moduleWarningsLog += (
                                    f"like[topics]: [warning] topic [{topic}] hidden by Instagram "
                                    f"(age-restricted search)\n"
                                )
                            else:
                                moduleErrorsLog += f"like[topics]: [error] could not refresh search results for [{topic}]\n"
                            break
                    hscroll(driver, 900)
                    hsleep(2, 3)
                    scrolls += 1
                else:
                    break

            # Saturation check: how much of this topic's feed was already liked.
            _topic_processed = (likes_performed - topic_likes_at_start) + topic_already_liked
            if _topic_processed >= _TOPIC_SATURATION_MIN_POSTS:
                _sat = topic_already_liked / _topic_processed
                if _sat >= _TOPIC_SATURATION_WARN_RATIO:
                    _sat_pct = round(_sat * 100)
                    _p(client_log_line(account, _scope, f"{_lbl}Warning: topic [{topic}] {_sat_pct}% already liked ({topic_already_liked} of {_topic_processed} posts) - consider topic replacement"))
                    moduleWarningsLog += f"like[topics]: [warning] topic [{topic}] {_sat_pct}% already liked ({topic_already_liked}/{_topic_processed}) - consider topic replacement\n"

        # Final status. When the shortfall is explained entirely by Instagram
        # hiding restricted topics (no genuine errors accumulated), report it
        # as a warning with actionable guidance — the customer fixes this by
        # changing topics or age-verifying the account, not by filing a bug.
        if likes_performed < target_count:
            _p(client_log_line(account, _scope, f"{_lbl}Incomplete[{likes_performed}/{target_count}]"))
            if restricted_topics and not moduleErrorsLog:
                _shortfall = "no" if likes_performed == 0 else "limited"
                moduleWarningsLog += (
                    f"like[topics]: [warning] {_shortfall} topic posts liked "
                    f"({likes_performed}/{target_count}) — {len(restricted_topics)} topic(s) "
                    f"age-restricted by Instagram; change topics or age-verify the account\n"
                )
            else:
                if likes_performed == 0:
                    msg = "[error] no topic posts liked"
                else:
                    msg = "[error] limited topic posts liked"
                moduleErrorsLog += f"like[topics]: {msg} ({likes_performed}/{target_count})\n"
        else:
            _p(client_log_line(account, _scope, f"{_done_lbl}-Completed[{likes_performed}/{target_count}]"))

    except Exception as error:
        noteError = f"do_like_posts_topic catch all: {str(error)}"
        moduleErrorsLog += process_exception(True, noteError, True, False)

    return likes_performed, moduleErrorsLog, moduleWarningsLog
