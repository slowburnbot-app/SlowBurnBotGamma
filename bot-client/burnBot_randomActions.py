import time
import random
import builtins as _builtins

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait

from burnBot_human import hsleep, htype, hclick, hhover, hscroll
from burnBot_client_log import client_log_line

_p = _builtins.print  # set per-call by do_random_action; safe because sessions run sequentially


def _human_delay(min_seconds: float, max_seconds: float):
    """
    Sleep a random amount of time between min_seconds and max_seconds.
    Centralized so we can tune overall pacing in one place later.
    """
    try:
        a = float(min_seconds)
        b = float(max_seconds)
        if a < 0:
            a = 0
        if b < a:
            b = a
        hsleep(a, b)
    except Exception:
        # Never break flows due to delay issues
        try:
            time.sleep(1)
        except Exception:
            pass


def do_random_action(driver, account, _print=None):
    global _p
    _p = _print if _print is not None else _builtins.print
    """
    Random Action routine (dispatcher).

    Randomly selects one of the available random actions to perform.
    Add new actions here over time.
    """
    try:
        actions = ["reels", "explore"]
        choice = random.choice(actions)

        if choice == "reels":
            do_random_actions(driver, account, reels_to_watch=random.randint(1, 3))
        else:
            do_random_explore(driver, account, posts_to_click=random.randint(1, 3))
    except Exception:
        # Never break main bot flow due to random action issues
        try:
            _p(client_log_line(account, "random", "warning: dispatcher failed"))
        except Exception:
            pass


def do_random_explore(driver, account, posts_to_click: int | None = None):
    """
    Random Action module: [random][explore]
    - Navigate to Explore page
    - Randomly open 1-3 posts, wait briefly, then close
    """
    try:
        if posts_to_click is None:
            posts_to_click = random.randint(1, 3)
        posts_to_click = max(1, min(3, int(posts_to_click)))
    except Exception:
        posts_to_click = random.randint(1, 3)

    try:
        _p(client_log_line(account, "random", f"explore[post]-Attempting[{posts_to_click:02d}]"))

        driver.get("https://www.instagram.com/explore/")
        WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
        _human_delay(4, 8)

        body = None
        try:
            body = driver.find_element(By.TAG_NAME, "body")
        except Exception:
            body = None

        opened = 0
        seen_hrefs = set()

        # Try a few rounds to find clickable tiles (Explore is dynamic)
        for _ in range(4):
            if opened >= posts_to_click:
                break

            # Explore grid tiles usually link to /p/, /reel/, /tv/
            anchors = driver.find_elements(
                By.CSS_SELECTOR,
                "a[href*='/p/'], a[href*='/reel/'], a[href*='/tv/']",
            )

            # Filter anchors with hrefs we haven't used yet
            candidates = []
            for a in anchors:
                try:
                    href = (a.get_attribute("href") or "").strip()
                    if not href or href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)
                    candidates.append(a)
                except Exception:
                    continue

            if not candidates:
                try:
                    hscroll(driver, 800)
                except Exception:
                    pass
                _human_delay(3, 6)
                continue

            random.shuffle(candidates)

            for a in candidates:
                if opened >= posts_to_click:
                    break

                try:
                    # Scroll into view then click
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", a)
                    except Exception:
                        pass
                    _human_delay(0.8, 2.2)

                    try:
                        hclick(driver, a)
                    except Exception:
                        try:
                            a.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", a)

                    opened += 1
                    # View the post briefly
                    _human_delay(5, 10)

                    # Close the post modal (best-effort)
                    try:
                        close_buttons = driver.find_elements(By.CSS_SELECTOR, "svg[aria-label='Close'], button[aria-label='Close']")
                        if close_buttons:
                            _human_delay(0.4, 1.4)
                            close_buttons[0].click()
                        elif body:
                            body.send_keys(Keys.ESCAPE)
                        else:
                            driver.execute_script("window.history.back();")
                    except Exception:
                        try:
                            if body:
                                body.send_keys(Keys.ESCAPE)
                        except Exception:
                            pass

                    _human_delay(2, 5)

                except Exception:
                    # If a click fails, keep trying other candidates
                    continue

            # Small scroll between rounds
            if opened < posts_to_click:
                try:
                    hscroll(driver, 700)
                except Exception:
                    pass
                _human_delay(3, 6)

        _p(client_log_line(account, "random", f"explore[post]-Completed[{opened}/{posts_to_click}]"))

    except Exception:
        try:
            _p(client_log_line(account, "random", "explore[post]-warning: action failed"))
        except Exception:
            pass


def do_random_actions(driver, account, reels_to_watch: int = 2):
    """
    Random Action module (v1):
    - Navigate to Reels
    - Watch a reel, pause, scroll, watch another reel

    This is intentionally simple so we can add more random actions later.
    """
    try:
        reels_to_watch = max(1, int(reels_to_watch or 1))
    except Exception:
        reels_to_watch = 2

    try:
        _p(client_log_line(account, "random", f"watch[reels]-Attempting[{reels_to_watch:02d}]"))
        reels_watched = 0

        driver.get("https://www.instagram.com/reels/")
        WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
        _human_delay(4, 8)

        body = None
        try:
            body = driver.find_element(By.TAG_NAME, "body")
        except Exception:
            body = None

        for i in range(reels_to_watch):
            # "Watch"
            _human_delay(7, 14)

            # Pause (best-effort)
            try:
                if body:
                    body.send_keys(Keys.SPACE)
            except Exception:
                pass

            _human_delay(2, 5)

            # Unpause (best-effort)
            try:
                if body:
                    body.send_keys(Keys.SPACE)
            except Exception:
                pass

            reels_watched += 1

            # Move to next reel (best-effort)
            if i < reels_to_watch - 1:
                try:
                    if body:
                        body.send_keys(Keys.ARROWDOWN)
                        _human_delay(1.2, 2.8)
                        body.send_keys(Keys.ARROWDOWN)
                    else:
                        hscroll(driver, 900)
                except Exception:
                    try:
                        hscroll(driver, 900)
                    except Exception:
                        pass

                _human_delay(3, 7)

        _p(client_log_line(account, "random", f"watch[reels]-Completed[{reels_watched}/{reels_to_watch}]"))

    except Exception:
        # Random actions should never crash the main follow flow
        try:
            _p(client_log_line(account, "random", "watch[reels]-warning: action failed"))
        except Exception:
            pass


# Backward-compatible alias (older call sites may still use this name)
do_random_reels = do_random_actions

