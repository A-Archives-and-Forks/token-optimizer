#!/bin/bash
# Token Optimizer - One-command installer
#
# Usage:
#   git clone https://github.com/alexgreensh/token-optimizer.git ~/.claude/token-optimizer
#   bash ~/.claude/token-optimizer/install.sh
#
# What it does:
#   1. Checks prerequisites (Python 3.9+, git, ~/.claude/)
#   2. Clones (or updates) the repo into ~/.claude/token-optimizer
#   3. Symlinks the skill into ~/.claude/skills/token-optimizer
#   4. Prints success + usage instructions
#
# Idempotent: safe to run multiple times.
#
# Copyright (C) 2026 Alex Greenshpun
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

set -euo pipefail

GITHUB_REPO="alexgreensh/token-optimizer"
REPO_HTTPS="https://github.com/${GITHUB_REPO}.git"
REPO_SSH="git@github.com:${GITHUB_REPO}.git"
INSTALL_DIR="${HOME}/.claude/token-optimizer"
SKILL_DIR="${HOME}/.claude/skills"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/token-optimizer.XXXXXX")"
CHECKSUM_FILE="${TMP_DIR}/CHECKSUMS.sha256"
RELEASE_TAG=""
CHECKSUM_ASSET_URL=""
INSTALL_OLD_HEAD=""
INSTALL_UPDATED=0
VERIFIED_RELEASE_HEAD=""
trap 'rm -rf "$TMP_DIR"' EXIT

# ── Colors ────────────────────────────────────────────────────

