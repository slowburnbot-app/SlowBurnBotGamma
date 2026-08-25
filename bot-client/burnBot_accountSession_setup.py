# burnBot_accountSession_setup.py

import os
import json
import time
import socket
import threading
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager
from burnBot_config import CONFIG, resolve_path
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line


def is_bot_debug_enabled():
    """
    Return True if verbose bot debug logging is enabled.

    Canonical implementation — all other modules import this one. Resolution
    order: the remote UserConfig.bot_debug value (set from the dashboard,
    polled into burnBot_status by burnBot.py) wins once one has ever been
    received; otherwise fall back to [bot_settings] bot_debug in
    burnBot_config.ini; otherwise False.

    This used to have a second, independent copy in burnBot_login.py with
    the opposite fallback (True here, False there) — unified to False so a
    fresh install doesn't spam debug output before the dashboard toggle (or
    this INI key) has ever been touched.

    burnBot_status is imported lazily to sidestep the burnBot_imports <->
    burnBot_login import cycle (burnBot_imports imports burnBot_login at
    module load; a top-level import here is unaffected by that cycle, but
    lazy keeps this function import-order-independent regardless of who
    calls it first).
    """
    try:
        import burnBot_status as _st
        remote = _st.get_remote_debug()
        if remote is not None:
            return remote
    except Exception:
        pass
    try:
        return CONFIG.getboolean('bot_settings', 'bot_debug', fallback=False)
    except Exception:
        return False

try:
    import requests
except ImportError:
    requests = None

# Import psutil for process management (already in burnBot_imports)
try:
    import psutil
except ImportError:
    psutil = None


def build_user_data_dir(account):
    """
    Build the user data directory path for an account.
    
    Args:
        account: Account username/identifier
        
    Returns:
        str: Path to the user data directory
    """
    # Get base directory from config
    chrome_user_data_dir_base = CONFIG['browser-config'].get(
        'chrome_user_data_dir_base'
    ) or 'ChromeUserData'
    # Resolve path relative to project directory
    chrome_user_data_dir_base = resolve_path(chrome_user_data_dir_base)
    
    # Build user data dir path per account
    chrome_user_data_dir = os.path.join(chrome_user_data_dir_base, f'user_{account}')
    return chrome_user_data_dir


def launch_manual_browser(account):
    """
    Launch a plain (non-Selenium) Chrome window into the account's own profile, so an
    operator connected via noVNC can manually check login state / solve a CAPTCHA.

    Launched as a bare subprocess (not through webdriver.Chrome) so it's not tied to a
    Selenium session and isn't closed by driver.quit() or the close_browser_after_* flags.
    Chrome's own profile-directory locking means if the bot's automation browser is
    already running on this profile, this call simply focuses/reuses that window instead
    of starting a second process against the same profile.

    Args:
        account: Account username/identifier

    Returns:
        bool: True if the process was launched (or handed off to Chrome's existing
              instance for that profile), False if the Chrome binary could not be started.
    """
    import subprocess

    chrome_user_data_dir = build_user_data_dir(account)
    chrome_path = CONFIG.get('browser-config', 'chrome_path', fallback='/usr/bin/google-chrome')
    chrome_path = resolve_chrome_binary(resolve_path(chrome_path)) or '/usr/bin/google-chrome'

    args = [
        chrome_path,
        f'--user-data-dir={chrome_user_data_dir}',
        f'--profile-directory={account}',
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--use-gl=angle',
        '--use-angle=swiftshader',
        '--disable-setuid-sandbox',
        '--window-size=1920,1080',
        '--window-position=0,0',
    ]

    # Nuitka sets LD_LIBRARY_PATH/LD_PRELOAD to its extracted temp dir; subprocesses
    # inherit these and can pick up the frozen libcrypto/libssl instead of system ones.
    # Same workaround as _start_vnc_services() in burnBot.py.
    clean_env = os.environ.copy()
    for _var in ('LD_LIBRARY_PATH', 'LD_PRELOAD'):
        clean_env.pop(_var, None)

    try:
        subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=clean_env,
        )
        return True
    except Exception:
        return False


def update_local_state(account, chrome_user_data_dir):
    """
    Update Chrome's Local State file with account information.
    
    Args:
        account: Account username/identifier
        chrome_user_data_dir: Path to Chrome user data directory
    """
    local_state_file = os.path.join(chrome_user_data_dir, 'Local State')
    if os.path.exists(local_state_file):
        try:
            with open(local_state_file, 'r', encoding='utf-8') as f:
                local_state = json.load(f)
            
            if 'profile' not in local_state:
                local_state['profile'] = {}
            if 'info_cache' not in local_state['profile']:
                local_state['profile']['info_cache'] = {}
            
            if account not in local_state['profile']['info_cache']:
                local_state['profile']['info_cache'][account] = {}
            
            local_state['profile']['info_cache'][account]['name'] = account
            local_state['profile']['info_cache'][account]['user_name'] = account
            local_state['profile']['info_cache'][account]['gaia_name'] = account
            
            with open(local_state_file, 'w', encoding='utf-8') as f:
                json.dump(local_state, f, indent=2)
            
            debug_line(f"- [{account}]: updated Local State")
        except Exception as e:
            print(f"- [{account}]: could not update Local State: {e}")


def find_chrome_debugging_port(chrome_user_data_dir, account):
    """
    Try to find the remote debugging port for an existing Chrome instance.
    Checks the DevToolsActivePort file that Chrome creates.

    Args:
        chrome_user_data_dir: Path to Chrome user data directory
        account: Account username/identifier

    Returns:
        int: Port number if found, None otherwise
    """
    try:
        # Chrome stores the debugging port in DevToolsActivePort file
        # Locations to check (in order of preference):
        # 1. Profile-specific directory: user_data_dir/profile_name/DevToolsActivePort
        # 2. Default profile directory: user_data_dir/Default/DevToolsActivePort
        # 3. User data directory root: user_data_dir/DevToolsActivePort
        possible_locations = [
            os.path.join(chrome_user_data_dir, account, "DevToolsActivePort"),  # Profile-specific
            os.path.join(chrome_user_data_dir, "Default", "DevToolsActivePort"),  # Default profile
            os.path.join(chrome_user_data_dir, "DevToolsActivePort"),  # Root directory
        ]

        for devtools_file in possible_locations:
            if os.path.exists(devtools_file):
                try:
                    with open(devtools_file, 'r') as f:
                        lines = f.readlines()
                        if lines:
                            # First line is usually the port
                            try:
                                port = int(lines[0].strip())
                                # Verify port is actually listening
                                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                sock.settimeout(1)
                                result = sock.connect_ex(('127.0.0.1', port))
                                sock.close()
                                if result == 0:
                                    debug_line(f"- [{account}]: Found DevToolsActivePort file at: {devtools_file}")
                                    return port
                            except (ValueError, socket.error, socket.timeout):
                                pass
                except (IOError, PermissionError):
                    # File might be locked, try next location
                    continue
    except Exception as e:
        debug_line(f"- [{account}]: Error checking DevToolsActivePort: {e}")
    return None


