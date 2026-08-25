# burnBot_login.py

from burnBot_imports import *
from burnBot_human import hsleep, htype, hclick, hhover, hscroll
from burnBot_utils import close_windows, has_internet_connection, process_exception, delay
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line
import burnBot_status as status_store


def _find_browser_window_for_driver(driver):
    """Best-effort: pygetwindow window that matches Selenium driver.title (foreground target for ESC)."""
    try:
        import pygetwindow as gw
    except ImportError:
        return None
    try:
        dt = (driver.title or "").strip()
        if dt:
            for w in gw.getAllWindows():
                if not w.title:
                    continue
                if dt == w.title or dt in w.title or w.title in dt:
                    return w
                head = dt.split(" - ")[0].strip()
                if head and head in w.title:
                    return w
        for needle in ("Instagram", "instagram", "Chrome"):
            wins = gw.getWindowsWithTitle(needle)
            if wins:
                return wins[0]
        for w in gw.getAllWindows():
            if w.title and "chrome" in w.title.lower():
                return w
    except Exception:
        pass
    return None


def os_focus_browser_and_press_escape(presses=8, pause=0.48, context_label="", driver=None):
    """
    Activate the Chrome window at the OS level and send Escape via PyAutoGUI.
    Selenium send_keys often fails for Instagram overlays when the browser is not focused.
    """
    try:
        import pyautogui
        import pygetwindow as gw  # noqa: F401 — used via _find_browser_window_for_driver
    except ImportError:
        debug_line(f"-- DEBUG: [{context_label}] pyautogui/pygetwindow missing; OS-level ESC skipped")
        return False

    win = _find_browser_window_for_driver(driver) if driver else None
    if not win:
        try:
            import pygetwindow as gw
            for needle in ("Instagram", "instagram", "Chrome"):
                wins = gw.getWindowsWithTitle(needle)
                if wins:
                    win = wins[0]
                    break
        except Exception:
            pass
    if not win:
        debug_line(f"-- DEBUG: [{context_label}] No window found to activate for OS ESC")
        return False

    try:
        try:
            if win.isMinimized:
                win.restore()
        except Exception:
            pass
        win.activate()
        time.sleep(0.95)
        for _ in range(presses):
            pyautogui.press("esc")
            time.sleep(pause)
        debug_line(f"-- DEBUG: [login][escape][os] presses={presses} window={win.title!r} ({context_label})")
        return True
    except Exception as e:
        debug_line(f"-- DEBUG: [{context_label}] OS-level ESC error: {e}")
        return False


def press_escape_to_dismiss_overlays(
    driver,
    presses=4,
    pause=0.45,
    context_label="",
    *,
    os_esc_before=False,
    os_esc_after=False,
    os_presses=3,
):
    """
    Dismiss Instagram account / 'Sign in as' sheets. Too many Escape events (especially OS-level
    PyAutoGUI before AND after Selenium) can reload the page and bring the sheet back — use a
    single modest OS burst when needed (os_esc_before), avoid os_esc_after unless necessary.

    os_presses: PyAutoGUI Escape count when os_esc_before/os_esc_after is True.
    presses: Selenium-side rounds (lighter than hammering OS keys repeatedly).
    """
    prefix = f"[{context_label}] " if context_label else ""
    if os_esc_before and os_presses > 0:
        os_focus_browser_and_press_escape(
            os_presses, pause, context_label=f"{context_label}_os", driver=driver
        )

    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    for i in range(presses):
        try:
            driver.execute_script(
                "var b = document.body; if (b) { b.setAttribute('tabindex','-1'); b.focus(); }"
            )
        except Exception:
            pass
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass
        try:
            driver.execute_script(
                """
                (function () {
                  var ev = function (type) {
                    return new KeyboardEvent(type, {
                      key: 'Escape', code: 'Escape', keyCode: 27, which: 27, bubbles: true, cancelable: true
                    });
                  };
                  var t = document.activeElement || document.body;
                  t.dispatchEvent(ev('keydown'));
                  t.dispatchEvent(ev('keyup'));
                })();
                """
            )
        except Exception:
            pass
        time.sleep(pause)

    if os_esc_after and os_presses > 0:
        os_focus_browser_and_press_escape(
            os_presses, pause, context_label=f"{context_label}_os_after", driver=driver
        )

    if presses:
        debug_line(f"-- DEBUG: {prefix}Escape overlay dismissal: {presses} selenium rounds")


def dismiss_browser_dialogs(driver, max_attempts=3, wait_between=0.3):
    """
    Check for and dismiss any browser dialogs (alert/confirm/prompt).
    These dialogs block all Selenium interactions until dismissed.
    
    Args:
        driver: Selenium WebDriver instance
        max_attempts: Maximum number of times to check for dialogs
        wait_between: Seconds to wait between attempts
        
    Returns:
        int: Number of dialogs dismissed
    """
    dialogs_dismissed = 0
    for attempt in range(max_attempts):
        try:
            alert = driver.switch_to.alert
            alert_text = alert.text
            print(client_log_line(None, "login", f"Browser dialog #{dialogs_dismissed + 1} detected: '{alert_text}' — dismissing…"))
            alert.accept()  # Try accepting instead of dismissing
            dialogs_dismissed += 1
            time.sleep(wait_between)
        except Exception:
            # No alert present
            if dialogs_dismissed > 0:
                time.sleep(0.2)  # Brief pause to ensure dialog is fully gone
            break
    
    if dialogs_dismissed > 0:
        print(client_log_line(None, "login", f"Dismissed {dialogs_dismissed} browser dialog(s)"))
    
    return dialogs_dismissed


def dismiss_notifications_prompt(driver, context_label="login"):
    """
    Dismiss Instagram's 'Turn on Notifications' popup by clicking 'Not Now'.
    ESC does not close this dialog — only the button works.
    Safe no-op if the prompt is not present.
    Returns True if dismissed.
    """
    _NOTIFICATION_KEYWORDS = [
        "turn on notifications",
        "enable notifications",
        "allow notifications",
        "get notified",
        "not now",  # the dismiss button itself signals the popup is present
    ]
    try:
        page_source = driver.page_source.lower()
        if not any(kw in page_source for kw in _NOTIFICATION_KEYWORDS):
            return False
    except Exception:
        return False

    debug_line(client_log_line(None, context_label, "notifications prompt detected — looking for 'Not Now'"))

    selectors = [
        # Exact text matches on button
        (By.XPATH, "//button[normalize-space(.)='Not Now']"),
        (By.XPATH, "//button[normalize-space(.)='Not now']"),
        # Partial text on button (handles nested spans)
        (By.XPATH, "//button[contains(normalize-space(string(.)), 'Not Now')]"),
        (By.XPATH, "//button[contains(normalize-space(string(.)), 'Not now')]"),
        # div role=button
        (By.XPATH, "//div[@role='button'][contains(normalize-space(string(.)), 'Not Now')]"),
        (By.XPATH, "//div[@role='button'][contains(normalize-space(string(.)), 'Not now')]"),
        # aria-label
        (By.XPATH, "//*[@aria-label='Not Now' or @aria-label='Not now']"),
        # Span inside any clickable ancestor
        (By.XPATH, "//span[normalize-space(.)='Not Now']/ancestor::*[@role='button' or self::button][1]"),
        (By.XPATH, "//span[normalize-space(.)='Not now']/ancestor::*[@role='button' or self::button][1]"),
    ]

    for sel_type, sel_value in selectors:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((sel_type, sel_value)))
            if not btn.is_displayed():
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.3)
            try:
                btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
            debug_line(client_log_line(None, context_label, "notifications prompt dismissed"))
            time.sleep(1.5)
            return True
        except Exception:
            continue

    debug_line(client_log_line(None, context_label, "notifications prompt found but 'Not Now' not clickable"))
    return False


