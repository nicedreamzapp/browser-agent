#!/bin/bash
# browser-broker one-line installer.
#   curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/get.sh | bash
#
# The broker lives in the broker/ folder of github.com/nicedreamzapp/browser-agent.
# Clones (or updates) that repo, relaunches your browser with CDP on a private
# port, and installs the proxy as a LaunchAgent that owns 9222.
set -e

# An older install cloned the standalone browser-broker repo into ~/.browser-broker.
# Keep using that checkout if it is there, so re-running this never makes a second copy.
if [ -z "$BROWSER_BROKER_DIR" ] && [ -d "$HOME/.browser-broker/.git" ]; then
  BROWSER_BROKER_DIR="$HOME/.browser-broker"
fi
DIR="${BROWSER_BROKER_DIR:-$HOME/.browser-agent}"
REPO="https://github.com/nicedreamzapp/browser-agent"

say(){ printf '\033[1;36m==>\033[0m %s\n' "$1"; }
die(){ printf '\033[1;31mx\033[0m %s\n' "$1" >&2; exit 1; }

# Don't stand up a second broker on top of a working one -- two LaunchAgents
# fighting over 9222 is worse than no broker at all.
if curl -sf -m 3 "http://127.0.0.1:${BROKER_PORT:-9223}/health" >/dev/null 2>&1; then
  say "a broker is already answering on ${BROKER_PORT:-9223} -- nothing to do"
  curl -s -m 3 "http://127.0.0.1:${BROKER_PORT:-9223}/status"; echo
  exit 0
fi

command -v git >/dev/null    || die "git is required"
command -v python3 >/dev/null || die "python3 3.9+ is required"

if [ -d "$DIR/.git" ]; then
  say "updating $DIR"
  git -C "$DIR" pull --ff-only --quiet || say "local changes kept, skipping pull"
else
  say "cloning into $DIR"
  git clone --quiet "$REPO" "$DIR"
fi
cd "$DIR"
# browser-agent checkout: the broker is in broker/. An old standalone checkout has it at the top.
[ -f broker/proxy.py ] && cd broker
BROKER_DIR="$(pwd)"

# Dependencies (websockets 13+ and websocket-client) are installed by install.sh,
# into the same Python the LaunchAgent will run.

case "$(uname -s)" in
  Darwin)
    say "relaunching your browser with CDP (your real profile, your real logins)"
    ./launch-browser.sh
    say "installing the proxy on :9222"
    ./install.sh
    ;;
  *)
    say "non-macOS: start your browser yourself with"
    echo "   --remote-debugging-port=9229 --remote-allow-origins=* \\"
    echo "   --disable-backgrounding-occluded-windows --disable-renderer-backgrounding"
    echo "   then: python3 $BROKER_DIR/proxy.py"
    exit 0
    ;;
esac

echo
say "done. Point anything at 9222 exactly as before:"
echo "   curl localhost:9223/status      # who holds which tab"
echo "   agent-browser --cdp 9222"
echo "   chrome-devtools-mcp --browser-url http://127.0.0.1:9222"
