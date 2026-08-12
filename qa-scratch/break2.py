import json, os, sys, tempfile, math
from datetime import datetime, timedelta
from pathlib import Path
SCRIPTS = Path("qa-scratch").resolve()
PROD = Path("plugins/token-optimizer/skills/token-optimizer/scripts").resolve()
sys.path.insert(0, str(PROD)); sys.path.insert(0, str(SCRIPTS))
import measure_fixAC2 as measure
FIX = json.load(open("tests/fixtures/history/openings_and_checkpoints.json"))
tmp = Path(tempfile.mkdtemp()); pool=[]
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
def idf(t,df,n): return min(math.log((n+1)/(df.get(t,0)+1))+1.0, measure._RELEVANCE_IDF_CAP)
def analyze(prompt, cp):
    pt = measure._topic_tokens(prompt, measure._RESUME_TOPIC_STOPWORDS)
    extra=set()
    for t in pt:
        if any(c in t for c in "\\/-_.:"):
            extra |= {w for w in measure._PATH_WORD_SPLIT_RE.split(t) if w and w not in measure._RESUME_TOPIC_STOPWORDS and measure._topic_token_kept(w)}
    pt = pt|extra
    doc = measure._checkpoint_sidecar_doc_tokens(cp)
    ptf = measure._checkpoint_path_tf(cp)
    pool_docs=[measure._checkpoint_sidecar_doc_tokens(p) for p in pool]
    df={}
    for pd in pool_docs:
        for t in pd: df[t]=df.get(t,0)+1
    n=len(pool_docs)
    hits0 = pt & doc
    hits = {t for t in hits0 if t not in measure._STRUCTURAL_PATH_WORDS}
    print(f"\n=== {prompt!r} -> {Path(cp).name[:24]}")
    print(f"  prompt tokens: {sorted(pt)}")
    print(f"  raw hits: {sorted(hits0)}  -> post-stoplist hits: {sorted(hits)}")
    for h in sorted(hits):
        print(f"    {h:14} idf={idf(h,df,n):.2f} df={df.get(h,0)}/{n} path_tf={ptf.get(h,0)} structural={h in measure._STRUCTURAL_PATH_WORDS}")
    # identity-hit gate
    ident = [h for h in hits if ptf.get(h,0)>=3 and idf(h,df,n)>=2.0]
    print(f"  identity-hit tokens (path_tf>=3 & idf>=2.0): {ident}")
analyze("continue the competitor monitor for beta client", pool[0])