# Instagram shows this interstitial ("Save your login info?") at /accounts/onetap/
# immediately AFTER a successful login. Its presence is a positive login signal.
INSTAGRAM_ONETAP_URL_FRAGMENT = "/accounts/onetap"

# Markers that identify the logged-in viewer in page_source. Instagram serves the
# username under different keys depending on the surface (feed vs. onetap shell), so
# accept any of them. (Confirmed 2026-08-25: the onetap page carries "PolarisViewer"
# with the username, not the "xdt_viewer" blob the feed uses.)
_LOGGED_IN_USERNAME_MARKERS = (
    '"xdt_viewer":{"user":{"username":"',
    '"PolarisViewer"',
)


def dismiss_save_login_info(driver, context_label="login"):
    """
    Dismiss Instagram's 'Save your login info?' interstitial by clicking 'Not now'.
    Safe no-op if the prompt is not present. Returns True if dismissed.
    """
    try:
        page_source = driver.page_source.lower()
    except Exception:
        return False
    if "save your login info" not in page_source and "save login info" not in page_source:
        return False

    debug_line(client_log_line(None, context_label, "'Save login info' prompt detected — looking for 'Not now'"))
    not_now_selectors = [
        (By.XPATH, "//button[contains(text(), 'Not now') or contains(text(), 'Not Now')]"),
        (By.XPATH, "//div[contains(text(), 'Not now') or contains(text(), 'Not Now')]//ancestor::button"),
        (By.XPATH, "//div[@role='button'][contains(normalize-space(string(.)), 'Not now') or contains(normalize-space(string(.)), 'Not Now')]"),
    ]
    for sel_type, sel_value in not_now_selectors:
        try:
            btn = WebDriverWait(driver, 6).until(EC.element_to_be_clickable((sel_type, sel_value)))
            try:
                btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
            debug_line(client_log_line(None, context_label, "'Save login info' prompt dismissed"))
            time.sleep(2.0)
            return True
        except Exception:
            continue
    debug_line(client_log_line(None, context_label, "'Save login info' prompt found but 'Not now' not clickable"))
    return False


def _extract_logged_in_username(page_source):
    """Return the logged-in username from page_source, or None. Tries the feed's
    xdt_viewer blob first, then any plausible "username":"..." occurrence."""
    marker = '"xdt_viewer":{"user":{"username":"'
    if marker in page_source:
        start = page_source.find(marker) + len(marker)
        end = page_source.find('"', start)
        name = page_source[start:end]
        if name:
            return name
    if '"username":"' in page_source:
        for match in re.findall(r'"username":"([^"]+)"', page_source):
            if len(match) > 3 and match.lower() not in ("sign up", "log in", "instagram"):
                return match
    return None


def enter_sms_code_in_browser(driver, code, account=None):
    """
    Enter a verification/SMS code into the Instagram challenge form already visible in the browser.
    Returns (is_logged_in: bool, username: str | None, errors: str).

    `account` is the handle we're logging in as; when login is confirmed only by the
    post-login URL (the onetap shell doesn't always carry the viewer blob) it's used as
    the returned username so the caller's `account == current_user` check passes.
    """
    moduleErrorsLog = ""
    try:
        # Locate the code input field
        code_input = None
        code_input_selectors = [
            (By.CSS_SELECTOR, "input[autocomplete='one-time-code']"),
            (By.CSS_SELECTOR, "input[name='verificationCode']"),
            (By.CSS_SELECTOR, "input[name='security_code']"),
            (By.CSS_SELECTOR, "input[type='text'][name*='code']"),
            (By.CSS_SELECTOR, "input[type='text'][name*='Code']"),
            (By.CSS_SELECTOR, "input[type='text'][aria-label*='ode']"),
            (By.CSS_SELECTOR, "input[placeholder*='ode']"),
            (By.XPATH, "//input[@type='text']"),
        ]
        for sel_type, sel_value in code_input_selectors:
            try:
                elements = driver.find_elements(sel_type, sel_value)
                for el in elements:
                    if el.is_displayed():
                        code_input = el
                        break
                if code_input:
                    break
            except Exception:
                continue

        if not code_input:
            _log_diag, _print_diag = _capture_page_diag(driver)
            if _print_diag:
                print(client_log_line(None, "login", _print_diag))
            err = "Could not find SMS code input field on page"
            if _log_diag:
                err += f" | login-diag: {_log_diag}"
            return False, None, err

        code_input.click()
        time.sleep(0.4)
        code_input.clear()
        time.sleep(0.3)
        code_input.send_keys(code)
        time.sleep(0.5)

        # Submit — prefer a visible submit button, fall back to Enter key
        submitted = False
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, "button[type='submit']")
            for btn in buttons:
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].click();", btn)
                    submitted = True
                    break
        except Exception:
            pass
        if not submitted:
            code_input.send_keys(Keys.RETURN)

        # Wait for the code to be processed. A correct code leaves the challenge and
        # lands on the "Save your login info?" interstitial (/accounts/onetap/) or the
        # feed. The interstitial is a JS-rendered shell, so poll the URL / a viewer
        # marker rather than trusting a fixed sleep + immediate page_source scrape
        # (that early-scrape false-negative was the 2026-08-25 SMS-login bug).
        def _looks_logged_in(d):
            try:
                if INSTAGRAM_ONETAP_URL_FRAGMENT in d.current_url:
                    return True
                src = d.page_source
            except Exception:
                return False
            # Still on the code-entry form? keep waiting.
            if "enter the code" in src.lower() or "verificationcode" in src.lower():
                return False
            return any(m in src for m in _LOGGED_IN_USERNAME_MARKERS)

        try:
            WebDriverWait(driver, 25).until(_looks_logged_in)
        except Exception:
            pass
        time.sleep(2)

        # Dismiss the post-login interstitials so the account lands on the feed and the
        # session persists. Both are safe no-ops when absent.
        dismiss_save_login_info(driver, context_label="sms_login")
        dismiss_notifications_prompt(driver, context_label="sms_login")

        # Confirm: onetap URL alone proves login; otherwise scrape the viewer username.
        try:
            on_onetap = INSTAGRAM_ONETAP_URL_FRAGMENT in driver.current_url
        except Exception:
            on_onetap = False
        page_source = driver.page_source
        username = _extract_logged_in_username(page_source)
        if username:
            return True, username, moduleErrorsLog
        if on_onetap:
            # Logged in (onetap only shows post-auth) but viewer blob not in this shell.
            return True, account, moduleErrorsLog

        moduleErrorsLog += "Code submitted but login not confirmed"
        return False, None, moduleErrorsLog

    except Exception as e:
        return False, None, f"enter_sms_code_in_browser error: {e}"


