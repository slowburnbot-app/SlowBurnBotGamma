# burnBot.py

from burnBot_imports import *
from burnBot_config import load_config, CONFIG, resolve_path, clear_api_credentials_in_ini
from burnBot_utils import close_windows, has_internet_connection, process_exception, delay, check_schedule, consume_connectivity_recovery_notice, register_shutdown_flag
from burnBot_accountSession import accountSession
from burnBot_accountSession_setup import is_bot_debug_enabled
from burnBot_run_log import debug_line
from burnBot_apiClient import (
    ApiClient,
    AuthenticationError,
    SubscriptionRequiredError,
    get_stored_api_credentials,
    set_shared_client,
    store_api_credentials,
)
from burnBot_runCounter import RunCounter
from burnBot_version import BOT_VERSION
from burnBot_client_log import client_log_line, mask_email, mask_phone
import burnBot_status as status_store
from burnBot_app import BurnBotApp
from datetime import datetime, timedelta
import math
import socket

def _beep(kind):
    # Tone tables: list of (freq_hz, duration_ms)
    _tones = {
        'startup':  [(880, 100), (1047, 100), (523, 320)],   # beep beep boop
        'shutdown': [(784, 200), (659, 200), (523, 400)],
        'keypress': [(880, 150), (660, 300)],
    }
    tones = _tones.get(kind)
    if not tones:
        return
    try:
        import winsound
        for freq, dur in tones:
            winsound.Beep(freq, dur)
        return
    except Exception:
        pass
    # Non-Windows (docker/Linux): ring the user's TERMINAL bell (not the VNC display).
    # One BEL travels container -> docker -> tmux -> Windows Terminal, which plays its
    # configured bellSound. The terminal owns the timbre/melody, so emit a single BEL.
    try:
        sys.__stdout__.write('\a')
        sys.__stdout__.flush()
    except Exception:
        pass

def _default_config_path():
    """Return the default INI path: next to the executable (frozen) or CWD (dev)."""
    exe_path = getattr(sys, "executable", None) or sys.argv[0]
    exe_dir = os.path.dirname(os.path.abspath(exe_path))
    return os.path.join(exe_dir, "burnBot_config.ini")


def _write_ini_from_activation(response: dict, config_path: str) -> None:
    """Write a burnBot_config.ini from the /bot/desktop/activate response.

    Browser/driver settings are written to [browser-config] and [browser-session] using hardcoded defaults.
    Only client_name and system_type come from the server (build_options).
    """
    import configparser
    opts = response.get("build_options", {})
    system_type = opts.get("system_type", "windows")

    cp = configparser.ConfigParser()
    cp["api"] = {"api_url": _HARDCODED_API_URL}
    cp["api_credentials"] = {"email": "", "password": ""}
    cp["bot_settings"] = {
        "client_id": str(response.get("client_id", 1)),
        "client_name": opts.get("client_name", ""),
        "system_type": system_type,
        "bot_debug": "False",
    }

    if system_type == "linux":
        cp["browser-config"] = {
            "chrome_version": "",
            "chrome_path": "/usr/bin/google-chrome",
            "chrome_user_data_dir_base": "ChromeUserData",
            "debug_base_port": "9222",
            "add_argument": "",
        }
        cp["browser-session"] = {
            "headless": "False",
            "close_browser_after_session": "False",
            "close_browser_after_exit": "False",
            "bot_idle_delay": "0.25",
            "novnc_url": opts.get("novnc_url", "http://localhost:6080/vnc.html"),
        }
    elif system_type == "macos":
        cp["browser-config"] = {
            "chrome_version": "143",
            # The user's own Google Chrome. Clear it to let Selenium Manager locate
            # Chrome itself (e.g. an install under ~/Applications).
            "chrome_path": MACOS_CHROME_PATH,
            "chrome_user_data_dir_base": "ChromeUserData",
            "debug_base_port": "9222",
            "add_argument": "",
        }
        cp["browser-session"] = {
            "headless": "False",
            "close_browser_after_session": "False",
            "close_browser_after_exit": "False",
            "bot_idle_delay": "0.25",
        }
    else:
        cp["browser-config"] = {
            "chrome_version": "143",
            # Empty → system-installed Google Chrome (auto-updating). Profiles still live
            # under PortableChrome\user_<account>. Set to a chrome.exe path to override.
            "chrome_path": "",
            "chrome_user_data_dir_base": "PortableChrome",
            # Base remote-debugging port; each account uses base+index. Raise it if 9222+
            # is taken on this machine (e.g. a netsh portproxy rule).
            "debug_base_port": "9222",
            "add_argument": "",
        }
        cp["browser-session"] = {
            "headless": "False",
            "close_browser_after_session": "False",
            "close_browser_after_exit": "False",
            "bot_idle_delay": "0.25",
        }

    with open(config_path, "w") as fh:
        cp.write(fh)


_HARDCODED_API_URL = "https://slowburnbotgamma-production.up.railway.app"
MACOS_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _parse_activation_token(token: str) -> tuple[str, int]:
    """
    Parse user_id and client_id from a token formatted as:
      {user_id}_{client_id}_{random_secret}
    Returns (user_id, client_id) or exits with an error message.
    """
    parts = token.split("_", 2)
    if len(parts) != 3:
        print("ERROR: Invalid activation token format. Copy the token exactly from your dashboard.")
        sys.exit(1)
    user_id, client_id_str, _ = parts
    try:
        client_id_int = int(client_id_str)
    except ValueError:
        print("ERROR: Invalid activation token format. Copy the token exactly from your dashboard.")
        sys.exit(1)
    return user_id, client_id_int


