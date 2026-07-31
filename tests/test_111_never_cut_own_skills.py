"""Issue #111: Token Optimizer must NEVER recommend cutting its own skills.

The first thing the tool did for a user was suggest trimming the token-optimizer
bundle (token-coach, fleet-auditor) to "save ~200 tokens" — self-cannibalizing.
The unused-skill / archive recommender now excludes our own measurement skills
regardless of invocation history.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import measure  # noqa: E402


def test_own_skills_are_recognized():
    for name in (
        "token-optimizer", "token-coach", "token-dashboard", "fleet-auditor",
        "token-optimizer:token-coach", "token-optimizer:quick",
        "TOKEN-OPTIMIZER",
    ):
        assert measure._is_own_tool_skill(name), name


def test_third_party_skills_are_not_flagged():
    for name in ("linkedin", "deep-research", "my-notes", "some-random-skill", "", None):
        assert not measure._is_own_tool_skill(name), name


def test_recommendations_never_list_own_skills_for_archiving():
    """generate_auto_recommendations must not name our own skills as unused/archivable."""
    trends = {
        "skills": {
            "never_used": [
                "token-optimizer", "token-coach", "token-dashboard", "fleet-auditor",
                "linkedin", "my-notes", "deep-research", "old-thing",
            ],
            "installed_count": 40,
        }
    }
    components = {"skills": {"count": 40, "tokens": 8000}}
    plan, _count = measure.generate_auto_recommendations(components, trends=trends, days=30)
    for own in ("token-coach", "fleet-auditor", "token-dashboard"):
        assert own not in plan, f"recommendation plan must not suggest cutting own skill {own}"
    # third-party unused skills should still be surfaced
    assert "linkedin" in plan or "deep-research" in plan