def find_existing_chrome_process(chrome_user_data_dir, account, expected_port):
    """
    Check if Chrome is already running for this profile and return its debugging port.

    Args:
        chrome_user_data_dir: Path to Chrome user data directory
        account: Account username/identifier
        expected_port: Expected remote debugging port for this account

    Returns:
        tuple: (is_running, port) - True if running, port number if found
    """
    debug_line(f"- [{account}]: Check for Chrome on port {expected_port}, profile: {chrome_user_data_dir}")

    # First, check if the expected port is listening (most reliable check)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(('127.0.0.1', expected_port))
        sock.close()
        if result == 0:
            # Port is listening - this is the most reliable indicator
            print(f"- [{account}]: Port {expected_port} is listening - Chrome likely running")
            # Try to verify it's our Chrome instance, but if port is listening, we'll try to connect anyway
            if psutil:
                try:
                    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                        try:
                            proc_name = proc.info['name'] or ''
                            cmdline = proc.info['cmdline'] or []
                            
                            if 'chrome' in proc_name.lower():
                                cmdline_str = ' '.join(cmdline).lower()
                                # Check if it's using our user data directory and profile
                                profile_match = chrome_user_data_dir.lower() in cmdline_str and account.lower() in cmdline_str
                                if profile_match:
                                    # Check if it has the expected port
                                    for arg in cmdline:
                                        if f'--remote-debugging-port={expected_port}' in arg.lower():
                                            print(f"- [{account}]: Verified Chrome process with matching profile and port")
                                            return True, expected_port
                                    # If profile matches but port doesn't, still return True with the port from DevToolsActivePort
                                    # This handles the case where Chrome was started with --remote-debugging-port=0
                                    print(f"- [{account}]: Chrome process matches profile but port differs - will use DevToolsActivePort port")
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            pass
                except Exception as e:
                    print(f"- [{account}]: Error checking Chrome processes: {e}")
            
            # Port is listening, so return True even if we can't verify the process
            # (the connection attempt will verify if it's the right Chrome instance)
            print(f"- [{account}]: Port {expected_port} is listening - will attempt connection")
            return True, expected_port
    except (socket.error, socket.timeout) as e:
        print(f"- [{account}]: Port {expected_port} is not listening: {e}")
    
    if psutil is None:
        return False, None
    
    # Fallback: Try to find the debugging port from DevToolsActivePort file
    # IMPORTANT: Verify the Chrome instance matches our profile AND port is actually listening
    # DISABLE DevToolsActivePort check for now - it's causing cross-account issues
    # The process checking below is more reliable
    if False:  # Temporarily disabled
        try:
            port = find_chrome_debugging_port(chrome_user_data_dir, account)
            if port:
                # CRITICAL: Double-check that the port is actually listening before returning it
                # The find_chrome_debugging_port function checks, but we'll verify again here
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(2)
                    result = sock.connect_ex(('127.0.0.1', port))
                    sock.close()
                    if result != 0:
                        print(f"- [{account}]: Port {port} from DevToolsActivePort is NOT listening - Chrome may have crashed")
                        return False, None
                except Exception as port_check_error:
                    print(f"- [{account}]: Cannot verify port {port} is listening: {port_check_error}")
                    return False, None

                # Also verify via DevTools API if available
                if requests:
                    try:
                        response = requests.get(f"http://127.0.0.1:{port}/json", timeout=2)
                        if response.status_code != 200:
                            print(f"- [{account}]: Port {port} is listening but DevTools API not responding")
                            return False, None
                    except Exception:
                        print(f"- [{account}]: Port {port} is listening but DevTools API check failed")
                        return False, None

                # Verify that the Chrome instance on this port matches our profile
                if psutil:
                    profile_match_found = False
                    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                        try:
                            proc_name = proc.info['name'] or ''
                            cmdline = proc.info['cmdline'] or []
                            if 'chrome' in proc_name.lower():
                                cmdline_str = ' '.join(cmdline).lower()
                                # Check if this Chrome process matches our profile
                                profile_match = chrome_user_data_dir.lower() in cmdline_str and account.lower() in cmdline_str
                                # Check if this process might be using the port we found
                                if profile_match:
                                    # CRITICAL: Verify the port matches the expected port
                                    if port == expected_port:
                                        profile_match_found = True
                                        print(f"- [{account}]: Found port {port} from DevToolsActivePort - VERIFIED matching profile and expected port")
                                        return True, port
                                    else:
                                        print(f"- [{account}]: Found port {port} from DevToolsActivePort but expected {expected_port} - ignoring")
                                        return False, None
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            pass

                    if not profile_match_found:
                        print(f"- [{account}]: WARNING: Port {port} found and listening but no Chrome process matches our profile")
                        print(f"- [{account}]: This Chrome instance may be using a different profile - will NOT reconnect")
                        return False, None
                else:
                    # If we can't verify profile, but port is listening, we'll try to connect
                    print(f"- [{account}]: Found port {port} from DevToolsActivePort - port is listening (cannot verify profile)")
                    return True, port
        except Exception as e:
            print(f"- [{account}]: Error reading DevToolsActivePort: {e}")
    
    # Fallback: Check if Chrome process is running with this profile
    # CRITICAL: Only return processes that match BOTH profile AND expected port
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                proc_name = proc.info['name'] or ''
                cmdline = proc.info['cmdline'] or []

                if 'chrome' in proc_name.lower():
                    cmdline_str = ' '.join(cmdline).lower()
                    # Check if it's using our user data directory and profile
                    # CRITICAL: Must match BOTH the user data directory AND the account name
                    user_data_dir_arg = f'--user-data-dir={chrome_user_data_dir.lower()}'
                    profile_dir_arg = f'--profile-directory={account.lower()}'

                    user_data_match = user_data_dir_arg in cmdline_str
                    profile_match = profile_dir_arg in cmdline_str

                    if is_bot_debug_enabled() and (user_data_match or profile_match or f'--remote-debugging-port={expected_port}' in cmdline_str):
                        # Only show processes that might be relevant
                        debug_line(f"- [{account}]: Checking Chrome process PID {proc.info['pid']}:")
                        debug_line(f"- [{account}]:   Expected user-data-dir: {chrome_user_data_dir.lower()}")
                        debug_line(f"- [{account}]:   Expected profile: {account.lower()}")
                        debug_line(f"- [{account}]:   Command line: {' '.join(cmdline)}")
                        debug_line(f"- [{account}]:   user_data_match={user_data_match}, profile_match={profile_match}")

                        # Show which arguments are found
                        found_user_data = None
                        found_profile = None
                        found_port = None
                        for arg in cmdline:
                            if arg.lower().startswith('--user-data-dir='):
                                found_user_data = arg
                            elif arg.lower().startswith('--profile-directory='):
                                found_profile = arg
                            elif arg.lower().startswith('--remote-debugging-port='):
                                found_port = arg
                        debug_line(f"- [{account}]:   Found user-data-dir: {found_user_data}")
                        debug_line(f"- [{account}]:   Found profile: {found_profile}")
                        debug_line(f"- [{account}]:   Found port: {found_port}")
                        debug_line(f"- [{account}]:   ---")

                    if user_data_match and profile_match:
                        # This is definitely our Chrome process
                        # Try to extract port from command line
                        found_port_in_cmdline = None
                        for arg in cmdline:
                            if '--remote-debugging-port=' in arg.lower():
                                try:
                                    found_port_in_cmdline = int(arg.split('=')[1])
                                    break
                                except (ValueError, IndexError):
                                    pass

                        if found_port_in_cmdline:
                            debug_line(f"- [{account}]: Process matches profile, found port {found_port_in_cmdline} in command line (expected {expected_port})")

                            # Check if the port from command line is listening
                            try:
                                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                sock.settimeout(1)
                                if sock.connect_ex(('127.0.0.1', found_port_in_cmdline)) == 0:
                                    # Port is listening - this is our Chrome process
                                    debug_line(f"- [{account}]: SUCCESS: Found Chrome process with matching profile and listening port {found_port_in_cmdline}")
                                    return True, found_port_in_cmdline
                                else:
                                    debug_line(f"- [{account}]: Port {found_port_in_cmdline} from command line is not listening - ignoring")
                            except Exception:
                                debug_line(f"- [{account}]: Could not verify if port {found_port_in_cmdline} is listening")
                        else:
                            # No port in command line - check if expected port is listening for this process
                            try:
                                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                sock.settimeout(1)
                                if sock.connect_ex(('127.0.0.1', expected_port)) == 0:
                                    debug_line(f"- [{account}]: Process matches profile, expected port {expected_port} is listening")
                                    return True, expected_port
                                else:
                                    debug_line(f"- [{account}]: Process matches profile but expected port {expected_port} is not listening")
                            except Exception:
                                pass

                    # FALLBACK: If we can't find the exact process, check if ANY Chrome process is using our expected port
                    # This handles cases where the main browser process isn't found but Chrome is still running on our port
                    elif not (user_data_match and profile_match):
                        # Check if this process has our expected port
                        for arg in cmdline:
                            if f'--remote-debugging-port={expected_port}' in arg.lower():
                                # This process is using our expected port - check if it's listening
                                try:
                                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                    sock.settimeout(1)
                                    if sock.connect_ex(('127.0.0.1', expected_port)) == 0:
                                        debug_line(f"- [{account}]: FALLBACK: Found process using expected port {expected_port} (PID {proc.info['pid']})")
                                        return True, expected_port
                                    sock.close()
                                except Exception:
                                    pass
                                break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except Exception as e:
        print(f"- [{account}]: Error finding Chrome process: {e}")

    return False, None


def get_window_count_via_devtools(debug_port):
    """
    Query Chrome DevTools API directly to get window count before connecting.
    This allows us to verify we connected to the EXISTING Chrome, not a new one.
    
    Args:
        debug_port: Remote debugging port number
        
    Returns:
        int: Number of page windows, or None if query failed
    """
    if requests is None:
        print("Warning: requests library not available, cannot query DevTools API")
        print("Note: Install with 'pip install requests' for better reconnection verification")
        return None
    
    try:
        response = requests.get(f"http://127.0.0.1:{debug_port}/json", timeout=2)
        if response.status_code == 200:
            targets = response.json()
            # Count only page targets (not background pages, extensions, etc.)
            window_count = len([t for t in targets if t.get('type') == 'page'])
            return window_count
        else:
            return None
    except Exception as e:
        return None


