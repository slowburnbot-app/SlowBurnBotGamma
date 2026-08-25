# burnBot_likePostsHome.py

import builtins as _builtins
from burnBot_imports import *
from burnBot_human import hsleep, htype, hclick, hhover, hscroll
from burnBot_utils import process_exception, get_post_author_username
from burnBot_login import check_phone_verification, switch_login
from burnBot_accountSession_setup import is_bot_debug_enabled
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line
import random
import time
import burnBot_status as status_store

_p = _builtins.print  # set per-call by do_like_posts_home; safe because sessions run sequentially

# Ads don't always expose the word "Sponsored" (IG obfuscates it to defeat ad
# blockers) or an /ads/about link, but they do carry a CTA button legitimate
# posts never have — v1.170 liked an ad logged as "[Learn more]" because both
# text signals were absent. Exact whole-text match on button/link elements.
_AD_CTA_TEXTS = (
    "Learn more", "Shop now", "Sign up", "Install now", "Book now",
    "Order now", "Contact us", "Apply now", "Get offer", "Get quote",
    "Download", "Subscribe", "Send message", "Call now", "Get directions",
    "Listen now", "Watch more", "See menu", "Play game",
)
_AD_CTA_XPATH = " | ".join(
    f".//*[(@role='button' or @role='link') and normalize-space()='{t}']"
    for t in _AD_CTA_TEXTS
)


def check_login(driver):
    moduleErrorsLog = ""
    try:
        driver.get("https://www.instagram.com/")
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)
        
        # Check for phone verification first
        is_verification_required, verification_reason = check_phone_verification(driver)
        if is_verification_required:
            error_msg = f"PHONE VERIFICATION REQUIRED - Reason: {verification_reason}"
            moduleErrorsLog += error_msg
            return "VERIFICATION_REQUIRED", None, moduleErrorsLog
        
        try:  # Check for login form - method 1 --wait up to 5 seconds to see if the username field appears
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.NAME, "username")))
            login_fields = driver.find_elements(By.NAME, "username")
            if login_fields:
                print("--Login page detected by username field — Not logged in")
                return False, None, moduleErrorsLog
        except TimeoutException:
            pass  # Username field not found within wait period — possibly logged in
        
        try:  # Check for login text - method 3
            login_indicators = ["Get the app.", "Save login info", "Trouble logging in?", "Forgot password?",
                                "Log in to Instagram", "Sign up"]
            WebDriverWait(driver, 10).until(
                lambda d: any(text.lower() in d.page_source.lower() for text in login_indicators)
            )
            
            print("--Login page detected by get app/save login text — Not logged in")
            return False, None, moduleErrorsLog
        except:
            pass
        
        try:  # Look for username in page source JSON data
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
                # Look for username pattern with surrounding context
                matches = re.findall(r'"username":"([^"]+)"', page_source)
                if matches:
                    # The logged-in user's username typically appears multiple times
                    # Try to find one that's not a random post author
                    for match in matches:
                        if len(match) > 3 and match.lower() not in ["sign up", "log in"]:
                            return True, match, moduleErrorsLog
        
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