INSTAGRAM_ACCOUNTS_LOGIN = "https://www.instagram.com/accounts/login/"


def navigate_to_instagram_login_if_needed(driver, *, long_initial_settle=True):
    """
    Open the credential login URL only when not already there — avoids an extra full
    reload when check_login and do_login run back-to-back on the same tab.
    Returns True if driver.get() was skipped (already on /accounts/login/).
    """
    try:
        u = (driver.current_url or "").lower()
    except Exception:
        u = ""
    if "instagram.com/accounts/login" in u:
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass
        time.sleep(2.0 if long_initial_settle else 1.0)
        debug_line(client_log_line(None, "login", "already on /accounts/login/ — skipped driver.get()"))
        return True
    driver.get(INSTAGRAM_ACCOUNTS_LOGIN)
    WebDriverWait(driver, 22).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    WebDriverWait(driver, 22).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(5.0 if long_initial_settle else 2.5)
    return False


def submit_instagram_credentials(driver, password_input, log_name="login"):
    """
    Submit the login form. IG often nests the label in a child (e.g. <button><div>Log in</div>),
    so contains(text(),...) on the button fails; prefer real submit buttons + JS click.
    """
    time.sleep(0.45)
    try:
        password_input.send_keys(Keys.TAB)
        time.sleep(0.4)
    except Exception:
        pass
    try:
        WebDriverWait(driver, 12).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "button[type='submit']"))
        )
    except Exception:
        pass
    time.sleep(0.35)

    def _disabled(btn):
        try:
            v = btn.get_attribute("disabled")
            return v is not None and str(v).lower() in ("true", "disabled")
        except Exception:
            return False

    try:
        buttons = driver.find_elements(By.CSS_SELECTOR, "button[type='submit']")
    except Exception:
        buttons = []
    for btn in buttons:
        try:
            if not btn.is_displayed() or _disabled(btn):
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});", btn
            )
            time.sleep(0.45)
            try:
                WebDriverWait(driver, 10).until(lambda d: btn.is_enabled())
            except Exception:
                pass
            try:
                btn.click()
                debug_line(client_log_line(log_name, "login", "debug submit: native click (type=submit)"))
                return True
            except Exception:
                driver.execute_script("arguments[0].click();", btn)
                debug_line(client_log_line(log_name, "login", "debug submit: JS click (type=submit)"))
                return True
        except Exception:
            continue

    tr = "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'"
    xpath_buttons = [
        f"//button[.//span[contains(translate(normalize-space(string(.)), {tr}), 'log in')]]",
        f"//button[contains(translate(normalize-space(string(.)), {tr}), 'log in')]",
        f"//div[@role='button'][.//span[contains(translate(normalize-space(string(.)), {tr}), 'log in')]]",
    ]
    for xp in xpath_buttons:
        try:
            btn = WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.XPATH, xp)))
            if not btn.is_displayed() or _disabled(btn):
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});", btn
            )
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", btn)
            debug_line(client_log_line(log_name, "login", "debug submit: xpath / role=button"))
            return True
        except Exception:
            continue

    return False


def dismiss_instagram_account_picker(driver, context_label="login", max_passes=4):
    """
    Instagram often shows an account sheet before the password form (e.g. 'Continue as @user',
    'Sign in as', saved session). Click through to manual / credential login when those controls exist.
    Safe no-op when the sheet is not present.
    """
    tr = "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'"
    # Prefer exact labels first (less false positives than substring on huge containers)
    exact_labels = [
        "Use another account",
        "Log in to an existing account",
        "Log in to existing account",
        "Log into another account",
        "Switch accounts",
        "Not you?",
    ]
    substring_hits = [
        "use another account",
        "log in to an existing account",
        "log into another account",
    ]

    def _click_el(el):
        try:
            clickable = driver.execute_script(
                """
                var n = arguments[0];
                for (var i = 0; i < 10 && n; i++) {
                    var role = n.getAttribute && n.getAttribute('role');
                    var tag = n.tagName;
                    if ((tag === 'BUTTON' || tag === 'A' || role === 'button' || role === 'menuitem') && typeof n.click === 'function')
                        return n;
                    n = n.parentElement;
                }
                return arguments[0];
                """,
                el,
            )
            driver.execute_script("arguments[0].click();", clickable)
            return True
        except Exception:
            try:
                el.click()
                return True
            except Exception:
                return False

    clicked_any = False
    for _ in range(max_passes):
        found = False
        for label in exact_labels:
            esc = label.replace("'", "\\'")
            for tag in ("span", "div", "button"):
                xp = (
                    f"//{tag}[normalize-space(.)='{esc}']/ancestor::*["
                    f"@role='button' or self::button or self::a][1]"
                )
                try:
                    els = driver.find_elements(By.XPATH, xp)
                    for el in els:
                        if el.is_displayed() and _click_el(el):
                            print(client_log_line(None, "login", f"{context_label} account picker: clicked {label!r}"))
                            time.sleep(1.6)
                            found = True
                            clicked_any = True
                            break
                    if found:
                        break
                except Exception:
                    continue
            if found:
                break

        if not found:
            for sub in substring_hits:
                xp = (
                    f"//*[self::span or self::div or self::button]"
                    f"[contains(translate(normalize-space(string(.)), {tr}), '{sub}')]"
                )
                try:
                    for el in driver.find_elements(By.XPATH, xp):
                        if not el.is_displayed():
                            continue
                        if _click_el(el):
                            print(client_log_line(None, "login", f"{context_label} account picker: matched {sub!r}"))
                            time.sleep(1.6)
                            found = True
                            clicked_any = True
                            break
                    if found:
                        break
                except Exception:
                    continue

        if not found:
            for xp in (
                "//div[@role='dialog']//*[@aria-label='Close' or @aria-label='close']",
                "//*[@role='dialog']//svg[@aria-label='Close']/ancestor::*[@role='button'][1]",
            ):
                try:
                    for el in driver.find_elements(By.XPATH, xp):
                        if el.is_displayed() and _click_el(el):
                            print(client_log_line(None, "login", f"{context_label} account picker: closed via dialog dismiss control"))
                            time.sleep(1.4)
                            found = True
                            clicked_any = True
                            break
                    if found:
                        break
                except Exception:
                    continue

        if not found:
            break

    # Light Selenium Escape only (OS burst happens in check_login / do_login — avoids double-reload)
    press_escape_to_dismiss_overlays(
        driver, presses=2, pause=0.35, context_label=context_label, os_esc_before=False, os_esc_after=False
    )

    return clicked_any