def verify_profile_match(driver, account, chrome_user_data_dir):
    """
    Verify we're connected to the correct Chrome profile.
    Enhanced verification using CDP commands and process checking.
    
    Args:
        driver: Chrome driver instance
        account: Account username/identifier
        chrome_user_data_dir: Expected Chrome user data directory
        
    Returns:
        bool: True if profile matches, False otherwise
    """
    if psutil is None:
        print(f"- [{account}]: Cannot verify profile (psutil not available)")
        # Fallback: just check responsiveness
        try:
            driver.current_url
            return True
        except:
            return False
    
    try:
        # First check if driver is responsive
        current_url = driver.current_url
        
        # Enhanced verification: Try to get user data directory via CDP
        try:
            # Try to get browser version info which may include user data dir
            # Add timeout protection to prevent hanging
            cdp_result = [None]
            cdp_exception = [None]
            
            def run_cdp():
                try:
                    cdp_result[0] = driver.execute_cdp_cmd('Browser.getVersion', {})
                except Exception as e:
                    cdp_exception[0] = e
            
            # Run CDP command with timeout
            cdp_thread = threading.Thread(target=run_cdp)
            cdp_thread.daemon = True
            cdp_thread.start()
            cdp_thread.join(timeout=5)  # 5 second timeout
            
            if cdp_thread.is_alive():
                # Thread is still running - command timed out
                print(f"- [{account}]: CDP command timed out, using process check instead")
                raise TimeoutError("CDP command timed out")
            
            if cdp_exception[0]:
                raise cdp_exception[0]
            
            version_info = cdp_result[0]
            if version_info is None:
                raise Exception("CDP command returned None")
            
            user_data_dir = version_info.get('userDataDir', '')
            
            if user_data_dir:
                # Normalize paths for comparison
                expected_dir_lower = chrome_user_data_dir.lower().replace('\\', '/')
                actual_dir_lower = user_data_dir.lower().replace('\\', '/')
                
                if expected_dir_lower in actual_dir_lower or actual_dir_lower in expected_dir_lower:
                    print(f"- [{account}]: Profile verification SUCCESS - user data dir matches via CDP")
                    return True
                else:
                    print(f"- [{account}]: Profile verification FAILED - user data dir mismatch via CDP")
                    print(f"- [{account}]:   Expected: {chrome_user_data_dir}")
                    print(f"- [{account}]:   Got: {user_data_dir}")
                    return False
        except Exception as cdp_error:
            # CDP command failed, fall back to process checking
            print(f"- [{account}]: CDP verification failed, using process check: {cdp_error}")
        
        # Fallback: Check Chrome processes to verify profile match
        profile_found = False
        chrome_user_data_dir_lower = chrome_user_data_dir.lower()
        account_lower = account.lower()
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                proc_name = proc.info['name'] or ''
                cmdline = proc.info['cmdline'] or []
                
                # Check for both 'chrome' and 'chromium' process names
                if 'chrome' in proc_name.lower() or 'chromium' in proc_name.lower():
                    cmdline_str = ' '.join(cmdline).lower()
                    
                    # Check if user data dir is in command line
                    user_data_dir_match = chrome_user_data_dir_lower in cmdline_str
                    
                    # Check if account name appears in command line (could be in --profile-directory=account)
                    account_match = account_lower in cmdline_str
                    
                    # Also check for profile-directory argument specifically
                    profile_dir_match = f'--profile-directory={account_lower}' in cmdline_str
                    
                    if user_data_dir_match and (account_match or profile_dir_match):
                        profile_found = True
                        debug_line(f"- [{account}]: Profile verification passed - found matching Chrome process (PID: {proc.info['pid']})")
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        if not profile_found:
            # Only show detailed error if debug is enabled (to reduce noise)
            debug_line(f"- [{account}]: Profile verification FAILED - no Chrome process found with matching profile")
            debug_line(f"- [{account}]: Expected profile: {chrome_user_data_dir} / {account}")
            # This is a warning, not a fatal error - Chrome might still work
            # The driver connection succeeded, so Chrome is likely working correctly
            return False
        
        return True
    except Exception as e:
        print(f"- [{account}]: Profile verification error: {e}")
        return False


def connect_to_existing_chrome(account, chrome_user_data_dir, debug_port, chrome_version=136, chrome_path=None):
    """
    Attempt to connect to an existing Chrome instance via remote debugging.
    Uses standard selenium.webdriver.Chrome for reliable reconnection.
    
    Args:
        account: Account username/identifier
        chrome_user_data_dir: Path to Chrome user data directory
        debug_port: Remote debugging port number
        chrome_version: Chrome version (default: 136) - Log/diagnostics only
        chrome_path: Resolved Chrome executable (optional). Not launched (the browser is
            already running) but passed as binary_location so Selenium Manager fetches the
            chromedriver matching *this* Chrome rather than the system one.

    Returns:
        webdriver.Chrome: Connected driver instance, or None if connection failed
        
    Note:
        When calling driver.quit() on a reconnected driver, it will only disconnect
        the WebDriver session, NOT close the Chrome browser. The browser will remain open.
    """
    driver = None
    try:
        print(f"- [{account}]: Attempting to connect to existing Chrome (port {debug_port})...")
        
        # First verify the port is actually listening
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(('127.0.0.1', debug_port))
            sock.close()
            if result != 0:
                debug_line(f"- [{account}]: Port {debug_port} is not listening - Chrome may not be running with remote debugging")
                return None
        except Exception as e:
            print(f"- [{account}]: Error checking port {debug_port}: {e}")
            return None
        
        # CRITICAL: Get expected window count BEFORE connecting
        # This allows us to verify we connected to the EXISTING Chrome, not a new one
        expected_window_count = get_window_count_via_devtools(debug_port)
        if expected_window_count is None:
            debug_line(f"- [{account}]: Cannot query existing Chrome windows via DevTools API")
            debug_line(f"- [{account}]: Proceeding with connection but cannot verify it's the existing instance")
        else:
            debug_line(f"- [{account}]: Existing Chrome has {expected_window_count} window(s) (verified via DevTools API)")
        
        # Create Chrome options with ONLY debuggerAddress (+ binary_location for driver version matching)
        options = ChromeOptions()
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{debug_port}")
        if chrome_path:
            options.binary_location = chrome_path
        
        debug_line(f"- [{account}]: Connecting via Selenium with debuggerAddress: 127.0.0.1:{debug_port}")
        
        # Use Selenium 4's automatic driver management
        # When using debuggerAddress, we need chromedriver but it won't launch Chrome
        # Selenium 4 will automatically download and manage the correct ChromeDriver version
        service = None
        
        try:
            # Use standard Selenium Chrome for reconnection
            # When debuggerAddress is set, Selenium will connect to existing Chrome, not launch new
            if service:
                driver = webdriver.Chrome(service=service, options=options)
            else:
                # Let Selenium find chromedriver automatically
                driver = webdriver.Chrome(options=options)
            print(f"- [{account}]: Selenium Chrome connection succeeded")
            
            # Verify we connected to EXISTING Chrome
            time.sleep(1)
            actual_window_count = len(driver.window_handles)
            
            # Verify profile match (but be more lenient when connecting to existing Chrome)
            debug_line(f"- [{account}]: Verifying profile match...")
            profile_ok = verify_profile_match(driver, account, chrome_user_data_dir)
            if not profile_ok:
                print(f"- [{account}]: WARNING: Profile verification failed, but connected to Chrome successfully")
                print(f"- [{account}]: Proceeding with connection (browser may work even if profile verification failed)")
                # Don't return None - give the browser a chance to work
                # The profile verification can be overly strict and fail even when Chrome is working
            
            # Verify window count
            if expected_window_count is not None:
                if actual_window_count != expected_window_count:
                    print(f"- [{account}]: ERROR: Window count mismatch!")
                    print(f"- [{account}]:   Expected: {expected_window_count}")
                    print(f"- [{account}]:   Got: {actual_window_count}")
                    print(f"- [{account}]: A NEW Chrome was created - aborting")
                    try:
                        driver.quit()
                    except:
                        pass
                    return None
                else:
                    debug_line(f"- [{account}]: Window count matches! ({actual_window_count} windows)")
            
            debug_line(f"- [{account}]: Successfully reconnected to existing Chrome via Selenium")
            debug_line(f"- [{account}]: Using reconnected Selenium driver (launch flags persist; nothing is injected post-launch)")
            
            return driver
            
        except Exception as conn_error:
            print(f"- [{account}]: Selenium Chrome connection failed: {type(conn_error).__name__}: {conn_error}")
            import traceback
            print(f"- [{account}]: Connection error traceback:\n{traceback.format_exc()}")
            
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            return None
        
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        print(f"- [{account}]: Failed to connect to existing Chrome: {error_type}: {error_msg}")
        
        # Provide more specific error information
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            print(f"- [{account}]: Connection timeout - Chrome may not be ready or port may be blocked")
        elif "connection refused" in error_msg.lower() or "refused" in error_msg.lower():
            print(f"- [{account}]: Connection refused - Chrome may not have remote debugging enabled")
        elif "address already in use" in error_msg.lower():
            print(f"- [{account}]: Port conflict - another process may be using the port")
        
        # Clean up any partial driver connection
        if driver:
            try:
                driver.quit()
            except:
                pass
        return None


