#!/bin/bash
input=$(cat)
echo "$input" | grep -q '"stop_hook_active": *true' && exit 0
sid=$(echo "$input" | grep -o '"session_id": *"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/')
cd "$CLAUDE_PROJECT_DIR" || exit 0
flag=".claude/state/quick-$sid"
[ -e "$flag" ] && exit 0
changed=$(git status --porcelain)
code=$(echo "$changed" | grep -v -e ' docs/' -e ' .claude/' -e 'CLAUDE.md')
docs=$(echo "$changed" | grep ' docs/.*\.md')
if [ -n "$code" ] && [ -z "$docs" ]; then
  echo "Code changed but no plan was updated. Planned work: update status/next in its docs/plans file. Quick fix: run 'mkdir -p .claude/state && touch $flag' and stop." >&2
  exit 2
fi
exit 0
