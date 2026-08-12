#!/usr/bin/env python3
"""Generate the two checkpoint pools for the container-word FP experiment.

pool-mixed/      : the 5 real-style checkpoints (gambit, token-optimizer,
                   attention-span, total-recall, miss-chief) from
                   tests/fixtures/history/openings_and_checkpoints.json.
pool-gambit-only/: 10 checkpoints ALL under clients/gambit/Retainer-Deliverables/
                   gambit-<sub>/... so the client name + retainer/deliverables are
                   pool-ubiquitous (IDF ~1.0) while sub-project words are distinctive.

Each checkpoint = a .md body + a .json sidecar matching the real format consumed by
_checkpoint_sidecar_doc_tokens / _checkpoint_path_tf. .md files are written fresh so
the recency prior applies.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MIXED = ROOT / "pool-mixed"
GAMBIT = ROOT / "pool-gambit-only"


def _write_cp(pool_dir: Path, filename: str, active_task, decisions,
              modified_files, recent_reads, topic=None):
    cp = pool_dir / filename
    cp.write_text(
        f"# Session State Checkpoint\n# Generated: test\n{active_task or ''}\n",
        encoding="utf-8",
    )
    os.utime(cp, (time.time(), time.time()))
    sidecar = {
        "version": 1,
        "generated": "test",
        "trigger": "stop",
        "session_id": "src-sid",
        "active_task": active_task,
        "topic": topic,
        "decisions": decisions or [],
        "modified_files": [
            {"path": p, "action": "edit", "range": None} for p in (modified_files or [])
        ],
        "recent_reads": recent_reads or [],
    }
    (pool_dir / cp.name.replace(".md", ".json")).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    return cp


# ---------------------------------------------------------------------------
# pool-mixed: the 5 real-style checkpoints (paths copied verbatim from the
# fixture so IDF/path_tf match the real replay benchmark).
# ---------------------------------------------------------------------------

MIXED_CHECKPOINTS = [
    {
        "filename": "aa11bb22-20260811-221941-milestone-edit-batch.md",
        "active_task": "let's finalize the connectors and make the scale environments adapted, i'll do the rest of the stuff",
        "decisions": [
            "the skill already has the fs/connector split so the answer is yes, cleanly",
            "draft a self-contained scan prompt so everything is connected",
        ],
        "modified_files": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables/gambit-competitor-monitor/references/07-market-sweep.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables/gambit-company-brain/competitor-monitor/reports/2026-08-11__BRIEF.html",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables/gambit-competitor-monitor/scripts/build_cloud_prompt.py",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables/gambit-competitor-monitor/references/09-environments.md",
        ],
        "recent_reads": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables/gambit-competitor-monitor/SKILL.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables/gambit-competitor-monitor/references/06-scheduling.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables/gambit-competitor-monitor/config/competitors.json",
        ],
    },
    {
        "filename": "aa22cc33-20260811-223302-quality-80.md",
        "active_task": "ok so what about the eval, we reran it, it now doesn't cost extra?",
        "decisions": [
            "clean measurement now that the fix means no checkpoint contamination on fresh runs",
            "the tests are green because they test the function in isolation",
        ],
        "modified_files": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/token-optimizer/sessions/2026-08-11_negative-savings-review/build/plans/2026-08-11-001-fix-checkpoint-injection-and-verbosity-plan.md",
            "/Users/alexgreenshpun/.claude/plugins/marketplaces/alexgreensh-token-optimizer/REVIEW-DEFECTS.md",
            "/Users/alexgreenshpun/.claude/plugins/marketplaces/alexgreensh-token-optimizer/plugins/token-optimizer/skills/token-optimizer/scripts/measure.py",
        ],
        "recent_reads": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/token-optimizer/sessions/2026-08-11_negative-savings-review/build/GATE-real-data.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/token-optimizer/sessions/2026-08-11_negative-savings-review/build/CRITICAL-DEFECT-real-data.md",
        ],
    },
    {
        "filename": "aa33dd44-20260811-223221-quality-80.md",
        "active_task": "just show me what changes in the readme, badges etc",
        "decisions": [
            "you reach the point in ~6 words instead of ~40, answer in the first line 75% of the time",
            "option A, measuring this well is actually the point",
        ],
        "modified_files": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/attention-span/benchmarks/harness/scannability.py",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/attention-span/.notes/decisions.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/attention-span/candidates/spartan-v2.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/attention-span/candidates/rundown-v2.md",
        ],
        "recent_reads": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/attention-span/output-styles/spartan.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/attention-span/output-styles/rundown.md",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/attention-span/README.md",
        ],
    },
    {
        "filename": "aa44ee55-20260811-120000-stop.md",
        "active_task": "rerun the retrieval benchmark and diff each question against the committed baseline",
        "decisions": ["keep locomo as the primary eval, longmemeval as secondary"],
        "modified_files": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/total-recall/core/retrieval.py",
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/total-recall/benchmarks/locomo/runner.py",
        ],
        "recent_reads": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/total-recall/core/recall.py",
        ],
    },
    {
        "filename": "aa55ff66-20260811-110000-stop.md",
        "active_task": "the telegram guardrail made her inept when i tag her in chats with other people",
        "decisions": ["scope the guardrail to outbound sends only, not tagged-in reads"],
        "modified_files": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/PROJECTS/miss-chief/heartbeat/goals.py",
        ],
        "recent_reads": [
            "/Users/alexgreenshpun/CascadeProjects/Prompts/PERSONAL_OS/SYSTEM/references/personality.md",
        ],
    },
]


# ---------------------------------------------------------------------------
# pool-gambit-only: 10 checkpoints all under
# clients/gambit/Retainer-Deliverables/gambit-<sub>/...
# gambit + retainer + deliverables appear in EVERY path of EVERY checkpoint
# (df=n -> IDF ~1.0). Sub-project words (competitor, monitor, company, brain,
# market, sweep, ...) appear only in their own sub -> high IDF.
# ---------------------------------------------------------------------------

BASE = "/Users/alexgreenshpun/CascadeProjects/Prompts/10x Company/clients/gambit/Retainer-Deliverables"

GAMBIT_CHECKPOINTS = [
    {
        "filename": "gb01-20260811-221941-milestone-edit-batch.md",
        "active_task": "let's finalize the connectors and make the scale environments adapted, i'll do the rest of the stuff",
        "decisions": [
            "the skill already has the fs/connector split so the answer is yes, cleanly",
            "draft a self-contained scan prompt so everything is connected",
        ],
        "modified_files": [
            f"{BASE}/gambit-competitor-monitor/references/07-market-sweep.md",
            f"{BASE}/gambit-company-brain/competitor-monitor/reports/2026-08-11__BRIEF.html",
            f"{BASE}/gambit-competitor-monitor/scripts/build_cloud_prompt.py",
            f"{BASE}/gambit-competitor-monitor/references/09-environments.md",
        ],
        "recent_reads": [
            f"{BASE}/gambit-competitor-monitor/SKILL.md",
            f"{BASE}/gambit-competitor-monitor/references/06-scheduling.md",
            f"{BASE}/gambit-competitor-monitor/config/competitors.json",
        ],
    },
    {
        "filename": "gb02-20260811-221942-stop.md",
        "active_task": "wire the company brain ingestion pipeline so the knowledge graph stays current",
        "decisions": ["use the entity resolver before writing edges"],
        "modified_files": [
            f"{BASE}/gambit-company-brain/scripts/ingest_graph.py",
            f"{BASE}/gambit-company-brain/data/entities.json",
            f"{BASE}/gambit-company-brain/references/03-ontology.md",
        ],
        "recent_reads": [
            f"{BASE}/gambit-company-brain/SKILL.md",
            f"{BASE}/gambit-company-brain/config/graph.json",
        ],
    },
    {
        "filename": "gb03-20260811-221943-stop.md",
        "active_task": "expand the market sweep to cover two more verticals and dedupe the results",
        "decisions": ["dedupe on normalized domain before scoring"],
        "modified_files": [
            f"{BASE}/gambit-market-sweep/scripts/sweep_verticals.py",
            f"{BASE}/gambit-market-sweep/data/sweep-2026-08-11.csv",
            f"{BASE}/gambit-market-sweep/output/sweep-report.md",
        ],
        "recent_reads": [
            f"{BASE}/gambit-market-sweep/references/02-verticals.md",
            f"{BASE}/gambit-market-sweep/config/verticals.json",
        ],
    },
    {
        "filename": "gb04-20260811-221944-stop.md",
        "active_task": "harden the scheduling engine so retries are idempotent",
        "decisions": ["persist run state in sqlite before dispatch"],
        "modified_files": [
            f"{BASE}/gambit-scheduling-engine/core/scheduler.py",
            f"{BASE}/gambit-scheduling-engine/tests/test_retries.py",
            f"{BASE}/gambit-scheduling-engine/build/plans/retry-plan.md",
        ],
        "recent_reads": [
            f"{BASE}/gambit-scheduling-engine/references/04-backoff.md",
            f"{BASE}/gambit-scheduling-engine/config/schedule.json",
        ],
    },
    {
        "filename": "gb05-20260811-221945-stop.md",
        "active_task": "adapt the environment adapter for the new scale profiles",
        "decisions": ["load profiles from config not code"],
        "modified_files": [
            f"{BASE}/gambit-environment-adapter/src/adapter.py",
            f"{BASE}/gambit-environment-adapter/config/environments.json",
            f"{BASE}/gambit-environment-adapter/docs/profiles.md",
        ],
        "recent_reads": [
            f"{BASE}/gambit-environment-adapter/references/05-profiles.md",
            f"{BASE}/gambit-environment-adapter/tests/test_adapter.py",
        ],
    },
    {
        "filename": "gb06-20260811-221946-stop.md",
        "active_task": "refactor the connector framework so fs and cloud share a base",
        "decisions": ["extract a base connector class"],
        "modified_files": [
            f"{BASE}/gambit-connector-framework/core/base_connector.py",
            f"{BASE}/gambit-connector-framework/lib/fs_connector.py",
            f"{BASE}/gambit-connector-framework/lib/cloud_connector.py",
        ],
        "recent_reads": [
            f"{BASE}/gambit-connector-framework/references/01-connectors.md",
            f"{BASE}/gambit-connector-framework/docs/architecture.md",
        ],
    },
    {
        "filename": "gb07-20260811-221947-stop.md",
        "active_task": "build the cloud prompt builder so scans are self-contained",
        "decisions": ["template the prompt from the skill definition"],
        "modified_files": [
            f"{BASE}/gambit-cloud-prompt-builder/scripts/build_cloud_prompt.py",
            f"{BASE}/gambit-cloud-prompt-builder/output/cloud-prompt.md",
            f"{BASE}/gambit-cloud-prompt-builder/data/templates.json",
        ],
        "recent_reads": [
            f"{BASE}/gambit-cloud-prompt-builder/references/08-templates.md",
            f"{BASE}/gambit-cloud-prompt-builder/config/builder.json",
        ],
    },
    {
        "filename": "gb08-20260811-221948-stop.md",
        "active_task": "stand up the reporting dashboards for the weekly retainer review",
        "decisions": ["use the html brief as the source of truth"],
        "modified_files": [
            f"{BASE}/gambit-reporting-dashboards/scripts/render_dashboard.py",
            f"{BASE}/gambit-reporting-dashboards/output/dashboard.html",
            f"{BASE}/gambit-reporting-dashboards/data/metrics.json",
        ],
        "recent_reads": [
            f"{BASE}/gambit-reporting-dashboards/references/10-metrics.md",
            f"{BASE}/gambit-reporting-dashboards/config/dashboards.json",
        ],
    },
    {
        "filename": "gb09-20260811-221949-stop.md",
        "active_task": "reorganize the reference library so entries are discoverable",
        "decisions": ["index by topic not by date"],
        "modified_files": [
            f"{BASE}/gambit-reference-library/references/11-index.md",
            f"{BASE}/gambit-reference-library/scripts/build_index.py",
            f"{BASE}/gambit-reference-library/data/index.json",
        ],
        "recent_reads": [
            f"{BASE}/gambit-reference-library/references/12-taxonomy.md",
            f"{BASE}/gambit-reference-library/docs/library.md",
        ],
    },
    {
        "filename": "gb10-20260811-221950-stop.md",
        "active_task": "externalize the config manager so secrets live in the vault",
        "decisions": ["rotate keys on a 30 day cadence"],
        "modified_files": [
            f"{BASE}/gambit-config-manager/src/config_loader.py",
            f"{BASE}/gambit-config-manager/config/secrets.json",
            f"{BASE}/gambit-config-manager/tests/test_loader.py",
        ],
        "recent_reads": [
            f"{BASE}/gambit-config-manager/references/13-vault.md",
            f"{BASE}/gambit-config-manager/docs/secrets.md",
        ],
    },
]


def build():
    for d in (MIXED, GAMBIT):
        d.mkdir(parents=True, exist_ok=True)
    for cp in MIXED_CHECKPOINTS:
        _write_cp(MIXED, cp["filename"], cp["active_task"], cp["decisions"],
                  cp["modified_files"], cp["recent_reads"])
    for cp in GAMBIT_CHECKPOINTS:
        _write_cp(GAMBIT, cp["filename"], cp["active_task"], cp["decisions"],
                  cp["modified_files"], cp["recent_reads"])
    print(f"pool-mixed: {len(list(MIXED.glob('*.md')))} checkpoints")
    print(f"pool-gambit-only: {len(list(GAMBIT.glob('*.md')))} checkpoints")


if __name__ == "__main__":
    build()
