#!/usr/bin/env bash
# to-hook-probe: record that a hook event fired, with enough context to
# debug the install path. Fail-open by design: this script must NEVER
# block the user's session, so every step tolerates failure and the exit
# code is always 0.
#
# Evidence it leaves, in order of reachability:
#   1. fired.log ledger + per-event dump under $TO_PROBE_DIR (default
#      ~/.to-hook-probe) and /tmp/to-hook-probe — readable from inside the
#      session ("ls ~/.to-hook-probe") and, for local Cowork VMs, from the
#      host if the tree is host-visible.
#   2. Optional phone-home: POST the dump to $TO_PROBE_URL/probe — the only
#      evidence channel that works for CLOUD sessions (no local disk).
#      Requires the collector's domain on Cowork's allowlist.
#
# Config: ${CLAUDE_PLUGIN_ROOT}/probe.env (copy probe.env.example) is
# sourced if present, so an org admin can bake TO_PROBE_URL into the
# uploaded plugin without editing this script.
set +e

EVENT="${1:-unknown}"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown-time)"

# Hook payload arrives on stdin as JSON; cap it so a huge tool result
# can't bloat the dump.
PAYLOAD="$(head -c 20000 2>/dev/null)"

[ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "${CLAUDE_PLUGIN_ROOT}/probe.env" ] && . "${CLAUDE_PLUGIN_ROOT}/probe.env" 2>/dev/null

PROBE_HOME="${TO_PROBE_DIR:-${HOME}/.to-hook-probe}"
DUMP=""
for dir in "$PROBE_HOME" /tmp/to-hook-probe; do
    mkdir -p "$dir" 2>/dev/null || continue
    printf '%s %s\n' "$STAMP" "$EVENT" >> "$dir/fired.log" 2>/dev/null
    file="$dir/probe-${EVENT}-$(date -u +%Y%m%d%H%M%S 2>/dev/null)-$$.log"
    {
        echo "== to-hook-probe =="
        echo "event: $EVENT"
        echo "time: $STAMP"
        echo "pid: $$"
        echo "cwd: $(pwd 2>/dev/null)"
        echo "uname: $(uname -a 2>/dev/null)"
        echo "whoami: $(whoami 2>/dev/null)"
        echo "plugin_root: ${CLAUDE_PLUGIN_ROOT:-<unset>}"
        echo "python3: $(command -v python3 2>/dev/null || echo '<none>')"
        echo ""
        echo "== env (values redacted for KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/AUTH) =="
        # Env names reveal the runtime (CLAUDE_*, session ids, cloud-vs-local);
        # values of anything credential-shaped are redacted so a phone-home
        # can't exfiltrate secrets.
        env 2>/dev/null | sort | sed -E 's/^([^=]*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)[^=]*)=.*/\1=<redacted>/I'
        echo ""
        echo "== hook stdin payload (first 20000 bytes) =="
        printf '%s\n' "$PAYLOAD"
    } > "$file" 2>/dev/null
    [ -z "$DUMP" ] && [ -s "$file" ] && DUMP="$file"
done

# Phone home (cloud sessions have no readable local disk, so this is the
# only evidence channel there). 3s cap; failure is silent.
if [ -n "${TO_PROBE_URL:-}" ] && [ -n "$DUMP" ] && command -v curl >/dev/null 2>&1; then
    curl -m 3 -s -o /dev/null -X POST \
        -H "Content-Type: text/plain" \
        -H "X-TO-Probe-Event: $EVENT" \
        --data-binary "@$DUMP" \
        "${TO_PROBE_URL%/}/probe" 2>/dev/null
fi

exit 0