if [ -t 1 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    RED='\033[0;31m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    GREEN='' YELLOW='' RED='' BOLD='' NC=''
fi

info()  { printf "${GREEN}>${NC} %s\n" "$1"; }
warn()  { printf "${YELLOW}!${NC} %s\n" "$1"; }
fail()  { printf "${RED}x${NC} %s\n" "$1"; exit 1; }

# ── OpenCode local-dir install (no npm) ───────────────────────
# `install.sh --opencode` builds the TypeScript plugin and drops a single
# bundled file into ~/.config/opencode/plugins/, which OpenCode auto-loads
# at startup. This is the offline / no-npm fallback to:
#     opencode plugin token-optimizer-opencode
# It needs bun (OpenCode's own runtime) and a checkout of this repo.

install_opencode() {
    command -v bun &>/dev/null || fail "bun not found. OpenCode runs on bun; install it first: https://bun.sh"

    # Locate the opencode/ source relative to this script.
    local script_dir oc_src
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    oc_src="${script_dir}/opencode"

    # If the source isn't present (e.g. a sparse Claude Code checkout), try to
    # add it to the sparse-checkout cone and pull it.
    if [ ! -d "$oc_src" ] && [ -d "${script_dir}/.git" ]; then
        warn "opencode/ not in this checkout. Adding it to sparse-checkout..."
        git -C "$script_dir" sparse-checkout add opencode/ 2>/dev/null || true
        git -C "$script_dir" pull --ff-only 2>/dev/null || true
    fi
    [ -d "$oc_src" ] || fail "opencode/ source not found. Clone the full repo: git clone ${REPO_HTTPS}"

    # Integrity: this path builds from your local clone, so the trust anchor is
    # the checkout itself. Surface the commit and flag a dirty tree so a tampered
    # or modified source tree is visible before it gets auto-loaded by OpenCode.
    # (npm is the cryptographically-verified channel; see the note below.)
    if [ -d "${script_dir}/.git" ]; then
        local oc_sha oc_dirty
        oc_sha="$(git -C "$script_dir" rev-parse --short HEAD 2>/dev/null || echo unknown)"
        info "Building from commit ${oc_sha}"
        oc_dirty="$(git -C "$script_dir" status --porcelain -- opencode/ 2>/dev/null)"
        if [ -n "$oc_dirty" ]; then
            warn "opencode/ has uncommitted local changes — building modified source:"
            printf '%s\n' "$oc_dirty" | sed 's/^/    /'
            if [ -e /dev/tty ]; then
                printf "Continue building this modified tree? (y/N) "
                read -r oc_confirm < /dev/tty
                [ "$oc_confirm" = "y" ] || [ "$oc_confirm" = "Y" ] || fail "Aborted by user."
            fi
        fi
    else
        warn "Not a git checkout — cannot verify source provenance. For a verified install use npm: opencode plugin token-optimizer-opencode"
    fi

    info "Installing OpenCode dependencies (bun install)..."
    # --frozen-lockfile: install exactly what bun.lock pins, no silent drift to a
    # newer (untested) transitive version at install time.
    if ! ( cd "$oc_src" && bun install --frozen-lockfile --silent ); then
        fail "bun install failed in ${oc_src} (lockfile out of sync? run 'bun install' in opencode/)."
    fi

    info "Building plugin bundle..."
    if ! ( cd "$oc_src" && bun run build:bundle ); then
        fail "Plugin bundle build failed."
    fi

    local bundle="${oc_src}/dist-bundle/token-optimizer.js"
    [ -f "$bundle" ] || fail "Bundle not produced at ${bundle}"

    local plugin_dir="${HOME}/.config/opencode/plugins"
    mkdir -p "$plugin_dir"
    cp "$bundle" "${plugin_dir}/token-optimizer.js"
    info "Installed to ${plugin_dir}/token-optimizer.js"

    echo ""
    printf "${BOLD}${GREEN}Token Optimizer for OpenCode installed!${NC}\n"
    echo ""
    echo "  Plugin:    ${plugin_dir}/token-optimizer.js (auto-loaded by OpenCode)"
    echo "  Tools:     token_status, token_dashboard"
    echo ""
    echo "  Start OpenCode and ask: \"run token_status\""
    echo "  Re-run this command after a git pull to update."
    echo ""
    echo "  Prefer npm? Once published:  opencode plugin token-optimizer-opencode"
    echo ""
    exit 0
}

# ── Hermes plugin install ─────────────────────────────────────
# `install.sh --hermes` installs the Token Optimizer plugin into
# ~/.hermes/plugins/token-optimizer/, which NousResearch Hermes auto-loads.
# Beta. Needs python3 and a checkout of this repo. Extra args (e.g. --dry-run,
# --uninstall) are forwarded to the underlying hermes-install command.
install_hermes() {
    command -v python3 &>/dev/null || fail "python3 not found. Token Optimizer for Hermes needs Python 3."

    local script_dir measure_py
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    measure_py="${script_dir}/skills/token-optimizer/scripts/measure.py"

    if [ ! -f "$measure_py" ] && [ -d "${script_dir}/.git" ]; then
        warn "skills/ not in this checkout. Adding it to sparse-checkout..."
        git -C "$script_dir" sparse-checkout add skills/ hermes/ 2>/dev/null || true
        git -C "$script_dir" pull --ff-only 2>/dev/null || true
    fi
    [ -f "$measure_py" ] || fail "$(printf 'Run install.sh from inside a token-optimizer checkout, not on its own. From any folder:\n    git clone --depth 1 %s\n    cd token-optimizer\n    bash install.sh --hermes' "$REPO_HTTPS")"

    if [ -d "${script_dir}/.git" ]; then
        local h_sha
        h_sha="$(git -C "$script_dir" rev-parse --short HEAD 2>/dev/null || echo unknown)"
        info "Installing from commit ${h_sha}"
    else
        warn "Not a git checkout — cannot verify source provenance."
    fi

    # Forward any extra flags (--dry-run, --uninstall, --json) after --hermes.
    local extra=()
    for a in "$@"; do [ "$a" = "--hermes" ] || extra+=("$a"); done

    info "Installing Token Optimizer into Hermes (~/.hermes/plugins/token-optimizer/)..."
    if ! python3 "$measure_py" hermes-install "${extra[@]}"; then
        fail "Hermes install failed."
    fi

    echo ""
    printf "${BOLD}${GREEN}Token Optimizer for Hermes installed (beta)!${NC}\n"
    echo ""
    echo "  Plugin:    ~/.hermes/plugins/token-optimizer/ (auto-loaded by Hermes)"
    echo "  Verify:    python3 ${measure_py} hermes-doctor"
    echo "  In Hermes: /token-optimizer  -  hermes token-optimizer (dashboard :24844)"
    echo "  Re-run this command after a git pull to update."
    echo ""
    exit 0
}

install_copilot() {
    command -v python3 &>/dev/null || fail "python3 not found. Token Optimizer for GitHub Copilot needs Python 3."

    local script_dir measure_py
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    measure_py="${script_dir}/skills/token-optimizer/scripts/measure.py"

    if [ ! -f "$measure_py" ] && [ -d "${script_dir}/.git" ]; then
        warn "skills/ not in this checkout. Adding it to sparse-checkout..."
        git -C "$script_dir" sparse-checkout add skills/ copilot/ 2>/dev/null || true
        git -C "$script_dir" pull --ff-only 2>/dev/null || true
    fi
    [ -f "$measure_py" ] || fail "$(printf 'Run install.sh from inside a token-optimizer checkout, not on its own. From any folder:\n    git clone --depth 1 %s\n    cd token-optimizer\n    bash install.sh --copilot' "$REPO_HTTPS")"

    if [ -d "${script_dir}/.git" ]; then
        local c_sha
        c_sha="$(git -C "$script_dir" rev-parse --short HEAD 2>/dev/null || echo unknown)"
        info "Installing from commit ${c_sha}"
    else
        warn "Not a git checkout — cannot verify source provenance."
    fi

    # Forward only recognized flags after --copilot (an unknown flag like
    # --hermes would otherwise reach argparse and fail the install with a
    # confusing "invalid choice" error).
    local extra=()
    for a in "$@"; do
        case "$a" in
            --dry-run) extra+=("$a") ;;
        esac
    done

    info "Installing Token Optimizer into GitHub Copilot CLI (~/.copilot/)..."
    if ! TOKEN_OPTIMIZER_RUNTIME=copilot python3 "$measure_py" copilot-install "${extra[@]}"; then
        fail "Copilot install failed."
    fi

    echo ""
    printf "${BOLD}${GREEN}Token Optimizer for GitHub Copilot installed (beta)!${NC}\n"
    echo ""
    echo "  Hooks:    ~/.copilot/hooks/token-optimizer.json (loaded by the Copilot CLI)"
    echo "  Verify:   TOKEN_OPTIMIZER_RUNTIME=copilot python3 ${measure_py} copilot-doctor"
    echo "  Summary:  TOKEN_OPTIMIZER_RUNTIME=copilot python3 ${measure_py} copilot-summary"
    echo "  VS Code:  enable both github.copilot.chat.agentDebugLog settings for per-request credit costs"
    echo "  Re-run this command after a git pull to update."
    echo ""
    exit 0
}

