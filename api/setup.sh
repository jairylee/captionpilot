#!/bin/bash
# Caption Pilot Admin Console — Setup Script
# Installs dependencies, generates token, and installs the LaunchAgent.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Caption Pilot Admin Console — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Install Python dependencies ──────────────────────────────────────────
echo "→ Installing Python dependencies…"
pip3 install --quiet fastapi uvicorn
echo "  ✓ fastapi, uvicorn installed"

# ── 2. Generate admin token (if not exists) ──────────────────────────────────
TOKEN_FILE="$SCRIPT_DIR/admin_token.txt"
if [ ! -f "$TOKEN_FILE" ]; then
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$TOKEN_FILE"
  echo "  ✓ Generated admin token → $TOKEN_FILE"
else
  echo "  ✓ Admin token already exists"
fi
TOKEN=$(cat "$TOKEN_FILE")

# ── 3. Install LaunchAgent ────────────────────────────────────────────────────
PLIST_SRC="$SCRIPT_DIR/com.captionpilot.admin.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.captionpilot.admin.plist"

echo "→ Installing LaunchAgent…"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"

# Unload if already loaded (ignore errors)
launchctl unload "$PLIST_DST" 2>/dev/null || true
sleep 1

# Load the agent
launchctl load "$PLIST_DST"
echo "  ✓ LaunchAgent installed and loaded"

# ── 4. Wait for server to start ───────────────────────────────────────────────
echo "→ Waiting for server to start…"
for i in $(seq 1 10); do
  if curl -s "http://localhost:8766/api/health" -H "Authorization: Bearer $TOKEN" > /dev/null 2>&1; then
    echo "  ✓ Server is running"
    break
  fi
  sleep 1
done

# ── 5. Print summary ──────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Caption Pilot Admin Console is ready!"
echo ""
echo "  API URL:    http://localhost:8766"
echo "  Admin UI:   file://$SCRIPT_DIR/../admin/index.html"
echo "  Token:      $TOKEN"
echo "  Token file: $TOKEN_FILE"
echo "  Log:        $(dirname "$SCRIPT_DIR")/../../logs/admin-server.log"
echo ""
echo "  To stop:   launchctl unload $PLIST_DST"
echo "  To start:  launchctl load   $PLIST_DST"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
