#!/bin/bash
# browser-broker / browser-agent uninstaller (macOS).
#   curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/uninstall.sh | bash
#   ... | bash -s -- --purge      also delete the clone (~/.browser-agent), its logs and lease state
#
# Removes the LaunchAgent that get.sh/install.sh made, the `browser-agent` command and the
# agent's venv. Your browser, its profile and its logins are never touched. Your browser
# keeps running with remote debugging until you quit it; reopen it normally to drop the flag.
#
# Env: BROKER_LABEL (default com.browser-broker), BROWSER_BROKER_DIR, KIT_BIN_DIR.
set -e
PURGE=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,10p' "$0" 2>/dev/null || true; exit 0 ;;
  esac
done
say(){ printf '\033[1;36m==>\033[0m %s\n' "$1"; }

if [ -z "$BROWSER_BROKER_DIR" ] && [ ! -d "$HOME/.browser-agent/.git" ] && [ -d "$HOME/.browser-broker/.git" ]; then
  BROWSER_BROKER_DIR="$HOME/.browser-broker"
fi
DIR="${BROWSER_BROKER_DIR:-$HOME/.browser-agent}"
LABEL="${BROKER_LABEL:-com.browser-broker}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN="${KIT_BIN_DIR:-$HOME/.local/bin}/browser-agent"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  say "stopped LaunchAgent $LABEL"
fi
if [ -f "$PLIST" ]; then rm -f "$PLIST"; say "removed $PLIST"; fi

# Only remove the launcher get.sh wrote (it carries this marker).
if [ -f "$BIN" ] && grep -q "installed by browser-agent/broker/get.sh" "$BIN" 2>/dev/null; then
  rm -f "$BIN"; say "removed $BIN"
fi
if [ -d "$DIR/.venv" ]; then rm -rf "$DIR/.venv"; say "removed the agent venv"; fi

if [ "$PURGE" = "1" ]; then
  if [ -d "$DIR" ]; then rm -rf "$DIR"; say "removed $DIR"; fi
else
  [ -d "$DIR" ] && say "kept $DIR (logs, recipes). Delete it with --purge."
fi
say "browser broker uninstalled"
