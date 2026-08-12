import json, os, sys, tempfile, importlib
from datetime import datetime, timedelta
from pathlib import Path
SCRIPTS = Path("plugins/token-optimizer/skills/token-optimizer/scripts").resolve()
sys.path.insert(0, str(SCRIPTS))
import measure

FIX = json.load(open("tests/fixtures/history/openings_and_checkpoints.json"))
tmp = Path(tempfile.mkdtemp())
# materialize checkpoints
pool=[]
for spec in FIX["checkpoints"]:
    fn=spec["filename"]; cp=tmp/fn
    cp.write_text(f"# Session State Checkpoint\n# Generated: test\nbody: {spec.get('active_task','')}\n",encoding="utf-8")
    sidecar={"version":1,"trigger":spec.get("trigger","stop"),"session_id":"src-sid",
             "active_task":spec.get("active_task"),"decisions":spec.get("decisions",[]),
             "modified_files":[{"path":p,"action":"edit","range":None} for p in spec.get("modified_files",[])],
             "recent_reads":list(spec.get("recent_reads",[]))}
    (tmp/fn.replace(".md",".json")).write_text(json.dumps(sidecar),encoding="utf-8")
    age=spec.get("age_seconds",60)
    ts=(datetime.now()-timedelta(seconds=age)).timestamp()
    os.utime(cp,(ts,ts)); os.utime(tmp/fn.replace(".md",".json"),(ts,ts))
    pool.append(str(cp))

prompts = [
  ("GP gambit", "continue working on the gambit competitor monitor"),
  ("GP tok-opt", "continue the token optimizer checkpoint work"),
  ("GP attention", "continue the attention span project"),
  ("FP retainer-deliv", "continue the retainer deliverables"),
  ("FP clients-work", "continue the clients work"),
  ("FP competitor-monitor-beta", "continue the competitor monitor for beta client"),
  ("FP reports", "continue the reports"),
  ("FP references", "continue the references work"),
  ("FP resume-retainer", "let's resume the retainer"),
  ("FP scripts", "continue the scripts"),
  ("FP config", "continue the config"),
  ("FP company-brain", "continue the company brain"),
  ("stuffed no-cue", "retainer deliverables competitor monitor reports"),
  ("#129 bare", "continue"),
  ("cross-client acme", "continue the competitor analysis for acme corp"),
  ("fresh cats", "write a limerick about cats"),
]
print(f"pool size={len(pool)} threshold=0.25")
for label,p in prompts:
    best=None;bs=-1;rows=[]
    for cp in pool:
        s=measure.checkpoint_relevance_score(p,cp,pool=pool)
        rows.append((s,Path(cp).name)); 
        if s>bs: bs=s;best=Path(cp).name
    rows.sort(reverse=True)
    flag="FP!!" if bs>=0.25 and label.startswith("FP") else ("OK" if bs>=0.25 and label.startswith("GP") else ("ok<0.25" if bs<0.25 else ""))
    print(f"[{flag:6}] {label:24} best={bs:.3f} {best[:28]}  top2={rows[0][0]:.3f},{rows[1][0]:.3f}")