def _run_activation_prompt() -> dict:
    """
    Show a simple console prompt for activation on first run.
    Returns the parsed activation response from the server.
    """
    print("=" * 60)
    print("  SlowBurnBot — First Run Setup")
    print("  Paste the Activation Token from your dashboard → Clients page.")
    print("=" * 60)
    token = input("Activation Token: ").strip()
    user_id, client_id_int = _parse_activation_token(token)

    tmp_client = ApiClient(_HARDCODED_API_URL)
    result = tmp_client.activate_desktop_build(
        user_id=user_id,
        client_id=client_id_int,
        activation_token=token,
        bot_version=BOT_VERSION,
    )
    return result


# Load config — None means INI is missing (first run)
# argv[1] overrides the default path (used by Linux/Docker to point at a mounted volume)
config_path = sys.argv[1] if len(sys.argv) > 1 else _default_config_path()
config_file = load_config(config_path)

if config_file is None:
    # First run: prompt for activation, write INI, then reload
    print(f"No config file found at {config_path}. Starting activation…")
    try:
        _activation_result = _run_activation_prompt()
    except Exception as _act_err:
        print(f"[ERROR] Activation failed: {_act_err}")
        sys.exit(1)
    _write_ini_from_activation(_activation_result, config_path)
    print(f"Configuration saved to {config_path}")
    config_file = load_config(config_path)
    if config_file is None:
        print("[ERROR] Failed to load config after activation.")
        sys.exit(1)

# Load API connection
api_url = CONFIG.get('api', 'api_url', fallback='')
if not api_url:
    print("ERROR: 'api_url' not set in [api] section of config file")
    sys.exit(1)

apiClient = ApiClient(api_url)
set_shared_client(apiClient)


def _api_credentials_from_ini():
    email = CONFIG.get("api_credentials", "email", fallback="").strip()
    password = CONFIG.get("api_credentials", "password", fallback="").strip()
    return email, password


def _resolve_api_credentials():
    """Return (email, password, source).

    Prefers the OS keyring; falls back to the [api_credentials] section of
    the INI for one-time migration.  source is 'keyring', 'ini', or None.
    """
    email, password = get_stored_api_credentials()
    if email and password:
        return email, password, "keyring"
    email, password = _api_credentials_from_ini()
    if email and password:
        return email, password, "ini"
    return None, None, None


def _migrate_ini_credentials_to_keyring(email, password):
    """After first successful login from INI fallback, store in keyring and
    wipe the plaintext entries from the on-disk config."""
    try:
        store_api_credentials(email, password)
    except Exception as e:
        status_store.add_log(client_log_line(None, "api", f"Could not store credentials in keyring: {e}"))
        return
    if clear_api_credentials_in_ini():
        status_store.add_log(client_log_line(None, "api", "Plaintext credentials migrated from config to OS keyring."))


def _try_api_relogin_from_config(client):
    """Re-authenticate when the keyring JWT is stale.  Pulls creds from the
    keyring first, falls back to the INI for legacy installs."""
    email, password, source = _resolve_api_credentials()
    if not email or not password:
        return False
    status_store.add_log(client_log_line(None, "api", "Session expired — re-authenticating…"))
    ok = client.login(email, password)
    if ok:
        status_store.add_log(client_log_line(None, "api", "Re-login OK."))
        if source == "ini":
            _migrate_ini_credentials_to_keyring(email, password)
    return bool(ok)


_vnc_services_started = False

