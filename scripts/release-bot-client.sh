#!/usr/bin/env bash
# Push a bot-client release tag and wait for build artifacts before syncing the server.
#
# Usage:
#   bash scripts/release-bot-client.sh
#
# Steps:
#   1. Push bot-client-vX.XXX tag → triggers GitHub Actions (EXE + Docker build, ~3-4 min)
#   2. Polls /admin/sync-bot-version every 20s until both artifacts exist (max 10 min)
#   3. Once artifacts are live, the server advances current_bot_version and the
#      dashboard banner updates.
#
# Requires ADMIN_EMAIL and ADMIN_PASSWORD to be set — either exported in the
# shell or stored in a .env file at the repo root.
# PUBLIC_API_URL defaults to the production Railway URL if not set.

set -e

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# Source .env for ADMIN_EMAIL, ADMIN_PASSWORD, PUBLIC_API_URL if present
if [ -f .env ]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
fi

PUBLIC_API_URL="${PUBLIC_API_URL:-https://slowburnbotgamma-production.up.railway.app}"

if [ -z "$ADMIN_EMAIL" ] || [ -z "$ADMIN_PASSWORD" ]; then
  echo "error: ADMIN_EMAIL and ADMIN_PASSWORD must be set (in .env or environment)." >&2
  exit 1
fi

# git push retry — 2026-09-04: a release backgrounded via the Claude Code
# Bash tool hit "Invalid username or token" from GitHub on both pushes back
# to back, then succeeded immediately on retry with an identical
# environment (same HOME, same gh credential-helper resolution) — a
# transient auth/network blip, not anything foreground/background-specific.
# A short retry absorbs that instead of aborting the whole release on it.
_push_with_retry() {
  local max_attempts=3 delay=5 attempt=1
  while true; do
    if git push "$@"; then
      return 0
    fi
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "error: git push $* failed after ${max_attempts} attempts" >&2
      return 1
    fi
    echo "  ... git push $* failed (attempt ${attempt}/${max_attempts}); retrying in ${delay}s" >&2
    sleep "$delay"
    attempt=$(( attempt + 1 ))
    delay=$(( delay * 2 ))
  done
}

BOT_VERSION=$(python3 -c "
import re, sys
m = re.search(r'BOT_VERSION\s*=\s*[\"\']([\d.]+)[\"\']', open('bot-client/burnBot_version.py').read())
print(m.group(1) if m else sys.exit(1))
")
TAG="bot-client-v${BOT_VERSION}"

echo "==> Bot version: ${BOT_VERSION}"

# Create tag if it doesn't exist locally
if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "==> Tag ${TAG} already exists locally."
else
  git tag "$TAG"
  echo "==> Created tag ${TAG}."
fi

# Push main branch first so sync endpoint reads current BOT_VERSION from HEAD
_push_with_retry origin main || exit 1
echo "==> Pushed main branch."

# Push tag (triggers GitHub Actions build)
_push_with_retry origin "$TAG" || exit 1
echo "==> Pushed ${TAG} — GitHub Actions build triggered (~3-4 min)."

# Authenticate
echo "==> Authenticating..."
LOGIN_RESP=$(curl -s -X POST "${PUBLIC_API_URL}/auth/jwt/login" \
  -d "username=${ADMIN_EMAIL}&password=${ADMIN_PASSWORD}")
TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null)
if [ -z "$TOKEN" ]; then
  echo "error: login failed — $(echo "$LOGIN_RESP" | python3 -m json.tool 2>/dev/null || echo "$LOGIN_RESP")" >&2
  exit 1
fi

# Poll sync endpoint until both artifacts exist (EXE in S3 + Docker image in GHCR)
echo "==> Waiting for build artifacts..."
DEADLINE=$(( $(date +%s) + 1200 ))
POLL_INTERVAL=20

while :; do
  HTTP_CODE=$(curl -s -o /tmp/_sync_resp.json -w "%{http_code}" -X POST \
    "${PUBLIC_API_URL}/admin/sync-bot-version" \
    -H "Authorization: Bearer ${TOKEN}")

  if [ "$HTTP_CODE" = "200" ]; then
    UPDATED_TO=$(python3 -c "import json; print(json.load(open('/tmp/_sync_resp.json')).get('updated_to',''))" 2>/dev/null)
    echo "==> Server synced: current_bot_version = ${UPDATED_TO}"
    break
  elif [ "$HTTP_CODE" = "202" ]; then
    STATE=$(python3 -c "
import json
d = json.load(open('/tmp/_sync_resp.json'))
print(f'exe={d.get(\"exe_ready\")} image={d.get(\"image_ready\")}')" 2>/dev/null)
    NOW=$(date +%s)
    if [ "$NOW" -ge "$DEADLINE" ]; then
      echo "error: build did not complete within 20 min (${STATE})" >&2
      exit 1
    fi
    REMAINING=$(( DEADLINE - NOW ))
    echo "  ... build still running (${STATE}); retrying in ${POLL_INTERVAL}s (${REMAINING}s left)"
    sleep "$POLL_INTERVAL"
  elif [ "$HTTP_CODE" = "500" ] || [ "$HTTP_CODE" = "502" ] || [ "$HTTP_CODE" = "503" ] || [ "$HTTP_CODE" = "000" ]; then
    NOW=$(date +%s)
    if [ "$NOW" -ge "$DEADLINE" ]; then
      echo "error: server unreachable after 20 min (HTTP ${HTTP_CODE})" >&2
      exit 1
    fi
    REMAINING=$(( DEADLINE - NOW ))
    echo "  ... server not ready (HTTP ${HTTP_CODE}); retrying in ${POLL_INTERVAL}s (${REMAINING}s left)"
    sleep "$POLL_INTERVAL"
  else
    echo "error: sync returned HTTP ${HTTP_CODE} — $(cat /tmp/_sync_resp.json)" >&2
    exit 1
  fi
done
