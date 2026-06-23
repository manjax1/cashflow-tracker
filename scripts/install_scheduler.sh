#!/bin/bash
# Installs a launchd plist to run spending-tracker sync every Monday at 7:00 AM
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/com.spendingtracker.sync.plist"
PROJECT_DIR="/Users/manjax/Documents/Code/AI/spending-tracker"

mkdir -p "$PLIST_DIR"

cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.spendingtracker.sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_DIR/scripts/start_sync.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$PROJECT_DIR/logs/launchd_out.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT_DIR/logs/launchd_err.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
EOF

launchctl load "$PLIST_FILE"
echo "✅ Scheduler installed — sync runs every Monday at 7:00 AM"
echo "   Plist: $PLIST_FILE"
