#!/bin/bash
set -e

# Virtual display
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
sleep 1

# The arm64 image ships Debian chromium-driver; Selenium Manager has no arm64 driver.
if [ -x /usr/bin/chromedriver ]; then
  export SLOWBURN_CHROMEDRIVER=/usr/bin/chromedriver
fi

exec ./slowburnbot "$@"