def _capture_page_diag(driver):
    """
    Capture page URL, visible button texts, role-button/link texts, iframe count,
    body text, and a page source snippet for element-not-found diagnostics.
    Returns (log_str, print_str).
    log_str is compact (URL + element counts); print_str also includes body/source.

    Relocated to burnBot_run_log.capture_page_diag (shared with the general
    failure-artifact capture path) — kept as a private alias here so the
    existing call sites below are untouched.
    """
    from burnBot_run_log import capture_page_diag
    return capture_page_diag(driver)


def dismiss_instagram_cookie_consent(driver, context_label="login"):
    """
    Click 'Allow all cookies' on Instagram's GDPR cookie consent overlay.
    This dialog appears on Linux/fresh profiles before the login form renders,
    blocking all input fields.

    Improvements over the original one-shot version:
    - Waits for the page to actually mount something (React needs a moment) before
      searching, so we don't search an empty DOM and silently give up.
    - Searches standard tags (button/div/span) AND [role=button] elements, which
      Meta uses for some consent dialogs.
    - Recurses one level into nested iframes (not just top-level frames).
    - Retries up to 3 passes; after each successful click waits to verify the login
      form appeared — handles multi-step consent flows.
    Safe no-op when not present.
    """
    accept_labels = [
        "Allow all cookies",
        "Allow All",
        "Accept All",
        "Allow essential and optional cookies",
        "Allow",
    ]
    tr = "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'"

    def _click_el(el):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception:
            try:
                el.click()
                return True
            except Exception:
                return False

    def _search_and_click():
        """Search the current frame context for any consent accept button and click it."""
        for label in accept_labels:
            esc = label.replace("'", "\\'")
            label_lower = label.lower()
            # Standard HTML elements
            for tag in ("button", "div", "span"):
                xp = (
                    f"//{tag}[normalize-space(.)='{esc}' or "
                    f"contains(translate(normalize-space(string(.)), {tr}), '{label_lower}')]"
                )
                try:
                    els = driver.find_elements(By.XPATH, xp)
                    for el in els:
                        if el.is_displayed() and _click_el(el):
                            print(client_log_line(None, "login", f"{context_label} cookie consent: clicked '{label}'"))
                            time.sleep(1.8)
                            return True
                except Exception:
                    continue
            # role=button elements (Meta uses div[role=button] for some consent UIs)
            xp_role = (
                f"//*[@role='button' and ("
                f"normalize-space(.)='{esc}' or "
                f"contains(translate(normalize-space(string(.)), {tr}), '{label_lower}'))]"
            )
            try:
                els = driver.find_elements(By.XPATH, xp_role)
                for el in els:
                    if el.is_displayed() and _click_el(el):
                        print(client_log_line(None, "login", f"{context_label} cookie consent: clicked '{label}' (role=button)"))
                        time.sleep(1.8)
                        return True
            except Exception:
                pass
        return False

    def _login_form_visible():
        try:
            return driver.find_element(By.CSS_SELECTOR, "input[name='username']").is_displayed()
        except Exception:
            return False

    def _search_in_context():
        """Search main document + all top-level iframes (one level of nesting inside each)."""
        if _search_and_click():
            return True
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for frame in iframes:
                found = False
                try:
                    driver.switch_to.frame(frame)
                    found = _search_and_click()
                    if not found:
                        # Recurse one level into nested frames
                        try:
                            nested_frames = driver.find_elements(By.TAG_NAME, "iframe")
                            for nested_frame in nested_frames:
                                try:
                                    driver.switch_to.frame(nested_frame)
                                    found = _search_and_click()
                                except Exception:
                                    found = False
                                finally:
                                    try:
                                        driver.switch_to.parent_frame()
                                    except Exception:
                                        pass
                                if found:
                                    break
                        except Exception:
                            pass
                except Exception:
                    found = False
                finally:
                    try:
                        driver.switch_to.default_content()
                    except Exception:
                        pass
                if found:
                    return True
        except Exception:
            pass
        return False

    # Wait for the page to mount something before searching (React takes a moment).
    # Return immediately if the login form is already visible — no consent to dismiss.
    try:
        WebDriverWait(driver, 8).until(lambda d: (
            _login_form_visible()
            or bool(d.find_elements(By.TAG_NAME, "button"))
            or bool(d.find_elements(By.XPATH, "//*[@role='button']"))
        ))
    except Exception:
        pass  # Timed out waiting — proceed anyway

    if _login_form_visible():
        return True  # Login form already visible; nothing to dismiss

    # Up to 3 passes: click a consent button → verify the login form appeared.
    # This handles multi-step consent flows or cases where a second consent layer
    # mounts after the first is dismissed.
    for _attempt in range(3):
        if _search_in_context():
            # Give the page a moment to react to the consent click
            time.sleep(0.5)
            try:
                WebDriverWait(driver, 5).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, "input[name='username']"))
                )
                return True  # Login form visible — consent fully dismissed
            except Exception:
                pass  # Form not yet visible; try another consent pass
        else:
            break  # No consent button found in any context

    return _login_form_visible()


def check_phone_verification(driver):
    """
    Check if Instagram is asking for phone verification code.
    
    Args:
        driver: Selenium WebDriver instance
        
    Returns:
        tuple: (is_verification_required, verification_text)
    """
    try:
        page_source = driver.page_source.lower()
        current_url = driver.current_url.lower()
        
        # Check for phone verification indicators in page source
        verification_indicators = [
            "enter the code",
            "code sent to",
            "verification code",
            "confirm it's you",
            "confirm that it's you",
            "security code",
            "phone verification",
            "enter confirmation code",
            "we sent a code",
            "check your phone",
            "enter security code",
            "authentication code",
            "we'll send you a code",
            "suspicious activity",
            "unusual activity",
            "help us confirm it"
        ]
        
        # Check page source for verification text
        for indicator in verification_indicators:
            if indicator in page_source:
                debug_line(f"-- DEBUG: Verification indicator found: '{indicator}'")
                return True, indicator
        
        # Check URL patterns for verification/challenge
        url_patterns = ["challenge", "accounts/confirm", "two_factor", "checkpoint"]
        for pattern in url_patterns:
            if pattern in current_url:
                debug_line(f"-- DEBUG: Verification URL pattern found: '{pattern}'")
                return True, f"challenge_url_{pattern}"
        
        # Check for code input field (various selectors)
        try:
            code_input_selectors = [
                "input[type='text'][name*='code']",
                "input[type='text'][name*='Code']",
                "input[type='text'][aria-label*='code']",
                "input[type='text'][aria-label*='Code']",
                "input[placeholder*='code']",
                "input[placeholder*='Code']",
                "input[name='verificationCode']",
                "input[name='security_code']",
                "input[autocomplete='one-time-code']"
            ]
            
            for selector in code_input_selectors:
                try:
                    code_inputs = driver.find_elements(By.CSS_SELECTOR, selector)
                    if code_inputs and len(code_inputs) > 0:
                        # Verify the input is visible (not hidden)
                        for code_input in code_inputs:
                            if code_input.is_displayed():
                                debug_line(f"-- DEBUG: Verification code input found with selector: '{selector}'")
                                return True, "code_input_field"
                except:
                    continue
        except:
            pass
        
        # Check for specific verification-related button text
        try:
            verification_button_text = ["send code", "get code", "request code", "resend code"]
            for btn_text in verification_button_text:
                if btn_text in page_source:
                    debug_line(f"-- DEBUG: Verification button text found: '{btn_text}'")
                    return True, f"button_{btn_text}"
        except:
            pass
        
        return False, None
    
    except Exception as e:
        # If we can't check, assume no verification needed
        debug_line(f"-- DEBUG: Error checking phone verification: {str(e)}")
        return False, None


