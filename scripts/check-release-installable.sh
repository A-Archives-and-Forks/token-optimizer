#!/bin/bash
# Answers one question: can a new user install this right now?
#
# Why this exists: between v5.11.57 and v5.11.64, eight consecutive releases
# shipped with no CHECKSUMS.sha256 asset. install.sh verifies every install
# against that asset and verification is the DEFAULT path (install.sh:787);
# resolve_latest_release() fails when the asset is absent (install.sh:815) and
# the caller turns that into a hard abort (install.sh:962). So for eight
# releases the documented `curl | bash` install aborted for every new user, and
# the only way in was TOKEN_OPTIMIZER_SKIP_VERIFY=1 -- teaching people to switch
# off integrity checking in order to install a tool.
#
# Nothing caught it. The repo looked healthy from the inside: tests green, docs
# building, the maintainer's own machine on the current version (because
# /plugin installs from the marketplace clone and never crosses this gate). The
# break was only visible from where a stranger stands. Guards that nothing
# enforces are decoration, so this one runs in CI and looks from the outside.
#
# It deliberately re-implements the install.sh gate rather than importing it:
# the point is to fail when a NEW USER's install would fail, so it has to ask
# the same question of the same live API, not trust a shared helper that could
# drift in lockstep with the thing it is checking.
#
# Usage:
#   scripts/check-release-installable.sh              # asset presence only
#   scripts/check-release-installable.sh --verify-tree # also verify the manifest
#                                                      # against the checked-out tree
#                                                      # (caller must be AT the tag)
#
# Exit 0 installable, 1 with the failure printed.
#
# Copyright (C) 2026 Alex Greenshpun
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:-alexgreensh/token-optimizer}"
VERIFY_TREE=0
[ "${1:-}" = "--verify-tree" ] && VERIFY_TREE=1

fail() {
    # ::error:: makes it a GitHub Actions annotation; harmless locally.
    printf '::error::%s\n' "$1" >&2
    printf 'RESULT: NOT INSTALLABLE\n' >&2
    exit 1
}

if ! command -v python3 &>/dev/null; then
    fail "python3 not found; cannot parse the releases API response."
fi