# Route --opencode / --hermes / --copilot before the Claude Code prerequisite
# checks (OpenCode needs bun; Hermes and Copilot need python3, not the Claude
# Code plugin env).
for arg in "$@"; do
    case "$arg" in
        --opencode) install_opencode ;;
        --hermes) install_hermes "$@" ;;
        --copilot) install_copilot "$@" ;;
    esac
done

# ── Prerequisites ─────────────────────────────────────────────

info "Checking prerequisites..."

# Python 3.9+
if ! command -v python3 &>/dev/null; then
    fail "python3 not found. Install Python 3.9+ first."
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] 2>/dev/null || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; } 2>/dev/null; then
    fail "Python ${PY_VERSION} found, but 3.9+ is required."
fi
info "Python ${PY_VERSION} OK"

# Git
if ! command -v git &>/dev/null; then
    fail "git not found. Install git first."
fi
info "git OK"

# curl (needed for out-of-band checksum verification)
if ! command -v curl &>/dev/null; then
    fail "curl not found. Install curl first."
fi

# Claude Code directory
if [ ! -d "${HOME}/.claude" ]; then
    fail "~/.claude/ not found. Install Claude Code first: https://claude.ai/download"
fi
info "~/.claude/ OK"

# ── Plugin Conflict Check ────────────────────────────────────

if [ -d "${HOME}/.claude/plugins/cache" ]; then
    if find "${HOME}/.claude/plugins/cache" -name "plugin.json" -exec grep -l '"name"[[:space:]]*:[[:space:]]*"token-optimizer"' {} \; 2>/dev/null | head -1 | grep -q .; then
        warn "Token Optimizer is already installed as a Claude Code plugin."
        warn "The script installer creates a skill symlink, which would duplicate the plugin."
        warn "If you want the script version instead, first uninstall the plugin:"
        warn "  /plugin uninstall token-optimizer@alexgreensh-token-optimizer"
        echo ""
        if [ -t 0 ] || [ -e /dev/tty ]; then
            printf "Continue anyway? (y/N) "
            read -r confirm < /dev/tty
            [ "$confirm" = "y" ] || [ "$confirm" = "Y" ] || exit 0
        else
            warn "Non-interactive mode detected. Skipping (use plugin install instead)."
            exit 0
        fi
    fi