def do_login(driver, username, password):
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
        # Navigate to Instagram login page if not already there
        try:
            driver.get("https://www.instagram.com/accounts/login/")
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(2)
        except Exception as error:
            noteError = f"Error navigating to login page: {str(error)}"
            printError = True
            logError = True
            debugError = False
            moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        
        # Handle "Get the app" or "Save login info" prompts if present
        try:
            page_source = driver.page_source
            if any(text in page_source for text in ["Get the app", "Save login info"]):
                try:
                    loginButton = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.CLASS_NAME, "x10w6t97"))
                    )
                    actions = ActionChains(driver)
                    actions.move_to_element(loginButton)
                    actions.click(loginButton)
                    actions.perform()
                    time.sleep(3)
                except Exception:
                    pass  # Button might not be present or already clicked
                
                try:  # Click save login info button if present
                    saveInfoButton = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((By.CLASS_NAME, "_acap"))
                    )
                    actions = ActionChains(driver)
                    actions.move_to_element(saveInfoButton)
                    time.sleep(1)
                    actions.click(saveInfoButton)
                    actions.perform()
                    time.sleep(2)
                except Exception:
                    pass  # Button might not be present
        except Exception as error:
            noteError = f"Error handling login prompts: {str(error)}"
            printError = False
            logError = False
            debugError = False
            moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        
        # Enter username and password
        try:
            time.sleep(2)  # Wait for page to stabilize
            loginUsername = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[aria-label='Phone number, username, or email']"))
            )
            loginUsername.clear()
            htype(loginUsername, username)
            
            loginPassword = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[aria-label='Password']"))
            )
            loginPassword.clear()
            htype(loginPassword, password)
            
            # Submit login form
            loginPassword.send_keys(Keys.RETURN)
            
            # Wait for page to load after login attempt
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            time.sleep(5)  # Give extra time for Instagram to process login
        
        except Exception as error:
            noteError = f"Error entering credentials: {str(error)}"
            printError = True
            logError = True
            debugError = False
            moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
            return False, None, moduleErrorsLog
        
        # Check for phone verification BEFORE checking login success
        is_verification_required, verification_reason = check_phone_verification(driver)
        if is_verification_required:
            error_msg = f"PHONE VERIFICATION REQUIRED - Reason: {verification_reason}"
            moduleErrorsLog += error_msg
            # Return special status to indicate verification needed
            return "VERIFICATION_REQUIRED", None, moduleErrorsLog
        
        # Check results of login attempt
        try:
            # Wait for page to fully load
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
            time.sleep(3)
            
            # Check again for verification (in case it appeared after page load)
            is_verification_required, verification_reason = check_phone_verification(driver)
            if is_verification_required:
                error_msg = f"PHONE VERIFICATION REQUIRED - Reason: {verification_reason}"
                moduleErrorsLog += error_msg
                return "VERIFICATION_REQUIRED", None, moduleErrorsLog
            
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
                moduleErrorsLog += "Still on login page after login attempt - login likely failed"
                return False, None, moduleErrorsLog
                
        except Exception as error:
            noteError = f"Error verifying login: {str(error)}"
            printError = False
            logError = True
            debugError = False
            moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        
        return False, None, moduleErrorsLog
    
    except Exception as error:  ### catch all errors
        noteError = f"do_login catch all: {str(error)}"
        printError = True
        logError = True
        debugError = False
        moduleErrorsLog += process_exception(printError, noteError, logError, debugError)
        return False, None, moduleErrorsLog