# TEST SEAM: tests/test_release_installable.py points this at a local fixture to
# exercise the failure paths. A guard whose red path is never executed is a guard
# nobody knows works, and this one's whole job is to go red. Not for production
# use -- overriding it against a fake endpoint defeats the check entirely.
api="${RELEASES_API_URL_FOR_TESTS:-https://api.github.com/repos/${GITHUB_REPO}/releases/latest}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Authenticated when a token is present (CI), anonymous otherwise. Anonymous
# API calls are rate-limited per IP and shared runners burn that quota fast,
# which is why CI passes github.token through.
#
# Retries because this job runs on every push and PR. A single transient API blip
# would turn a contributor's unrelated PR red with an error they cannot fix, and
# an occasional unexplained red is how a check earns the "just hit re-run" reflex.
# A guard people reflexively re-run is a guard nobody reads.
curl_args=(-fsSL --retry 3 --retry-delay 2 --retry-connrefused "$api" -o "${tmp}/latest.json")
if [ -n "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ]; then
    curl_args+=(-H "Authorization: Bearer ${GH_TOKEN:-$GITHUB_TOKEN}")
fi
curl "${curl_args[@]}" || fail "Could not reach the GitHub releases API at ${api}."

# Same two fields, same emptiness test, as install.sh resolve_latest_release().
cat > "${tmp}/parse.py" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
tag = data.get("tag_name", "") or ""
asset = ""
for a in data.get("assets", []):
    if a.get("name") == "CHECKSUMS.sha256":
        asset = a.get("browser_download_url", "") or ""
        break
names = [a.get("name") for a in data.get("assets", [])]
print(tag)
print(asset)
print(",".join(n for n in names if n))
PYEOF

parsed="$(python3 "${tmp}/parse.py" "${tmp}/latest.json")"
RELEASE_TAG="$(printf '%s\n' "$parsed" | sed -n '1p')"
CHECKSUM_ASSET_URL="$(printf '%s\n' "$parsed" | sed -n '2p')"
ASSET_NAMES="$(printf '%s\n' "$parsed" | sed -n '3p')"

[ -n "$RELEASE_TAG" ] || fail "The releases API returned no tag_name for ${GITHUB_REPO}."

if [ -z "$CHECKSUM_ASSET_URL" ]; then
    fail "Release ${RELEASE_TAG} has no CHECKSUMS.sha256 asset (assets: ${ASSET_NAMES:-none}). Every verified install aborts at install.sh:962. Fix: run scripts/sign-release.sh ${RELEASE_TAG}, or re-run the Sign release workflow for that tag."
fi

printf 'latest release : %s\n' "$RELEASE_TAG"
printf 'manifest asset : present\n'

curl -fsSL --retry 3 --retry-delay 2 --retry-connrefused \
    "$CHECKSUM_ASSET_URL" -o "${tmp}/CHECKSUMS.sha256" \
    || fail "Release ${RELEASE_TAG} advertises a CHECKSUMS.sha256 asset but it could not be downloaded."

[ -s "${tmp}/CHECKSUMS.sha256" ] \
    || fail "The CHECKSUMS.sha256 asset on ${RELEASE_TAG} is empty."

# CRLF check comes FIRST, because the well-formedness regex below anchors only
# the start of the line and a trailing \r sails straight through it. A CRLF
# manifest then makes every recorded path end in \r, so a user's
# `sha256sum -c` looks for "skills/foo.py\r", does not find it, and the install
# dies at install.sh:1127 claiming the install may be compromised -- from a
# manifest this gate called well-formed. Caught by
# tests/test_release_installable.py::test_gate_rejects_a_manifest_with_crlf_line_endings.
if LC_ALL=C grep -q "$(printf '\r')" "${tmp}/CHECKSUMS.sha256"; then
    fail "The CHECKSUMS.sha256 asset on ${RELEASE_TAG} has CRLF line endings. Every path in it would end in a carriage return, so verification fails for every file during install. Fix: regenerate on a LF checkout (check core.autocrlf on the machine that signed it)."
fi

# Well-formedness: every line must be a sha256 followed by two spaces and a
# non-empty path. A truncated or HTML-error-page asset would otherwise pass
# presence and fail later, during a user's install, with "your install may be
# compromised".
if grep -qvE '^[0-9a-f]{64}  [^[:space:]]' "${tmp}/CHECKSUMS.sha256"; then
    fail "The CHECKSUMS.sha256 asset on ${RELEASE_TAG} is malformed (a line is not '<sha256>  <path>')."
fi

entries="$(wc -l < "${tmp}/CHECKSUMS.sha256" | tr -d ' ')"
printf 'manifest lines : %s\n' "$entries"

if [ "$VERIFY_TREE" = "1" ]; then
    # Only meaningful when the working tree IS the released tag. The committed
    # manifest is a release artifact and is expected to be stale on main between
    # releases, so this is opt-in and the caller is responsible for checking out
    # the tag first.
    HASH_CMD="sha256sum"
    [ "$(uname)" = "Darwin" ] && HASH_CMD="shasum -a 256"
    printf 'verifying tree against the released manifest...\n'
    if ! $HASH_CMD -c "${tmp}/CHECKSUMS.sha256" --quiet; then
        fail "The released manifest for ${RELEASE_TAG} does not match this tree. If this tree is at ${RELEASE_TAG}, the shipped manifest is stale or wrong and installs will abort at install.sh:1127 with 'your install may be compromised'. Fix: scripts/sign-release.sh ${RELEASE_TAG}."
    fi
    printf 'tree verified  : %s entries match\n' "$entries"
fi

printf 'RESULT: INSTALLABLE\n'