def check_login(driver, account=None):
    _acct = account or "login"
    moduleErrorsLog = ""
    try:
        # Check if driver is still connected before attempting operations
        try:
            driver.current_url  # Quick check to see if driver is still connected
        except Exception:
            error_msg = "Driver connection lost - cannot check login status"
            moduleErrorsLog += error_msg
            return False, None, moduleErrorsLog
        
        # Same URL as do_login — avoids a second full page load when check then attempts credentials
        navigate_to_instagram_login_if_needed(driver, long_initial_settle=True)
        time.sleep(1.0)
        # One OS Escape burst + few Selenium rounds; repeated OS ESC was reloading IG and re-showing the sheet
        press_escape_to_dismiss_overlays(
            driver,
            presses=4,
            pause=0.45,
            context_label="check_login",
            os_esc_before=True,
            os_esc_after=False,
            os_presses=3,
        )
        dismiss_instagram_account_picker(driver, context_label="check_login")
        dismiss_instagram_cookie_consent(driver, context_label="check_login")
        press_escape_to_dismiss_overlays(
            driver, presses=2, pause=0.35, context_label="check_login_light", os_esc_before=False
        )
        time.sleep(0.8)

        # Check for phone verification first
        is_verification_required, verification_reason = check_phone_verification(driver)
        if is_verification_required:
            error_msg = f"PHONE VERIFICATION REQUIRED - Reason: {verification_reason}"
            moduleErrorsLog += error_msg
            return "VERIFICATION_REQUIRED", None, moduleErrorsLog
        
        try:  # Method 1 — login form: wait for the username field
            press_escape_to_dismiss_overlays(
                driver, presses=1, pause=0.35, context_label="check_login_pre_username", os_esc_before=False
            )
            WebDriverWait(driver, 12).until(EC.presence_of_element_located((By.NAME, "username")))
            login_fields = driver.find_elements(By.NAME, "username")
            if login_fields:
                print(client_log_line(_acct, "login", "check: login form detected"))
                return False, None, moduleErrorsLog
        except TimeoutException:
            pass  # Username field not found — possibly logged in
        
        try:  # Method 4 (moved up) — positive check: look for username in page source JSON
            page_source = driver.page_source
            
            # Search for the xdt_viewer.user.username pattern in JSON
            if '"xdt_viewer":{"user":{"username":"' in page_source:
                start = page_source.find('"xdt_viewer":{"user":{"username":"') + len(
                    '"xdt_viewer":{"user":{"username":"')
                end = page_source.find('"', start)
                username = page_source[start:end]
                if username:
                    return True, username, moduleErrorsLog
            
            # Fallback: search for viewerId username pattern
            if '"username":"' in page_source and '"id":"' in page_source:
                import re
                matches = re.findall(r'"username":"([^"]+)"', page_source)
                if matches:
                    for match in matches:
                        if len(match) > 3 and match.lower() not in ["sign up", "log in"]:
                            return True, match, moduleErrorsLog
        
        except:
            pass
        
        try:  # Method 3 (fallback) — login indicator text; only strings that don't appear on logged-in pages
            login_indicators = ["Trouble logging in?", "Forgot password?",
                                "Log in to Instagram", "Save login info"]
            WebDriverWait(driver, 12).until(
                lambda d: any(text.lower() in d.page_source.lower() for text in login_indicators)
            )
            print(client_log_line(_acct, "login", "check: login indicators detected"))
            return False, None, moduleErrorsLog
        except:
            pass
        
        return False, None, moduleErrorsLog
    
    except Exception as error:  ### catch all errors
        noteError = "check_login catch all"
        printError = True
        logError = True
        debugError = False
        moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        return False, None, moduleErrorsLog


