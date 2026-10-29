#!/bin/bash
# browser-broker one-line installer.
#   curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/get.sh | bash
# Uninstall:
#   curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/uninstall.sh | bash
#
# The broker lives in the broker/ folder of github.com/nicedreamzapp/browser-agent.
# Clones (or updates) that repo, relaunches your browser with CDP on a private
# port, installs the proxy as a LaunchAgent that owns 9222, and puts a
# `browser-agent` command for agent.py in ~/.local/bin.
#
# Env: BROWSER_BROKER_DIR (clone dir), BROWSER_AGENT_REPO (git URL or local path),
#      BROKER_LABEL, BROKER_PROXY_PORT / BROKER_UPSTREAM_PORT / BROKER_PORT,
#      BROKER_SKIP_BROWSER=1 (do not relaunch the browser), BROKER_SKIP_AGENT=1,
#      KIT_BIN_DIR (where the browser-agent command goes, default ~/.local/bin).
set -e

# An older install cloned the standalone browser-broker repo into ~/.browser-broker.
# Keep using that checkout if it is there, so re-running this never makes a second copy.
if [ -z "$BROWSER_BROKER_DIR" ] && [ -d "$HOME/.browser-broker/.git" ]; then
  BROWSER_BROKER_DIR="$HOME/.browser-broker"
fi
DIR="${BROWSER_BROKER_DIR:-$HOME/.browser-agent}"
REPO="${BROWSER_AGENT_REPO:-https://github.com/nicedreamzapp/browser-agent}"

say(){ printf '\033[1;36m==>\033[0m %s\n' "$1"; }
die(){ printf '\033[1;31mx\033[0m %s\n' "$1" >&2; exit 1; }

# Don't stand up a second broker on top of a working one -- two LaunchAgents
# fighting over 9222 is worse than no broker at all.
BROKER_RUNNING=0
if curl -sf -m 3 "http://127.0.0.1:${BROKER_PORT:-9223}/health" >/dev/null 2>&1; then
  say "a broker is already answering on ${BROKER_PORT:-9223} -- leaving it as it is"
  curl -s -m 3 "http://127.0.0.1:${BROKER_PORT:-9223}/status"; echo
  BROKER_RUNNING=1
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
    if [ "$BROKER_RUNNING" != "1" ]; then
      if [ "${BROKER_SKIP_BROWSER:-0}" = "1" ]; then
        say "BROKER_SKIP_BROWSER=1: leaving your browser alone (run ./launch-browser.sh later)"
      else
        say "relaunching your browser with CDP (your real profile, your real logins)"
        ./launch-browser.sh
      fi
      say "installing the proxy on :${BROKER_PROXY_PORT:-9222}"
      ./install.sh
    fi
    ;;
  *)
    say "non-macOS: start your browser yourself with"
    echo "   --remote-debugging-port=9229 --remote-allow-origins=* \\"
    echo "   --disable-backgrounding-occluded-windows --disable-renderer-backgrounding"
    echo "   then: python3 $BROKER_DIR/proxy.py"
    exit 0
    ;;
esac

# The agent: agent.py needs only websockets. Give it its own small venv so it never
# fights the system Python, and a `browser-agent` command that runs it.
if [ "${BROKER_SKIP_AGENT:-0}" != "1" ]; then
  AGENT_DIR="$(cd "$BROKER_DIR/.." && pwd)"
  if [ -f "$AGENT_DIR/agent.py" ]; then
    APY=""
    for cand in python3.14 python3.13 python3.12 python3; do command -v "$cand" >/dev/null 2>&1 && { APY="$(command -v "$cand")"; break; }; done
    [ -n "$APY" ] || die "python3 is required for the browser agent"
    say "setting up the browser agent (venv in $AGENT_DIR/.venv)"
    [ -x "$AGENT_DIR/.venv/bin/python3" ] || "$APY" -m venv "$AGENT_DIR/.venv"
    "$AGENT_DIR/.venv/bin/python3" -m pip install --quiet --upgrade "websockets>=13" \
      || say "could not install websockets for the agent; the broker still works"
    BIN_DIR="${KIT_BIN_DIR:-$HOME/.local/bin}"
    mkdir -p "$BIN_DIR"
    cat > "$BIN_DIR/browser-agent" <<EOF
#!/bin/bash
# browser-agent launcher (installed by browser-agent/broker/get.sh)
exec "$AGENT_DIR/.venv/bin/python3" "$AGENT_DIR/agent.py" "\$@"
EOF
    chmod +x "$BIN_DIR/browser-agent"
    say "browser-agent command -> $BIN_DIR/browser-agent (needs the claude-code-local model server on :4000)"
  fi
fi

echo
say "done. Point anything at ${BROKER_PROXY_PORT:-9222} exactly as before:"
echo "   curl localhost:${BROKER_PORT:-9223}/status      # who holds which tab"
echo "   agent-browser --cdp 9222"
echo "   chrome-devtools-mcp --browser-url http://127.0.0.1:9222"
echo "   uninstall: curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/uninstall.sh | bash"
