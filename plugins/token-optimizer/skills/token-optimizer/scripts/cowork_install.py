#!/usr/bin/env python3
"""Package Token Optimizer for Claude Cowork (org admin plugin console).

Cowork does NOT read ~/.claude — plugins reach Cowork sessions (cloud AND
local VM) only via the Anthropic org admin plugin console, which
account-syncs a pushed plugin into every org user's sessions. So unlike
codex_install.py this "installer" never edits local config: it builds a
clean plugin payload (dir + zip) under dist/cowork/ for the admin to push,
plus the to-hook-probe diagnostic plugin, and prints the console steps.

TO is already a plugin in the shared Claude Code/Cowork format, so the
payload is the existing repo runtime set verbatim with ONE change:
hooks/hooks.json is trimmed to the event set that PROVABLY fires in Cowork
(UserPromptSubmit, PreToolUse, PostToolUse, Stop) and keepwarm is dropped
(its premise — keep a local CLI warm — does not transfer to Cowork).

No SessionStart remap lives here anymore. The master hooks/hooks.json is
Cowork-native in place: the run-once SessionStart features (ensure-health,
quality-cache --force, compact-restore --new-session-only) are ALSO wired
onto UserPromptSubmit behind a per-session run-once guard
(measure.py --once-per-session). On native Claude Code, SessionStart fires
them first and sets the guard marker, so the UserPromptSubmit copies no-op
(one stat) — zero behaviour change. In Cowork (SessionStart is dead) the
UserPromptSubmit copies do the work on the first prompt. So this packager is
now a PURE TRIM to the firing events: the trimmed UserPromptSubmit already
carries full parity, no injection required. SubagentStop fires in Cowork but
the master hooks.json wires nothing to it, so nothing rides it.

Degraded in Cowork (documented, unavoidable): compaction-restore on the
NATIVE compaction trigger. The master SessionStart "compact" matcher
(compact-restore --compact + read_cache --clear-compacted) and PreCompact /
PostCompact all rely on events that do not fire in Cowork, so they are
trimmed away. The Stop-hook compact-CAPTURE still saves state, and the
fresh-session compact-restore --new-session-only reads it back on the next
session's first prompt; only the in-place restore at the moment Cowork
auto-compacts is lost.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# The hook events that PROVABLY fire in cloud Cowork (Claude Code 2.1.231
# engine in a VM), verified live. Everything else in hooks/hooks.json
# (SessionStart, PreCompact, PostCompact, SessionEnd, StopFailure,
# CwdChanged) does NOT fire in Cowork and is dropped from the payload; hooks
# are additive and fail-open, so a dropped event degrades nothing. SessionStart
# is dead here — its run-once features are carried on UserPromptSubmit by the
# master hooks.json (see module docstring), so no remap is needed at pack time.
# SubagentStop fires in Cowork but the master hooks.json wires nothing to it.
COWORK_EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

# Commands containing these markers are excluded even within kept events.
DROP_COMMAND_MARKERS = ("keepwarm-arm",)

# Runtime set that must ride into the payload. Everything else in the repo
# (docs, tests, other host adapters, qa dirs) stays out of the zip.
PAYLOAD_INCLUDE = (
    ".claude-plugin",
    "hooks",
    "skills",
    "commands",
    "LICENSE",
    "PRIVACY.md",
    "README.md",
)

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _plugin_version(root: Path) -> str:
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return str(manifest.get("version", "0.0.0"))


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", "") or (Path.home() / ".claude")).expanduser()


def _guard_out_dir(out: Path) -> None:
    """Refuse to build inside ~/.claude: Cowork account-syncs plugins from
    the org console, not from local files, and writing there both misleads
    (looks installed, is not) and risks polluting the account-synced tree."""
    resolved = out.resolve(strict=False)
    claude_home = _claude_home().resolve(strict=False)
    if resolved == claude_home or resolved.is_relative_to(claude_home):
        raise ValueError(
            f"refusing to write the Cowork payload under {claude_home} — "
            "Cowork does not read local plugin files; build to a neutral dir "
            "and push via the org admin console"
        )


def build_cowork_hooks(template: dict[str, Any]) -> dict[str, Any]:
    """Trim the Claude Code hooks.json to the Cowork-firing event set.

    Pure trim, no injection: the master UserPromptSubmit group already carries
    the run-once SessionStart features (ensure-health / quality-cache --force /
    compact-restore --new-session-only, each guarded with --once-per-session),
    so trimming to COWORK_EVENTS yields full Cowork parity automatically.
    keepwarm is still dropped via DROP_COMMAND_MARKERS.
    """
    hooks = template.get("hooks", {})
    trimmed: dict[str, Any] = {}
    for event in COWORK_EVENTS:
        groups = []
        for group in hooks.get(event, []):
            kept = [
                h for h in group.get("hooks", [])
                if not any(marker in h.get("command", "") for marker in DROP_COMMAND_MARKERS)
            ]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            trimmed[event] = groups
    return {"hooks": trimmed}


def build_plugin_payload(root: Path, dist: Path) -> Path:
    version = _plugin_version(root)
    payload = dist / f"token-optimizer-cowork-{version}"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)
    for rel in PAYLOAD_INCLUDE:
        src = root / rel
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, payload / rel, ignore=_IGNORE)
        else:
            shutil.copy2(src, payload / rel)
    template = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    cowork_hooks = build_cowork_hooks(template)
    (payload / "hooks" / "hooks.json").write_text(
        json.dumps(cowork_hooks, indent=2) + "\n", encoding="utf-8"
    )
    _add_hooks_pointer(payload / ".claude-plugin" / "plugin.json")
    return payload


def _add_hooks_pointer(manifest_path: Path) -> None:
    """Point the manifest at hooks/hooks.json explicitly. Per issue #16288,
    some Cowork builds only load plugin hooks when the manifest declares
    them; Claude Code defaults to the same path, so the field is harmless
    everywhere else."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hooks"] = "./hooks/hooks.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_probe_payload(root: Path, dist: Path) -> Path | None:
    src = root / "cowork" / "to-hook-probe"
    if not src.exists():
        return None
    version = json.loads(
        (src / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    ).get("version", "0.0.0")
    payload = dist / f"to-hook-probe-{version}"
    if payload.exists():
        shutil.rmtree(payload)
    shutil.copytree(src, payload, ignore=_IGNORE)
    return payload


def _zip(payload: Path) -> Path:
    archive = shutil.make_archive(str(payload), "zip", root_dir=payload)
    return Path(archive)


ORG_CONSOLE_STEPS = """\
Next steps (org admin, needs a live console — see cowork/README.md):
  1. Anthropic admin console -> Settings -> Plugins.
  2. Register/upload the payload above (zip, or point the console at this
     git repo if it takes a marketplace source) and set availability:
     'available to install' / 'installed by default' / 'required'.
     Push to-hook-probe FIRST to prove hooks fire on your build.
  3. Add the TO telemetry/collector domain to Cowork's domain allowlist
     (org security settings) or hook phone-home + OTel POSTs are blocked.
  4. Run one Cowork session, then: python3 skills/token-optimizer/scripts/cowork_doctor.py
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the Token Optimizer Cowork plugin payload (org-console push; never touches ~/.claude)."
    )
    parser.add_argument("--out", default=None, help="Output dir (default: <repo>/dist/cowork)")
    parser.add_argument("--no-zip", action="store_true", help="Build payload dirs only, skip zips")
    parser.add_argument("--plugin-only", action="store_true", help="Skip the to-hook-probe payload")
    parser.add_argument("--probe-only", action="store_true", help="Build only the to-hook-probe payload")
    parser.add_argument("--dry-run", action="store_true", help="Print intended outputs without writing")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    args = parser.parse_args(argv)

    root = _repo_root()
    dist = Path(args.out).expanduser() if args.out else root / "dist" / "cowork"
    try:
        _guard_out_dir(dist)
    except ValueError as exc:
        print(f"[Token Optimizer] {exc}", file=sys.stderr)
        return 1

    result: dict[str, Any] = {
        "dist": str(dist),
        "version": _plugin_version(root),
        "events": list(COWORK_EVENTS),
        "dry_run": args.dry_run,
        "artifacts": [],
    }

    if args.dry_run:
        if not args.probe_only:
            result["artifacts"].append(str(dist / f"token-optimizer-cowork-{result['version']}"))
        if not args.plugin_only:
            result["artifacts"].append(str(dist / "to-hook-probe-<version>"))
    else:
        dist.mkdir(parents=True, exist_ok=True)
        payloads: list[Path] = []
        if not args.probe_only:
            payloads.append(build_plugin_payload(root, dist))
        if not args.plugin_only:
            probe = build_probe_payload(root, dist)
            if probe is not None:
                payloads.append(probe)
        for payload in payloads:
            result["artifacts"].append(str(payload))
            if not args.no_zip:
                result["artifacts"].append(str(_zip(payload)))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        verb = "Would build" if args.dry_run else "Built"
        print(f"[Token Optimizer] {verb} Cowork payload (hooks: {', '.join(COWORK_EVENTS)}):")
        for artifact in result["artifacts"]:
            print(f"  {artifact}")
        print()
        print(ORG_CONSOLE_STEPS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