def kill_chrome_processes_for_profile(chrome_user_data_dir, account, portable_chrome_path=None):
    """
    Kill only Portable Chrome processes that are using the specified user data directory.
    This prevents file locking issues when updating preferences.
    Will NOT kill regular Chrome instances - only Portable Chrome.
    
    Args:
        chrome_user_data_dir: Path to Chrome user data directory
        account: Account username/identifier
        portable_chrome_path: Path to Portable Chrome executable (optional, will be detected)
    """
    if psutil is None:
        return  # Can't kill processes without psutil
    
    # Get Portable Chrome path from config if not provided
    if portable_chrome_path is None:
        try:
            portable_chrome_path = CONFIG['browser-config'].get('chrome_path', '').strip()
            if portable_chrome_path:
                # Resolve path relative to project directory, then to the real binary
                portable_chrome_path = resolve_chrome_binary(resolve_path(portable_chrome_path))
            else:
                # Empty means system Chrome - we can't reliably detect the path for killing processes
                # So we'll skip this and just kill by user data dir
                portable_chrome_path = None
        except:
            # Fallback: use the user data dir base to infer portable chrome path
            portable_chrome_path = os.path.join(chrome_user_data_dir, '..', 'chrome.exe')
    
    # Get portable chrome directory if path is available
    portable_chrome_dir = None
    if portable_chrome_path:
        try:
            portable_chrome_dir = os.path.dirname(os.path.abspath(portable_chrome_path)).lower()
        except:
            portable_chrome_dir = None
    
    try:
        killed_count = 0
        
        # Only kill Chrome processes using this user data directory
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
            try:
                proc_name = proc.info['name'] or ''
                
                # Check if it's a Chrome process
                if 'chrome' in proc_name.lower() or 'chromedriver' in proc_name.lower():
                    should_kill = False
                    is_portable_chrome = False
                    matches_account = False
                    
                    # First, check if it's using the specified Chrome executable (if provided)
                    if portable_chrome_path and portable_chrome_dir:
                        try:
                            proc_exe = proc.info.get('exe', '')
                            if proc_exe:
                                proc_exe_lower = proc_exe.lower()
                                # Check if executable is in Portable Chrome directory
                                if portable_chrome_dir in proc_exe_lower:
                                    is_portable_chrome = True
                        except (psutil.AccessDenied, AttributeError):
                            pass
                        
                        # Also check exact executable path match
                        if not is_portable_chrome:
                            try:
                                proc_exe = proc.info.get('exe', '')
                                if proc_exe and os.path.exists(proc_exe):
                                    if os.path.abspath(proc_exe).lower() == os.path.abspath(portable_chrome_path).lower():
                                        is_portable_chrome = True
                            except (psutil.AccessDenied, AttributeError):
                                pass
                    else:
                        # System Chrome - check by user data directory instead
                        is_portable_chrome = True  # For system Chrome, we'll match by user data dir
                    
                    # Second, check if it matches our specific account/profile
                    if is_portable_chrome:
                        try:
                            cmdline = proc.info.get('cmdline', [])
                            if cmdline:
                                cmdline_str = ' '.join(cmdline).lower()
                                # CRITICAL: Only kill if it matches our specific profile/user data dir AND account
                                # This prevents killing other accounts' Chrome processes
                                if chrome_user_data_dir.lower() in cmdline_str and account.lower() in cmdline_str:
                                    matches_account = True
                        except (psutil.AccessDenied, AttributeError):
                            pass
                    
                    # Only kill if BOTH conditions are met:
                    # 1. It's using Portable Chrome executable
                    # 2. It matches our specific account/profile
                    should_kill = is_portable_chrome and matches_account
                    
                    if should_kill:
                        try:
                            # Kill the process and all its children
                            try:
                                # First try terminate (graceful)
                                proc.terminate()
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                            
                            # Wait a moment, then force kill if still running
                            try:
                                proc.wait(timeout=2)
                            except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                                # Force kill if still running
                                try:
                                    proc.kill()
                                except (psutil.NoSuchProcess, psutil.AccessDenied):
                                    pass
                            
                            # Also kill all child processes
                            try:
                                children = proc.children(recursive=True)
                                for child in children:
                                    try:
                                        child.terminate()
                                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                                        pass
                                    try:
                                        child.wait(timeout=1)
                                    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                                        try:
                                            child.kill()
                                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                                            pass
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                            
                            killed_count += 1
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                            
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        if killed_count > 0:
            time.sleep(3)  # Increased wait time for processes to fully terminate
            
            # Verify processes are actually gone
            remaining_count = 0
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    proc_name = proc.info['name'] or ''
                    if 'chrome' in proc_name.lower() or 'chromedriver' in proc_name.lower():
                        remaining_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            # Only check for remaining Portable Chrome processes (not all Chrome)
            remaining_count = 0
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    proc_name = proc.info['name'] or ''
                    if 'chrome' in proc_name.lower() or 'chromedriver' in proc_name.lower():
                        try:
                            proc_exe = proc.info.get('exe', '')
                            if proc_exe and portable_chrome_dir in proc_exe.lower():
                                remaining_count += 1
                        except (psutil.AccessDenied, AttributeError):
                            pass
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            
            if remaining_count > 0:
                debug_line(f"- [{account}]: Killed {killed_count} Portable Chrome process(es), but {remaining_count} still remain")
                debug_line(f"- [{account}]: NOTE: Regular Chrome processes are NOT affected - only Portable Chrome")
            else:
                debug_line(f"- [{account}]: Successfully killed {killed_count} Portable Chrome process(es)")
    except Exception as e:
        print(f"- [{account}]: Error killing Chrome processes: {e}")

    # Remove stale Chrome singleton lock files — left behind when Chrome crashes and
    # will cause the next launch to exit immediately thinking another instance is running.
    # On Linux, SingletonLock is a symlink; os.path.exists() returns False for dangling
    # symlinks (e.g. after a container restart changes the hostname), so use lexists().
    for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = os.path.join(chrome_user_data_dir, lock_file)
        try:
            if os.path.lexists(path):
                os.remove(path)
        except OSError:
            pass


def update_profile_preferences(account, chrome_user_data_dir):
    """
    Update Chrome profile preferences file with account information.
    Handles file locking by killing Chrome processes first if needed.
    
    Args:
        account: Account username/identifier
        chrome_user_data_dir: Path to Chrome user data directory
    """
    preferences_file = os.path.join(chrome_user_data_dir, account, 'Preferences')
    
    if not os.path.exists(preferences_file):
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(preferences_file), exist_ok=True)
        # Create empty preferences file
        with open(preferences_file, 'w', encoding='utf-8') as f:
            json.dump({}, f)
    
    # Try to update preferences with retry logic
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            # On first failure, try killing Chrome processes
            if attempt > 0:
                print(f"- [{account}]: Retrying preferences update (attempt {attempt + 1}/{max_retries})...")
                kill_chrome_processes_for_profile(chrome_user_data_dir, account)
                time.sleep(retry_delay)
            
            # Try to read and write with timeout
            with open(preferences_file, 'r', encoding='utf-8') as f:
                prefs = json.load(f)
            
            if 'profile' not in prefs:
                prefs['profile'] = {}
            
            prefs['profile']['name'] = account
            prefs['profile']['user_name'] = account
            prefs['profile']['exit_type'] = 'Normal'
            prefs['profile']['exited_cleanly'] = True

            # Drop junk keys an earlier version wrote into Preferences. Neither is a
            # real Chrome preference (excludeSwitches is a chromedriver capability),
            # so they did nothing except mark the profile as bot-managed.
            prefs.pop('excludeSwitches', None)
            prefs.pop('prefs', None)

            # Write with timeout protection
            with open(preferences_file, 'w', encoding='utf-8') as f:
                json.dump(prefs, f, indent=2)
            
            debug_line(f"- [{account}]: updated Preferences")
            return  # Success, exit function
            
        except PermissionError as e:
            if attempt < max_retries - 1:
                print(f"- [{account}]: Preferences file locked, killing Chrome processes and retrying...")
                kill_chrome_processes_for_profile(chrome_user_data_dir, account)
                time.sleep(retry_delay)
            else:
                print(f"- [{account}]: Could not update Preferences after {max_retries} attempts: {e}")
                print(f"- [{account}]: File may be locked by running Chrome instance. Please close Chrome manually.")
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"- [{account}]: Error updating Preferences (attempt {attempt + 1}): {e}, retrying...")
                time.sleep(retry_delay)
            else:
                print(f"- [{account}]: Could not update Preferences: {e}")


def load_base_arguments():
    """
    Load base Chrome arguments from the [browser-config] section of the config file.

    Returns:
        list: List of base argument strings
    """
    base_arguments = []
    if 'add_argument' in CONFIG['browser-config']:
        add_arg_str = CONFIG['browser-config']['add_argument']
        # Parse multi-line arguments (split by newline, strip whitespace, filter empty/commented lines)
        for line in add_arg_str.split('\n'):
            line = line.strip()
            if line and not line.startswith(';'):  # Skip empty lines and comments
                base_arguments.append(line)
    return base_arguments


def get_debugging_port(account_idx):
    """
    Get a fixed remote debugging port for an account based on its index.
    Uses base port 9222 + account index to ensure unique ports per account.
    
    Args:
        account_idx: Account index (0-based)
        
    Returns:
        int: Port number (9222 + account_idx)
    """
    base_port = 9222
    return base_port + account_idx


def resolve_chrome_binary(chrome_path):
    """
    Resolve the configured chrome_path to the real Chrome executable.

    - Empty/None → None (Selenium Manager picks the system Chrome).
    - PortableApps layout: `<dir>/chrome.exe` is a small launcher that spawns the real
      browser and exits — chromedriver reads that as "Chrome crashed". If
      `<dir>/App/Chrome-bin/chrome.exe` exists, return that instead.
    - Anything else is returned unchanged (e.g. /usr/bin/google-chrome exec's in place).
    """
    if not chrome_path:
        return None
    chrome_path = str(chrome_path).strip()
    if not chrome_path:
        return None
    real = os.path.join(os.path.dirname(os.path.abspath(chrome_path)), 'App', 'Chrome-bin', 'chrome.exe')
    if os.path.isfile(real):
        return real
    return chrome_path


def _major_version(text):
    """'147.0.7727.101' → 147; None if unparseable."""
    try:
        return int(str(text).strip().split('.')[0])
    except (ValueError, AttributeError):
        return None


def portable_chrome_major(chrome_binary):
    """
    Major version of a PortableApps Chrome from its App/Chrome-bin/<version>/ folder.
    Returns None for anything that isn't that layout.
    """
    if not chrome_binary:
        return None
    bin_dir = os.path.dirname(os.path.abspath(chrome_binary))
    if os.path.basename(bin_dir).lower() != 'chrome-bin':
        return None
    try:
        versions = [_major_version(d) for d in os.listdir(bin_dir)
                    if os.path.isdir(os.path.join(bin_dir, d)) and _major_version(d)]
    except OSError:
        return None
    return max(versions) if versions else None


def profile_last_major(chrome_user_data_dir):
    """Major version of the Chrome that last opened this user-data-dir (its 'Last Version' file)."""
    try:
        with open(os.path.join(chrome_user_data_dir, 'Last Version'), encoding='utf-8') as fh:
            return _major_version(fh.read())
    except OSError:
        return None


