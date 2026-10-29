#!/bin/bash
# Install browser-broker as a macOS LaunchAgent that starts at login and
# restarts itself if it dies. Run from the repo directory.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="${BROKER_LABEL:-com.browser-broker}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents"
# launchd does not see your shell's PATH, so "python3" there is usually Apple's
# /usr/bin/python3 while "python3" here may be Homebrew's. Pick ONE interpreter,
# make sure it has what proxy.py needs (websockets 13+), and write its full path
# into the plist. Installing into one Python and running another is what left a
# fresh install crash-looping every 30 seconds.
has_deps(){ "$1" -c "import websockets, websocket, sys; sys.exit(int(websockets.__version__.split('.')[0]) < 13)" 2>/dev/null; }
PY=""
for cand in "$BROKER_PYTHON" /usr/bin/python3 "$(command -v python3)"; do
  [ -n "$cand" ] && [ -x "$cand" ] || continue
  has_deps "$cand" && { PY="$cand"; break; }
  "$cand" -m pip install --user --quiet --upgrade "websockets>=13" websocket-client 2>/dev/null \
    && has_deps "$cand" && { PY="$cand"; break; }
done
[ -n "$PY" ] || { echo "could not get websockets>=13 into any python3; set BROKER_PYTHON=/path/to/python3" >&2; exit 1; }
echo "using $PY"
# Port overrides (BROKER_PROXY_PORT / BROKER_UPSTREAM_PORT / BROKER_PORT) go into the plist,
# because launchd does not pass your shell's environment to the agent.
ENV_XML=""
for v in BROKER_PROXY_PORT BROKER_UPSTREAM_PORT BROKER_PORT BROWSER_APP; do
  val="${!v:-}"
  [ -n "$val" ] && ENV_XML="$ENV_XML<key>$v</key><string>$val</string>"
done
[ -n "$ENV_XML" ] && ENV_XML="<key>EnvironmentVariables</key><dict>$ENV_XML</dict>"
sed -e "s|__BROKER_DIR__|$DIR|g" -e "s|__PYTHON__|$PY|g" -e "s|__LABEL__|$LABEL|g" \
    -e "s|<!--__ENV__-->|$ENV_XML|" "$DIR/launchd/com.browser-broker.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 3
curl -sf -m 5 "http://127.0.0.1:${BROKER_PORT:-9223}/health" && echo && echo "browser-broker installed and running" \
  || { echo "broker did not answer — is the browser up? try ./launch-browser.sh" >&2; exit 1; }
