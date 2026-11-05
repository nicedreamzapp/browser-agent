#!/bin/bash
# Install the desktop-lease reaper: every 5 minutes, plain code (no model), it pauses any
# terminal-mac VM that has no lease and has sat idle 10+ minutes, so it gives back its CPU
# and memory. Matt (2026-11-04, 10:20 PM): every model and job that is done frees its memory.
# Only useful on the machine that runs the VM; install.sh calls it when terminal-mac is there.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="${BROKER_LABEL:-com.browser-broker}.desktop-reap"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__BROKER_DIR__|$DIR|g" -e "s|__HOME__|$HOME|g" -e "s|__LABEL__|$LABEL|g" \
    "$DIR/launchd/com.browser-broker.desktop-reap.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "desktop reaper installed ($LABEL, every 5 minutes)"
