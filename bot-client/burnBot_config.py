# burnBot_config.py

import configparser
import sys
import os

CONFIG = configparser.ConfigParser()
CONFIG_FILE_PATH = None


def _inject_missing_sections() -> None:
    """Ensure [browser-config] and [browser-session] exist.

    Old config files pre-date these sections. Rather than crashing with a
    KeyError, inject sensible defaults so the bot can still start.
    The defaults match what _write_ini_from_activation() writes for each platform
    (windows is the only one that ever runs pre-existing user configs).
    """
    system_type = CONFIG.get("bot_settings", "system_type", fallback="windows")
    if not CONFIG.has_section("browser-config"):
        CONFIG.add_section("browser-config")
        if system_type == "linux":
            CONFIG.set("browser-config", "chrome_version", "")
            CONFIG.set("browser-config", "chrome_path", "/usr/bin/google-chrome")
            CONFIG.set("browser-config", "chrome_user_data_dir_base", "ChromeUserData")
        elif system_type == "macos":
            CONFIG.set("browser-config", "chrome_version", "143")
            CONFIG.set("browser-config", "chrome_path", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
            CONFIG.set("browser-config", "chrome_user_data_dir_base", "ChromeUserData")
        else:
            CONFIG.set("browser-config", "chrome_version", "143")
            CONFIG.set("browser-config", "chrome_path", "")  # empty → system Chrome
            CONFIG.set("browser-config", "chrome_user_data_dir_base", "PortableChrome")
        if not CONFIG.has_option("browser-config", "debug_base_port"):
            CONFIG.set("browser-config", "debug_base_port", "9222")
        CONFIG.set("browser-config", "add_argument", "")
    if not CONFIG.has_section("browser-session"):
        CONFIG.add_section("browser-session")
        CONFIG.set("browser-session", "headless", "False")
        CONFIG.set("browser-session", "close_browser_after_session", "False")
        CONFIG.set("browser-session", "close_browser_after_exit", "False")
        CONFIG.set("browser-session", "bot_idle_delay", "0.25")
        if system_type == "linux":
            CONFIG.set("browser-session", "novnc_url", "http://localhost:6080/vnc.html")


def load_config(default_config_file):
    global CONFIG, CONFIG_FILE_PATH

    config_file = sys.argv[1] if len(sys.argv) > 1 else default_config_file

    if not os.path.exists(config_file):
        return None  # Caller should treat missing INI as "first run"

    CONFIG.read(config_file)
    CONFIG_FILE_PATH = os.path.abspath(config_file)
    _inject_missing_sections()
    return config_file


def get_project_dir():
    """Return the directory of the config file, or the directory of the running executable."""
    if CONFIG_FILE_PATH:
        return os.path.dirname(CONFIG_FILE_PATH)
    exe_path = getattr(sys, "executable", None) or sys.argv[0]
    return os.path.dirname(os.path.abspath(exe_path))


def clear_api_credentials_in_ini():
    """Wipe email/password from [api_credentials] in the on-disk INI after first login."""
    if not CONFIG_FILE_PATH:
        return False
    if not CONFIG.has_section("api_credentials"):
        return False

    changed = False
    for key in ("email", "password"):
        if CONFIG.has_option("api_credentials", key) and CONFIG.get("api_credentials", key, fallback="").strip():
            CONFIG.set("api_credentials", key, "")
            changed = True
    if not changed:
        return False
    try:
        with open(CONFIG_FILE_PATH, "w") as fh:
            CONFIG.write(fh)
        return True
    except OSError:
        return False


def resolve_path(path):
    """Resolve a path relative to the project directory. Absolute paths returned as-is."""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(get_project_dir(), path))
