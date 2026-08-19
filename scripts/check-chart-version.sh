#!/usr/bin/env bash
#
# Fail if the Helm chart changed without a bump to `version:` in Chart.yaml.
#
# Why this exists: `Release Helm Chart` runs chart-releaser on every push to
# main that touches deployment/helm/kubently/**. chart-releaser creates a
# GitHub release tagged `kubently-<version>`, so an unchanged version makes it
# die with `tag_name already_exists` -- a failure that shows up only on main,
# blocks nothing, and leaves every chart fix unpublished (issue #100).
#
# Usage: scripts/check-chart-version.sh [base-ref]   (default: origin/main)
set -euo pipefail

BASE_REF="${1:-origin/main}"
CHART_DIR="deployment/helm/kubently"
CHART_FILE="${CHART_DIR}/Chart.yaml"
TAG_PREFIX="kubently-"

chart_version_at() {
  # $1: git ref, or "-" for the working tree
  if [ "$1" = "-" ]; then cat "$CHART_FILE"; else git show "$1:$CHART_FILE"; fi |
    awk '$1 == "version:" { print $2; exit }'
}

if git diff --quiet "$BASE_REF" -- "$CHART_DIR"; then
  echo "No changes under ${CHART_DIR}/ against ${BASE_REF} -- nothing to check."
  exit 0
fi

new="$(chart_version_at -)"
old="$(chart_version_at "$BASE_REF")"

echo "Chart changed against ${BASE_REF}:"
git diff --name-only "$BASE_REF" -- "$CHART_DIR" | sed 's/^/  /'
echo "Chart version: ${old} (${BASE_REF}) -> ${new} (this branch)"

fail() {
  echo
  echo "FAIL: $*"
  echo
  echo "This branch changes the Helm chart, so it must also bump 'version:' in"
  echo "${CHART_FILE}. Without a bump the release job on main dies with"
  echo "'tag_name already_exists' and the chart is never published (issue #100)."
  echo "Pick the next unused version above ${old} and add a CHANGELOG entry."
  exit 1
}

[ -n "$new" ] || fail "could not read 'version:' from ${CHART_FILE}."
[ -n "$old" ] || fail "could not read 'version:' from ${CHART_FILE} at ${BASE_REF}."

[ "$new" != "$old" ] || fail "chart version is still ${new}."

# Reject a downgrade or a sideways edit: the new version must sort above the old.
[ "$(printf '%s\n%s\n' "$old" "$new" | sort -V | tail -1)" = "$new" ] ||
  fail "chart version ${new} is not greater than ${old}."

# Reject a version chart-releaser has already released.
if git rev-parse -q --verify "refs/tags/${TAG_PREFIX}${new}" >/dev/null; then
  fail "${TAG_PREFIX}${new} is already a released tag."
fi

echo "OK: chart version bumped ${old} -> ${new}, and ${TAG_PREFIX}${new} is unreleased."