def build_chrome_arguments(account, chrome_user_data_dir, base_arguments, debugging_port):
    """
    Build the final list of Chrome arguments.

    The browser's own User-Agent is never overridden: `--user-agent=` only rewrites the
    UA header/string, not UA Client Hints or navigator.platform, so any override is
    self-contradicting. Let Chrome identify as the Chrome it actually is.

    Args:
        account: Account username/identifier
        chrome_user_data_dir: Path to Chrome user data directory
        base_arguments: List of base arguments from config file
        debugging_port: Remote debugging port number

    Returns:
        list: Final list of Chrome argument strings
    """
    arguments = []

    # User data dir and profile directory (account-specific, set dynamically)
    arguments.append(f'--user-data-dir={chrome_user_data_dir}')
    arguments.append(f'--profile-directory={account}')

    # Add fixed remote debugging port (overrides config if set to 0)
    arguments.append(f'--remote-debugging-port={debugging_port}')

    # Add other base arguments from config (excluding user-agent, user-data-dir, profile-directory, remote-debugging-port)
    for arg in base_arguments:
        if not any(arg.startswith(prefix) for prefix in ['--user-agent=', '--user-data-dir=', '--profile-directory=', '--remote-debugging-port=']):
            arguments.append(arg)

    return arguments


def setup_chrome_options(account, chrome_user_data_dir, debugging_port, chrome_binary=None):
    """
    Setup Chrome options with all necessary arguments.

    Args:
        account: Account username/identifier
        chrome_user_data_dir: Path to Chrome user data directory
        debugging_port: Remote debugging port number
        chrome_binary: Resolved Chrome executable (see resolve_chrome_binary); None → system Chrome

    Returns:
        ChromeOptions: Configured Chrome options object
    """
    # Load base arguments from config file
    base_arguments = load_base_arguments()

    # Build final arguments list
    arguments = build_chrome_arguments(account, chrome_user_data_dir, base_arguments, debugging_port)

    # Create and configure options
    options = ChromeOptions()
    for arg in arguments:
        options.add_argument(arg)

    # Launch the Chrome we configured (not whatever Selenium Manager finds on PATH).
    # Selenium Manager reads the version from this binary and fetches the matching chromedriver.
    if chrome_binary:
        options.binary_location = chrome_binary

    # Headless + Linux/Docker flags
    system_type = CONFIG.get('bot_settings', 'system_type', fallback='windows')
    # Linux always runs headed into Xvfb via noVNC — headless config is ignored
    headless = False if system_type == 'linux' else CONFIG.getboolean('browser-session', 'headless', fallback=False)
    if headless:
        options.add_argument('--headless=new')
    if system_type == 'linux':
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        # No GPU in a container. Chrome (M130+) will not fall back to software WebGL on
        # its own in headed mode, so ask for ANGLE-on-SwiftShader explicitly — a desktop
        # Chrome with no WebGL at all is a fingerprint. (Verified under Xvfb: with
        # --disable-gpu alone, canvas.getContext('webgl') is null.)
        options.add_argument('--disable-gpu')
        options.add_argument('--use-gl=angle')
        options.add_argument('--use-angle=swiftshader')
        options.add_argument('--disable-setuid-sandbox')
        # Xvfb has no window manager, so driver.maximize_window() is a no-op and
        # Chrome opens small in the top-left. Size the window to the Xvfb display
        # (1920x1080, set in entrypoint.sh) so it fills the noVNC view.
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--window-position=0,0')

    # Make navigator.webdriver read `false` — the value a normal Chrome reports.
    # Nothing else is patched: injected JS shims (fake window.chrome, redefined
    # navigator.webdriver, non-native permissions.query) are themselves detectable.
    if '--disable-blink-features=AutomationControlled' not in arguments:
        options.add_argument('--disable-blink-features=AutomationControlled')

    # Keep chromedriver from advertising itself (--enable-automation switch / extension)
    options.add_experimental_option("excludeSwitches", [
        "enable-automation",  # Removes automation flag
        "enable-logging"  # Reduces logging
    ])
    options.add_experimental_option('useAutomationExtension', False)
    
    # Set minimal preferences - only what's necessary
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,  # Block notifications
        "autofill.profile_enabled": False,  # Disable autofill
        "autofill.credit_card_enabled": False,  # Disable credit card autofill
        "profile.default_content_settings.popups": 0,  # Allow popups (prevents some dialogs)
        "intl.accept_languages": "en-US,en",  # Force English locale so Meta consent wall renders in English
    }
    options.add_experimental_option("prefs", prefs)

    # Add arguments to prevent sign-in prompts and password manager dialogs
    additional_args = [
        '--disable-features=PasswordManager,AutofillPasswordManager',  # Disable password manager completely
        '--disable-save-password-bubble',  # No save password prompts
        '--no-restore-last-session',        # Suppress "Restore pages?" dialog after crash
        '--disable-session-crashed-bubble', # Suppress crash recovery infobar
        '--lang=en-US',                     # Force Chrome UI and page locale to English (US)
    ]
    for arg in additional_args:
        if arg not in [a for a in options.arguments]:
            options.add_argument(arg)

    return options


