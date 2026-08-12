import json, os, sys, tempfile, importlib
from datetime import datetime, timedelta
from pathlib import Path
# load fixAC2 copy as a module
SCRIPTS = Path("qa-scratch").resolve()
PROD = Path("plugins/token-optimizer/skills/token-optimizer/scripts").resolve()
sys.path.insert(0, str(PROD))
sys.path.insert(0, str(SCRIPTS))
import measure_fixAC2 as measure

FIX = json.load(open("tests/fixtures/history/openings_and_checkpoints.json"))
tmp = Path(tempfile.mkdtemp())
pool=[]
for spec in FIX["checkpoints"]:
    fn=spec["filename"]; cp=tmp/fn
    cp.write_text(f"# chk\nbody: {spec.get('active_task','')}\n",encoding="utf-8")
    sidecar={"version":1,"trigger":"stop","session_id":"x","active_task":spec.get("active_task"),
             "decisions":spec.get("decisions",[]),
             "modified_files":[{"path":p,"action":"edit","range":None} for p in spec.get("modified_files",[])],
             "recent_reads":list(spec.get("recent_reads",[]))}
    (tmp/fn.replace(".md",".json")).write_text(json.dumps(sidecar),encoding="utf-8")
    age=spec.get("age_seconds",60); ts=(datetime.now()-timedelta(seconds=age)).timestamp()
    os.utime(cp,(ts,ts)); os.utime(tmp/fn.replace(".md",".json"),(ts,ts))
    pool.append(str(cp))

prompts = [
  ("GP gambit", "continue working on the gambit competitor monitor","HIT"),
  ("GP tok-opt", "continue the token optimizer checkpoint work","HIT"),
  ("GP attention", "continue the attention span project","HIT"),
  ("GP verbose1", "let's resume the gambit competitor monitor, grab the latest market sweep","HIT"),
  ("GP verbose2", "continue where we left off on the attention span benchmarks","HIT"),
  ("GP verbose3", "continue working on token optimizer for full parity of the recent changes","HIT"),
  ("FP retainer-deliv", "continue the retainer deliverables","NO"),
  ("FP clients-work", "continue the clients work","NO"),
  ("FP competitor-monitor-beta", "continue the competitor monitor for beta client","NO"),
  ("FP reports", "continue the reports","NO"),
  ("FP references", "continue the references work","NO"),
  ("FP scripts", "continue the scripts","NO"),
  ("FP config", "continue the config","NO"),
  ("FP company-brain", "continue the company brain","NO"),
  ("stuffed no-cue", "retainer deliverables competitor monitor reports","NO"),
  ("#129 bare", "continue","NO"),
  ("cross-client acme", "continue the competitor analysis for acme corp","NO"),
  ("fresh cats", "write a limerick about cats","NO"),
]
print("fixAC2 on REAL 5-pool  threshold=0.25")
allok=True
for label,p,exp in prompts:
    best=None;bs=-1
    for cp in pool:
        s=measure.checkpoint_relevance_score(p,cp,pool=pool)
        if s>bs: bs=s;best=Path(cp).name[:24]
    hit = bs>=0.25
    ok = (hit and exp=="HIT") or (not hit and exp=="NO")
    if not ok: allok=False
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {label:26} score={bs:.3f} expect={exp} got={'HIT' if hit else 'NO'}  {best}")
print("\nALL OK:", allok)