fi

# ── Integrity Metadata ─────────────────────────────────────────
# Checksums are fetched from the GitHub release (out-of-band), NOT from
# the repo tree. This prevents a single compromised commit from swapping
# both code and checksums simultaneously.
# Set TOKEN_OPTIMIZER_SKIP_VERIFY=1 to bypass (air-gapped installs).

verification_enabled() {
    [ "${TOKEN_OPTIMIZER_SKIP_VERIFY:-}" != "1" ]
}

resolve_latest_release() {
    local release_json parsed
    release_json=$(curl -fsSL \
        "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" \
        2>/dev/null) || return 1

    parsed=$(printf '%s' "$release_json" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get("tag_name", ""))
    asset = ""
    for a in data.get("assets", []):
        if a.get("name") == "CHECKSUMS.sha256":
            asset = a.get("browser_download_url", "")
            break
    print(asset)
except Exception:
    print("")
    print("")
' 2>/dev/null) || return 1

    RELEASE_TAG=$(printf '%s\n' "$parsed" | sed -n '1p')
    CHECKSUM_ASSET_URL=$(printf '%s\n' "$parsed" | sed -n '2p')
    [ -n "$RELEASE_TAG" ] && [ -n "$CHECKSUM_ASSET_URL" ]
}

rollback_install_update() {
    if [ -n "$INSTALL_OLD_HEAD" ] && [ "$INSTALL_UPDATED" = "1" ] && [ -d "${INSTALL_DIR}/.git" ]; then
        local attempted_head
        attempted_head=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
        warn "Rolling back unverified update from ${attempted_head} to ${INSTALL_OLD_HEAD}"
        if git -C "$INSTALL_DIR" reset --hard "$INSTALL_OLD_HEAD" >/dev/null 2>&1; then
            info "Rollback succeeded"
        else
            warn "Rollback failed. Re-clone from: https://github.com/${GITHUB_REPO}"
        fi
    fi
}

fail_verified_install() {
    rollback_install_update
    fail "$1"
}

fetch_release_checksums() {
    [ -n "$CHECKSUM_ASSET_URL" ] || return 1
    curl -fsSL -o "$CHECKSUM_FILE" "$CHECKSUM_ASSET_URL" 2>/dev/null && [ -s "$CHECKSUM_FILE" ]
}

verify_checksum_manifest_coverage() {
    local target_dir="${1:-$INSTALL_DIR}"
    local manifest_list tracked_list missing
    manifest_list="${TMP_DIR}/checksum-manifest.paths"
    tracked_list="${TMP_DIR}/tracked-runtime.paths"

    awk 'NF >= 2 {print $2}' "$CHECKSUM_FILE" | sort -u > "$manifest_list"
    git -C "$target_dir" ls-files \
        install.sh \
        hooks/ \
        skills/ \
        .claude-plugin/ \
        .codex-plugin/ \
        .codex/ \
        | sort -u > "$tracked_list"

    missing="$(comm -23 "$tracked_list" "$manifest_list" || true)"
    if [ -n "$missing" ]; then
        warn "Release checksum manifest is missing installed runtime files:"
        printf '%s\n' "$missing" | sed 's/^/    /'
        return 1
    fi
    return 0
}

verify_checksums_in_dir() {
    local target_dir="$1"
    (
        cd "$target_dir" || exit 1
        sha256sum -c "$CHECKSUM_FILE" --quiet 2>/dev/null || \
        shasum -a 256 -c "$CHECKSUM_FILE" --quiet 2>/dev/null
    ) || return 1
    verify_checksum_manifest_coverage "$target_dir"
}