def create_driver(account, account_idx=0):
    """
    Create and configure Chrome driver for an account.
    Handles all setup: user data dir, Local State, Preferences, Chrome options.
    Uses fixed remote debugging port based on account index for reliable reconnection.

    Args:
        account: Account username/identifier
        account_idx: Account index (0-based) for port assignment
        
    Returns:
        webdriver.Chrome: Configured Chrome driver instance
        - Creates new Chrome instance with ChromeService
        - Can reconnect to existing instances using remote debugging
        
    Note:
        - New instances: driver.quit() will close the Chrome browser
        - Reconnected instances: driver.quit() will only disconnect, browser stays open
    """
    
    # Get fixed debugging port for this account
    debugging_port = get_debugging_port(account_idx)
    debug_line(f"- [{account}]: using remote debugging port: {debugging_port}")
    
    # Load configuration from [browser-config] and [browser-session] sections
    # If chrome_path is empty, Selenium will use system Chrome.
    # Otherwise resolve it to the real executable (PortableApps launcher → App/Chrome-bin/chrome.exe).
    chrome_path = CONFIG['browser-config'].get('chrome_path', '').strip()
    if chrome_path:
        chrome_path = resolve_chrome_binary(resolve_path(chrome_path))
    else:
        chrome_path = None
    chrome_version = int(CONFIG['browser-config'].get('chrome_version', '143') or '143')
    HEADLESS = CONFIG.getboolean('browser-session', 'headless', fallback=False)
    
    # Detect if running via SSH or remote session (Windows)
    is_remote_session = False
    can_display_windows = True
    
    # Check for SSH/remote session indicators
    ssh_env_vars = ['SSH_TTY', 'SSH_CONNECTION', 'SSH_CLIENT', 'SSH_AUTH_SOCK']
    has_ssh_env = any(os.environ.get(var) for var in ssh_env_vars)
    
    # On Windows, check session type
    try:
        import sys
        if sys.platform == 'win32':
            import ctypes
            # Check if running in a console session (non-interactive)
            # This is a rough check - if we're in a non-interactive session, windows may not be visible
            session_id = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
            # Session ID 0 typically means non-interactive
            if session_id == 0:
                is_remote_session = True
                can_display_windows = False
    except Exception:
        pass
    
    # If SSH environment variables are present, likely running via SSH
    if has_ssh_env:
        is_remote_session = True
        can_display_windows = False
    
    # Warn user if running remotely without headless mode
    if is_remote_session and not HEADLESS:
        print(f"- [{account}]: WARNING: Detected SSH/remote session")
        print(f"- [{account}]: Chrome windows will NOT be visible when running via SSH")
        print(f"- [{account}]: Chrome is running in background (you can verify via Task Manager)")
        print(f"- [{account}]: To see Chrome windows, use RDP/VNC or set headless=true in config")
        print(f"- [{account}]: The bot will continue running - Chrome is functional even if not visible")
    
    # Build user data dir path per account
    chrome_user_data_dir = build_user_data_dir(account)

    # A profile that was last opened by a newer Chrome than the configured PortableChrome
    # (typical for installs that ran on system Chrome before binary_location was honoured)
    # would make Chrome refuse to start. Fall back to system Chrome rather than break.
    _portable_major = portable_chrome_major(chrome_path)
    _profile_major = profile_last_major(chrome_user_data_dir)
    if _portable_major and _profile_major and _profile_major > _portable_major:
        print(client_log_line(
            account, "browser",
            f"PortableChrome is v{_portable_major} but this profile was last opened by Chrome v{_profile_major} — "
            f"using system Chrome instead (update PortableChrome or clear chrome_path to silence this)",
        ))
        chrome_path = None

    # Try to reconnect to existing Chrome instance first
    # Use the expected fixed port for this account
    is_running, debug_port = find_existing_chrome_process(chrome_user_data_dir, account, debugging_port)
    if is_running and debug_port:
        # Pass chrome version and path for proper connection
        driver = connect_to_existing_chrome(account, chrome_user_data_dir, debug_port, 
                                            chrome_version=chrome_version, chrome_path=chrome_path)
        if driver:
            debug_line(f"- [{account}]: Reconnected to existing Chrome instance on port {debug_port}")
            debug_line(f"- [{account}]: IMPORTANT: Using existing Chrome - no new window should be created")
            # Skip file updates and new driver creation since we're using existing instance
            # Return immediately to prevent any new Chrome instance creation
            return driver
        else:
            # Reconnection failed - just kill processes directly
            # No need to try reconnecting again - it already failed once
            print(f"- [{account}]: Failed to reconnect, killing Chrome processes for this profile...")
            kill_chrome_processes_for_profile(chrome_user_data_dir, account)
            # Wait a bit for processes to fully terminate
            time.sleep(2)
    elif is_running:
        # Chrome process found but no debugging port detected - try connecting to expected port anyway
        # (since we use fixed ports, the port should be the expected one)
        print(f"- [{account}]: Chrome process found but port not detected, attempting connection to expected port {debugging_port}...")
        driver = connect_to_existing_chrome(account, chrome_user_data_dir, debugging_port, 
                                            chrome_version=chrome_version, chrome_path=chrome_path)
        if driver:
            print(f"- [{account}]: Successfully connected to Chrome on expected port {debugging_port}")
            return driver
        else:
            # Connection failed - kill and create new
            print(f"- [{account}]: Connection to expected port failed, killing and creating new instance")
            kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
            time.sleep(2)
    else:
        # No existing process found - proceed normally (may still need to kill if port is in use)
        # Check if port is in use by another process
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', debugging_port))
            sock.close()
            if result == 0:
                # Port is in use — kill the process holding it (may belong to another account's Chrome)
                print(f"- [{account}]: Port {debugging_port} is in use, killing process on port...")
                killed_port = False
                if psutil:
                    try:
                        for conn in psutil.net_connections(kind='tcp'):
                            if conn.laddr.port == debugging_port and conn.status == 'LISTEN':
                                try:
                                    psutil.Process(conn.pid).kill()
                                    killed_port = True
                                except Exception:
                                    pass
                    except Exception:
                        pass
                if not killed_port:
                    kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
                time.sleep(2)
        except (socket.error, socket.timeout):
            pass
    
    # Always clean singleton lock files before launch — stale locks from a previous
    # container session (persisted on the volume) cause "Chrome instance exited" crashes.
    # Use lexists() so dangling symlinks (SingletonLock on Linux) are also removed.
    for _lock in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        _lock_path = os.path.join(chrome_user_data_dir, _lock)
        try:
            if os.path.lexists(_lock_path):
                os.remove(_lock_path)
        except OSError:
            pass

    # Update Local State file
    update_local_state(account, chrome_user_data_dir)

    # Update profile preferences (will retry if locked)
    update_profile_preferences(account, chrome_user_data_dir)
    
    # Verify Chrome executable exists (only if path is specified)
    if chrome_path and not os.path.exists(chrome_path):
        raise FileNotFoundError(f"Chrome executable not found at: {chrome_path}")
    
    # Check for excessive Chrome processes BEFORE starting (indicates previous failures)
    # If there are too many orphaned processes, kill Chrome processes using this user data dir
    if psutil:
        try:
            chrome_count = 0
            chrome_user_data_dir_lower = chrome_user_data_dir.lower()
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    proc_name = proc.info['name'] or ''
                    if 'chrome' in proc_name.lower() or 'chromedriver' in proc_name.lower():
                        # Check if it's using our user data directory
                        cmdline = proc.info.get('cmdline', [])
                        cmdline_str = ' '.join(cmdline).lower() if cmdline else ''
                        if chrome_user_data_dir_lower in cmdline_str:
                            chrome_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                    pass
            
            if chrome_count > 20:
                print(f"- [{account}]: WARNING: Found {chrome_count} Chrome processes using this user data dir - cleaning up orphaned processes...")
                
                # Kill Chrome processes using this user data directory when there are too many
                # This prevents orphaned processes from blocking Chrome startup
                killed_all = 0
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        proc_name = proc.info['name'] or ''
                        if 'chrome' in proc_name.lower() or 'chromedriver' in proc_name.lower():
                            cmdline = proc.info.get('cmdline', [])
                            cmdline_str = ' '.join(cmdline).lower() if cmdline else ''
                            if chrome_user_data_dir_lower in cmdline_str:
                                # Kill Chrome processes using this user data directory
                                try:
                                    proc.terminate()
                                    try:
                                        proc.wait(timeout=1)
                                    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                                        proc.kill()
                                    # Kill children
                                    try:
                                        for child in proc.children(recursive=True):
                                            try:
                                                child.terminate()
                                                child.wait(timeout=0.5)
                                            except:
                                                try:
                                                    child.kill()
                                                except:
                                                    pass
                                    except:
                                        pass
                                    killed_all += 1
                                except (psutil.NoSuchProcess, psutil.AccessDenied):
                                    pass
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError):
                        pass
                
                if killed_all > 0:
                    print(f"- [{account}]: Killed {killed_all} orphaned Chrome process(es)")
                    time.sleep(5)  # Wait for processes to fully terminate
                else:
                    # Fallback: kill only this account's processes
                    kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
                    time.sleep(2)
        except Exception as e:
            print(f"- [{account}]: Could not check Chrome processes before start: {e}")
    
    # Create driver with retry logic (Chrome may need time to start)
    max_retries = 3
    retry_delay = 5  # Increased delay between retries
    driver = None
    
    for attempt in range(max_retries):
        try:
            # Before creating a new Chrome instance, check if one is already running for this account
            # This prevents creating multiple instances if a previous attempt succeeded but we didn't detect it
            if attempt > 0:  # Only check on retries (first attempt already checked above)
                is_running_check, debug_port_check = find_existing_chrome_process(chrome_user_data_dir, account, debugging_port)
                if is_running_check and debug_port_check:
                    print(f"- [{account}]: Found existing Chrome instance on port {debug_port_check}, attempting to connect...")
                    driver = connect_to_existing_chrome(account, chrome_user_data_dir, debug_port_check, 
                                                        chrome_version=chrome_version, chrome_path=chrome_path)
                    if driver:
                        print(f"- [{account}]: Successfully connected to existing Chrome instance")
                        return driver
                    else:
                        print(f"- [{account}]: Failed to connect to existing Chrome, will create new instance")
            
            print(client_log_line(account, "browser", f"create chrome driver -attempt[{attempt + 1}/{max_retries}]"))
            
            # CRITICAL: Create fresh ChromeOptions for each attempt
            options = setup_chrome_options(account, chrome_user_data_dir, debugging_port, chrome_binary=chrome_path)
            
            # Verify the debugging port argument was set correctly (only on first attempt)
            if attempt == 0:
                port_found_in_options = False
                for arg in options.arguments:
                    if '--remote-debugging-port=' in arg:
                        debug_line(f"- [{account}]: Verified debugging port in options: {arg}")
                        port_found_in_options = True
                        break
                
                if not port_found_in_options:
                    debug_line(f"- [{account}]: WARNING: Debugging port argument not found in options")
            
            # Use Selenium 4's automatic driver management (no service needed)
            # Selenium 4 will automatically download and manage the correct ChromeDriver version
            # This avoids version mismatches - Selenium 4 handles compatibility automatically
            service = None
            
            debug_line(f"- [{account}]: Launching Chrome (this may take a moment)...")
            
            # Try to create driver - this will launch Chrome
            # Use threading with timeout to prevent indefinite hanging
            driver_result = [None]
            driver_exception = [None]
            
            def create_chrome_driver():
                try:
                    # Suppress Chrome's stdout/stderr when debug is disabled
                    if not is_bot_debug_enabled():
                        import sys
                        import io
                        # Redirect stdout/stderr during Chrome creation to suppress Chrome messages
                        original_stdout = sys.stdout
                        original_stderr = sys.stderr
                        sys.stdout = io.StringIO()
                        sys.stderr = io.StringIO()
                        try:
                            # Create standard Selenium Chrome driver with our service and options
                            # The debugging port is set via options (--remote-debugging-port argument)
                            if service:
                                driver_result[0] = webdriver.Chrome(
                                    service=service,
                                    options=options
                                )
                            else:
                                # Let Selenium 4 auto-manage the driver
                                driver_result[0] = webdriver.Chrome(options=options)
                        finally:
                            # Restore stdout/stderr
                            sys.stdout = original_stdout
                            sys.stderr = original_stderr
                    else:
                        # Debug mode: show all Chrome output
                        if service:
                            driver_result[0] = webdriver.Chrome(
                                service=service,
                                options=options
                            )
                        else:
                            # Let Selenium 4 auto-manage the driver
                            driver_result[0] = webdriver.Chrome(options=options)
                except Exception as e:
                    driver_exception[0] = e
            
            # CRITICAL: Ensure the debugging port is free before launching Chrome
            # Chrome may choose a different port if the specified one is in use
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                result = sock.connect_ex(('127.0.0.1', debugging_port))
                sock.close()
                if result == 0:
                    print(f"- [{account}]: WARNING: Port {debugging_port} is already in use - Chrome may choose a different port")
                    print(f"- [{account}]: This can cause connection failures")
                    # Try to kill any Chrome processes using this port
                    if psutil:
                        try:
                            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                                try:
                                    proc_name = proc.info['name'] or ''
                                    cmdline = proc.info['cmdline'] or []
                                    if 'chrome' in proc_name.lower():
                                        cmdline_str = ' '.join(cmdline).lower()
                                        if f'--remote-debugging-port={debugging_port}' in cmdline_str:
                                            print(f"- [{account}]: Killing Chrome process (PID: {proc.info['pid']}) using port {debugging_port}")
                                            proc.terminate()
                                            try:
                                                proc.wait(timeout=5)
                                            except psutil.TimeoutExpired:
                                                proc.kill()
                                except (psutil.NoSuchProcess, psutil.AccessDenied):
                                    pass
                        except Exception as e:
                            print(f"- [{account}]: Error clearing port {debugging_port}: {e}")
            except Exception as port_check_error:
                print(f"- [{account}]: Could not check port {debugging_port}: {port_check_error}")

            # Launch Chrome in a separate thread with timeout
            chrome_thread = threading.Thread(target=create_chrome_driver)
            chrome_thread.daemon = True
            chrome_thread.start()

            # Wait for Chrome to start with timeout (increased for reliability)
            chrome_thread.join(timeout=120)  # Increased to 2 minutes

            # Check if thread is still alive (timed out)
            if chrome_thread.is_alive():
                # Chrome creation is taking too long - timeout
                print(f"- [{account}]: Chrome launch TIMEOUT after 120 seconds - checking if Chrome actually started...")

                # Check if Chrome is actually running on the expected port before killing it
                chrome_running = False
                actual_port = None
                
                # First check if the expected port is listening (most reliable check)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(3)  # Increased timeout for more reliable detection
                    result = sock.connect_ex(('127.0.0.1', debugging_port))
                    sock.close()
                    if result == 0:
                        # Port is listening - Chrome may have started!
                        print(f"- [{account}]: Port {debugging_port} is listening - Chrome may have started successfully")
                        # Double-check with DevTools API if available
                        if requests:
                            try:
                                response = requests.get(f"http://127.0.0.1:{debugging_port}/json", timeout=2)
                                if response.status_code == 200:
                                    chrome_running = True
                                    actual_port = debugging_port
                                    print(f"- [{account}]: Verified Chrome is running on port {debugging_port} via DevTools API")
                            except Exception:
                                # Port is listening but DevTools API not responding yet - still try to connect
                                chrome_running = True
                                actual_port = debugging_port
                                print(f"- [{account}]: Port {debugging_port} is listening (DevTools API check pending)")
                        else:
                            chrome_running = True
                            actual_port = debugging_port
                except Exception as port_check_error:
                    debug_line(f"- [{account}]: Error checking port {debugging_port}: {port_check_error}")
                
                # Also check if Chrome process is running with our profile
                # Look for any Chrome process that matches our profile, regardless of port
                if not chrome_running and psutil:
                    try:
                        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                            try:
                                proc_name = proc.info['name'] or ''
                                if 'chrome' in proc_name.lower():
                                    cmdline = proc.info.get('cmdline', [])
                                    if cmdline:
                                        cmdline_str = ' '.join(cmdline).lower()
                                        # Check if it matches our profile
                                        user_data_match = f'--user-data-dir={chrome_user_data_dir.lower()}' in cmdline_str
                                        profile_match = f'--profile-directory={account.lower()}' in cmdline_str

                                        if user_data_match and profile_match:
                                            # Found our Chrome process! Now find what port it's using
                                            proc_port = None
                                            for arg in cmdline:
                                                if '--remote-debugging-port=' in arg.lower():
                                                    try:
                                                        proc_port = int(arg.split('=')[1])
                                                        break
                                                    except (ValueError, IndexError):
                                                        pass

                                            if proc_port:
                                                # Check if this port is listening
                                                try:
                                                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                                    sock.settimeout(2)
                                                    if sock.connect_ex(('127.0.0.1', proc_port)) == 0:
                                                        chrome_running = True
                                                        actual_port = proc_port
                                                        print(f"- [{account}]: Found Chrome process running on port {proc_port}")
                                                        sock.close()
                                                        break
                                                except Exception:
                                                    pass
                            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                                pass
                    except Exception as e:
                        print(f"- [{account}]: Error checking for Chrome process: {e}")
                
                if chrome_running and actual_port:
                    # Chrome IS running! Try to connect to it instead of killing it
                    print(f"- [{account}]: Chrome is running - attempting to connect to existing instance on port {actual_port}...")
                    try:
                        connected_driver = connect_to_existing_chrome(account, chrome_user_data_dir, actual_port, 
                                                                      chrome_version=chrome_version, chrome_path=chrome_path)
                        if connected_driver:
                            print(f"- [{account}]: Successfully connected to Chrome that was already running!")
                            # Return immediately - we have a working driver
                            return connected_driver
                        else:
                            print(f"- [{account}]: Failed to connect to running Chrome - will kill and retry")
                            chrome_running = False
                    except Exception as connect_error:
                        print(f"- [{account}]: Error connecting to running Chrome: {connect_error} - will kill and retry")
                        chrome_running = False
                
                if not chrome_running:
                    # Chrome is NOT running - safe to kill and retry
                    print(f"- [{account}]: Chrome is NOT running on expected port {debugging_port} - killing processes and cleaning up...")
                    kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
                else:
                    # Chrome IS running but we couldn't connect - this is rare but could happen
                    # Instead of killing and retrying (which might close a working browser),
                    # let's wait a bit longer and see if it becomes connectable
                    print(f"- [{account}]: Chrome is running but connection failed - waiting 30 seconds for Chrome to become ready...")
                    time.sleep(30)

                    # Try one more connection attempt
                    try:
                        final_driver = connect_to_existing_chrome(account, chrome_user_data_dir, actual_port,
                                                                  chrome_version=chrome_version, chrome_path=chrome_path)
                        if final_driver:
                            print(f"- [{account}]: SUCCESS - Connected to Chrome on second attempt!")
                            return final_driver
                    except Exception as final_error:
                        print(f"- [{account}]: Final connection attempt failed: {final_error}")

                    # Only kill and retry if all connection attempts fail
                    print(f"- [{account}]: All connection attempts failed - killing processes and cleaning up...")
                    kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
                    
                    # Also kill ALL orphaned Portable Chrome processes if there are many
                    # BUT ONLY if Chrome is truly not running (to avoid killing active instances)
                    if psutil:
                        portable_chrome_dir = os.path.dirname(os.path.abspath(chrome_path)).lower()
                        orphaned_count = 0
                        for proc in psutil.process_iter(['pid', 'name', 'exe']):
                            try:
                                proc_name = proc.info['name'] or ''
                                if 'chrome' in proc_name.lower() or 'chromedriver' in proc_name.lower():
                                    proc_exe = proc.info.get('exe', '')
                                    if proc_exe and portable_chrome_dir in proc_exe.lower():
                                        orphaned_count += 1
                            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                                pass
                        
                        if orphaned_count > 10:
                            print(f"- [{account}]: Found {orphaned_count} Portable Chrome processes - killing ONLY truly orphaned processes (preserving active instances)...")
                            killed_orphans = 0
                            # Get list of active debugging ports (9222, 9223, etc.) to preserve active Chrome instances
                            active_ports = set()
                            base_port = 9222
                            for i in range(10):  # Check ports 9222-9231
                                port = base_port + i
                                try:
                                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                    sock.settimeout(0.5)
                                    if sock.connect_ex(('127.0.0.1', port)) == 0:
                                        active_ports.add(port)
                                    sock.close()
                                except:
                                    pass
                            
                            for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
                                try:
                                    proc_name = proc.info['name'] or ''
                                    if 'chrome' in proc_name.lower() or 'chromedriver' in proc_name.lower():
                                        proc_exe = proc.info.get('exe', '')
                                        if proc_exe and portable_chrome_dir in proc_exe.lower():
                                            # Check if this process has an active debugging port
                                            cmdline = proc.info.get('cmdline', [])
                                            proc_port = None
                                            is_active = False
                                            
                                            if cmdline:
                                                for arg in cmdline:
                                                    if '--remote-debugging-port=' in arg.lower():
                                                        try:
                                                            proc_port = int(arg.split('=')[1])
                                                            if proc_port in active_ports:
                                                                is_active = True
                                                                break
                                                        except (ValueError, IndexError):
                                                            pass
                                            
                                            # Only kill if it's NOT an active Chrome instance
                                            if not is_active:
                                                try:
                                                    proc.terminate()
                                                    try:
                                                        proc.wait(timeout=1)
                                                    except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                                                        proc.kill()
                                                    # Kill children
                                                    try:
                                                        for child in proc.children(recursive=True):
                                                            try:
                                                                child.terminate()
                                                                child.wait(timeout=0.5)
                                                            except:
                                                                try:
                                                                    child.kill()
                                                                except:
                                                                    pass
                                                    except:
                                                        pass
                                                    killed_orphans += 1
                                                except (psutil.NoSuchProcess, psutil.AccessDenied):
                                                    pass
                                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError):
                                    pass
                            
                            if killed_orphans > 0:
                                print(f"- [{account}]: Killed {killed_orphans} orphaned Portable Chrome process(es) (preserved {len(active_ports)} active instance(s))")
                
                # Wait longer for processes to fully terminate and cleanup
                time.sleep(8)
                
                # Verify processes are actually gone before retrying
                if psutil:
                    remaining = 0
                    for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
                        try:
                            proc_name = proc.info['name'] or ''
                            if 'chrome' in proc_name.lower():
                                # Check if it's our account's Chrome
                                cmdline = proc.info.get('cmdline', [])
                                if cmdline:
                                    cmdline_str = ' '.join(cmdline).lower()
                                    if chrome_user_data_dir.lower() in cmdline_str and account.lower() in cmdline_str:
                                        remaining += 1
                                        # Try to kill it again
                                        try:
                                            proc.kill()
                                        except:
                                            pass
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            pass
                    
                    if remaining > 0:
                        print(f"- [{account}]: WARNING: {remaining} Chrome process(es) still running after cleanup, waiting longer...")
                        time.sleep(5)
                        # Final aggressive kill
                        kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
                        time.sleep(3)
                
                # Note: The daemon thread will continue running but can't create driver since we're not waiting for it
                # The thread will be garbage collected when it finishes or when the function returns
                
                if attempt < max_retries - 1:
                    print(f"- [{account}]: Retrying Chrome creation (attempt {attempt + 2}/{max_retries})...")
                    time.sleep(2)  # Additional wait before retry
                    continue
                else:
                    error_msg = f"Chrome launch timed out after 30 seconds (attempted {max_retries} times)"
                    print(f"- [{account}]: FATAL ERROR: {error_msg}")
                    print(f"- [{account}]: Possible causes:")
                    print(f"- [{account}]:   - Chrome executable may be corrupted or incompatible")
                    print(f"- [{account}]:   - System resources may be insufficient")
                    print(f"- [{account}]:   - Another process may be blocking Chrome startup")
                    print(f"- [{account}]:   - Profile directory may be locked or corrupted")
                    raise TimeoutError(error_msg)
            
            # Check if Chrome creation succeeded or threw an exception
            if driver_exception[0]:
                raise driver_exception[0]
            
            if driver_result[0] is None:
                raise Exception("Chrome driver creation returned None (unknown error)")
            
            driver = driver_result[0]
            debug_line(f"- [{account}]: Chrome driver object created, verifying connection...")
            
            # Give Chrome more time to fully initialize and load profile
            debug_line(f"- [{account}]: Waiting for Chrome to fully initialize and load profile...")
            time.sleep(8)  # Increased wait time for profile to load
            
            # Verify driver is actually connected by checking window_handles
            try:
                windows = driver.window_handles
                window_text = "window" if len(windows) == 1 else "windows"
                _ordinals = ["1st", "2nd", "3rd"]
                _ordinal = _ordinals[attempt] if attempt < len(_ordinals) else f"{attempt + 1}th"
                print(client_log_line(account, "browser", f"chrome driver connected on {_ordinal} attempt"))
            except Exception as verify_error:
                print(f"- [{account}]: Driver created but connection verification failed: {verify_error}")
                print(f"- [{account}]: This may indicate Chrome crashed or failed to start properly")
                
                # Check if Chrome processes are still running
                if psutil:
                    try:
                        chrome_count = 0
                        for proc in psutil.process_iter(['pid', 'name']):
                            try:
                                proc_name = proc.info['name'] or ''
                                if 'chrome' in proc_name.lower():
                                    chrome_count += 1
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        print(f"- [{account}]: Chrome processes currently running: {chrome_count}")
                    except:
                        pass
                
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
                driver = None
                if attempt < max_retries - 1:
                    print(f"- [{account}]: Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    continue
                else:
                    raise Exception(f"Chrome driver connection verification failed after {max_retries} attempts")
            
            # Verify profile match with retry logic (profile may need time to load)
            debug_line(f"- [{account}]: Verifying profile match...")
            
            # Wait a bit for Chrome to fully start before verification
            time.sleep(2)
            
            profile_verified = False
            profile_retry_count = 0
            max_profile_retries = 5  # Increased retries
            
            while not profile_verified and profile_retry_count < max_profile_retries:
                if verify_profile_match(driver, account, chrome_user_data_dir):
                    profile_verified = True
                    debug_line(f"- [{account}]: Profile verification SUCCESS")
                else:
                    profile_retry_count += 1
                    if profile_retry_count < max_profile_retries:
                        debug_line(f"- [{account}]: Profile verification failed, waiting for profile to load (retry {profile_retry_count}/{max_profile_retries})...")
                        time.sleep(2)  # Wait for profile to load
                    else:
                        # Don't fail completely - Chrome might still work, just log the warning
                        # The profile verification might fail due to timing, but Chrome could still be functional
                        debug_line(f"- [{account}]: WARNING: Profile verification failed after {max_profile_retries} attempts")
                        debug_line(f"- [{account}]: Chrome may still be working correctly - verification is not always reliable")
            
            # Verify Chrome process is actually running with correct profile
            # This is a non-critical check - if driver connection succeeded, Chrome is working
            if psutil:
                chrome_running = False
                try:
                    chrome_user_data_dir_lower = chrome_user_data_dir.lower()
                    account_lower = account.lower()
                    
                    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                        try:
                            proc_name = proc.info['name'] or ''
                            cmdline = proc.info['cmdline'] or []
                            
                            # Check for both 'chrome' and 'chromium' process names
                            if 'chrome' in proc_name.lower() or 'chromium' in proc_name.lower():
                                cmdline_str = ' '.join(cmdline).lower()
                                
                                # Check if user data dir is in command line
                                user_data_dir_match = chrome_user_data_dir_lower in cmdline_str
                                
                                # Check if account name appears (could be in --profile-directory=account)
                                account_match = account_lower in cmdline_str
                                profile_dir_match = f'--profile-directory={account_lower}' in cmdline_str
                                
                                if user_data_dir_match and (account_match or profile_dir_match):
                                    chrome_running = True
                                    debug_line(f"- [{account}]: Verified Chrome process is running with correct profile (PID: {proc.info['pid']})")
                                    break
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            pass
                    
                    if not chrome_running:
                        # Only show warning if debug enabled - driver connection succeeded, so Chrome is working
                        debug_line(f"- [{account}]: WARNING: Chrome driver created but process verification failed")
                        debug_line(f"- [{account}]: Chrome is likely working correctly - driver connection succeeded")
                except Exception as proc_check_error:
                    debug_line(f"- [{account}]: Could not verify Chrome process: {proc_check_error}")
            
            # If we get here, driver is connected and verified
            try:
                driver.maximize_window()
            except Exception:
                # Maximize might fail in headless/remote sessions, that's okay
                pass
            
            # Nothing is injected post-launch. Earlier versions patched navigator.webdriver,
            # window.chrome and permissions.query here and forced Accept-Language/locale via
            # CDP; those shims are themselves fingerprintable, and --lang + the
            # intl.accept_languages pref already give an English UI and a native header.

            # Log what actually launched (real browser version, driver version, binary) —
            # this is how version drift between the configured Chrome and the running one
            # becomes visible per customer in the uploaded run log.
            try:
                _caps = driver.capabilities or {}
                _bv = _caps.get('browserVersion') or '----'
                _dv = ((_caps.get('chrome') or {}).get('chromedriverVersion') or '').split(' ')[0] or '----'
                print(client_log_line(account, "browser", f"chrome:[{_bv}] / driver:[{_dv}] / binary:[{chrome_path or 'system'}]"))
            except Exception as _cap_err:
                debug_line(f"- [{account}]: Could not read browser capabilities: {_cap_err}")

            debug_line(f"- [{account}]: driver/browser created and verified")
            
            # If running remotely, remind user how to verify Chrome is running
            if is_remote_session and not HEADLESS:
                print(f"- [{account}]: NOTE: Verify Chrome is running by checking Task Manager (chrome.exe processes)")
                print(f"- [{account}]: NOTE: Chrome window is not visible via SSH but browser is functional")
            
            return driver
            
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            print(f"- [{account}]: Chrome driver creation failed (attempt {attempt + 1}/{max_retries}): {error_type}: {error_msg}")
            
            # Clean up any partial driver instance
            if driver:
                try:
                    driver.quit()
                except:
                    pass
                driver = None
            
            # Check for specific error types
            if "session not created" in error_msg.lower() or "cannot connect" in error_msg.lower():
                # This usually means Chrome started but WebDriver can't connect
                print(f"- [{account}]: Connection error detected - Chrome may have started but WebDriver cannot connect")
                
                # Check for Chrome processes - if they exist, Chrome started but might be in a bad state
                if psutil:
                    chrome_procs = []
                    try:
                        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                            try:
                                proc_name = proc.info['name'] or ''
                                cmdline = proc.info['cmdline'] or []
                                if 'chrome' in proc_name.lower():
                                    cmdline_str = ' '.join(cmdline).lower()
                                    if chrome_user_data_dir.lower() in cmdline_str and account.lower() in cmdline_str:
                                        chrome_procs.append(proc.info['pid'])
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        
                        if chrome_procs:
                            print(f"- [{account}]: Found {len(chrome_procs)} Chrome process(es) for this profile - Chrome started but connection failed")
                            print(f"- [{account}]: This may be a timing issue or Chrome is in a bad state")
                    except Exception as proc_check:
                        print(f"- [{account}]: Could not check Chrome processes: {proc_check}")
                
                if attempt < max_retries - 1:
                    print(f"- [{account}]: Killing any Chrome processes and retrying in {retry_delay} seconds...")
                    
                    # Kill Portable Chrome processes before retrying
                    kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
                    
                    # Additional wait to ensure processes are fully terminated
                    time.sleep(2)
                    
                    # Wait before retrying
                    time.sleep(retry_delay)
                    continue
                else:
                    print(f"- [{account}]: Failed to create Chrome driver after {max_retries} attempts")
                    print(f"- [{account}]: Possible causes:")
                    print(f"- [{account}]:   1. Chrome executable is corrupted or incompatible")
                    print(f"- [{account}]:   2. User data directory is corrupted (try deleting it)")
                    print(f"- [{account}]:   3. Insufficient permissions")
                    print(f"- [{account}]:   4. Chrome version mismatch (expected: {chrome_version})")
                    print(f"- [{account}]: Last error: {error_type}: {error_msg}")
                    raise
            
            # For other errors, retry once more before failing
            if attempt < max_retries - 1:
                print(f"- [{account}]: Retrying in {retry_delay} seconds...")
                # Kill Portable Chrome processes before retrying
                kill_chrome_processes_for_profile(chrome_user_data_dir, account, chrome_path)
                time.sleep(retry_delay)
            else:
                raise
    
    # If we get here, all retries failed
    raise Exception(f"Failed to create Chrome driver after {max_retries} attempts")
