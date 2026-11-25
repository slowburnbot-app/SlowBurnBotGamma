"""Import helper for the bot-client tests: Selenium is not installed in the test
environment, and the seed logic does not need a real browser. Stub the selenium
modules, then put bot-client on the path."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

for _name in (
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.common",
    "selenium.webdriver.common.by",
    "selenium.webdriver.common.keys",
    "selenium.webdriver.common.action_chains",
    "selenium.webdriver.support",
    "selenium.webdriver.support.ui",
    "selenium.webdriver.support.expected_conditions",
    "selenium.common",
    "selenium.common.exceptions",
):
    sys.modules.setdefault(_name, MagicMock())


class _SeleniumError(Exception):
    pass


sys.modules["selenium.common.exceptions"].NoSuchElementException = _SeleniumError
sys.modules["selenium.common.exceptions"].StaleElementReferenceException = _SeleniumError

_CLIENT = str(Path(__file__).resolve().parent.parent / "bot-client")
if _CLIENT not in sys.path:
    sys.path.insert(0, _CLIENT)