verify_release_candidate_before_live_update() {
    local candidate_dir candidate_url
    candidate_dir="${TMP_DIR}/release-candidate"
    candidate_url=$(git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || echo "$REPO_HTTPS")

    [ -s "$CHECKSUM_FILE" ] || fetch_release_checksums || return 1
    rm -rf "$candidate_dir"
    git clone --depth 1 --filter=blob:none --sparse --branch "$RELEASE_TAG" \
        "$candidate_url" "$candidate_dir" >/dev/null 2>&1 || return 1
    git -C "$candidate_dir" sparse-checkout set \
        skills/ hooks/ .claude-plugin/ .codex-plugin/ .codex/ \
        >/dev/null 2>&1 || return 1
    verify_checksums_in_dir "$candidate_dir" || return 1
    VERIFIED_RELEASE_HEAD=$(git -C "$candidate_dir" rev-parse HEAD 2>/dev/null || echo "")
    [ -n "$VERIFIED_RELEASE_HEAD" ]
}

if verification_enabled; then
    info "Resolving latest verified release..."
    resolve_latest_release || fail_verified_install "Could not resolve the latest GitHub Release and checksum asset. Integrity verification is required. Set TOKEN_OPTIMIZER_SKIP_VERIFY=1 only if you explicitly accept this risk."
    info "Latest verified release: ${RELEASE_TAG}"
else
    warn "Skipping integrity verification (TOKEN_OPTIMIZER_SKIP_VERIFY=1)"
fi

# ── Clone or Update ───────────────────────────────────────────

clone_repo() {
    local clone_log="${TMP_DIR}/clone.log"

    # Sparse checkout: only pull Claude Code files, skip OpenClaw platform files
    try_clone() {
        local url="$1"
        if verification_enabled; then
            git clone --depth 1 --filter=blob:none --sparse --branch "$RELEASE_TAG" "$url" "$INSTALL_DIR" 2>"$clone_log" || return 1
        else
            git clone --depth 1 --filter=blob:none --sparse "$url" "$INSTALL_DIR" 2>"$clone_log" || return 1
        fi
        # Cone mode only accepts directories; root-level files are included automatically
        git -C "$INSTALL_DIR" sparse-checkout set \
            skills/ hooks/ .claude-plugin/ .codex-plugin/ .codex/ \
            2>>"$clone_log" || return 1
    }

    if try_clone "$REPO_HTTPS"; then
        rm -f "$clone_log"
        return 0
    fi
    warn "HTTPS clone failed. Details: $(cat "$clone_log" 2>/dev/null)"
    rm -rf "$INSTALL_DIR"
    info "Trying SSH..."
    if try_clone "$REPO_SSH"; then
        rm -f "$clone_log"
        return 0
    fi
    warn "SSH clone also failed. Details: $(cat "$clone_log" 2>/dev/null)"
    rm -f "$clone_log"
    rm -rf "$INSTALL_DIR"
    fail "Could not clone repository. Check network connectivity and GitHub access."
}

update_repo() {
    local before_head after_head fetched_head
    before_head=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo "")
    if verification_enabled; then
        info "Updating to verified release ${RELEASE_TAG}..."
        verify_release_candidate_before_live_update || return 1
        git -C "$INSTALL_DIR" fetch --force --depth 1 origin "refs/tags/${RELEASE_TAG}:refs/tags/${RELEASE_TAG}" || return 1
        fetched_head=$(git -C "$INSTALL_DIR" rev-parse "${RELEASE_TAG}^{commit}" 2>/dev/null || echo "")
        [ -n "$fetched_head" ] && [ "$fetched_head" = "$VERIFIED_RELEASE_HEAD" ] || return 1
        git -C "$INSTALL_DIR" checkout --detach -q "$VERIFIED_RELEASE_HEAD" || return 1
    else
        git -C "$INSTALL_DIR" pull --ff-only || {
            warn "git pull failed. Try: cd ${INSTALL_DIR} && git pull"
            warn "Continuing with existing version."
        }
    fi
    after_head=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo "")
    if [ -n "$before_head" ] && [ "$after_head" != "$before_head" ]; then
        INSTALL_UPDATED=1
    fi
}

