#!/usr/bin/env python3
"""Install Token Optimizer into Hermes as a plugin.

Copies the ``hermes/`` payload directory from the Token Optimizer repo into
``~/.hermes/plugins/token-optimizer/`` (or ``$HERMES_HOME/plugins/token-optimizer/``).

The operation is idempotent: re-running replaces files in place without
corrupting any existing Hermes state or other plugins.

Usage (from measure.py dispatch or directly):
    python3 hermes_install.py [--dry-run] [--uninstall] [--json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from runtime_env import hermes_home

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PLUGIN_NAME = "token-optimizer"
_HERMES_PLUGIN_DIR_NAME = "plugins"


def _repo_root() -> Path:
    """Return the token-optimizer repo root (3 parents up from this script)."""
    return Path(__file__).resolve().parents[3]


def _payload_dir() -> Path:
    """Return the ``hermes/`` payload directory bundled in the repo."""
    return _repo_root() / "hermes"


def _plugin_install_dir(hermes_root: Path) -> Path:
    """Return the path where Hermes should find our plugin."""
    return hermes_root / _HERMES_PLUGIN_DIR_NAME / _PLUGIN_NAME


# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------

def _assert_no_symlink_escape(path: Path, expected_parent: Path) -> None:
    """Raise ValueError if *path* would escape *expected_parent* after resolution."""
    try:
        resolved = path.resolve(strict=False)
        expected_resolved = expected_parent.resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Path resolution failed for {path}: {exc}") from exc
    if not resolved.is_relative_to(expected_resolved):
        raise ValueError(f"{path} escapes {expected_parent}")


# ---------------------------------------------------------------------------
# Core install / uninstall
# ---------------------------------------------------------------------------

def install(
    *,
    dry_run: bool = False,
) -> tuple[Path, str, dict]:
    """Copy the ``hermes/`` payload into Hermes's plugins directory.

    Returns (install_dir, action, details).

    Idempotent: existing files are overwritten; extra files in the target dir
    that came from a previous install are retained (they may have been written
    by Hermes or the user). We only copy *our* payload files, never delete.
    """
    payload = _payload_dir()
    if not payload.is_dir():
        raise ValueError(
            f"Hermes plugin payload not found at {payload}. "
            "The repo's hermes/ directory must exist before installing."
        )

    hermes_root = hermes_home()
    install_dir = _plugin_install_dir(hermes_root)

    # Verify the install_dir is inside the hermes root (not a symlink escape).
    _assert_no_symlink_escape(install_dir, hermes_root)

    files_to_copy = sorted(payload.rglob("*"))
    payload_files = [f for f in files_to_copy if f.is_file()]

    details: dict = {
        "plugin_dir": str(install_dir),
        "hermes_home": str(hermes_root),
        "files": [str(f.relative_to(payload)) for f in payload_files],
        "dry_run": dry_run,
    }

    if dry_run:
        return install_dir, "would-install", details

    # Create the target directory tree.
    install_dir.mkdir(parents=True, exist_ok=True)

    # Copy each file, preserving relative structure.
    for src in payload_files:
        rel = src.relative_to(payload)
        dst = install_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # Hermes discovers plugins by directory presence (plugin.yaml + __init__.py);
    # no separate config registration is required. Print activation note anyway.
    details["activation"] = (
        "Plugin installed. Hermes auto-discovers plugins from its plugins/ directory. "
        "No additional activation step needed."
    )
    return install_dir, "installed", details


def uninstall(*, dry_run: bool = False) -> tuple[Path, str, dict]:
    """Remove Token Optimizer from Hermes's plugins directory."""
    hermes_root = hermes_home()
    install_dir = _plugin_install_dir(hermes_root)

    _assert_no_symlink_escape(install_dir, hermes_root)

    details: dict = {
        "plugin_dir": str(install_dir),
        "hermes_home": str(hermes_root),
        "dry_run": dry_run,
        "existed": install_dir.exists(),
    }

    if not install_dir.exists():
        return install_dir, "not-found", details

    if not dry_run:
        shutil.rmtree(install_dir)

    return install_dir, "removed" if not dry_run else "would-remove", details


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install Token Optimizer as a Hermes plugin."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print intended action without writing",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove Token Optimizer from Hermes plugins",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.uninstall:
            install_dir, action, details = uninstall(dry_run=args.dry_run)
        else:
            install_dir, action, details = install(dry_run=args.dry_run)
    except ValueError as exc:
        print(f"[Token Optimizer] {exc}", file=sys.stderr)
        return 1

    payload = {
        "action": action,
        "plugin_dir": str(install_dir),
        "dry_run": args.dry_run,
        "details": details,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        prefix = "Would update" if args.dry_run else "Updated"
        n_files = len(details.get("files", []))
        print(f"[Token Optimizer] {prefix} {install_dir} ({action}; {n_files} files)")
        if not args.dry_run and "activation" in details:
            print(f"[Token Optimizer] {details['activation']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
