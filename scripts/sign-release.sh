#!/bin/bash
# Generates CHECKSUMS.sha256 and attaches it to a GitHub release.
#
# Usage:
#   scripts/sign-release.sh v5.7.2
#
# Prerequisites: gh CLI authenticated, tag already pushed.
#
# Copyright (C) 2026 Alex Greenshpun
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

set -euo pipefail

TAG="${1:-}"

if [ -z "$TAG" ]; then
    echo "Usage: scripts/sign-release.sh <tag>"
    echo "Example: scripts/sign-release.sh v5.7.2"
    exit 1
fi

if ! command -v gh &>/dev/null; then
    echo "Error: gh CLI not found. Install: https://cli.github.com"
    exit 1
fi

if ! gh release view "$TAG" &>/dev/null; then
    echo "Error: release $TAG not found. Create it first:"
    echo "  git tag $TAG && git push origin $TAG"
    echo "  gh release create $TAG --title \"$TAG\" --generate-notes"
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
CHECKSUM_FILE="${REPO_ROOT}/CHECKSUMS.sha256"

echo "Generating checksums for tracked scripts..."

HASH_CMD="sha256sum"
if [ "$(uname)" = "Darwin" ]; then
    HASH_CMD="shasum -a 256"
fi

{
    echo "${REPO_ROOT}/install.sh"
    echo "${REPO_ROOT}/hooks/hooks.json"
    find "${REPO_ROOT}/skills/token-optimizer/scripts" -type f -name "*.py"
} | sort | while read -r f; do
    [ -f "$f" ] || continue
    $HASH_CMD "$f" | sed "s|${REPO_ROOT}/||"
done > "$CHECKSUM_FILE"

CHECKSUM_COUNT=$(wc -l < "$CHECKSUM_FILE" | tr -d ' ')
echo "Generated ${CHECKSUM_COUNT} checksums"

if [ "$CHECKSUM_COUNT" -eq 0 ]; then
    echo "Error: no files found to checksum. Aborting upload."
    exit 1
fi

echo "Uploading CHECKSUMS.sha256 to release $TAG..."
gh release upload "$TAG" "$CHECKSUM_FILE" --clobber

echo ""
echo "Done. Verify with:"
echo "  gh release view $TAG"