def _start_vnc_services(pin=''):
    """Start x11vnc and websockify on Linux, routing their output to the TUI log.
    Called once after login so the PIN is available to pass to x11vnc."""
    global _vnc_services_started
    if _vnc_services_started:
        return
    if sys.platform != 'linux':
        status_store.set_vnc_ready()
        return
    _vnc_services_started = True
    import subprocess

    # Nuitka sets LD_LIBRARY_PATH to its extracted temp dir. Subprocesses inherit
    # this and pick up the frozen libcrypto/libssl instead of system ones, causing
    # websockify (and potentially x11vnc) to crash on import. Strip them here.
    clean_env = os.environ.copy()
    for _var in ('LD_LIBRARY_PATH', 'LD_PRELOAD'):
        clean_env.pop(_var, None)

    _start_announced = threading.Event()

    def _drain(proc, label):
        try:
            time.sleep(3)  # let cfg startup messages finish before VNC output appears
            if not _start_announced.is_set():
                _start_announced.set()
                status_store.add_log(client_log_line(None, "x11/noVNC", "services starting"))
            for raw in proc.stdout:
                line = raw.decode(errors='replace').rstrip()
                if line:
                    status_store.add_log(client_log_line(None, label, line))
        except Exception:
            pass

    def _vnc_footer(delay):
        time.sleep(delay)
        status_store.set_vnc_ready()
        status_store.add_log(client_log_line(None, "x11/noVNC", "services ready"))

    x11vnc_args = ['x11vnc', '-display', ':99', '-forever', '-rfbport', '5900', '-quiet']
    if pin:
        x11vnc_args += ['-passwd', pin]
    else:
        x11vnc_args += ['-nopw']

    _vnc_started = False
    try:
        x11vnc = subprocess.Popen(
            x11vnc_args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=clean_env
        )
        threading.Thread(target=_drain, args=(x11vnc, 'x11/noVNC'), daemon=True).start()
        _vnc_started = True
    except FileNotFoundError:
        pass

    try:
        wsify = subprocess.Popen(
            ['websockify', '--web', '/usr/share/novnc/', '--heartbeat', '30', '6080', 'localhost:5900'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=clean_env
        )
        threading.Thread(target=_drain, args=(wsify, 'x11/noVNC'), daemon=True).start()
        _vnc_started = True
    except FileNotFoundError:
        pass

    if _vnc_started:
        threading.Thread(target=_vnc_footer, args=(6,), daemon=True).start()
    else:
        status_store.set_vnc_ready()

# Load bot_idle_delay for main loop check interval (in minutes, convert to seconds)
bot_idle_delay_minutes = CONFIG.getfloat('browser-session', 'bot_idle_delay', fallback=0.25)
bot_idle_delay = bot_idle_delay_minutes * 60  # Convert to seconds

# Helper function for interruptible sleep
def sleep_with_interrupt_check(seconds, stop_flag, label=None, console=None):
    """Sleep for `seconds`, waking early if stop_flag is set. Returns True if interrupted."""
    intervals = int(seconds / 0.5)
    for _ in range(intervals):
        if stop_flag.is_set() or status_store.is_stop_requested():
            return True
        time.sleep(0.5)
    return False


# ------------------------------------------------------------------
# Authentication — prompt for login if no stored token
# ------------------------------------------------------------------
if not apiClient.has_token():
    stored_email, stored_password, source = _resolve_api_credentials()

    if stored_email and stored_password:
        status_store.add_log(client_log_line(None, "api", f"Using stored credentials ({source})…"))
        if apiClient.login(stored_email, stored_password):
            if source == "ini":
                _migrate_ini_credentials_to_keyring(stored_email, stored_password)
        else:
            status_store.add_log(client_log_line(None, "api", "Stored credentials failed. Falling back to manual entry."))

    if not apiClient.has_token():
        print("Login required.")
        print("=" * 60)
        email = input("Email: ").strip()
        password = input("Password: ").strip()
        if not apiClient.login(email, password):
            print("Login failed. Exiting.")
            sys.exit(1)
        # Persist to keyring so the next run can authenticate non-interactively.
        try:
            store_api_credentials(email, password)
        except Exception as e:
            status_store.add_log(client_log_line(None, "api", f"Could not store credentials in keyring: {e}"))

    status_store.add_log(client_log_line(None, "api", "Login successful."))

if sys.platform == 'linux':
    _novnc_url = CONFIG.get('browser-session', 'novnc_url', fallback='http://localhost:6080/vnc.html').strip() or 'http://localhost:6080/vnc.html'
    if 'resize=' not in _novnc_url:
        _sep = '&' if '#' in _novnc_url else '#'
        _novnc_url += f'{_sep}autoconnect=1&resize=scale'
    status_store.set_vnc_info(url=_novnc_url)

# Account tracking data structures
all_accounts = []
started_accounts = []
enabled_accounts = {}
account_thread_index = {}

account_schedules = {}
account_last_run = {}
account_next_run = {}
threads = []
threads_active = []
stop_flag = threading.Event()
register_shutdown_flag(stop_flag)  # let wait_for_internet_restore() notice shutdown while offline

run_counter = RunCounter(resolve_path("burnBot_runs.json"))

client_id = CONFIG.get('bot_settings', 'client_id', fallback='')

def normalize_group(value):
    s = str(value).strip() if value is not None else ""
    if not s:
        return ""
    try:
        return str(int(float(s)))
    except Exception:
        return s

client_id_norm = normalize_group(client_id)

# Fast config reads needed by the app constructor (no API calls)
_sg = str(CONFIG.get('bot_settings', 'client_id', fallback='')).strip()
try:
    _sg = f"{int(_sg):02d}"
except Exception:
    pass
_client_name = CONFIG.get('bot_settings', 'client_name', fallback='').strip()


class _BotConsole:
    @staticmethod
    def print(*args, **kwargs):
        status_store.add_log(" ".join(str(a) for a in args))

console = _BotConsole()


class _TuiStdout:
    def write(self, text):
        if text and not text.isspace():
            status_store.add_log(text.rstrip())
    def flush(self):
        pass
    def isatty(self):
        return False


try:
    def _bot_loop():
        # Wait for TUI to finish rendering before generating any log output
        time.sleep(1.5)

        # ------------------------------------------------------------------
        # Startup fetch: entitlement + user config + account list combined
        # ------------------------------------------------------------------
        _init_group = int(client_id_norm) if client_id_norm else None
        _known_version = ""
        all_accounts = []
        try:
            try:
                _init_state = apiClient.get_client_state(group_number=_init_group)
            except AuthenticationError:
                if _try_api_relogin_from_config(apiClient):
                    _init_state = apiClient.get_client_state(group_number=_init_group)
                else:
                    raise
            entitlement = _init_state.get("entitlement", {})
            if not entitlement.get("active"):
                status_store.add_log(client_log_line(None, "api", "Subscription is not active."))
                status_store.add_log(client_log_line(None, "api", f"Plan: {entitlement.get('plan_tier', 'free')}"))
                status_store.add_log(client_log_line(None, "api", "Please activate your subscription at the dashboard."))
                stop_flag.set()
                return
            all_accounts = _init_state.get("accounts") or []
            _known_version = _init_state.get("version", "")
            status_store.set_current_bot_version(_init_state.get("current_bot_version"))
            for _acct in all_accounts:
                _name = _acct.get("name", "")
                if _name:
                    if _acct.get("system_disabled"):
                        _init_st = "system-disabled"
                    elif not _acct.get("enabled", False):
                        _init_st = "disabled"
                    else:
                        _init_st = "initializing"
                    status_store.update(_name, status=_init_st, next_run="—", last_action="—", run_info="—")
        except AuthenticationError:
            status_store.add_log(client_log_line(None, "api", "Session expired. Add email/password under [api_credentials] in burnBot_config.ini for auto re-login, or restart and sign in when prompted."))
            status_store.set_auth_failed()
            stop_flag.set()
            return
        except Exception as e:
            status_store.add_log(client_log_line(None, "api", f"Startup fetch failed: {e}"))
            stop_flag.set()
            return

        # ------------------------------------------------------------------
        # Fetch user config and emit startup log
        # ------------------------------------------------------------------
        _plan = entitlement.get("plan_tier", "free")
        _cs  = CONFIG.get('browser-session', 'close_browser_after_session', fallback='FALSE').strip().upper()
        _ce  = CONFIG.get('browser-session', 'close_browser_after_exit',    fallback='FALSE').strip().upper()
        _idl = str(bot_idle_delay_minutes).zfill(2)
        _st  = CONFIG.get('bot_settings', 'system_type',           fallback='').strip()
        _notif_cfg = _init_state.get("user_config") or {}
        status_store.seed_notify_from_config(_notif_cfg)
        status_store.set_remote_debug(_notif_cfg.get('bot_debug'))
        _vnc_pin = (_notif_cfg.get('vnc_pin') or '').strip()
        _cur_vnc_url, _ = status_store.get_vnc_info()
        status_store.set_vnc_info(url=_cur_vnc_url, pin=_vnc_pin)
        _skl = str(_notif_cfg.get('skip_login_check', False)).upper()
        _lgt = str(_notif_cfg.get('login_tries', 3)).zfill(2)
        _lks = str(_notif_cfg.get('like_suggested', False)).upper()
        _lkp = str(_notif_cfg.get('like_sponsored', False)).upper()
        _ant = (_notif_cfg.get('notices_type') or 'none').strip()
        _ans = str(_notif_cfg.get('notices_session', False)).upper()
        _ane = (_notif_cfg.get('notify_email') or '').strip()
        _anp = (_notif_cfg.get('notify_phone') or '').strip()

        def _log(msg):
            status_store.add_log(msg)
            time.sleep(0.05)

        _started_at = datetime.now().strftime("%I:%M %p")
        _client_label = _client_name or "unnamed"
        _dbg_on = is_bot_debug_enabled()  # resolved value (remote UserConfig.bot_debug, else this INI key)
        _cs_on = _cs.upper() == "TRUE"
        _ce_on = _ce.upper() == "TRUE"
        _lks_on = _lks.upper() == "TRUE"
        _lkp_on = _lkp.upper() == "TRUE"
        _skl_on = _skl.upper() == "TRUE"
        _ans_on = _ans.upper() == "TRUE"
        _log(client_log_line(None, "config", f"SlowBurn Client v{BOT_VERSION}  id:[{_sg}] / tag:[{_client_label}] / system:[{_st or 'unknown'}]"))
        _log(client_log_line("config", "api_url", f"[{api_url}]"))
        _log(client_log_line("config", "config_file", f"[{config_file}]"))
        _log(client_log_line("config", "debug-output", f"[{_dbg_on}]"))
        _log(client_log_line("config", "script-loop-delay", f"[{_idl}m]"))
        _log(client_log_line("config", "close_session", f"[{_cs_on}]"))
        _log(client_log_line("config", "close_exit", f"[{_ce_on}]"))
        _log(client_log_line("config", "like", f"suggested:[{_lks_on}] / sponsored:[{_lkp_on}]"))
        _log(client_log_line("config", "login", f"skip_check:[{_skl_on}] / login_attempts:[{_lgt}]"))
        _log(client_log_line("config", "notify[session]", f"type:[{_ant}] / email:[{mask_email(_ane)}] / phone:[{mask_phone(_anp)}]"))
        _log(client_log_line("config", "notify[issues]", f"type:[{_ant}] / email:[{mask_email(_ane)}] / phone:[{mask_phone(_anp)}]"))
        # Effective timezone — Chrome reports this to sites via Intl; a UTC container
        # under a Docker run that omitted `-v /etc/localtime:/etc/localtime:ro` is a mismatch.
        _tz = datetime.now().astimezone()
        _tz_name = _tz.tzname() or "unknown"
        _tz_off = _tz.strftime("%z")
        _log(client_log_line("config", "timezone", f"[{_tz_name}] / offset:[{_tz_off}]"))
        if _st == "linux" and _tz_off in ("+0000", "-0000") and _tz_name.upper() in ("UTC", "GMT"):
            _log(client_log_line("config", "timezone", "WARNING: container is on UTC — add `-v /etc/localtime:/etc/localtime:ro` to the docker run command so the browser reports your local timezone"))
        _log(client_log_line("config", "api", f"Subscription:[active] / plan:[{_plan}]"))
        _beep('startup')
        _start_vnc_services(pin=_vnc_pin)

        # Redirect stdout so plain print() in any module routes to the TUI log
        import sys as _sys
        _sys.stdout = _TuiStdout()

        # ------------------------------------------------------------------
        # Heartbeat setup
        # ------------------------------------------------------------------
        try:
            _local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            _local_ip = ""
        _heartbeat_system_type = _st

        _hb_lock = threading.Lock()
        _hb_state = {"status": "idle", "account": None}

        def _send_hb(status, account):
            with _hb_lock:
                _hb_state["status"] = status
                _hb_state["account"] = account
            apiClient.send_heartbeat(client_id_norm, _heartbeat_system_type, _local_ip, status, account, bot_version=BOT_VERSION)
            status_store.mark_heartbeat()
            try:
                _to = sys.__stdout__
                if _to:
                    _to.write(f'\033]2;SlowBurnBot v{BOT_VERSION}\007')
                    _to.flush()
            except Exception:
                pass

        def _heartbeat_loop():
            while not stop_flag.is_set():
                if stop_flag.wait(15):
                    return
                with _hb_lock:
                    status = _hb_state["status"]
                    account = _hb_state["account"]
                apiClient.send_heartbeat(client_id_norm, _heartbeat_system_type, _local_ip, status, account, bot_version=BOT_VERSION)
                status_store.mark_heartbeat()

        threading.Thread(target=_heartbeat_loop, daemon=True).start()

        # ------------------------------------------------------------------
        # Proactive token rotation. The persisted token may already be near
        # the end of its server-side lifetime (14 days), so mint a fresh one
        # now that we know the current one works, then re-mint daily.
        # ------------------------------------------------------------------
        _TOKEN_REFRESH_INTERVAL = 24 * 3600
        _TOKEN_REFRESH_RETRY = 3600
        if apiClient.refresh_token():
            status_store.add_log(client_log_line(None, "api", "Access token refreshed."))
        _last_token_refresh = time.monotonic()

        def _inactive_reason(acct):
            """Status label for an account the loop must not run, or None if it is runnable."""
            if acct.get("system_disabled"):
                return "system-disabled"
            if not acct.get("enabled", False):
                return "disabled"
            return None

        def _refresh_state():
            """Consolidated state poll (accounts + settings + user config + entitlement).

            Rebinds all_accounts when the server reports a change. Returns
            "auth_failed" / "stopped" when the loop must end, else None. Called
            at the top of every loop iteration AND periodically while a session
            is running, so dashboard edits (e.g. disabling an account) land
            without waiting for the running session to finish.
            """
            nonlocal _known_version, all_accounts
            _state = None
            try:
                group_param = int(client_id_norm) if client_id_norm else None
                try:
                    _state = apiClient.get_client_state(group_number=group_param, known_version=_known_version)
                except AuthenticationError:
                    if _try_api_relogin_from_config(apiClient):
                        try:
                            _state = apiClient.get_client_state(group_number=group_param, known_version=_known_version)
                        except AuthenticationError:
                            console.print(client_log_line(None, "system", "Session expired after re-login. Check [api_credentials] or dashboard."))
                            status_store.set_auth_failed()
                            stop_flag.set()
                            return "auth_failed"
                    else:
                        console.print(client_log_line(None, "system", "Session expired. Set [api_credentials] in burnBot_config.ini or restart and log in."))
                        status_store.set_auth_failed()
                        stop_flag.set()
                        return "auth_failed"
            except Exception as e:
                debug_line(client_log_line(None, "system", f"Warning - state refresh failed, using cached values: {e}"))

            # Mid-run entitlement check and account/settings refresh
            if _state is not None:
                _entitlement = _state.get("entitlement", {})
                if not _entitlement.get("active"):
                    status_store.add_log(client_log_line(None, "api", "Subscription is no longer active — stopping."))
                    stop_flag.set()
                    return "stopped"
                if _state.get("changed"):
                    _known_version = _state.get("version", _known_version)
                    all_accounts = _state.get("accounts") or all_accounts
                    status_store.set_remote_debug((_state.get("user_config") or {}).get('bot_debug'))
                status_store.set_current_bot_version(_state.get("current_bot_version"))
            return None

        def _mark_inactive_rows(skip=None):
            """Push disabled/system-disabled status for every non-runnable account (except `skip`)."""
            for _a in all_accounts:
                _n = _a.get("name", "")
                _reason = _inactive_reason(_a)
                if _n and _n != skip and _reason:
                    status_store.update(_n, status=_reason, next_run="—", last_action="—", run_info="—")

        _SESSION_STATE_POLL = 30  # seconds between state polls while a session is running

        # ------------------------------------------------------------------
        # Main loop
        # ------------------------------------------------------------------
        while True:
            current_time = datetime.now().astimezone()

            # Daily token refresh (retry hourly on failure)
            if time.monotonic() - _last_token_refresh >= _TOKEN_REFRESH_INTERVAL:
                if apiClient.refresh_token():
                    status_store.add_log(client_log_line(None, "api", "Access token refreshed."))
                    _last_token_refresh = time.monotonic()
                else:
                    _last_token_refresh = time.monotonic() - _TOKEN_REFRESH_INTERVAL + _TOKEN_REFRESH_RETRY

            # Consolidated state fetch (accounts + settings + user config + entitlement)
            _outcome = _refresh_state()
            if _outcome == "auth_failed":
                break
            if _outcome == "stopped":
                return

            # Pre-populate table so all accounts appear immediately on first loop
            for _acct in all_accounts:
                _name = _acct.get("name", "")
                if _name and _name not in status_store._store:
                    _pre_st = _inactive_reason(_acct) or "initializing"
                    status_store.update(_name, status=_pre_st, next_run="—", last_action="—", run_info="—")

            # Rebuild enabled accounts and update schedules
            enabled_accounts = {}
            for acct in all_accounts:
                account_name = acct.get("name", "")
                account_id = acct.get("id", "")
                is_enabled = acct.get("enabled", False)
                is_system_disabled = acct.get("system_disabled", False)

                if is_enabled and not is_system_disabled:
                    enabled_accounts[account_name] = acct

                # Fetch settings for schedule data (use cache)
                settings = apiClient.get_account_settings(account_id) if account_id else None

                if settings:
                    schedule_days = settings.get("schedule_days") or ""
                    schedule_start = settings.get("schedule_start") or ""
                    schedule_end = settings.get("schedule_end") or ""
                    delay_base = settings.get("delay_base_minutes", 60) or 60
                    delay_random = settings.get("delay_random_minutes", 0) or 0
                    schedule_max = settings.get("max_runs_per_day", 0) or 0
                    schedule_max_random = settings.get("max_runs_random_per_day", 0) or 0

                    old_delay = account_schedules.get(account_name, {}).get('delay_sig')
                    new_delay_sig = (float(delay_base), float(delay_random))
                    if old_delay is not None and old_delay != new_delay_sig:
                        account_next_run[account_name] = None
                        run_counter.set_next_run_time(account_name, None)

                    old_max_sig = account_schedules.get(account_name, {}).get('max_sig')
                    new_max_sig = (int(schedule_max), int(schedule_max_random))
                    max_sig_changed = old_max_sig is not None and old_max_sig != new_max_sig

                    account_schedules[account_name] = {
                        'days': schedule_days,
                        'start': schedule_start,
                        'end': schedule_end,
                        'delay': {'base': float(delay_base), 'random': float(delay_random)},
                        'delay_sig': new_delay_sig,
                        'max': account_schedules.get(account_name, {}).get('max', int(schedule_max)),
                        'max_base': int(schedule_max),
                        'max_random': int(schedule_max_random),
                        'max_sig': new_max_sig,
                        'max_random_offset': account_schedules.get(account_name, {}).get('max_random_offset'),
                        'start_random_offset': account_schedules.get(account_name, {}).get('start_random_offset'),
                        'last_offset_date': None if max_sig_changed else account_schedules.get(account_name, {}).get('last_offset_date'),
                    }

                # Ensure last run is loaded; push persisted display values to status_store on first load
                if account_name not in account_last_run:
                    lr = run_counter.get_last_run_time(account_name)
                    account_last_run[account_name] = lr
                    # Restore the persisted next-run time so a client reload doesn't
                    # reset the countdown — critical for accounts that haven't run
                    # yet today, whose next_run would otherwise re-anchor to process
                    # start time on every restart.
                    _persisted_next = run_counter.get_next_run_time(account_name)
                    if _persisted_next is not None and account_name not in account_next_run:
                        account_next_run[account_name] = _persisted_next
                    _restore = {}
                    if lr is not None:
                        _restore["last_run"] = lr.strftime("%m/%d %I:%M %p")
                    _la = run_counter.get_last_action(account_name)
                    if _la:
                        _restore["last_action"] = _la
                    if _restore:
                        status_store.update(account_name, **_restore)

            # Show status for all accounts, trigger active ones if it's time to run
            _any_account_waiting = False  # Track if any account is in-schedule and waiting
            for acct in all_accounts:
                account_name = acct.get("name", "")
                account_id = acct.get("id", "")

                if account_name not in enabled_accounts:
                    status_store.update(account_name, status=_inactive_reason(acct) or "disabled", next_run="—", last_action="—", run_info="—")
                    continue

                # Pause check — user typed /pause at runtime
                if status_store.is_bot_paused():
                    run_count = run_counter.get_run_count(account_name)
                    schedule_max = account_schedules.get(account_name, {}).get('max', 0) or 0
                    run_info = f"[{run_count}/{int(schedule_max)}]" if schedule_max > 0 else f"[{run_count}]"
                    status_store.update(account_name, status="paused", next_run="—", run_info=run_info)
                    continue

                # Account is active - check schedule and timing
                if account_name not in account_schedules:
                    status_store.update(account_name, status="no schedule", next_run="—", run_info="—")
                    continue

                schedule = account_schedules[account_name]
                last_run = account_last_run.get(account_name)
                delay_config = schedule.get('delay', {'base': 60.0, 'random': 0.0})
                next_run_time = account_next_run.get(account_name)

                # Calculate daily random offsets (start time and max runs)
                today_date = current_time.date()
                if schedule.get('last_offset_date') != today_date:
                    random_start_offset = 0
                    if isinstance(delay_config, dict) and delay_config.get('random', 0) > 0:
                        random_start_offset = random.uniform(0, delay_config['random'])
                    schedule['start_random_offset'] = random_start_offset

                    max_base = schedule.get('max_base', schedule.get('max', 0)) or 0
                    max_rng = schedule.get('max_random', 0) or 0
                    max_offset = random.randint(1, max_rng) if max_rng > 0 else 0
                    schedule['max_random_offset'] = max_offset
                    schedule['max'] = (max_base + max_offset) if max_base > 0 else 0

                    schedule['last_offset_date'] = today_date

                # Apply random offset to start time
                adjusted_start = schedule['start']
                if schedule.get('start_random_offset', 0) > 0 and adjusted_start:
                    try:
                        start_time = datetime.strptime(adjusted_start, "%I:%M %p")
                        start_time += timedelta(minutes=schedule['start_random_offset'])
                        adjusted_start = start_time.strftime("%I:%M %p")
                    except Exception:
                        adjusted_start = schedule['start']

                # Check if we're within the scheduled period
                if not check_schedule(schedule['days'], adjusted_start, schedule['end']):
                    _off_run_count = run_counter.get_run_count(account_name)
                    _off_smax = schedule.get('max', 0) or 0
                    _off_run_info = f"[{_off_run_count}/{int(_off_smax)}]" if _off_smax > 0 else f"[{_off_run_count}]"
                    status_store.update(account_name, status="off-schedule", next_run="—", run_info=_off_run_info)
                    account_next_run.pop(account_name, None)
                    continue

                # Check if max runs reached for today
                schedule_max = schedule.get('max', 0)
                current_run_count = run_counter.get_run_count(account_name)
                max_runs_reached = (schedule_max > 0 and current_run_count >= schedule_max)

                # Check if it's time to run this account
                should_run = False
                time_until_next = None

                if max_runs_reached:
                    should_run = False
                    time_until_next = None
                else:
                    if next_run_time is None:
                        base_delay = delay_config.get('base', 60.0)
                        random_delay = delay_config.get('random', 0.0)
                        delay_minutes = base_delay + (random.uniform(1, random_delay) if random_delay > 0 else 0)
                        last_run_today = last_run is not None and last_run.date() == current_time.date()
                        base_time = last_run if last_run_today else current_time
                        next_run_time = base_time + timedelta(minutes=delay_minutes)
                        account_next_run[account_name] = next_run_time
                        run_counter.set_next_run_time(account_name, next_run_time)

                    should_run = current_time >= next_run_time
                    time_until_next = next_run_time - current_time

                # Show account status / trigger run
                if should_run:
                    # `acct`/`enabled_accounts` were captured when this iteration began; an
                    # earlier account's session may have blocked for a long time since then.
                    # Re-poll and re-check so an account disabled on the dashboard mid-iteration
                    # is not triggered on stale data.
                    _refresh_state()
                    _live = next((a for a in all_accounts if a.get("name") == account_name), acct)
                    _live_reason = _inactive_reason(_live)
                    if _live_reason:
                        status_store.update(account_name, status=_live_reason, next_run="—", last_action="—", run_info="—")
                        continue

                    status_store.wait_vnc_ready()
                    run_count = run_counter.get_run_count(account_name)
                    next_run = run_count + 1
                    run_info = f"[{next_run}/{schedule_max}]" if schedule_max > 0 else f"[{next_run}]"
                    console.print(client_log_line(None, "bot", f"{account_name} - Triggering to ACTIVE state - run {run_info}"))
                    status_store.update(account_name, status="initializing", next_run="—", run_info=run_info, effective_max_runs=int(schedule_max))

                    # Get or create thread on-demand
                    account_idx = account_thread_index.get(account_name)
                    if account_idx is None:
                        event = threading.Event()
                        event.set()
                        account_idx = len(threads_active)
                        permanent_idx = account_idx
                        t = threading.Thread(target=accountSession,
                                            args=(account_name, account_id, account_idx, threads_active, stop_flag, apiClient, permanent_idx, console))
                        threads_active.append(event)
                        threads.append(t)
                        started_accounts.append(account_name)
                        account_thread_index[account_name] = account_idx
                        t.start()
                        while threads_active[account_idx].is_set():
                            if not t.is_alive():
                                break
                            time.sleep(1)

                    # If the session thread died (e.g. its initial settings fetch
                    # failed, or an uncaught exception unwound it) it will have
                    # cleared its event on the way out — indistinguishable from a
                    # normal idle-and-ready thread by the event alone. Check
                    # liveness before re-triggering it: setting the event on a dead
                    # thread has nobody left to clear it, which hung the whole bot
                    # (all accounts) until /pause. Forget the cached index so the
                    # next scheduled run spawns a fresh thread instead.
                    session_thread = threads[account_idx]
                    if not session_thread.is_alive():
                        console.print(client_log_line(account_name, "error", "session thread is dead — will respawn next cycle"))
                        account_thread_index.pop(account_name, None)
                        status_store.update(account_name, status="error", next_run="—", run_info="—")
                        continue

                    # Send running heartbeat before session starts
                    _send_hb("running", account_name)

                    # Set account to active
                    threads_active[account_idx].set()

                    # Wait for account to complete and return to idle. Keep polling state
                    # meanwhile so the other accounts' rows reflect dashboard edits (a
                    # disable toggle used to sit invisible until this session finished).
                    _next_state_poll = time.monotonic() + _SESSION_STATE_POLL
                    while threads_active[account_idx].is_set():
                        if status_store.is_bot_paused():
                            threads_active[account_idx].clear()
                        if not session_thread.is_alive():
                            break
                        if time.monotonic() >= _next_state_poll:
                            _refresh_state()
                            _mark_inactive_rows(skip=account_name)
                            _next_state_poll = time.monotonic() + _SESSION_STATE_POLL
                        time.sleep(1)

                    if not session_thread.is_alive():
                        console.print(client_log_line(account_name, "error", "session thread died mid-run — will respawn next cycle"))
                        account_thread_index.pop(account_name, None)
                        status_store.update(account_name, status="error", next_run="—", run_info="—")
                        continue

                    # Update last run time and increment run counter
                    run_finished_time = datetime.now().astimezone()
                    account_last_run[account_name] = run_finished_time
                    new_run_count = run_counter.increment_run_count(account_name)
                    run_counter.set_last_run_time(account_name, run_finished_time, last_action=status_store.get_last_action(account_name) or "session complete")

                    # Set a stable next_run_time
                    delay_config = account_schedules.get(account_name, {}).get('delay', {'base': 60.0, 'random': 0.0})
                    if isinstance(delay_config, dict):
                        base_delay = delay_config.get('base', 60.0)
                        random_delay = delay_config.get('random', 0.0)
                        delay_minutes = base_delay + random.uniform(0, random_delay)
                    else:
                        delay_minutes = float(delay_config)

                    account_next_run[account_name] = run_finished_time + timedelta(minutes=delay_minutes)
                    run_counter.set_next_run_time(account_name, account_next_run[account_name])

                    schedule = account_schedules.get(account_name, {})
                    schedule_max = schedule.get('max', 0)

                    run_info_done = f"[{new_run_count}/{schedule_max}]" if schedule_max > 0 else f"[{new_run_count}]"
                    console.print(client_log_line(account_name, "summary", f"run {run_info_done} - DONE"))
                    next_run_str = account_next_run[account_name].strftime("%m/%d %I:%M %p")
                    status_store.update(account_name, status="idle", next_run=next_run_str, run_info=run_info_done)
                elif max_runs_reached:
                    run_count = run_counter.get_run_count(account_name)
                    _max_run_info = f"[{run_count}/{int(schedule_max)}]" if schedule_max > 0 else f"[{run_count}]"
                    status_store.update(account_name, status="max runs", next_run="—", run_info=_max_run_info)
                    apiClient.invalidate_settings_cache(account_id)
                else:
                    next_run_str = account_next_run[account_name].strftime("%m/%d %I:%M %p") if account_next_run.get(account_name) else "—"
                    run_count = run_counter.get_run_count(account_name)
                    schedule_max = account_schedules.get(account_name, {}).get('max', 0) or 0
                    run_info = f"[{run_count}/{int(schedule_max)}]" if schedule_max > 0 else f"[{run_count}]"
                    status_store.update(account_name, status="waiting", next_run=next_run_str, run_info=run_info)
                    _any_account_waiting = True

            # Send heartbeat — determine overall client status
            if _any_account_waiting:
                _hb_status, _hb_account = "delay", None
            else:
                _hb_status, _hb_account = "idle", None
            _send_hb(_hb_status, _hb_account)

            if sleep_with_interrupt_check(bot_idle_delay, stop_flag, console=console):
                return

    app = BurnBotApp(BOT_VERSION, _sg, _client_name, _bot_loop, stop_flag)
    status_store.set_app(app)

    # Set tmux window name + pane title before Textual takes over stdout
    try:
        _t = sys.__stdout__
        if _t:
            _t.write('\033kSlowBurnBot\033\\')   # tmux window name (static)
            _t.write(f'\033]2;SlowBurnBot v{BOT_VERSION}\007')   # pane title: app name + version
            _t.flush()
    except Exception:
        pass

    app.run()
    os._exit(0)

except KeyboardInterrupt:
    print()
    _beep('shutdown')
    print("Stopping all sessions...")

    stop_flag.set()

    for i in range(len(threads_active)):
        if threads_active[i]:
            threads_active[i].clear()

    print("Waiting for all threads to finish...")
    print("(Press Ctrl+C again to force exit)")

    try:
        for i, t in enumerate(threads):
            if t and t.is_alive():
                account_name = started_accounts[i] if i < len(started_accounts) else f"thread-{i}"
                print(f"Waiting for {account_name} thread...")
                try:
                    t.join(timeout=10)
                except Exception:
                    pass
    except KeyboardInterrupt:
        print()
        print("Force exit requested - threads may not have completed cleanup")

    close_browser_exit = CONFIG.getboolean('browser-session', 'close_browser_after_exit', fallback=False)

    print("=" * 60)
    print("[bot]: All threads finished.")

    if close_browser_exit:
        print("[bot]: Browsers will close on exit.")
    else:
        print("[bot]: Browsers left open.")
        print()
        print("** Close this terminal window to keep browsers running **")
        print("   (Script will wait here indefinitely)")
        print()
        while True:
            time.sleep(3600)
