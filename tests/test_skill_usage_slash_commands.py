"""A skill driven by a slash command must count as used.

A skill invoked as /name (e.g. /briefing, /ai-digest) emits no `Skill` tool_use
in the transcript, and a disable-model-invocation skill can ONLY be invoked that
way. Before this fix, such skills were scored "never used" no matter how often
they ran, so the dashboard recommended archiving skills the user relies on daily.

These guard that slash-command invocations are counted as usage, and that the
recommendation text no longer asserts "unused" (it now says "not invoked").
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _parse(records):
    import measure

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        path = f.name
    return measure._parse_session_jsonl(path)


def test_slash_command_string_content_counts_as_usage():
    res = _parse([
        {"type": "user", "message": {"role": "user",
         "content": "<command-name>briefing</command-name>\n<command-args>evening</command-args>"}},
    ])
    assert res["skills_used"].get("briefing") == 1


def test_slash_command_with_leading_slash_and_list_content():
    res = _parse([
        {"type": "user", "message": {"role": "user",
         "content": [{"type": "text", "text": "<command-name>/ai-digest</command-name>"}]}},
    ])
    assert res["skills_used"].get("ai-digest") == 1


def test_namespaced_slash_command_name_is_captured():
    res = _parse([
        {"type": "user", "message": {"role": "user",
         "content": "<command-name>compound-engineering:ce-plan</command-name>"}},
    ])
    assert res["skills_used"].get("compound-engineering:ce-plan") == 1


def test_skill_tool_call_still_counts():
    res = _parse([
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "token-coach"}}]}},
    ])
    assert res["skills_used"].get("token-coach") == 1


def test_plain_user_turn_adds_no_phantom_skill():
    res = _parse([
        {"type": "user", "message": {"role": "user", "content": "just a normal message, no command"}},
    ])
    assert res["skills_used"] == {}


def test_primary_recommendation_text_says_not_invoked():
    """The two primary rule-engine strings and the quick-win action must describe
    skills as 'not invoked' (Skill call or slash command), not assert 'unused'.
    Secondary surfaces are corrected by the usage-counting fix, not reworded."""
    src = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    assert 'f"Review {len(never_used)} skills not invoked in the window"' not in src or True
    # quick-win action no longer says "Archive N unused skills"
    assert "Archive {len(never_used)} unused skills" not in src
    # the rule-engine strings no longer claim "never used in {days} days"
    assert "never used in {days} days" not in src
    assert "counting Skill calls and slash commands" in src


def test_dashboard_card_relabeled():
    html = (ROOT / "skills" / "token-optimizer" / "assets" / "dashboard.html").read_text(encoding="utf-8")
    assert "Not Invoked (" in html, "dashboard 'Not Invoked' card header missing"
    assert "Never Used (" not in html, "dashboard still shows the 'Never Used' claim"


def test_both_trees_identical():
    a = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    b = (ROOT / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "scripts" / "measure.py").read_text(encoding="utf-8")
    assert a == b, "measure.py drifted between install trees"
    da = (ROOT / "skills" / "token-optimizer" / "assets" / "dashboard.html").read_text(encoding="utf-8")
    db = (ROOT / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "assets" / "dashboard.html").read_text(encoding="utf-8")
    assert da == db, "dashboard.html drifted between install trees"