if [ -d "${INSTALL_DIR}/.git" ]; then
    info "Existing install found. Updating..."
    INSTALL_OLD_HEAD=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || true)

    # Enable sparse checkout on existing installs (migrates full clones)
    if ! git -C "$INSTALL_DIR" sparse-checkout list &>/dev/null || \
       git -C "$INSTALL_DIR" sparse-checkout list 2>/dev/null | grep -q "^/$"; then
        info "Migrating to sparse checkout (removing OpenClaw files)..."
        git -C "$INSTALL_DIR" sparse-checkout init --cone 2>/dev/null || true
        # Cone mode only accepts directories; root-level files are included automatically
        git -C "$INSTALL_DIR" sparse-checkout set \
            skills/ hooks/ .claude-plugin/ .codex-plugin/ .codex/ \
            2>/dev/null || true
    fi

    # Self-heal: v5.7.5-5.7.9 had a sparse-checkout bug that pruned skills/ and hooks/.
    # If they're missing after update, fix the sparse checkout config.
    if [ ! -d "${INSTALL_DIR}/skills" ] || [ ! -d "${INSTALL_DIR}/hooks" ]; then
        warn "Broken sparse checkout detected (skills/ or hooks/ missing). Repairing..."
        git -C "$INSTALL_DIR" sparse-checkout set \
            skills/ hooks/ .claude-plugin/ .codex-plugin/ .codex/ \
            2>/dev/null || git -C "$INSTALL_DIR" sparse-checkout disable 2>/dev/null || true
        if [ ! -d "${INSTALL_DIR}/skills" ]; then
            warn "Sparse checkout repair failed. Disabling sparse checkout..."
            git -C "$INSTALL_DIR" sparse-checkout disable 2>/dev/null || true
        fi
        if [ -d "${INSTALL_DIR}/skills" ]; then
            info "Sparse checkout repaired"
        else
            fail "Could not restore skills/ directory. Try: cd ${INSTALL_DIR} && git sparse-checkout disable"
        fi
    fi

    update_repo || fail_verified_install "Could not update to verified release ${RELEASE_TAG}. Check network connectivity or re-clone from: https://github.com/${GITHUB_REPO}"

    # A release checkout can change sparse checkout behavior. Repair again after update.
    if [ ! -d "${INSTALL_DIR}/skills" ] || [ ! -d "${INSTALL_DIR}/hooks" ]; then
        warn "Broken sparse checkout detected after update. Repairing..."
        git -C "$INSTALL_DIR" sparse-checkout set \
            skills/ hooks/ .claude-plugin/ .codex-plugin/ .codex/ \
            2>/dev/null || git -C "$INSTALL_DIR" sparse-checkout disable 2>/dev/null || true
        if [ ! -d "${INSTALL_DIR}/skills" ]; then
            warn "Sparse checkout repair failed. Disabling sparse checkout..."
            git -C "$INSTALL_DIR" sparse-checkout disable 2>/dev/null || true
        fi
        [ -d "${INSTALL_DIR}/skills" ] || fail_verified_install "Could not restore skills/ directory. Try re-cloning from: https://github.com/${GITHUB_REPO}"
    fi
elif [ -d "$INSTALL_DIR" ]; then
    BACKUP="${INSTALL_DIR}.backup.$(date +%Y%m%d_%H%M%S)"
    warn "Non-git install found at ${INSTALL_DIR}"
    warn "Backing up to ${BACKUP}"
    mv "$INSTALL_DIR" "$BACKUP"
    info "Cloning Token Optimizer..."
    clone_repo
else
    info "Cloning Token Optimizer..."
    clone_repo
fi

# ── Integrity Verification ────────────────────────────────────
if verification_enabled; then
    info "Fetching checksums from GitHub release..."
    if [ -s "$CHECKSUM_FILE" ] || fetch_release_checksums; then
        info "Verifying file integrity (out-of-band checksums)..."
        verify_checksums_in_dir "$INSTALL_DIR" || fail_verified_install "Integrity check FAILED. Files do not match release checksums or the release manifest is incomplete. Your install may be compromised. Re-clone from: https://github.com/${GITHUB_REPO}"
        info "Integrity check passed"
    else
        fail_verified_install "Could not fetch CHECKSUMS.sha256 from the latest GitHub Release. Integrity verification is required. Set TOKEN_OPTIMIZER_SKIP_VERIFY=1 only if you explicitly accept this risk."
    fi
fi

