#!/usr/bin/env bash
# Locate a usable Python 3 interpreter and exec it with the given arguments.
# Survives:
#   - macOS / Linux (python3 on PATH)
#   - Windows python.org installs at spaced paths like "C:\Program Files\Python313\"
#   - Windows py-launcher-only installs (py -3)
#   - Windows Store Python (real installs probed with --version; non-functional
#     AppExecutionAlias stubs skipped automatically)
# On Windows (Git Bash/MSYS), exec prefers pythonw.exe (GUI subsystem) over
# python.exe to avoid the per-hook console-window flash and orphaned
# conhost.exe. See _maybe_swap_to_pythonw for the constraints. py-launcher-only
# installs (`py -3`) still flash: py.exe has no GUI twin in its own directory
# and would need pyw.exe plus a new safe-prefix entry; documented, not fixed.
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

_is_msys_platform() {
    local platform
    platform=$(uname -s 2>/dev/null) || return 1
    case "$platform" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
    esac
    return 1
}

_cache_dir_is_per_user() {
    local cache_dir="$1" cache_real root root_real
    cache_real=$(CDPATH='' cd -- "$cache_dir" 2>/dev/null && pwd -P) || return 1
    for root in "${XDG_CACHE_HOME:-}" "${HOME:-}"; do
        [ -n "$root" ] && [ -d "$root" ] || continue
        root_real=$(CDPATH='' cd -- "$root" 2>/dev/null && pwd -P) || continue
        case "$cache_real" in
            "$root_real"/*) return 0 ;;
        esac
    done
    return 1
}

_cache_dir_ready() {
    local cache_dir="$1"
    if [ ! -d "$cache_dir" ]; then
        (umask 077; mkdir -p "$cache_dir") >/dev/null 2>&1 || return 1
    fi
    [ -d "$cache_dir" ] && [ -w "$cache_dir" ] || return 1
    if _is_msys_platform; then
        # Git Bash/MSYS2/Cygwin emulate POSIX ownership, so `test -O` can reject
        # the current user's own directory. Canonical confinement under HOME or
        # XDG_CACHE_HOME supplies the equivalent Windows ACL trust boundary.
        _cache_dir_is_per_user "$cache_dir"
    else
        # On native POSIX, require ownership rather than mere writability so a
        # planted cache record in another user's directory is never trusted.
        [ -O "$cache_dir" ]
    fi
}

_setup_interpreter_cache() {
    local launcher_dir plugin_dir hash_output plugin_hash path_hash_output path_hash cache_dir

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
    if ! path_hash_output=$(printf '%s' "${PATH:-}" | cksum 2>/dev/null); then
        return 0
    fi
    path_hash=${path_hash_output%% *}
    case "$path_hash" in
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

    _PY_CACHE_FILE="${cache_dir%/}/interpreter-${plugin_hash}-${path_hash}.cache"
}

# On Windows (Git Bash/MSYS), python.exe is a console-subsystem binary: each
# hook invocation allocates a transient console window (the "flash") and can
# orphan a conhost.exe. pythonw.exe is the GUI-subsystem twin shipped beside
# it by python.org; launching it allocates no console. We prefer pythonw.exe
# at EXEC time (not discovery time) so a stale cache record that still names
# python.exe does not keep flashing forever -- the cache key is unchanged, so
# the swap must happen on every exec, in both _exec_cached_interpreter and
# _exec_discovered_interpreter.
#
# Constraints (each is a real failure mode, not paranoia):
#   * Same directory only: pythonw.exe must sit next to the resolved
#     python.exe (same install, same version). We never synthesise a path
#     from PATH.
#   * Skip WindowsApps: a Microsoft Store pythonw AppExecutionAlias exits
#     silently outside an interactive token (see measure.py torture HIGH-1).
#   * stdout must be a pipe, not a tty: pythonw is GUI-subsystem. When its
#     std handles are not valid pipes, CPython sets sys.stdout/sys.stderr to
#     None and the hook protocol goes dark while the process exits 0 -- the
#     top regression for this change. A tty on fd 1 means a console is
#     already attached (no flash to avoid) AND pythonw could null it, so we
#     keep python.exe there. The hook context always provides a pipe, which
#     pythonw inherits intact.
#   * Non-Windows is a strict no-op: the MSYS guard short-circuits before any
#     filesystem probe, so POSIX behaviour is byte-for-byte unchanged.
#   * py.exe (the `py -3` launcher) is NOT swapped: its GUI twin would be
#     pyw.exe plus a new safe-prefix entry, and `py -3` resolves to python.exe
#     anyway. py-launcher-only installs therefore still flash; documented,
#     not fixed here.
#
# Sets _PYW_INTERP to the chosen interpreter (pythonw.exe when swapped, else
# the input unchanged). Selection never fails -- on any doubt we keep the
# caller's interpreter, so this can only ever opt INTO pythonw, never block
# an exec. Uses a variable (not stdout) so the [ -t 1 ] guard sees the
# launcher's real fd 1, not a command-substitution pipe.
_maybe_swap_to_pythonw() {
    local interp="$1" dir pythonw
    _PYW_INTERP="$interp"
    case "$interp" in
        */python.exe|*/python3.exe) ;;
        *) return 0 ;;
    esac
    _is_msys_platform || return 0
    if [ -t 1 ]; then
        return 0
    fi
    dir=${interp%/*}
    pythonw="${dir}/pythonw.exe"
    [ -f "$pythonw" ] && [ -x "$pythonw" ] && [ -s "$pythonw" ] || return 0
    _is_safe_prefix "$pythonw" || return 0
    case "$pythonw" in
        */WindowsApps/*|*/windowsapps/*) return 0 ;;
    esac
    # Liveness-probe the twin before committing to it. A corrupt/garbage
    # pythonw.exe (e.g. a half-overwritten install twin, or a 0xC000-style
    # Windows stub) passes the -f/-x/-s metadata checks but exits nonzero
    # (often 127) when actually run. exec consumes the process, so a dead
    # twin would brick every hook exec and the launcher's fallback ladder
    # becomes unreachable. Probe exactly like find_interpreter probes
    # WindowsApps python.exe: 2s timeout, -c "" (no program), stdin from
    # /dev/null so the hook's real stdin is never consumed by the probe.
    # On any doubt, keep python.exe (return 0) -- this can only ever opt
    # INTO pythonw, never block an exec.
    # C6: --kill-after=1s escalates to SIGKILL 1s after SIGTERM. pythonw is
    # GUI-subsystem and can ignore SIGTERM (no console handler), leaving a
    # hung twin holding the 2s budget past expiry and stalling the hook.
    if command -v timeout >/dev/null 2>&1; then
        timeout --kill-after=1s 2s "$pythonw" -c "" </dev/null >/dev/null 2>&1 || return 0
    else
        "$pythonw" -c "" </dev/null >/dev/null 2>&1 || return 0
    fi
    _PYW_INTERP="$pythonw"
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
    # A cache record must name the discovered interpreter (python.exe), never
    # its pythonw.exe twin (the swap is exec-only, see _maybe_swap_to_pythonw).
    # A record ending in /pythonw.exe is either poisoned or a stale artefact
    # of a broken twin that should have been probed out -- reject and let
    # discovery re-run, rather than exec'ing a possibly-dead twin directly.
    case "$interp" in
        */pythonw.exe) return 1 ;;
    esac
    [ -x "$interp" ] && [ -s "$interp" ] || return 1
    # A cache entry never bypasses the anti-PATH-hijack policy.
    _is_safe_prefix "$interp" || return 1
    # Windows: prefer the GUI-subsystem pythonw.exe twin at exec time so a
    # cached python.exe record does not keep flashing. No-op off-Windows.
    _maybe_swap_to_pythonw "$interp"
    interp=$_PYW_INTERP
    [ -n "$interp" ] || return 1
    [ -x "$interp" ] && [ -s "$interp" ] || return 1
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
    # Windows: prefer the GUI-subsystem pythonw.exe twin at exec time. The
    # cache record keeps naming python.exe (the discovered interpreter), and
    # the swap is reapplied on every exec -- including cache hits -- so a
    # stale record never freezes us on the flashing python.exe. No-op
    # off-Windows.
    _maybe_swap_to_pythonw "$interp"
    interp=$_PYW_INTERP
    [ -n "$interp" ] || return 1
    [ -x "$interp" ] && [ -s "$interp" ] || return 1
    _is_safe_prefix "$interp" || return 1
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
                    # C6: --kill-after=1s escalates to SIGKILL 1s after SIGTERM.
                    # A Store stub can spawn a child that ignores SIGTERM, leaving
                    # the probe hung past the 2s budget and stalling discovery.
                    if command -v timeout >/dev/null 2>&1; then
                        timeout --kill-after=1s 2s "$binpath" --version >/dev/null 2>&1 || continue
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
