# burnBot_human.py
"""Human-shaped timing, typing, clicking, and scrolling helpers (Category 2b/2c).

Drop-in replacements for the flat-uniform sleeps, whole-string send_keys,
teleport clicks, and fixed-pixel scrolls that make the bot's *behavior* look
scripted even when its browser identity is honest. Each helper preserves the
caller's tuned range/target and keeps existing fallbacks working:

  time.sleep(random.uniform(a, b))              -> hsleep(a, b)
  element.send_keys(text)                        -> htype(element, text)
  ActionChains(d).move_to_element(e).click(e)... -> hclick(d, e)
  driver.execute_script("window.scrollBy(...)")  -> hscroll(d, px)

No burnBot_* imports here on purpose (avoid import cycles).
"""
import random
import time

from selenium.webdriver.common.action_chains import ActionChains


# --- timing -----------------------------------------------------------------

# Fraction of pauses that get a longer "distraction" tail, so the cadence is
# bursty (human) rather than a tight band around one value (uniform).
_LONG_PAUSE_CHANCE = 0.07


def hdelay(lo, hi):
    """Return a human-shaped duration in seconds for the range [lo, hi].

    Body is triangular (peaked at the midpoint -> same mean as the old
    random.uniform, but not flat); occasionally a longer tail is added.
    """
    if hi < lo:
        lo, hi = hi, lo
    if hi <= lo:
        base = float(lo)
    else:
        base = random.triangular(lo, hi)  # mode defaults to (lo + hi) / 2
    if random.random() < _LONG_PAUSE_CHANCE:
        span = (hi - lo) or max(float(lo), 1.0)
        base += random.uniform(0.8, 1.8) * span + random.uniform(0.4, 1.5)
    return base


def hsleep(lo, hi):
    """Sleep a human-shaped interval. Drop-in for time.sleep(random.uniform(lo,hi))."""
    dur = hdelay(lo, hi)
    time.sleep(dur)
    return dur


# --- typing -----------------------------------------------------------------

def htype(element, text, clear=False):
    """Type text one character at a time with jittered pacing.

    Drop-in for element.send_keys(text). Per-character keystrokes fire the
    input events React search/login boxes expect, and the timing reads human.
    """
    if clear:
        try:
            element.clear()
        except Exception:
            pass
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.triangular(0.04, 0.17))
        if ch == " " and random.random() < 0.15:
            time.sleep(random.uniform(0.15, 0.45))  # brief between-word think


# --- clicking ---------------------------------------------------------------

def hclick(driver, element, settle=True):
    """Approach an element in two moves with hover pauses, then click.

    Drop-in for ActionChains(driver).move_to_element(el).click(el).perform().
    If the multi-step motion throws (e.g. offset out of bounds), it falls back
    to the plain single move+click -- so its floor equals the original code.
    Any exception from that fallback propagates, exactly like the original.
    """
    try:
        actions = ActionChains(driver)
        actions.move_to_element_with_offset(element, 2, 2)
        actions.pause(random.uniform(0.05, 0.18))
        actions.move_to_element(element)
        if settle:
            actions.pause(random.uniform(0.08, 0.30))
        actions.click(element)
        actions.perform()
        return
    except Exception:
        pass
    ActionChains(driver).move_to_element(element).click(element).perform()


def hhover(driver, element):
    """Move to an element with a short settle pause (no click)."""
    ActionChains(driver).move_to_element(element).pause(random.uniform(0.05, 0.20)).perform()


# --- scrolling --------------------------------------------------------------

def hscroll(driver, base_px, jitter=0.35):
    """Scroll roughly base_px, jittered and split into two deltas."""
    total = int(base_px * random.uniform(1 - jitter, 1 + jitter)) or int(base_px)
    first = int(total * random.uniform(0.45, 0.65))
    driver.execute_script("window.scrollBy(0, arguments[0]);", first)
    time.sleep(random.uniform(0.15, 0.5))
    driver.execute_script("window.scrollBy(0, arguments[0]);", total - first)
    return total
