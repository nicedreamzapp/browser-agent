#!/bin/bash
# brave-cdp-guard — if Brave gets opened the normal way (Dock icon, a link from another app, an update
# relaunch) it comes up WITHOUT remote control, and every agent goes blind until someone notices
# (M5, 2026-10-02: off from ~5pm to 9:44pm). Runs every 10s. Only acts on a Brave that JUST started
# (under 90s old), so it never yanks a window Matt has been using: quits it and reopens it with
# control on and his tabs restored, then kicks the broker. At most once per 5 minutes.
PORT=9229; DIR="$(cd "$(dirname "$0")" && pwd)"; LOG="$DIR/brave-cdp-guard.log"; STAMP="$DIR/.brave-cdp-guard.last"
# Only Matt's real Brave counts. A Brave on a throwaway --user-data-dir (a test or paint window) is not
# his browser; picking it up as "the" Brave hid the fact that his real one was gone (2026-10-05).
pid=$(for p in $(pgrep -x "Brave Browser"); do ps -o command= -p "$p" | grep -q -- "--user-data-dir" || echo "$p"; done | head -1)
[ -z "$pid" ] && exit 0
curl -sf -m 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && exit 0
age=$(ps -o etime= -p "$pid" | awk -F'[-:]' '{n=NF; s=$n+($(n-1))*60; if(n>=3)s+=$(n-2)*3600; if(n>=4)s+=$(n-3)*86400; print s}')
now=$(date +%s); last=$(cat "$STAMP" 2>/dev/null || echo 0)
if [ "${age:-999}" -gt 90 ]; then
  [ $((now - last)) -gt 3600 ] && { echo "$(date '+%F %T') Brave up ${age}s without control; too old to restart safely, left alone" >> "$LOG"; echo "$now" > "$STAMP"; }
  exit 0
fi
[ $((now - last)) -lt 300 ] && exit 0
echo "$now" > "$STAMP"
echo "$(date '+%F %T') Brave opened without control (${age}s old): reopening with it on" >> "$LOG"
bash "$DIR/launch-browser.sh" >> "$LOG" 2>&1
for l in com.browser-broker com.divinetribe.browser-broker; do launchctl print "gui/$(id -u)/$l" >/dev/null 2>&1 && launchctl kickstart -k "gui/$(id -u)/$l"; done
