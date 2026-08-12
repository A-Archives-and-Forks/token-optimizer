#!/usr/bin/env python3
"""Replicate the assertions in tests/test_relevance_scorer.py against fixAC2
WITHOUT touching the production measure.py. Proves the recommended fix does not
regress the existing gate (T1/T2/T3/T4/T8 + genuine_broad_resume).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SCRIPTS = REPO / "plugins" / "token-optimizer" / "skills" / "token-optimizer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


m = load("measure_fixAC2", ROOT / "measure_fixAC2.py")
THRESH = m.CHECKPOINT_RELEVANCE_THRESHOLD

import tempfile
tmp = Path(tempfile.mkdtemp())


def write_cp(name, active_task=None, topic=None, decisions=None,
             modified_files=None, recent_reads=None, body=""):
    cp = tmp / name
    cp.write_text(f"# Session State Checkpoint\n# Generated: test\n{body}\n", encoding="utf-8")
    sidecar = {
        "version": 1, "generated": "test", "trigger": "stop", "session_id": "src-sid",
        "active_task": active_task, "topic": topic, "decisions": decisions or [],
        "modified_files": [{"path": p, "action": "edit", "range": None} for p in (modified_files or [])],
        "recent_reads": recent_reads or [],
    }
    (tmp / cp.name.replace(".md", ".json")).write_text(json.dumps(sidecar), encoding="utf-8")
    return cp


fails = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}  {detail}")
    if not cond:
        fails.append(label)


# T1
cp = write_cp("aaaa1111-20260811-120000-checkpoint.md",
              active_task="fix checkpoint injection targeting in token optimizer",
              modified_files=["plugins/token-optimizer/scripts/measure.py"])
s = m.checkpoint_relevance_score("continue the token optimizer checkpoint injection fix", cp, pool=[cp])
check("T1 high topical overlap >= thresh", s >= THRESH, f"score={s:.3f}")

# T2
cp_a = write_cp("aaaa1111-20260811-120000-checkpoint.md",
                active_task="work on token optimizer checkpoint injection", modified_files=["alpha/measure.py"])
cp_b = write_cp("bbbb2222-20260811-120100-checkpoint.md",
                active_task="work on marketing audit content strategy", modified_files=["beta/audit.md"])
cp_c = write_cp("cccc3333-20260811-120200-checkpoint.md",
                active_task="work on billing payment integration", modified_files=["gamma/billing.py"])
pool = [cp_a, cp_b, cp_c]
g = m.checkpoint_relevance_score("continue work on the project", cp_a, pool=pool)
c = m.checkpoint_relevance_score("continue token optimizer checkpoint work", cp_a, pool=pool)
check("T2 generic-only < thresh", g < THRESH, f"generic={g:.3f}")
check("T2 content > generic", c > g, f"content={c:.3f} generic={g:.3f}")
check("T2 content >= thresh", c >= THRESH, f"content={c:.3f}")

# T3
polluted = ("<task-notification>system: scheduled task #7 fired</task-notification> "
            "fix checkpoint injection in token optimizer")
cp = write_cp("aaaa1111-20260811-120000-checkpoint.md", active_task=polluted,
              modified_files=["plugins/token-optimizer/scripts/measure.py"])
real = m.checkpoint_relevance_score("continue the token optimizer checkpoint injection fix", cp, pool=[cp])
noise = m.checkpoint_relevance_score("scheduled task notification fired system", cp, pool=[cp])
check("T3 real >= thresh", real >= THRESH, f"real={real:.3f}")
check("T3 noise < thresh", noise < THRESH, f"noise={noise:.3f}")

# T4 (#129)
cp = write_cp("aaaa1111-20260811-000000-checkpoint.md",
              active_task="unrelated marketing audit work", modified_files=["clients/acme/audit.md"])
s = m.checkpoint_relevance_score("continue", cp, pool=[cp])
check("T4 bare continue < thresh", s < THRESH, f"score={s:.3f}")

# T8 keyword stuffing
def big_doc():
    return write_cp("aaaa1111-20260811-120000-checkpoint.md",
                    active_task=("Refactor payment gateway reconcile stripe webhook retries migrate "
                                 "ledger schema backfill invoices harden idempotency reconciliation "
                                 "dashboard currency rounding refunds"),
                    decisions=["adopt double-entry bookkeeping model", "shard ledger by tenant identifier",
                               "encrypt cardholder tokens at rest", "replay webhooks through durable queue",
                               "expose settlement metrics prometheus exporter"],
                    modified_files=["src/payments/gateway.py", "src/payments/ledger.py",
                                    "src/payments/webhooks.py", "src/payments/settlement.py",
                                    "src/billing/invoices.py", "src/billing/refunds.py"])


cp = big_doc()
stuffed = "stripe webhook ledger"
pt = m._topic_tokens(stuffed, m._RESUME_TOPIC_STOPWORDS)
dt = m._checkpoint_sidecar_doc_tokens(cp)
check("T8 fixture sanity (stuffed subset of doc)", pt and pt.issubset(dt))
s = m.checkpoint_relevance_score(stuffed, cp, pool=[cp])
check("T8 stuffed < thresh", s < THRESH, f"score={s:.3f}")
padded = ("kubernetes helm chart rollout canary istio sidecar mesh observability grafana stripe")
s2 = m.checkpoint_relevance_score(padded, cp, pool=[cp])
check("T8 padded < thresh", s2 < THRESH, f"score={s2:.3f}")

# genuine_broad_resume
cp = big_doc()
genuine = ("continue the payment gateway work: the stripe webhook retries, the ledger schema migration, "
           "the invoices backfill and the refunds reconciliation")
s = m.checkpoint_relevance_score(genuine, cp, pool=[cp])
check("genuine_broad_resume >= thresh", s >= THRESH, f"score={s:.3f}")

print(f"\nthreshold={THRESH}  failures={len(fails)}  {fails}")
sys.exit(1 if fails else 0)