def do_login(driver, username, password, apiClient=None):
    """
    Attempt to log in to Instagram

    Args:
        driver: Selenium WebDriver instance
        username: Instagram username
        password: Instagram password

    Returns:
        tuple: (is_logged_in, current_user, errors)
    """
    moduleErrorsLog = ""
    
    try:
        # Check if driver is still connected before attempting operations
        try:
            driver.current_url  # Quick check to see if driver is still connected
        except Exception:
            error_msg = "Driver connection lost - cannot perform login"
            moduleErrorsLog += error_msg
            return False, None, moduleErrorsLog
        
        # Navigate only if not already on /accounts/login/ (check_login usually left us there)
        try:
            skipped_reload = navigate_to_instagram_login_if_needed(driver, long_initial_settle=True)
            # check_login already dismissed the account sheet; skip Escape here to avoid extra reloads
            if not skipped_reload:
                press_escape_to_dismiss_overlays(
                    driver,
                    presses=4,
                    pause=0.45,
                    context_label="do_login_post_nav",
                    os_esc_before=True,
                    os_esc_after=False,
                    os_presses=3,
                )
            dismiss_instagram_account_picker(driver, context_label="do_login")
            
            debug_line(f"-- DEBUG: Login page ready, current URL: {driver.current_url}")
            
        except Exception as error:
            noteError = f"Error navigating to login page: {str(error)}"
            printError = True
            logError = True
            debugError = False
            moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        
        # Enter username and password
        try:
            time.sleep(1.5)  # Wait for page to stabilize
            
            # OS Escape already ran after /accounts/login/ load; more PyAutoGUI ESC here tended to reload IG.
            press_escape_to_dismiss_overlays(
                driver, presses=3, pause=0.4, context_label="do_login_before_fields", os_esc_before=False
            )
            time.sleep(0.4)
            
            dismiss_instagram_account_picker(driver, context_label="do_login_pre_fields")
            dismiss_instagram_cookie_consent(driver, context_label="do_login_pre_fields")

            # If the login form still hasn't appeared after consent dismissal, do one hard reload
            # and retry — handles the case where the consent wall was in an iframe that prevented
            # the React login form from mounting in the first place.
            try:
                WebDriverWait(driver, 5).until(
                    EC.visibility_of_element_located((By.NAME, "username"))
                )
            except Exception:
                print(client_log_line(None, "login", "username field not visible after consent dismiss — reloading login page"))
                try:
                    driver.get(INSTAGRAM_ACCOUNTS_LOGIN)
                    WebDriverWait(driver, 22).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )
                    time.sleep(4.0)
                    dismiss_instagram_account_picker(driver, context_label="do_login_reload")
                    dismiss_instagram_cookie_consent(driver, context_label="do_login_reload")
                    time.sleep(1.0)
                except Exception:
                    pass

            # Check current URL after ESC to see if page changed
            try:
                current_url = driver.current_url
                debug_line(f"-- DEBUG: Current URL after ESC: {current_url}")
            except Exception as e:
                print(client_log_line(None, "login", f"Could not get URL: {e}"))
            
            # Prefer name=username (avoids matching phone / other text fields).
            # Use element_to_be_clickable (visible + enabled) so a field hidden behind
            # a consent overlay doesn't pass the wait and then fail on .click().
            # 8s per selector (was 18s): worst-case ~32s, still covers slow page loads.
            loginUsername = None
            username_selectors = [
                (By.CSS_SELECTOR, "input[name='username']"),
                (By.CSS_SELECTOR, "input[type='text'][autocomplete='username']"),
                (By.CSS_SELECTOR, "input[type='text']"),
                (By.XPATH, "//input[@type='text']"),
            ]

            for selector_type, selector_value in username_selectors:
                try:
                    loginUsername = WebDriverWait(driver, 8).until(
                        EC.element_to_be_clickable((selector_type, selector_value))
                    )
                    break
                except Exception:
                    continue

            if not loginUsername:
                # One last consent-dismissal attempt before giving up — handles the case
                # where the reload brought up a fresh consent wall.
                dismiss_instagram_cookie_consent(driver, context_label="do_login_last_chance")
                time.sleep(1.0)
                for selector_type, selector_value in username_selectors:
                    try:
                        loginUsername = WebDriverWait(driver, 6).until(
                            EC.element_to_be_clickable((selector_type, selector_value))
                        )
                        break
                    except Exception:
                        continue

            if not loginUsername:
                _log_diag, _print_diag = _capture_page_diag(driver)
                if _print_diag:
                    print(client_log_line(None, "login", _print_diag))
                if _log_diag:
                    moduleErrorsLog += f" | login-diag: {_log_diag}"
                raise Exception("Could not find username input field")
            
            # Clear and enter username
            loginUsername.click()
            time.sleep(0.2)

            # Check for dialogs after clicking (clicking can trigger dialogs)
            if dismiss_browser_dialogs(driver, max_attempts=2):
                # Re-click the field after dismissing dialog
                loginUsername.click()
                time.sleep(0.2)

            loginUsername.clear()
            time.sleep(0.2)
            htype(loginUsername, username)
            time.sleep(0.3)
            
            debug_line(f"-- DEBUG: Username entered successfully")
            
            # Find password field — use element_to_be_clickable so an overlay-obscured
            # field doesn't pass the wait. 8s per selector matches the username timeout.
            loginPassword = None
            password_selectors = [
                (By.CSS_SELECTOR, "input[type='password']"),
                (By.CSS_SELECTOR, "input[name='password']"),
                (By.XPATH, "//input[@type='password']")
            ]

            for selector_type, selector_value in password_selectors:
                try:
                    loginPassword = WebDriverWait(driver, 8).until(
                        EC.element_to_be_clickable((selector_type, selector_value))
                    )
                    break
                except Exception:
                    continue

            if not loginPassword:
                _log_diag, _print_diag = _capture_page_diag(driver)
                if _print_diag:
                    print(client_log_line(None, "login", _print_diag))
                if _log_diag:
                    moduleErrorsLog += f" | login-diag: {_log_diag}"
                raise Exception("Could not find password input field")
            
            # Clear and enter password
            loginPassword.click()
            time.sleep(0.2)

            # Check for dialogs after clicking password field
            if dismiss_browser_dialogs(driver, max_attempts=2):
                # Re-click the field after dismissing dialog
                loginPassword.click()
                time.sleep(0.2)

            loginPassword.clear()
            time.sleep(0.2)
            htype(loginPassword, password)
            time.sleep(0.3)
            
            debug_line(f"-- DEBUG: Password entered successfully")
            
            login_submitted = submit_instagram_credentials(driver, loginPassword, log_name=username)
            if not login_submitted:
                debug_line(f"-- DEBUG: [{username}] submit click failed, sending Return on password field")
                time.sleep(0.5)
                try:
                    loginPassword.send_keys(Keys.RETURN)
                except Exception:
                    pass
            
            # Wait for page to load after login attempt
            WebDriverWait(driver, 28).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            time.sleep(7)  # Give extra time for Instagram to process login
            
            # Immediately check for verification after submitting credentials
            debug_line(f"-- DEBUG: Checking for verification prompt after login submission...")
            is_verification, verify_reason = check_phone_verification(driver)
            if is_verification:
                debug_line(f"-- DEBUG: Verification detected immediately after login: {verify_reason}")
                error_msg = f"PHONE VERIFICATION REQUIRED - Reason: {verify_reason}"
                moduleErrorsLog += error_msg
                return "VERIFICATION_REQUIRED", None, moduleErrorsLog
        
        except Exception as error:
            noteError = f"Error entering credentials: {str(error)}"
            printError = True
            logError = True
            debugError = False
            moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
            
            # ALWAYS print error details for login failures (not just in debug mode)
            try:
                current_url = driver.current_url
                print(client_log_line(username, "login", f"error url: {current_url}"))
                print(client_log_line(username, "login", f"error {str(error)}"))
            except Exception as url_error:
                print(client_log_line(username, "login", f"error {str(error)} (couldn't get URL: {url_error})"))
            
            return False, None, moduleErrorsLog
        
        # Check results of login attempt
        try:
            # Wait for page to fully load
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            time.sleep(4.5)
            
            # Handle "Save login info" prompt if it appears after successful login
            dismiss_save_login_info(driver, context_label="do_login")

            # Handle notifications prompt if it appears
            dismiss_notifications_prompt(driver, context_label="do_login")
            
            # Check again for verification (in case it appeared after page load)
            is_verification_required, verification_reason = check_phone_verification(driver)
            if is_verification_required:
                error_msg = f"PHONE VERIFICATION REQUIRED - Reason: {verification_reason}"
                moduleErrorsLog += error_msg
                return "VERIFICATION_REQUIRED", None, moduleErrorsLog

            # Detect reCAPTCHA / bot-challenge page — not caught by check_phone_verification
            try:
                _cur_url = driver.current_url
                if "auth_platform/recaptcha" in _cur_url:
                    _log_diag, _print_diag = _capture_page_diag(driver)

                    # Read noVNC URL from config (set by customer in INI)
                    try:
                        novnc_url = CONFIG.get('browser-session', 'novnc_url', fallback='http://localhost:6080/vnc.html')
                    except Exception:
                        novnc_url = 'http://localhost:6080/vnc.html'

                    print(client_log_line(username, "login", f"CAPTCHA challenge detected — open {novnc_url} to solve"))

                    # Send notification so customer knows action is required
                    try:
                        from burnBot_notifications import send_captcha_challenge_alert
                        send_captcha_challenge_alert(username, novnc_url, apiClient=apiClient, _print=print)
                    except Exception:
                        pass

                    # Pause bot thread — TUI shows short prompt in input field
                    status_store.request_operator_input("Type 'done' after solving CAPTCHA:")

                    # Verify page state after operator confirms — don't navigate, check in place
                    try:
                        WebDriverWait(driver, 10).until(
                            lambda d: d.execute_script("return document.readyState") == "complete"
                        )
                        _post_url = driver.current_url
                        _post_src = driver.page_source
                        if "auth_platform/recaptcha" not in _post_url and "accounts/login" not in _post_url:
                            if '"xdt_viewer":{"user":{"username":"' in _post_src:
                                _s = _post_src.find('"xdt_viewer":{"user":{"username":"') + len('"xdt_viewer":{"user":{"username":"')
                                _e = _post_src.find('"', _s)
                                _solved_user = _post_src[_s:_e]
                                if _solved_user:
                                    print(client_log_line(username, "login", f"CAPTCHA solved — logged in as {_solved_user}"))
                                    return True, _solved_user, moduleErrorsLog
                    except Exception:
                        pass

                    err = "Instagram CAPTCHA challenge — not resolved after operator input"
                    if _log_diag:
                        err += f" | login-diag: {_log_diag}"
                    moduleErrorsLog += err
                    return False, None, moduleErrorsLog
            except Exception:
                pass

            # Look for username in page source JSON data
            page_source = driver.page_source
            
            # Search for the xdt_viewer.user.username pattern in JSON
            if '"xdt_viewer":{"user":{"username":"' in page_source:
                start = page_source.find('"xdt_viewer":{"user":{"username":"') + len(
                    '"xdt_viewer":{"user":{"username":"')
                end = page_source.find('"', start)
                logged_in_user = page_source[start:end]
                if logged_in_user:
                    return True, logged_in_user, moduleErrorsLog
            
            # Fallback: check for other username patterns
            if '"username":"' in page_source:
                import re
                matches = re.findall(r'"username":"([^"]+)"', page_source)
                if matches:
                    # Filter out common non-username matches
                    for match in matches:
                        if len(match) > 3 and match.lower() not in ["sign up", "log in", "instagram"]:
                            return True, match, moduleErrorsLog
            
            # Check if we're still on login page (login failed)
            if any(text in page_source.lower() for text in ["log in to instagram", "trouble logging in", "forgot password"]):
                _log_diag, _print_diag = _capture_page_diag(driver)
                if _print_diag:
                    print(client_log_line(username, "login", _print_diag))
                err = "Still on login page after login attempt - login likely failed"
                if _log_diag:
                    err += f" | login-diag: {_log_diag}"
                moduleErrorsLog += err
                return False, None, moduleErrorsLog

        except Exception as error:
            noteError = f"Error verifying login: {str(error)}"
            printError = False
            logError = True
            debugError = False
            moduleErrorsLog += process_exception(printError, noteError, logError, debugError)

        # Fallback: login result unrecognised — capture page state for diagnostics
        _log_diag, _print_diag = _capture_page_diag(driver)
        if _print_diag:
            print(client_log_line(username, "login", _print_diag))
        if _log_diag:
            moduleErrorsLog += f"login result unrecognised | login-diag: {_log_diag}"
        return False, None, moduleErrorsLog
    
    except Exception as error:  ### catch all errors
        noteError = f"do_login catch all: {str(error)}"
        printError = True
        logError = True
        debugError = False
        moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        return False, None, moduleErrorsLog


