#!/bin/bash
PLIST_FILE="$HOME/Library/LaunchAgents/com.spendingtracker.sync.plist"

if [ -f "$PLIST_FILE" ]; then
  launchctl unload "$PLIST_FILE"
  rm "$PLIST_FILE"
  echo "✅ Scheduler uninstalled"
else
  echo "⚠️  No scheduler plist found at $PLIST_FILE"
fi
