#!/bin/sh
# One-step setup for Claude Code agents. Run (re-run any time to update):
#   curl -fsSL https://YOUR-DASHBOARD/install.sh | sh
set -e
DASH="${AGENT_DASH_URL:-__DASH_URL__}"
APP="$HOME/.agent-tracker/app"
REPO="https://github.com/Zoldenburg-me/dashboard-ai"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
for cmd in git python3 claude curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing '$cmd'. Install it and run this again."; exit 1; }
done

say "1/4  Getting the tracker code"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch -q origin main && git -C "$APP" reset -q --hard origin/main
else
  mkdir -p "$(dirname "$APP")" && git clone -q "$REPO" "$APP"
fi

say "2/4  Checking the dashboard password"
TOKEN="${AGENT_DASH_TOKEN:-}"
tries=0
while :; do
  if [ -z "$TOKEN" ]; then
    printf 'Dashboard password (input hidden): ' >/dev/tty
    stty -echo </dev/tty; read -r TOKEN </dev/tty; stty echo </dev/tty; echo >/dev/tty
  fi
  code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $TOKEN" "$DASH/api/stats" || true)
  [ "$code" = "200" ] && break
  tries=$((tries + 1)); TOKEN=""
  echo "That didn't work (HTTP $code)."
  [ "$tries" -ge 3 ] && { echo "Find the password in Vercel > Settings > Environment Variables > AGENT_DASH_TOKEN."; exit 1; }
done

say "3/4  Removing old agent-tracker entries"
claude mcp remove agent-tracker -s user >/dev/null 2>&1 || true
# Per-folder entries override the global one, so clear them from every folder.
python3 - <<'PY'
import json, os, shutil
p = os.path.expanduser("~/.claude.json")
try:
    data = json.load(open(p))
except (OSError, ValueError):
    raise SystemExit
n = 0
for proj in (data.get("projects") or {}).values():
    if isinstance(proj, dict) and "agent-tracker" in (proj.get("mcpServers") or {}):
        del proj["mcpServers"]["agent-tracker"]; n += 1
if n:
    shutil.copy(p, p + ".bak")
    json.dump(data, open(p, "w"), indent=2)
    print(f"     cleared {n} per-folder entr{'y' if n == 1 else 'ies'} (backup: ~/.claude.json.bak)")
PY

say "4/4  Adding agent-tracker for every folder"
claude mcp add agent-tracker --scope user \
  -e AGENT_DASH_URL="$DASH" -e AGENT_DASH_TOKEN="$TOKEN" \
  -- python3 "$APP/mcp_server.py" >/dev/null

say "Done."
echo "Restart Claude Code in any folder. The agent picks its own name and shows up at $DASH"
echo "To update later, run the same command again."