def do_like_posts_home(driver, account, target_count, apiClient=None, account_id=None, _print=None, log_scope=None, action_label=None):
    global _p
    _p = _print if _print is not None else _builtins.print
    _scope = log_scope or "like-home"
    _lbl = f"{action_label}-" if action_label else ""
    _done_lbl = (action_label[0].upper() + action_label[1:]) if action_label else "Done"
    """
    Like posts from the Instagram home feed

    Args:
        driver: Selenium WebDriver instance
        account: Account name (for logging)
        target_count: Number of posts to like
        apiClient: Optional ApiClient instance for ignore list access
        account_id: Account UUID (unused here, kept for consistent interface)

    Returns:
        tuple: (likes_performed, errors_log)
    """
    likes_performed = 0
    moduleErrorsLog = ""
    max_scrolls = 30
    scrolls = 0
    processed_articles = []  # Track processed articles to avoid duplicates

    # Load like_suggested and like_sponsored settings from API
    _user_cfg = apiClient.get_user_config() if apiClient else {}
    like_suggested = _user_cfg.get('like_suggested', True)
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
    
    try:
        # Always navigate to the home feed before starting — the driver may be on
        # any page (explore, a profile, etc.) from a prior action or random action
        driver.get('https://www.instagram.com/')
        WebDriverWait(driver, 15).until(lambda d: d.execute_script('return document.readyState') == 'complete')
        driver.execute_script("window.scrollTo(0, 0)")
        hsleep(2, 4)
        
        target_formatted = f"{target_count:02d}"
        
        while likes_performed < target_count and scrolls < max_scrolls:
            articles = driver.find_elements(By.TAG_NAME, 'article')
            new_articles = [art for art in articles if art not in processed_articles]
            
            if len(new_articles) > 0:
                for article in new_articles:
                    if status_store.is_bot_paused():
                        return likes_performed, moduleErrorsLog
                    try:
                        try:
                            article_account = get_post_author_username(article)
                        except Exception:
                            article_account = "unknown"

                        # No parseable header profile link means this article is
                        # an ad or an unrecognized layout — never like blind
                        # (v1.170's first-text-match extraction liked ads logged
                        # as "[Learn more]"/caption text, and the ignore list
                        # can't match a garbage name).
                        if article_account == "unknown":
                            _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [unknown-author]"))
                            if is_bot_debug_enabled():
                                try:
                                    article_html = article.get_attribute("outerHTML")
                                    debug_line(client_log_line(account, _scope, f"debug unknown-author article HTML: {article_html[:2000]}"))
                                except Exception:
                                    pass
                            if article not in processed_articles:
                                processed_articles.append(article)
                            continue


                        # Skip suggested posts if like_suggested is disabled
                        if not like_suggested:
                            try:
                                is_suggested = (
                                    "Suggested for you" in article.text or
                                    len(article.find_elements(By.XPATH, ".//div[@role='button' and text()='Follow']")) > 0
                                )
                                if is_suggested:
                                    display_name = article_account[:15] if len(article_account) > 15 else article_account
                                    _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{display_name}] - [suggested]"))
                                    if article not in processed_articles:
                                        processed_articles.append(article)
                                    continue
                            except:
                                pass

                        # Skip sponsored posts if like_sponsored is disabled
                        if not like_sponsored:
                            try:
                                article_inner_text = driver.execute_script(
                                    "return arguments[0].innerText || ''", article
                                )
                                is_ad = 'Sponsored' in article_inner_text

                                if not is_ad:
                                    is_ad = len(article.find_elements(
                                        By.XPATH, ".//*[contains(@href,'/ads/about')]"
                                    )) > 0

                                if not is_ad:
                                    cta_hits = article.find_elements(By.XPATH, _AD_CTA_XPATH)
                                    if cta_hits:
                                        is_ad = True
                                        if is_bot_debug_enabled():
                                            debug_line(client_log_line(account, _scope, f"debug @{article_account} ad via CTA button"))

                                if is_bot_debug_enabled():
                                    debug_line(client_log_line(account, _scope, f"debug @{article_account} is_ad={is_ad}"))
                                    if not is_ad:
                                        article_html = article.get_attribute("outerHTML")
                                        debug_line(client_log_line(account, _scope, f"debug article HTML snippet: {article_html[:800]}"))

                                if is_ad:
                                    display_name = article_account[:15] if len(article_account) > 15 else article_account
                                    _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{display_name}] - [sponsored]"))
                                    if article not in processed_articles:
                                        processed_articles.append(article)
                                    continue
                            except Exception as e:
                                debug_line(client_log_line(account, _scope, f"debug sponsored check error: {e}"))

                        # Check if account is on ignore list
                        if article_account in ignore_list:
                            display_name = article_account[:15] if len(article_account) > 15 else article_account
                            _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{display_name}] - [ignored]"))
                            if article not in processed_articles:
                                processed_articles.append(article)
                            continue
                        
                        like_box = WebDriverWait(article, 10).until(
                            EC.presence_of_element_located((By.CLASS_NAME, "xyb1xck"))
                        )
                        like_status = like_box.get_attribute("aria-label")
                        
                        if like_status == "Like":
                            display_name = article_account[:15] if len(article_account) > 15 else article_account

                            like_button = WebDriverWait(article, 5).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, "svg[aria-label='Like']"))
                            )
                            hhover(driver, article)
                            hclick(driver, like_button)

                            # Count the like only after the heart actually flips
                            # (2026-08-13: an entire topics action's clicks
                            # silently failed to register — click-and-count
                            # would have reported 6/6 while liking nothing).
                            # Scoped to the action-bar <section> so a comment
                            # heart can't satisfy the check.
                            try:
                                WebDriverWait(article, 6).until(
                                    lambda a: len(a.find_elements(
                                        By.XPATH,
                                        ".//section//*[@role='button'][.//*[local-name()='svg' and @aria-label='Unlike']]"
                                    )) > 0
                                )
                                likes_performed += 1
                                count_formatted = f"{likes_performed:02d}"
                                _p(client_log_line(account, _scope, f"{_lbl}[{count_formatted}/{target_formatted}] - [{display_name}]"))
                            except TimeoutException:
                                debug_line(client_log_line(account, _scope, f"skip @{display_name} reason=like_state_unchanged"))

                            hsleep(6, 8)
                        else:
                            if like_status:
                                display_name = article_account[:15] if len(article_account) > 15 else article_account
                                _p(client_log_line(account, _scope, f"{_lbl}[-skip] - [{display_name}] - [already liked]"))
                        
                        if article not in processed_articles:
                            processed_articles.append(article)
                        
                        if likes_performed >= target_count:
                            break
                    
                    except (NoSuchElementException, StaleElementReferenceException) as error:
                        noteError = "Article element error (stale/not found)"
                        moduleErrorsLog += process_exception(False, noteError, False, False)
                        pass
                    except TimeoutException:
                        pass
                    
                    hscroll(driver, 400)
                    hsleep(4, 6)
                
                if likes_performed >= target_count:
                    break
            
            else:
                _p(client_log_line(account, _scope, "reload (no articles)"))
                driver.get('https://www.instagram.com/')
                time.sleep(5)
            
            scrolls += 1
        
        if likes_performed < target_count:
            _p(client_log_line(account, _scope, f"{_lbl}Incomplete[{likes_performed}/{target_count}]"))
        else:
            _p(client_log_line(account, _scope, f"{_done_lbl}-Completed[{likes_performed}/{target_count}]"))
    
    except Exception as error:
        noteError = f"do_like_posts_home catch all: {str(error)}"
        moduleErrorsLog += process_exception(True, noteError, True, False)
    
    return likes_performed, moduleErrorsLog
