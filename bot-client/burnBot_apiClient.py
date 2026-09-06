# burnBot_apiClient.py
# Replaces burnBot_driveManager.py — all data access goes through the FastAPI backend

import json
import threading
import time
from datetime import datetime, date

import httpx

from burnBot_client_log import client_log_line


# The process-wide ApiClient instance, registered by burnBot.py at startup.
# Other modules must use get_shared_client() — NEVER `from burnBot import apiClient`:
# burnBot.py runs as __main__ with no main guard, so importing it by name
# re-executes the entire script (second login, second TUI, os._exit) and
# kills the process with no traceback.
_shared_client = None


def set_shared_client(client) -> None:
    global _shared_client
    _shared_client = client


def get_shared_client():
    """Return the ApiClient registered by burnBot.py, or None before startup completes."""
    return _shared_client


def _log_api_err(e, msg: str) -> None:
    """Log an API error, using a distinct offline prefix for network/DNS failures."""
    if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, httpx.ConnectTimeout)):
        print(client_log_line("api", "connection[offline]", f"{msg} (network unreachable)"))
    else:
        print(client_log_line(None, "api", f"{msg}: {e}"))


def _as_local_aware(dt):
    """Local wall time as timezone-aware so ISO strings include offset (avoids server treating naive as UTC)."""
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        return dt.astimezone()
    return dt
import keyring

KEYRING_SERVICE = "SlowBurnBot"
KEYRING_TOKEN_KEY = "access_token"
KEYRING_API_USER_KEY = "api_email"
KEYRING_API_PASS_KEY = "api_password"


def get_stored_api_credentials():
    """Return (email, password) from keyring, or (None, None) if missing."""
    try:
        email = keyring.get_password(KEYRING_SERVICE, KEYRING_API_USER_KEY)
        password = keyring.get_password(KEYRING_SERVICE, KEYRING_API_PASS_KEY)
    except Exception:
        return None, None
    return (email or None), (password or None)


def store_api_credentials(email, password):
    """Persist API credentials in the OS keyring."""
    keyring.set_password(KEYRING_SERVICE, KEYRING_API_USER_KEY, email)
    keyring.set_password(KEYRING_SERVICE, KEYRING_API_PASS_KEY, password)


