#!/usr/bin/env bash
# scripts/ghcr-prune.sh — one-time cleanup to reclaim GHCR storage.
#
# Why this script exists:
#   image-build.yml pushed a :latest + :sha-<7> on every successful
#   build. After ~60 successful builds the free 0.5 GB tier is full.
#   The prune step in image-build.yml keeps the 5 newest :sha-* tags
#   going forward; this script does the same retroactive cleanup.
#
# Usage:
#   1. Run this script: `bash scripts/ghcr-prune.sh`
#   2. `gh auth refresh -s delete:packages` when prompted — this
#      grants the scope to the existing gh CLI token so the script
#      can list + delete container versions.
#   3. Re-run `gh auth refresh -d delete:packages` afterwards to
#      drop the scope back to least-privilege (your token currently
#      has: gist, read:org, repo, workflow).
#
# Safe to run repeatedly; idempotent.
#
# Output is dry-run by default. Pass `--apply` to actually delete.

set -euo pipefail

PACKAGE="${1:-image-search}"
KEEP="${2:-5}"
APPLY="false"
if [[ "${1:-}" == "--apply" || "${2:-}" == "--apply" ]]; then
  APPLY="true"
fi

if [[ "$APPLY" == "false" ]]; then
  echo "DRY RUN: pass --apply to actually delete."
fi

# Make sure the user has the packages scopes. If not, prompt for
# refresh; we don't auto-run `gh auth refresh` because it's a
# permission escalation.
scopes=$(gh auth status --json scopes 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
# v3 returns a list, older returns string
print(' '.join(d) if isinstance(d, list) else d)
" 2>/dev/null || echo "")

if [[ "$scopes" != *"delete:packages"* || "$scopes" != *"read:packages"* ]]; then
  echo ""
  echo "Your gh token is missing delete:packages and read:packages."
  echo "Run this in another terminal to grant them:"
  echo ""
  echo "  gh auth refresh -s delete:packages -s read:packages"
  echo ""
  echo "Then re-run this script."
  exit 1
fi

# List all versions for this container package.
echo ""
echo "Listing versions for $PACKAGE..."
mapfile -t ALL < <(gh api "/users/isaaclb98/packages/container/$PACKAGE/versions?per_page=100" --jq '.[] | "\(.id)\t\(.name)\t\(.updated_at)\t\(.tags | join(","))"')

echo "Total versions: ${#ALL[@]}"
echo ""
printf '%-10s  %-12s  %-20s  %s\n' "ID" "UPDATED" "TAGS"
for v in "${ALL[@]}"; do
  IFS=$'\t' read -r id name updated tags <<<"$v"
  printf '%-10s  %-12s  %-20s  %s\n' "$id" "${updated:0:10}" "$name" "$tags"
done

# Find sha-* tags, sort by updated_at desc (newest first), keep KEEP.
echo ""
echo "=== sha-* tags, sorted by updated_at (newest first) ==="
declare -a SHA_VERSIONS=()
for v in "${ALL[@]}"; do
  IFS=$'\t' read -r id name updated tags <<<"$v"
  if [[ "$tags" == *"sha-"* ]]; then
    SHA_VERSIONS+=("$v")
  fi
done

# Already in newest-first order from the API. Pick the KEEP newest.
KEEP_VERSIONS=("${SHA_VERSIONS[@]:0:$KEEP}")
DELETE_VERSIONS=("${SHA_VERSIONS[@]:$KEEP}")

echo "Will keep: ${#KEEP_VERSIONS[@]} sha-* tags"
for v in "${KEEP_VERSIONS[@]}"; do
  IFS=$'\t' read -r id name updated tags <<<"$v"
  echo "  keep: $tags"
done

echo ""
echo "Will delete: ${#DELETE_VERSIONS[@]} sha-* tags"
TO_DELETE=()
for v in "${DELETE_VERSIONS[@]}"; do
  IFS=$'\t' read -r id name updated tags <<<"$v"
  echo "  delete: $tags (id=$id)"
  TO_DELETE+=("$id")
done

if [[ "$APPLY" == "false" ]]; then
  echo ""
  echo "DRY RUN — pass --apply to delete the above."
  exit 0
fi

# Confirm.
echo ""
read -rp "Delete ${#TO_DELETE[@]} versions? [y/N] " confirm
if [[ "$confirm" != "y" ]]; then
  echo "Cancelled."
  exit 0
fi

# Delete.
count=0
for id in "${TO_DELETE[@]}"; do
  if gh api -X DELETE "/users/isaaclb98/packages/container/$PACKAGE/versions/$id"; then
    count=$((count + 1))
  else
    echo "Failed to delete version $id" >&2
  fi
done
echo ""
echo "Deleted $count of ${#TO_DELETE[@]} versions."
echo ""
echo "Optional cleanup: revoke the packages scopes you granted:"
echo "  gh auth refresh -d delete:packages -d read:packages"