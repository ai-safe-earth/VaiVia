#!/bin/bash
input=$(cat)
sid=$(echo "$input" | grep -o '"session_id": *"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/')
cd "$CLAUDE_PROJECT_DIR" || exit 0
mkdir -p .claude/state
flag=".claude/state/quick-$sid"
if echo "$input" | grep -qi '"prompt": *"quick:'; then touch "$flag"; fi
if echo "$input" | grep -qi '"prompt": *"plan:'; then rm -f "$flag"; fi
find .claude/state -name 'quick-*' -mtime +2 -delete 2>/dev/null
exit 0
