"""In-session routing advisor: the floor is the load-bearing guarantee.

A very cheap model or a low/minimal effort must only ever stand for an easy
task. This holds on every supported platform and must survive classifier drift,
so it is asserted exhaustively rather than by example.

Run: python3 -m pytest tests/test_routing_advisor.py -v
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "token-optimizer" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import routing_advisor as ra  # noqa: E402

RUNTIMES = list(ra.ROUTING_TABLES.keys())

NON_EASY_TASKS = [
    "migrate the production auth database to a new schema",
    "fix the race condition in the payment billing service",
    "redesign the distributed rollout architecture",
    "refactor the authentication module",
    "add a small feature to this module",
    "write a config parser",
    "update the payments webhook handler",
]

EASY_TASKS = [
    "fix typo in README",
    "rename this variable",
    "reformat this function",
    "list the files in the directory",
]


# --- the floor guarantee (most important) ------------------------------------

def test_floor_holds_on_every_runtime():
    for rt in RUNTIMES:
        for task in NON_EASY_TASKS:
            r = ra.recommend(task, rt)
            if r["significance"] != "easy":
                assert r["model"] not in ra.VERY_CHEAP_MODELS, (rt, task, r)
                assert r["tier"] != "budget", (rt, task, r)
                assert r["effort"] not in ("minimal", "low"), (rt, task, r)


def test_easy_path_can_use_cheap_model():
    # The cheap path must remain reachable for genuinely easy work on claude.
    r = ra.recommend("fix typo in README", "claude")
    assert r["significance"] == "easy"
    assert r["model"] == "haiku"
    assert r["effort"] in ("minimal", "low")


# --- effort domain per platform ----------------------------------------------

def test_effort_within_platform_domain():
    all_tasks = NON_EASY_TASKS + EASY_TASKS
    for rt in RUNTIMES:
        accepted = ra.ROUTING_TABLES[rt]["efforts"]
        for task in all_tasks:
            r = ra.recommend(task, rt)
            assert r["effort"] in accepted, (rt, task, r["effort"], accepted)


def test_recommend_has_all_keys():
    keys = {"significance", "confidence", "category", "tier", "model", "effort",
            "effort_kind", "effort_knob", "floor", "why"}
    for rt in RUNTIMES:
        r = ra.recommend("do something", rt)
        assert keys <= set(r), (rt, set(r))
        assert set(r["floor"]) == {"min_tier", "min_effort"}


# --- significance classifier --------------------------------------------------

def test_significance_examples():
    assert ra.classify_significance("fix typo in README") == ("easy", "high")
    sig, _ = ra.classify_significance("migrate the production auth database to a new schema")
    assert sig == "hard"


def test_significance_fail_open():
    assert ra.classify_significance(None) == ("standard", "low")
    assert ra.classify_significance("???") == ("standard", "low")


def test_word_boundary_no_false_escalation():
    # "author" must not trip "auth"; "reproduction" must not trip "production".
    assert ra.classify_significance("update author bio")[0] != "hard"
    assert ra.classify_significance("write reproduction steps for the bug")[0] != "hard"


# --- category classifier ------------------------------------------------------

def test_category_examples():
    assert ra.classify_category("implement a function to parse the file") == "code"
    assert ra.classify_category("analyze why this design trade-off matters") == "reasoning"
    assert ra.classify_category("hello there") == "simple"


def test_category_word_boundary():
    # "api" must not match inside "capitalization"; "plan" not inside "airplane".
    assert ra.classify_category("fix the capitalization") == "simple"
    assert ra.classify_category("draw an airplane") == "simple"


# --- fail-open on odd input ---------------------------------------------------

def test_recommend_never_raises():
    for bad in [None, "", "   ", 12345, ["a", "list"], {"a": 1}]:
        r = ra.recommend(bad, "claude")
        assert "model" in r and r["model"] not in ra.VERY_CHEAP_MODELS or r["significance"] == "easy"


def test_unknown_and_unhashable_runtime():
    r1 = ra.recommend("do a thing", "nonexistent-runtime")
    assert r1["model"] not in ra.VERY_CHEAP_MODELS
    # unhashable runtime must not raise (never-raises contract).
    r2 = ra.recommend("do a thing", ["unhashable"])
    assert "model" in r2


# --- baseline (guidance display) ---------------------------------------------

def test_baseline_policy_mapping():
    for rt in RUNTIMES:
        em, ee = ra.baseline("easy", rt)
        sm, se = ra.baseline("standard", rt)
        hm, he = ra.baseline("hard", rt)
        # standard and hard must not name a very cheap model.
        assert sm not in ra.VERY_CHEAP_MODELS, (rt, sm)
        assert hm not in ra.VERY_CHEAP_MODELS, (rt, hm)
        # efforts are valid for the platform.
        for e in (ee, se, he):
            assert e in ra.ROUTING_TABLES[rt]["efforts"], (rt, e)
