"""The shipped READMEs must not be gutted, truncated, or stripped of license.

This rides the existing tests.yml matrix rather than adding a workflow, so the
gate runs on every push and PR on the same enforcement path the suite already
proved out. See scripts/check_readme_integrity.py for why it exists.

The negative tests below are the point. A gate that only ever passes is
indistinguishable from a gate that cannot fail, so each check is exercised
against a malformed or incomplete README shape and asserted to reject it on its
own, without help from the others.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_readme_integrity.py"

# Representative gutted README shape: a truncated tag with no substantive
# document content.
GUTTED_README = (
    '<p align="center">\n'
    '  <img src="skills/token-o\n'
    "\n"
    "---\n"
    "* Incomplete document *\n"
)


def _run() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )


@pytest.fixture
def readme_swap():
    """Swap root README content, always restoring the original."""
    path = REPO / "README.md"
    original = path.read_text(encoding="utf-8")

    def _swap(content: str):
        path.write_text(content, encoding="utf-8")
        return _run()

    try:
        yield _swap
    finally:
        path.write_text(original, encoding="utf-8")


def test_shipped_readmes_are_intact():
    result = _run()
    assert result.returncode == 0, (
        "A shipped README failed its integrity check:\n\n"
        f"{result.stdout}{result.stderr}"
    )


def test_rejects_gutted_readme(readme_swap):
    """A short README with an unterminated tag must fail both shape checks."""
    result = readme_swap(GUTTED_README)
    assert result.returncode == 1, "gutted README passed the gate"
    for expected in ("below the 400-line floor", "unterminated <img> tag"):
        assert expected in result.stdout, f"missing finding: {expected}"


def test_rejects_mass_deletion_alone(readme_swap):
    """Size floor fires even when the remaining content is well-formed.

    Guards against a future refactor that makes the HTML check load-bearing for
    the whole gate: a tidy 20-line README with the license intact is still a
    gutting, and must be rejected on size alone.
    """
    tidy = (
        "# Token Optimizer\n\nSee LICENSE (PolyForm Noncommercial).\n"
        "Created by Alex Greenshpun.\n"
    )
    result = readme_swap(tidy)
    assert result.returncode == 1
    assert "below the 400-line floor" in result.stdout
    assert "unterminated" not in result.stdout


def test_rejects_unterminated_tag_alone(readme_swap):
    """HTML check fires on a full-length README with one truncated tag.

    The realistic sabotage is not a five-line file, it is one bad tag buried in
    an otherwise normal document, which every other check would wave through.
    """
    original = (REPO / "README.md").read_text(encoding="utf-8")
    result = readme_swap(original + '\n<img src="trailing-truncation\n')
    assert result.returncode == 1
    assert "unterminated <img> tag" in result.stdout
    assert "line floor" not in result.stdout


def test_rejects_license_or_attribution_removal(readme_swap):
    """Stripping the license or the author's name fails on its own."""
    original = (REPO / "README.md").read_text(encoding="utf-8")
    stripped = original.replace("PolyForm Noncommercial", "MIT").replace(
        "Alex Greenshpun", "Anonymous"
    )
    result = readme_swap(stripped)
    assert result.returncode == 1
    assert "license name is missing" in result.stdout
    assert "author attribution is missing" in result.stdout


def test_arithmetic_less_than_is_not_a_tag(readme_swap):
    """`<400 tokens` in prose must not read as an unterminated tag.

    A false positive here would block ordinary prose edits.
    """
    original = (REPO / "README.md").read_text(encoding="utf-8")
    result = readme_swap(original + "\nCompresses to <400 tokens on a clean run.\n")
    assert result.returncode == 0, (
        f"prose '<400' misread as markup:\n\n{result.stdout}{result.stderr}"
    )
