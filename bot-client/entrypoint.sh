#!/bin/bash
set -e

# A container restarted by --restart keeps its filesystem. The lock file and socket of
# the previous Xvfb would make the new one refuse to start, and Chrome would then fail
# with "Missing X server or $DISPLAY".
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

# Virtual display
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
sleep 1

# The image ships a chromedriver that matches its Chrome (Debian chromium-driver on arm64,
# the Chrome for Testing build on amd64), so Selenium Manager never downloads one at launch.
if [ -x /usr/bin/chromedriver ]; then
  export SLOWBURN_CHROMEDRIVER=/usr/bin/chromedriver
fi

exec ./slowburnbot "$@"