def clear_api_credentials():
    """Remove stored API credentials from the keyring."""
    for key in (KEYRING_API_USER_KEY, KEYRING_API_PASS_KEY):
        try:
            keyring.delete_password(KEYRING_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


class ApiClient:
    """
    Thread-safe HTTP client that replaces DriveManager.
    All Google Sheets operations are now API calls to the FastAPI backend.
    """

    def __init__(self, api_url):
        self.api_url = api_url.rstrip("/")
        self.client = httpx.Client(base_url=self.api_url, timeout=30)
        self._token_lock = threading.Lock()
        self._access_token = None

        # Caches
        self._settings_cache = {}       # {account_id: (data, timestamp)}
        self._settings_cache_ttl = 60  # 1 minute
        self._ignore_cache = None
        self._ignore_cache_ts = 0.0
        self._ignore_cache_ttl = 300

        # Load token from keyring on startup
        self._load_token()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _load_token(self):
        """Load access token from keyring, with INI file as fallback (Linux/headless)."""
        try:
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_TOKEN_KEY)
            if token:
                self._access_token = token
                return
        except Exception:
            pass
        # INI fallback — survives container restarts as long as the INI is on a volume
        try:
            from burnBot_config import CONFIG
            token = CONFIG.get("api", "token", fallback="").strip()
            if token:
                self._access_token = token
        except Exception:
            pass

    def _save_token(self):
        """Save access token to keyring and INI file fallback."""
        if not self._access_token:
            return
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_TOKEN_KEY, self._access_token)
        except Exception:
            pass
        # Always write to INI so Linux/headless installs persist across restarts
        try:
            from burnBot_config import CONFIG, CONFIG_FILE_PATH
            if CONFIG_FILE_PATH:
                if not CONFIG.has_section("api"):
                    CONFIG.add_section("api")
                CONFIG.set("api", "token", self._access_token)
                with open(CONFIG_FILE_PATH, "w") as fh:
                    CONFIG.write(fh)
        except Exception:
            pass

    def _clear_token(self):
        """Remove stored token from keyring and INI."""
        self._access_token = None
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_TOKEN_KEY)
        except Exception:
            pass
        try:
            from burnBot_config import CONFIG, CONFIG_FILE_PATH
            if CONFIG_FILE_PATH and CONFIG.has_option("api", "token"):
                CONFIG.set("api", "token", "")
                with open(CONFIG_FILE_PATH, "w") as fh:
                    CONFIG.write(fh)
        except Exception:
            pass

    def _auth_headers(self):
        """Return Authorization header dict."""
        if not self._access_token:
            return {}
        return {"Authorization": f"Bearer {self._access_token}"}

    def has_token(self):
        """Check if we have an access token (may or may not be valid)."""
        return self._access_token is not None

    def login(self, email, password):
        """
        Authenticate with the API and store the JWT token.
        Returns True on success, False on failure.
        """
        try:
            resp = self.client.post(
                "/auth/jwt/login",
                data={"username": email, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 200:
                data = resp.json()
                self._access_token = data["access_token"]
                self._save_token()
                return True
            else:
                print(client_log_line(None, "api", f"Login failed (HTTP {resp.status_code})"))
                return False
        except Exception as e:
            _log_api_err(e, "Login error")
            return False

    def refresh_token(self):
        """
        Rotate the access token via /auth/jwt/refresh while it is still valid.
        The backend revokes the old token as soon as the new one is minted, so
        the swap must happen under the token lock. Returns True if a new token
        was stored; failures are non-fatal (the current token stays in place).
        """
        if not self._access_token:
            return False
        try:
            resp = self.client.post("/auth/jwt/refresh", headers=self._auth_headers())
            if resp.status_code == 200:
                new_token = resp.json().get("access_token")
                if new_token:
                    with self._token_lock:
                        self._access_token = new_token
                        self._save_token()
                    return True
        except Exception as e:
            _log_api_err(e, "Token refresh error")
        return False

    def _relogin(self):
        """Re-authenticate using stored credentials. Returns True on success."""
        with self._token_lock:
            email, password = get_stored_api_credentials()
            if email and password:
                return self.login(email, password)
            return False

    def _request(self, method, path, clear_on_auth_fail=True, **kwargs):
        """
        Make an authenticated request with auto-retry on 401.
        Raises an exception on persistent auth failure.

        clear_on_auth_fail=False keeps a 401 from wiping the stored token —
        for non-critical calls (heartbeat) that must never kill the session
        other threads are using.
        """
        headers = kwargs.pop("headers", {})
        token_used = self._access_token
        if token_used:
            headers["Authorization"] = f"Bearer {token_used}"

        resp = self.client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401:
            # The daily rotation revokes the old token the instant the new one
            # is minted, so a request built just before the swap can land with
            # a revoked token. If the stored token changed while this request
            # was in flight, the 401 is that race, not a real expiry — retry
            # once with the current token.
            with self._token_lock:
                current_token = self._access_token
            if current_token and current_token != token_used:
                headers["Authorization"] = f"Bearer {current_token}"
                resp = self.client.request(method, path, headers=headers, **kwargs)

        if resp.status_code == 401:
            if self._relogin():
                headers.update(self._auth_headers())
                resp = self.client.request(method, path, headers=headers, **kwargs)
            if resp.status_code == 401:
                if clear_on_auth_fail:
                    self._clear_token()
                raise AuthenticationError("Session expired. Please log in again.")

        if resp.status_code == 402:
            raise SubscriptionRequiredError("Active subscription required.")

        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Entitlement
    # ------------------------------------------------------------------

    def check_entitlement(self):
        """
        Check subscription status. Returns dict with:
            active (bool), plan_tier (str), current_period_end (str|None)
        """
        resp = self._request("GET", "/bot/entitlement")
        return resp.json()

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def send_heartbeat(self, client_id, system_type, ip_address, status, current_account=None, bot_version=""):
        """Send a heartbeat to the backend. Non-critical — failures are silently ignored."""
        try:
            self._request("POST", "/bot/heartbeat", clear_on_auth_fail=False, json={
                "client_id": int(client_id) if client_id else 0,
                "system_type": system_type or "",
                "ip_address": ip_address or "",
                "status": status or "idle",
                "current_account": current_account,
                "bot_version": bot_version or "",
            })
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def get_accounts(self, group_number=None):
        """
        Fetch all accounts for the user, optionally filtered by group.
        Returns list of account dicts.
        """
        params = {}
        if group_number is not None:
            params["group_number"] = group_number
        resp = self._request("GET", "/accounts", params=params)
        return resp.json()

    def get_client_state(self, group_number=None, known_version=None):
        """
        Consolidated poll: entitlement + user_config + accounts + per-account settings.

        Returns a dict:
          {
            "version": str,
            "changed": bool,
            "entitlement": {active, plan_tier, current_period_end},
            "user_config": {...} | None,   # present only when changed
            "accounts": [{...account fields..., "settings": {...}}] | None  # present only when changed
          }

        When changed=True, populates _settings_cache and _config_cache so downstream
        calls to get_account_settings() and get_user_config() read fresh data without
        additional API calls.
        """
        params = {}
        if group_number is not None:
            params["group_number"] = group_number
        if known_version:
            params["known_version"] = known_version
        resp = self._request("GET", "/bot/client-state", params=params)
        data = resp.json()

        if data.get("changed"):
            now = time.time()
            for acct in (data.get("accounts") or []):
                acct_id = acct.get("id")
                s = acct.get("settings")
                if acct_id and s is not None:
                    # Match the shape of GET /bot/settings/{id}: the account-level
                    # action-limit fields ride along with the settings row so a
                    # cache primed here honours cooldowns exactly like a direct fetch.
                    merged = {
                        **s,
                        "action_limits": acct.get("action_limits") or [],
                        "status_page_checked_at": acct.get("status_page_checked_at"),
                    }
                    self._settings_cache[acct_id] = (merged, now)
            user_config = data.get("user_config")
            if user_config is not None:
                self._config_cache = user_config
                self._config_cache_ts = now

        return data

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_account_settings(self, account_id):
        """
        Fetch account settings. Cached for _settings_cache_ttl seconds.
        Returns dict or None.
        """
        now = time.time()
        cached = self._settings_cache.get(account_id)
        if cached and (now - cached[1]) < self._settings_cache_ttl:
            return cached[0]

        try:
            resp = self._request("GET", f"/bot/settings/{account_id}")
            data = resp.json()
            self._settings_cache[account_id] = (data, now)
            return data
        except Exception as e:
            # Return cached data if available, even if stale
            if cached:
                return cached[0]
            _log_api_err(e, f"Failed to fetch settings for {account_id}")
            return None

    def invalidate_settings_cache(self, account_id=None):
        """Force refresh on next settings fetch."""
        if account_id:
            self._settings_cache.pop(account_id, None)
        else:
            self._settings_cache.clear()

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def get_ig_password(self, account_id):
        """Fetch decrypted IG password for an account."""
        try:
            resp = self._request("GET", f"/bot/credentials/{account_id}")
            return resp.json().get("ig_password")
        except Exception as e:
            _log_api_err(e, f"Failed to fetch credentials for {account_id}")
            return None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log_session_run(self, account_id, start_time, end_time,
                        action1_type="", action1_count=0,
                        action2_type="", action2_count=0,
                        action3_type="", action3_count=0,
                        action4_type="", action4_count=0,
                        error_message="", warning_message="", run_sequence=1):
        """
        Post a session log to the API.
        Returns dict with 'id' on success, None on failure.
        """
        if isinstance(start_time, datetime):
            start_time = _as_local_aware(start_time)
        if isinstance(end_time, datetime):
            end_time = _as_local_aware(end_time)
        payload = {
            "account_id": str(account_id),
            "run_date": start_time.strftime("%Y-%m-%d") if isinstance(start_time, datetime) else str(start_time),
            "run_sequence": run_sequence,
            "start_time": start_time.isoformat(timespec="seconds") if isinstance(start_time, datetime) else None,
            "end_time": end_time.isoformat(timespec="seconds") if isinstance(end_time, datetime) else None,
            "action_1_type": action1_type or None,
            "action_1_count": action1_count,
            "action_2_type": action2_type or None,
            "action_2_count": action2_count,
            "action_3_type": action3_type or None,
            "action_3_count": action3_count,
            "action_4_type": action4_type or None,
            "action_4_count": action4_count,
            "error_message": error_message or None,
            "warning_message": warning_message or None,
        }
        try:
            resp = self._request("POST", "/bot/session-log", json=payload)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to log session")
            return None

    def log_activity(self, account_id, action, status, details="", kind="activity"):
        """Post a fine-grained activity log. `kind` defaults to "activity";
        pass "failure" for the diagnostic records burnBot_run_log.report_failure
        uploads (see also `kind="error"`, sent via log_error below)."""
        payload = {
            "account_id": str(account_id),
            "kind": kind,
            "action": action,
            "status": status,
            "details": details or None,
        }
        try:
            self._request("POST", "/bot/activity-log", json=payload)
        except Exception as e:
            _log_api_err(e, "Failed to log activity")

    def log_error(self, account_id, error_message):
        """Post an error log entry."""
        payload = {
            "account_id": str(account_id),
            "kind": "error",
            "action": "error",
            "status": "failed",
            "details": error_message,
        }
        try:
            self._request("POST", "/bot/activity-log", json=payload)
        except Exception as e:
            _log_api_err(e, "Failed to log error")

    # ------------------------------------------------------------------
    # Action limits (Instagram throttling detected by burnBot_actionLimit)
    # ------------------------------------------------------------------

    def report_action_limit(self, account_id, action, tier, reason, details=None):
        """Record a detected action limit. For tier="hard" the backend starts an
        escalating cooldown and replies {action, tier, reason, strike, until}.
        Returns that dict, or None on failure (never raises)."""
        payload = {
            "account_id": str(account_id),
            "action": action,
            "tier": tier,
            "reason": (reason or "")[:200],
            "details": json.dumps(details, default=str)[:8000] if details else None,
        }
        try:
            resp = self._request("POST", "/bot/action-limit", json=payload)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to report action limit")
            return None

    def report_status_page_check(self, account_id, clean, text=""):
        """Record a read of Instagram's Account Status pages. `clean` is True,
        False or None (unreadable). A clean read lets the backend shorten a
        48h/72h cooldown back to 24h; it replies {ok, released: [{action,
        until}]}. Returns that dict, or None."""
        payload = {
            "account_id": str(account_id),
            "clean": None if clean is None else bool(clean),
            "text": (text or "")[:8000] or None,
        }
        try:
            resp = self._request("POST", "/bot/status-page-check", json=payload)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to report status page check")
            return None

    # ------------------------------------------------------------------
    # Follow targets
    # ------------------------------------------------------------------

    def get_follow_targets(self, account_id, status=None, older_than_days=None, page=1, page_size=500):
        """
        Fetch follow targets for an account with optional filtering.
        Returns list of target dicts.
        """
        params = {"page": page, "page_size": page_size}
        if status:
            params["status"] = status
        if older_than_days is not None:
            params["older_than_days"] = older_than_days
        try:
            resp = self._request("GET", f"/bot/follow-targets/{account_id}", params=params)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to fetch follow targets")
            return []

    def get_all_follow_target_handles(self, account_id):
        """
        Fetch ALL follow target handles for duplicate checking.
        Pages through results automatically. Returns set of lowercase handles.
        """
        handles = set()
        page = 1
        while True:
            targets = self.get_follow_targets(account_id, page=page, page_size=5000)
            if not targets:
                break
            for t in targets:
                handles.add(t["target_handle"].lower())
            if len(targets) < 5000:
                break
            page += 1
        return handles

    def create_follow_target(self, account_id, target_handle, source=None, status="following", follow_date=None):
        """Create a new follow target record."""
        payload = {
            "account_id": str(account_id),
            "target_handle": target_handle,
            "source": source,
            "status": status,
            "follow_date": follow_date.isoformat() if isinstance(follow_date, date) else follow_date,
        }
        try:
            resp = self._request("POST", "/bot/follow-targets", json=payload)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to create follow target")
            return None

    def update_follow_target(self, target_id, status=None, unfollow_date=None, follow_back=None):
        """Update a follow target (status, unfollow_date, follow_back)."""
        payload = {}
        if status is not None:
            payload["status"] = status
        if unfollow_date is not None:
            payload["unfollow_date"] = unfollow_date.isoformat() if isinstance(unfollow_date, date) else unfollow_date
        if follow_back is not None:
            payload["follow_back"] = follow_back
        try:
            resp = self._request("PATCH", f"/bot/follow-targets/{target_id}", json=payload)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to update follow target")
            return None

    # ------------------------------------------------------------------
    # Follow seeds (target accounts for follow[account list …] / follow[likers-accounts])
    # ------------------------------------------------------------------

    def get_seeds(self, account_id):
        """Active seeds with their follow-back numbers.
        Returns {"items": [...], "active_count": n, "account_rate": float|None}.
        Raises on API failure so callers can fall back to the legacy account_group text."""
        resp = self._request("GET", f"/bot/seeds/{account_id}")
        return resp.json()

    def create_seed(self, account_id, handle, origin="manual"):
        payload = {"account_id": str(account_id), "handle": handle, "origin": origin}
        try:
            resp = self._request("POST", "/bot/seeds", json=payload)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to create follow seed")
            return None

    def update_seed(self, seed_id, **fields):
        payload = {}
        for key, value in fields.items():
            if value is None:
                continue
            payload[key] = value.isoformat() if isinstance(value, datetime) else value
        try:
            resp = self._request("PATCH", f"/bot/seeds/{seed_id}", json=payload)
            return resp.json()
        except Exception as e:
            _log_api_err(e, "Failed to update follow seed")
            return None

    # ------------------------------------------------------------------
    # Ignore list
    # ------------------------------------------------------------------

    def get_ignore_handles(self):
        """
        Fetch the user's ignore list. Cached for _ignore_cache_ttl seconds.
        Returns list of handle strings.
        """
        now = time.time()
        if self._ignore_cache is not None and (now - self._ignore_cache_ts) < self._ignore_cache_ttl:
            return self._ignore_cache

        try:
            resp = self._request("GET", "/bot/ignore-handles")
            handles = resp.json().get("handles", [])
            self._ignore_cache = handles
            self._ignore_cache_ts = now
            return handles
        except Exception as e:
            if self._ignore_cache is not None:
                return self._ignore_cache
            _log_api_err(e, "Failed to fetch ignore handles")
            return []

    # ------------------------------------------------------------------
    # Notifications (server-side dispatch)
    # ------------------------------------------------------------------

    def notify(self, channel, to, body, subject=None):
        """Ask the backend to send an email or SMS.

        channel: "email" or "sms"
        to: email address or phone number
        body: message text
        subject: email subject (ignored for sms)

        Returns (ok: bool, error_detail: str) — callers unpack both.
        """
        if channel not in ("email", "sms"):
            print(client_log_line(None, "api", f"notify() called with invalid channel: {channel}"))
            return False, f"invalid channel: {channel}"
        if not to or not body:
            return False, "missing 'to' or 'body'"
        payload = {"channel": channel, "to": to, "body": body}
        if subject:
            payload["subject"] = subject
        try:
            self._request("POST", "/bot/notify", json=payload)
            return True, ""
        except Exception as e:
            try:
                detail = e.response.json().get("detail", str(e))
            except Exception:
                detail = str(e)
            return False, detail

    # ------------------------------------------------------------------
    # User config (notification prefs)
    # ------------------------------------------------------------------

    _config_cache = None
    _config_cache_ts = 0.0
    _config_cache_ttl = 300  # 5 minutes

    def get_user_config(self):
        """
        Fetch user-wide config (notification prefs) from the API.
        Cached for _config_cache_ttl seconds.
        Returns dict with notices_type, notices_session, notify_email, notify_phone.
        """
        now = time.time()
        if self._config_cache is not None and (now - self._config_cache_ts) < self._config_cache_ttl:
            return self._config_cache

        try:
            resp = self._request("GET", "/bot/config")
            data = resp.json()
            self._config_cache = data
            self._config_cache_ts = now
            return data
        except Exception as e:
            if self._config_cache is not None:
                return self._config_cache
            _log_api_err(e, "Failed to fetch user config")
            return None

    def update_user_config(self, **fields):
        """
        Persist a partial update to user-wide config (e.g. notices_session, notices_type)
        via PUT /bot/config. Updates the local cache with the server's response on success.
        Returns the updated config dict, or None on failure (existing cache is left untouched).
        """
        try:
            resp = self._request("PUT", "/bot/config", json=fields)
            data = resp.json()
            self._config_cache = data
            self._config_cache_ts = time.time()
            return data
        except Exception as e:
            _log_api_err(e, "Failed to update user config")
            return None

    # ------------------------------------------------------------------
    # Run count
    # ------------------------------------------------------------------

    def get_run_count(self, account_id, run_date=None):
        """
        Get session log count for an account on a given date (defaults to today).
        Returns int.
        """
        params = {}
        if run_date:
            params["run_date"] = run_date.isoformat() if isinstance(run_date, date) else run_date
        try:
            resp = self._request("GET", f"/bot/run-count/{account_id}", params=params)
            return resp.json().get("count", 0)
        except Exception as e:
            _log_api_err(e, "Failed to fetch run count")
            return 0


    def activate_desktop_build(self, user_id: str, client_id: int,
                               activation_token: str, bot_version: str) -> dict:
        """
        Single-use activation handshake for first launch of the generic binary.
        Returns the server response dict containing 'build_options' and 'api_url'
        so the caller can write burnBot_config.ini.
        """
        try:
            resp = self.client.post(
                "/bot/desktop/activate",
                json={
                    "user_id": user_id,
                    "client_id": client_id,
                    "activation_token": activation_token,
                    "bot_version": bot_version,
                },
                timeout=30,
            )
        except Exception as e:
            raise AuthenticationError(f"Activation request failed: {e}")

        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (401, 403, 404, 410):
            detail = resp.json().get("detail", resp.status_code)
            raise AuthenticationError(f"Activation rejected: {detail}")
        resp.raise_for_status()


# ------------------------------------------------------------------
# Custom exceptions
# ------------------------------------------------------------------

class AuthenticationError(Exception):
    """Raised when the user needs to re-authenticate."""
    pass


class SubscriptionRequiredError(Exception):
    """Raised when an active subscription is required (HTTP 402)."""
    pass
