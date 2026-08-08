"""A slash-command invocation of an INSTALLED skill must count as usage.

A skill invoked as /name (e.g. /briefing) emits no `Skill` tool_use, and a
disable-model-invocation skill can ONLY be invoked that way. Before this fix such
skills were scored "never used" however often they ran, so the dashboard
recommended archiving skills the user relies on daily.

The counting is gated so nothing else leaks into skill stats:
  - the full <command-name>...</command-name> tag AND a <command-args> sibling are
    required, so a pasted mention in prose does not count;
  - only names that resolve to an installed skill count, so built-in/non-skill
    commands (/clear, /compact, ...) never pollute the "Skills Used" surfaces.
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

# The installed skills the resolver will see in these tests.
_FAKE_COMPONENTS = {
    "skills": {
        "names": ["briefing", "ai-digest", "token-coach", "compound-engineering"],
        "name_to_dir": {"Cross Session": "cross-session"},
    }
}


@pytest.fixture(autouse=True)
def _stub_installed_skills(monkeypatch):
    import measure
    monkeypatch.setattr(measure, "_cached_measure_components", lambda: _FAKE_COMPONENTS)


def _parse(records):
    import measure

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
        path = f.name
    return measure._parse_session_jsonl(path)


def _cmd(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_slash_invocation_of_installed_skill_counts():
    res = _parse([_cmd("<command-name>briefing</command-name>\n<command-args>evening</command-args>")])
    assert res["skills_used"].get("briefing") == 1


def test_leading_slash_and_list_content_counts():
    res = _parse([{
        "type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "<command-name>/ai-digest</command-name>\n<command-args></command-args>"}]},
    }])
    assert res["skills_used"].get("ai-digest") == 1


def test_namespaced_slash_command_resolves_to_installed_parent():
    res = _parse([_cmd("<command-name>compound-engineering:ce-plan</command-name>\n<command-args></command-args>")])
    assert res["skills_used"].get("compound-engineering") == 1


def test_non_skill_builtin_command_is_not_counted():
    """F1: /clear, /compact etc. are not installed skills -> must not pollute usage."""
    res = _parse([
        _cmd("<command-name>clear</command-name>\n<command-args></command-args>"),
        _cmd("<command-name>compact</command-name>\n<command-args></command-args>"),
    ])
    assert res["skills_used"] == {}


def test_pasted_mention_without_command_args_is_not_counted():
    """F2: a <command-name> tag pasted in prose (no <command-args> sibling) must
    not count as an invocation, even for an installed skill name."""
    res = _parse([_cmd("I saw `<command-name>briefing</command-name>` in my transcript log")])
    assert res["skills_used"] == {}


def test_skill_tool_call_still_counts():
    res = _parse([{
        "type": "assistant", "message": {"role": "assistant",
        "content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "token-coach"}}]},
    }])
    assert res["skills_used"].get("token-coach") == 1


def test_plain_user_turn_adds_no_phantom_skill():
    res = _parse([_cmd("just a normal message, no command")])
    assert res["skills_used"] == {}


def test_primary_recommendation_text_says_not_invoked():
    """The primary rule-engine strings, the quick-win action, and the CLI summary
    line must describe skills as 'not invoked', never assert 'unused'/'never used'."""
    src = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    assert "Archive {len(never_used)} unused skills" not in src
    assert "never used in {days} days" not in src
    assert "skills were never used in 30 days" not in src
    assert "counting Skill calls and slash commands" in src


def test_dashboard_card_relabeled():
    html = (ROOT / "skills" / "token-optimizer" / "assets" / "dashboard.html").read_text(encoding="utf-8")
    assert "Not Invoked (" in html
    assert "Never Used (" not in html


def test_both_trees_identical():
    a = (SCRIPTS / "measure.py").read_text(encoding="utf-8")
    b = (ROOT / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "scripts" / "measure.py").read_text(encoding="utf-8")
    assert a == b, "measure.py drifted between install trees"
    da = (ROOT / "skills" / "token-optimizer" / "assets" / "dashboard.html").read_text(encoding="utf-8")
    db = (ROOT / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "assets" / "dashboard.html").read_text(encoding="utf-8")
    assert da == db, "dashboard.html drifted between install trees"
