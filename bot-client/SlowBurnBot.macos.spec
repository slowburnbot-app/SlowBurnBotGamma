# -*- mode: python ; coding: utf-8 -*-
# macOS build: a bare console binary (no .app bundle) so the INI, Chrome profiles and
# burnBot_runs.json land next to the executable exactly as they do on Windows.
from PyInstaller.utils.hooks import collect_all

# Collect all Textual submodules + CSS/theme data files
_textual_datas, _textual_binaries, _textual_hiddenimports = collect_all("textual")

# Collect rich (dynamic unicode data submodules)
_rich_datas, _rich_binaries, _rich_hiddenimports = collect_all("rich")

a = Analysis(
    ["burnBot.py"],
    pathex=[],
    binaries=_textual_binaries + _rich_binaries,
    datas=_textual_datas + _rich_datas,
    hiddenimports=[
        *_textual_hiddenimports,
        *_rich_hiddenimports,
        # No keyrings.alt: the keyring library uses the macOS Keychain natively.
        "selenium.webdriver.chrome.webdriver",
        "selenium.webdriver.chrome.service",
        "selenium.webdriver.chrome.options",
        "selenium.webdriver.common.service",
        "selenium.webdriver.common.utils",
        "selenium.webdriver.remote.remote_connection",
        "selenium.webdriver.support.ui",
        "selenium.webdriver.support.expected_conditions",
        "selenium.webdriver.support.wait",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Bundling these would trigger Accessibility / Screen Recording prompts for an
    # unsigned binary; burnBot_login.py already degrades to a logged "skipped" line.
    excludes=["pyautogui", "pygetwindow"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SlowBurnBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX corrupts macOS binaries — never carry over the Windows spec's upx=True
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # native arch of the runner (arm64 on macos-latest)
    codesign_identity=None,  # ad-hoc signed by PyInstaller; a real identity slots in here later
    entitlements_file=None,
)
