#!/usr/bin/env bash
# Post a real `kubently debug` transcript as PR verification evidence.
#
# Usage:
#   scripts/cli-evidence.sh <pr-number> "<question>"
#   DRY_RUN=1 scripts/cli-evidence.sh - "<question>"   # print transcript, post nothing
#
# Env knobs: EVIDENCE_WAIT (agent wait secs, default 90), DRY_RUN.
# Requires: kubently CLI configured against a live deployment; gh authed.
set -euo pipefail

PR="${1:?usage: cli-evidence.sh <pr-number> \"<question>\"}"
QUESTION="${2:?usage: cli-evidence.sh <pr-number> \"<question>\"}"

# Drive a real CLI session: ask, wait for the agent, exit. Strip ANSI/cursor codes.
RAW=$( { printf '%s\n' "$QUESTION"; sleep "${EVIDENCE_WAIT:-90}"; printf 'exit\n'; } \
  | kubently debug 2>&1 \
  | perl -pe 's/\e\[[0-9;]*[A-Za-z]//g; s/\e\][^\a]*\a//g' | tr -d '\r' )

# Trim the banner: keep from the echoed question onward.
TRANSCRIPT=$(printf '%s\n' "$RAW" | awk -v q="$QUESTION" 'index($0, q){found=1} found')
[ -n "$TRANSCRIPT" ] || { echo "ERROR: no transcript captured (is the API up?)" >&2; exit 1; }

BODY=$(cat <<EOF
**CLI verification evidence** — real \`kubently debug\` session against the live deployment:

\`\`\`
$TRANSCRIPT
\`\`\`
EOF
)

if [ "${DRY_RUN:-0}" = "1" ]; then
  printf '%s\n' "$BODY"
else
  gh pr comment "$PR" --body "$BODY"
fi
