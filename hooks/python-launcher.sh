#!/usr/bin/env bash
# Locate a usable Python 3 interpreter and exec it with the given arguments.
# Survives:
#   - macOS / Linux (python3 on PATH)
#   - Windows python.org installs at spaced paths like "C:\Program Files\Python313\"
#   - Windows py-launcher-only installs (py -3)
#   - Windows Store Python (real installs probed with --version; non-functional
#     AppExecutionAlias stubs skipped automatically)
# Exits 127 with a diagnostic message if none found.

set -eu

# Known-safe prefixes for Python interpreter binaries.
# Binaries outside these directories are rejected even if on PATH.
# This prevents a compromised PATH entry from hijacking the interpreter.
# All prefixes are hardcoded (not derived from PATH-controlled binaries
# like `brew --prefix`, which would be circular trust).
_SAFE_PREFIXES="/usr/bin /usr/local/bin /opt/homebrew/bin /opt/homebrew/opt /home/linuxbrew/.linuxbrew/bin"

_is_safe_prefix() {
    local IFS=$' \t\n'
    local binpath="$1" prefix
    # Reject path traversal FIRST: a '..' component lets a textual prefix match
    # (e.g. /usr/bin/../../tmp/evil/python3) pass the allow-list globs below yet
    # resolve OUTSIDE a trusted dir at exec time. Interpreter paths are absolute
    # and never legitimately contain a '..' path component.
    case "$binpath" in
        *"/../"*|*"/..") return 1 ;;
    esac
    for prefix in $_SAFE_PREFIXES; do
        case "$binpath" in
            "$prefix"/*) return 0 ;;
        esac
    done
    # Windows install locations (git-bash/MSYS path form, e.g. /c/...).
    # Drive-letter-anchored to preserve the anti-PATH-hijack intent.
    # Version-number suffixes block directory-name spoofing (e.g. Python3-evil).
    case "$binpath" in
        /[a-zA-Z]/Program\ Files/Python[23]*)                          return 0 ;;
        /[a-zA-Z]/Program\ Files\ \(x86\)/Python[23]*)                 return 0 ;;
        /[a-zA-Z]/Python3[0-9]*)                                       return 0 ;;
        /[a-zA-Z]/Users/*/AppData/Local/Programs/Python/*)              return 0 ;;
        /[a-zA-Z]/Users/*/AppData/Local/Microsoft/WindowsApps/*)        return 0 ;;
        # All-users `py` launcher lives in the (admin-only-writable) Windows dir.
        # Exact filename keeps the anti-hijack intent (no wildcard in that dir).
        /[a-zA-Z]/Windows/py.exe)                                      return 0 ;;
    esac
    return 1
}

# The cache is an optional optimization only. Any setup, read, or write failure
# leaves _PY_CACHE_FILE empty (or is ignored) so interpreter discovery proceeds
# exactly as it did before caching was added.
_PY_CACHE_FILE=""

_cache_dir_ready() {
    local cache_dir="$1"
    if [ ! -d "$cache_dir" ]; then
        (umask 077; mkdir -p "$cache_dir") >/dev/null 2>&1 || return 1
    fi
    # Require the dir to be owned by us (-O), not merely writable: a dir another
    # user pre-created (and left world-writable) would otherwise be accepted and
    # its planted cache record trusted. Owned + created umask 077 keeps it 0700.
    [ -d "$cache_dir" ] && [ -w "$cache_dir" ] && [ -O "$cache_dir" ]
}

_setup_interpreter_cache() {
    local launcher_dir plugin_dir hash_output plugin_hash cache_dir

    launcher_dir=${0%/*}
    [ "$launcher_dir" != "$0" ] || launcher_dir=.
    if ! plugin_dir=$(CDPATH='' cd -- "$launcher_dir" 2>/dev/null && pwd -P); then
        return 0
    fi

    # cksum is POSIX and is present in the Unix environments supported by this
    # launcher, including Git Bash. If unavailable, caching simply stays off.
    if ! hash_output=$(printf '%s' "$plugin_dir" | cksum 2>/dev/null); then
        return 0
    fi
    plugin_hash=${hash_output%% *}
    case "$plugin_hash" in
        ''|*[!0-9]*) return 0 ;;
    esac

    if [ "${TOKEN_OPTIMIZER_PY_CACHE+x}" = x ]; then
        cache_dir=$TOKEN_OPTIMIZER_PY_CACHE
        [ -n "$cache_dir" ] || return 0
        _cache_dir_ready "$cache_dir" || return 0
    else
        cache_dir=""
        if [ -n "${XDG_CACHE_HOME:-}" ]; then
            cache_dir="${XDG_CACHE_HOME}/token-optimizer/pylauncher"
        elif [ -n "${HOME:-}" ]; then
            cache_dir="${HOME}/.cache/token-optimizer/pylauncher"
        fi

        # No shared/world-predictable fallback (e.g. /tmp/token-optimizer-$UID):
        # such a path lets another local user pre-create the dir and plant a
        # poisoned interpreter record. If no per-user home cache dir is available
        # and writable, caching stays OFF and discovery runs exactly as before
        # (fail-open). HOME is set in every supported hook env, incl. Git Bash.
        { [ -n "$cache_dir" ] && _cache_dir_ready "$cache_dir"; } || return 0
    fi

    _PY_CACHE_FILE="${cache_dir%/}/interpreter-${plugin_hash}.cache"
}

_exec_cached_interpreter() {
    local record payload interp marker

    [ -n "$_PY_CACHE_FILE" ] && [ -f "$_PY_CACHE_FILE" ] || return 1
    record=""
    if ! IFS= read -r record 2>/dev/null < "$_PY_CACHE_FILE"; then
        return 1
    fi
    case "$record" in
        $'INTERP\t'*) payload=${record#*$'\t'} ;;
        *) return 1 ;;
    esac
    case "$payload" in
        *$'\t'*)
            interp=${payload%%$'\t'*}
            marker=${payload#*$'\t'}
            [ "$marker" = "-3" ] || return 1
            ;;
        *)
            interp=$payload
            marker=""
            ;;
    esac
    [ -n "$interp" ] || return 1
    case "$interp" in
        /*) ;;
        *) return 1 ;;
    esac
    [ -x "$interp" ] && [ -s "$interp" ] || return 1
    # A cache entry never bypasses the anti-PATH-hijack policy.
    _is_safe_prefix "$interp" || return 1

    if [ "$marker" = "-3" ]; then
        exec "$interp" -3 "$@"
        return 1
    fi
    exec "$interp" "$@"
}

_write_interpreter_cache() {
    local interp="$1" marker="$2" cache_tmp

    [ -n "$_PY_CACHE_FILE" ] || return 0
    cache_tmp="${_PY_CACHE_FILE}.tmp.$$"
    if [ "$marker" = "-3" ]; then
        (umask 077; set -C; printf 'INTERP\t%s\t-3\n' "$interp" > "$cache_tmp" &&
            mv -f "$cache_tmp" "$_PY_CACHE_FILE") 2>/dev/null || :
    else
        (umask 077; set -C; printf 'INTERP\t%s\n' "$interp" > "$cache_tmp" &&
            mv -f "$cache_tmp" "$_PY_CACHE_FILE") 2>/dev/null || :
    fi
    return 0
}

_exec_discovered_interpreter() {
    local interp="$1" marker="$2"
    shift 2

    # Discovery already applied the safe-prefix check. Re-applying it here
    # makes that invariant explicit at the shared cache-write/exec boundary.
    _is_safe_prefix "$interp" || return 1
    _write_interpreter_cache "$interp" "$marker"
    if [ "$marker" = "-3" ]; then
        exec "$interp" -3 "$@"
    fi
    exec "$interp" "$@"
}

_setup_interpreter_cache
_exec_cached_interpreter "$@" || :

find_interpreter() {
    local name="$1"
    local IFS=:
    local dir binpath ext
    for dir in $PATH; do
        [ -n "$dir" ] || dir="."
        for ext in "" ".exe"; do
            binpath="${dir}/${name}${ext}"
            [ -x "$binpath" ] || continue
            [ -s "$binpath" ] || continue
            # Reject interpreters outside known-safe prefix directories.
            # Prevents PATH-order attacks where a malicious dir appears first.
            _is_safe_prefix "$binpath" || continue
            case "$binpath" in
                */WindowsApps/*|*/windowsapps/*)
                    # WindowsApps may contain real Store-installed Python OR
                    # non-functional AppExecutionAlias stubs (non-zero-byte, pass -s).
                    # Probe with --version (2s timeout) to distinguish them.
                    if command -v timeout >/dev/null 2>&1; then
                        timeout 2s "$binpath" --version >/dev/null 2>&1 || continue
                    else
                        "$binpath" --version >/dev/null 2>&1 || continue
                    fi
                    ;;
            esac
            printf "%s\n" "$binpath"
            return 0
        done
    done
    return 1
}

if py3=$(find_interpreter "python3"); then
    _exec_discovered_interpreter "$py3" "" "$@"
fi

if py=$(find_interpreter "python"); then
    _exec_discovered_interpreter "$py" "" "$@"
fi

if pyl=$(find_interpreter "py"); then
    _exec_discovered_interpreter "$pyl" "-3" "$@"
fi

# Direct probe: hook environments often have a stripped PATH that excludes
# the user's Python. Check known locations directly as a fallback.
for _direct in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 \
               /home/linuxbrew/.linuxbrew/bin/python3; do
    if [ -x "$_direct" ] && [ -s "$_direct" ] && _is_safe_prefix "$_direct"; then
        _exec_discovered_interpreter "$_direct" "" "$@"
    fi
done

echo "token-optimizer: no usable Python 3 interpreter found" >&2
echo "  tried: python3, python, py -3, direct paths" >&2
echo "  on Windows: install Python from https://python.org/ and restart Claude Code" >&2
exit 127