# Log the current commit SHA so users can audit which version is installed.
SHA_LOG_DIR="${HOME}/.claude/token-optimizer"
mkdir -p "$SHA_LOG_DIR"
CURRENT_SHA=$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo "unknown")
CURRENT_SHORT=$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
printf "%s\t%s\t%s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$CURRENT_SHA" "install" \
    >> "${SHA_LOG_DIR}/.last-verified-sha"
info "Verified commit ${CURRENT_SHORT} logged to ${SHA_LOG_DIR}/.last-verified-sha"

# ── Symlink Skill ─────────────────────────────────────────────

mkdir -p "$SKILL_DIR"
SKILL_LINK="${SKILL_DIR}/token-optimizer"

if [ -d "$SKILL_LINK" ] && [ ! -L "$SKILL_LINK" ]; then
    warn "/token-optimizer skill directory exists (not a symlink). Skipping."
    warn "To use the repo version, move it: mv ${SKILL_LINK} ${SKILL_LINK}.local"
elif [ -f "$SKILL_LINK" ] && [ ! -L "$SKILL_LINK" ]; then
    warn "Regular file exists at ${SKILL_LINK}. Moving to ${SKILL_LINK}.bak"
    mv "$SKILL_LINK" "${SKILL_LINK}.bak"
    ln -sf "${INSTALL_DIR}/skills/token-optimizer" "$SKILL_LINK"
    info "Linked /token-optimizer skill"
else
    ln -sf "${INSTALL_DIR}/skills/token-optimizer" "$SKILL_LINK"
    info "Linked /token-optimizer skill"
fi

# ── Make Scripts Executable ───────────────────────────────────

chmod +x "${INSTALL_DIR}/skills/token-optimizer/scripts/measure.py" 2>/dev/null || true

# ── Setup Quality Bar (auto-install cache hook + status line) ─

info "Setting up quality bar..."
if python3 "${INSTALL_DIR}/skills/token-optimizer/scripts/measure.py" setup-quality-bar 2>/dev/null; then
    info "Quality bar installed (status line + cache hook)"
else
    warn "Could not auto-install quality bar. Run manually in Claude Code:"
    warn "  python3 measure.py setup-quality-bar"
fi

# ── Setup All Hooks (v5.0.1: merge plugin hooks.json into settings.json) ────
# Canonical way for script installs to get the full v5 hook set.
# Idempotent: safe to re-run on every install and verified release update.
# Upgrades from v4.x pick up v5 active compression hooks here.

info "Installing all Token Optimizer hooks..."
HOOK_OUTPUT=$(python3 "${INSTALL_DIR}/skills/token-optimizer/scripts/measure.py" setup-all-hooks 2>&1)
HOOK_EXIT=$?
if [ $HOOK_EXIT -eq 0 ]; then
    HOOK_SUMMARY=$(echo "$HOOK_OUTPUT" | grep -E "Added [0-9]+|All hooks already present" | head -1)
    if [ -n "$HOOK_SUMMARY" ]; then
        info "$(echo "$HOOK_SUMMARY" | sed 's/^[[:space:]]*\[setup-all-hooks\][[:space:]]*//')"
    else
        info "Hooks installed"
    fi
    # setup_all_hooks updates last_hook_heal_check automatically on success,
    # suppressing the redundant ensure-health run for the next 24h.
else
    warn "Could not auto-install hooks. Run manually:"
    warn "  python3 ${INSTALL_DIR}/skills/token-optimizer/scripts/measure.py setup-all-hooks"
fi

# ── Summary ───────────────────────────────────────────────────

COMMIT=$(git -C "$INSTALL_DIR" rev-parse --short HEAD 2>/dev/null || echo "?")

echo ""
printf "${BOLD}${GREEN}Token Optimizer installed!${NC}\n"
echo ""
echo "  Location:  ${INSTALL_DIR}"
echo "  Commit:    ${COMMIT}"
echo "  Skill:     /token-optimizer"
echo "  Quality:   ContextQ score in status line (updates every ~2 min)"
echo ""
echo "  Measure current overhead:"
echo "    python3 ${INSTALL_DIR}/skills/token-optimizer/scripts/measure.py report"
echo ""
echo "  Start a Claude Code session and run:"
echo "    /token-optimizer"
echo ""
echo "  Full docs: https://github.com/alexgreensh/token-optimizer"
echo ""