def switch_login(driver, targetAccount):
    moduleErrorsLog = ""
    try:
        try:
            ## find and click the "More" button - handle both expanded and collapsed states
            try:
                # Try to find the Settings SVG and click its parent anchor tag
                moreButton = WebDriverWait(driver, 8).until(
                    EC.element_to_be_clickable((By.XPATH, "//svg[@aria-label='Settings']/ancestor::a"))
                )
            except:
                # Try expanded state (text visible)
                moreButton = WebDriverWait(driver, 8).until(
                    EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'More')]/ancestor::a"))
                )
            
            moreButton.click()
            time.sleep(3)
            
            ## wait for and click the "Switch accounts" option in the menu
            switchLink = WebDriverWait(driver, 14).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Switch accounts')]"))
            )
            switchLink.click()
            
            hsleep(4, 7)
        except (NoSuchElementException, StaleElementReferenceException, TimeoutException) as e:
            moduleErrorsLog += f"cant find switch account link: {str(e)}"
            return False, None, moduleErrorsLog
        
        try:
            wait = WebDriverWait(driver, 14)
            
            # 1) Use the newest switcher container (menu/dialog/listbox)
            container_xp = "(//div[@role='menu' or @role='dialog' or @role='listbox'])[last()]"
            menu_container = wait.until(EC.visibility_of_element_located((By.XPATH, container_xp)))
            
            # 2) Find the exact account label INSIDE that container (prefer <span>, fall back to <div>)
            try:
                name_el = menu_container.find_element(By.XPATH, f".//span[normalize-space(text())='{targetAccount}']")
            except Exception:
                name_el = menu_container.find_element(By.XPATH, f".//div[normalize-space(text())='{targetAccount}']")
            
            # 3) Scroll the label into the dialog's view (no window scrolling)
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'nearest'});", name_el)
            
            # 4) Click the nearest clickable ancestor (anchors/buttons/role-rows)
            clickable = driver.execute_script("""
                const el = arguments[0];
                return el.closest('a,button,[role="menuitem"],[role="option"],[role="menuitemradio"],[role="button"]') || el;
            """, name_el)
            
            # Make sure that specific element is visible, then click
            wait.until(EC.visibility_of(clickable))
            try:
                clickable.click()
            except Exception:
                # Fallback: JS click avoids coordinate hit-tests
                driver.execute_script("arguments[0].click();", clickable)
            
            time.sleep(6.5)
        
        
        except (NoSuchElementException, StaleElementReferenceException, TimeoutException) as e:
            moduleErrorsLog += f"Target account '{targetAccount}' not found in switch list: {str(e)}"
            return False, None, moduleErrorsLog
        
        #### check results of switch attempt
        WebDriverWait(driver, 20).until(lambda d: d.execute_script("return document.readyState") == "complete")
        time.sleep(4.5)
        dismiss_notifications_prompt(driver, context_label="switch_login")
        
        # Extract username from page source JSON data
        try:
            page_source = driver.page_source
            
            if '"xdt_viewer":{"user":{"username":"' in page_source:
                start = page_source.find('"xdt_viewer":{"user":{"username":"') + len(
                    '"xdt_viewer":{"user":{"username":"')
                end = page_source.find('"', start)
                current_ActiveAccount = page_source[start:end]
                
                if current_ActiveAccount and targetAccount.lower() == current_ActiveAccount.lower():
                    return True, current_ActiveAccount, moduleErrorsLog
        except:
            pass
        
        moduleErrorsLog += f"Switch verification failed. Expected: {targetAccount}"
        return False, None, moduleErrorsLog
    
    except Exception as error:  ### catch all errors
        noteError = "switch_login catch all"
        printError = True
        logError = True
        debugError = True
        moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        return False, None, moduleErrorsLog


def handle_account_login(driver, account, accountPass, apiClient=None):
    """
    Handle the complete login flow for an Instagram account.
    Includes login checking, login attempts, phone verification, and account switching.

    Args:
        driver: Selenium WebDriver instance
        account: Target Instagram account username
        accountPass: Account password
        apiClient: ApiClient instance for reading session settings

    Returns:
        tuple: (login_success, current_user, login_failure_exit, attempts_made, verification_requested)
        - login_success: True if successfully logged in to target account
        - current_user: Username of currently logged in user (or None)
        - login_failure_exit: True if login failed and should exit/skip bot script
        - attempts_made: Number of login attempts made
        - verification_requested: True if Instagram requested verification code
    """
    # Load login settings from API
    _user_cfg = apiClient.get_user_config() if apiClient else {}
    skipLoginCheck = _user_cfg.get('skip_login_check', False)
    login_tries = _user_cfg.get('login_tries', 3)
    
    loginFailureExit = False
    attempts_made = 0
    verification_requested = False
    loginDiag = ""

    if skipLoginCheck:
        print(client_log_line(account, "login", "skipping login check (manual setting)"))
        return True, account, False, 0, False, ""  # Assume logged in if skipping check
    else:
        current_user = None
        is_logged_in = False
        
        for attempt in range(1, login_tries + 1):
            attempts_made = attempt
            # Check login status
            print(client_log_line(account, "login", f"check try={attempt}/{login_tries} → checking"))
            is_logged_in, current_user, loginErrors = check_login(driver, account=account)
            
            # Check if phone verification is required
            if is_logged_in == "VERIFICATION_REQUIRED":
                print(client_log_line(
                    account, "login",
                    f"verification challenge try={attempt}/{login_tries} — waiting for SMS code",
                ))
                try:
                    from burnBot_notifications import send_sms_challenge_alert
                    send_sms_challenge_alert(account, apiClient=apiClient, _print=print)
                except Exception as _sms_notif_err:
                    print(client_log_line(account, "notify", f"Warning — SMS challenge alert failed: {_sms_notif_err}"))
                sms_code = status_store.request_operator_input(f"SMS code for @{account}:")
                if sms_code.strip():
                    print(client_log_line(account, "login", "SMS code received — submitting to Instagram"))
                    is_logged_in, current_user, loginErrors = enter_sms_code_in_browser(driver, sms_code.strip(), account=account)
                    if not is_logged_in:
                        print(client_log_line(account, "login", f"SMS code entry failed: {loginErrors}"))
                        loginFailureExit = True
                        verification_requested = True
                        break
                else:
                    print(client_log_line(account, "login", "SMS code entry cancelled"))
                    loginFailureExit = True
                    verification_requested = True
                    break

            # Log errors if any (to console only, session log will capture final result)
            if loginErrors:
                debug_line(client_log_line(account, "login", f"debug check_login errors: {loginErrors}"))

            print(client_log_line(
                account, "login",
                f"check try={attempt}/{login_tries} → ok={is_logged_in} user={current_user}",
            ))

            # Attempt login if needed
            if not is_logged_in:
                print(client_log_line(account, "login", f"login try={attempt}/{login_tries} → attempting"))
                is_logged_in, current_user, loginErrors = do_login(driver, account, accountPass, apiClient=apiClient)

                if is_logged_in == "VERIFICATION_REQUIRED":
                    print(client_log_line(
                        account, "login",
                        f"verification challenge try={attempt}/{login_tries} — waiting for SMS code",
                    ))
                    try:
                        from burnBot_notifications import send_sms_challenge_alert
                        send_sms_challenge_alert(account, apiClient=apiClient, _print=print)
                    except Exception as _sms_notif_err:
                        print(client_log_line(account, "notify", f"Warning — SMS challenge alert failed: {_sms_notif_err}"))
                    sms_code = status_store.request_operator_input(f"SMS code for @{account}:")
                    if sms_code.strip():
                        print(client_log_line(account, "login", "SMS code received — submitting to Instagram"))
                        is_logged_in, current_user, loginErrors = enter_sms_code_in_browser(driver, sms_code.strip(), account=account)
                        if not is_logged_in:
                            print(client_log_line(account, "login", f"SMS code entry failed: {loginErrors}"))
                            loginFailureExit = True
                            verification_requested = True
                            break
                    else:
                        print(client_log_line(account, "login", "SMS code entry cancelled"))
                        loginFailureExit = True
                        verification_requested = True
                        break
                
                if loginErrors:
                    loginDiag += loginErrors
                    debug_line(client_log_line(account, "login", f"debug do_login errors: {loginErrors}"))

                print(client_log_line(
                    account, "login",
                    f"login try={attempt}/{login_tries} → ok={is_logged_in} user={current_user}",
                ))
            
            # Profile is logged in as a different user. Do NOT walk Instagram's account
            # switcher (switch_login) — doing so links the two accounts server-side.
            # Fail the session and let the operator log out manually (noVNC / desktop).
            if is_logged_in and is_logged_in != "VERIFICATION_REQUIRED" and current_user and account != current_user:
                error_msg = (
                    f"profile is logged in as @{current_user} — refusing to switch accounts; "
                    f"log out of @{current_user} in this browser profile manually, then re-run"
                )
                print(client_log_line(account, "login", f"failed — {error_msg}"))
                loginDiag += f" wrong_user={current_user}"
                return False, current_user, True, attempts_made, False, loginDiag
            
            # Break if successful (skip if verification required)
            if account == current_user and is_logged_in and is_logged_in != "VERIFICATION_REQUIRED":
                time.sleep(2.0)
                dismiss_notifications_prompt(driver, context_label="post_login")
                # Note: Success will be logged by accountSession.py in log_session_run()
                return True, current_user, False, attempts_made, False, ""
            
            time.sleep(4 + attempt)
        
        # Exit if not logged in or in wrong account (or verification still required)
        if account != current_user or not is_logged_in or is_logged_in == "VERIFICATION_REQUIRED":
            loginFailureExit = True
            
            if is_logged_in == "VERIFICATION_REQUIRED":
                verification_requested = True
                # Challenge already summarized above; avoid repeating ERROR line
                return False, current_user, True, attempts_made, verification_requested, loginDiag

            error_msg = f"Login failure - wrong user: {current_user} or not logged in: {is_logged_in}"
            print(client_log_line(account, "login", f"failed — {error_msg}"))
            return False, current_user, True, attempts_made, verification_requested, loginDiag

        # Should not reach here, but return success if we do
        return True, current_user, False, attempts_made, False, ""