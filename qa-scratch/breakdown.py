import json, os, sys, tempfile, math
from datetime import datetime, timedelta
from pathlib import Path
SCRIPTS = Path("plugins/token-optimizer/skills/token-optimizer/scripts").resolve()
sys.path.insert(0, str(SCRIPTS))
import measure
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
    # pool df
    pool_docs=[measure._checkpoint_sidecar_doc_tokens(p) for p in pool]
    df={}
    for pd in pool_docs:
        for t in pd: df[t]=df.get(t,0)+1
    n=len(pool_docs)
    hits = pt & doc
    pw = sum(idf(t,df,n) for t in pt) or 1.0
    mp = sum(idf(t,df,n) for t in hits)
    prec = mp/pw
    def pw_w(t):
        tf=ptf.get(t,0)
        return 1.0 if tf<=0 else 1.0+measure._RELEVANCE_PATH_TF_WEIGHT*min(tf,measure._RELEVANCE_PATH_TF_CAP)
    mdw = sum(idf(t,df,n)*pw_w(t) for t in hits)
    dw = sum(idf(t,df,n)*pw_w(t) for t in doc) or 1.0
    rec = mdw/dw
    f1 = 2*prec*rec/(prec+rec) if prec+rec>0 else 0
    bonus = 0.15*(0.5+0.5*prec) if f1>0 and measure._resume_intent(prompt) else 0
    print(f"\n=== {prompt!r}\n  target cp={Path(cp).name[:24]}")
    print(f"  prompt tokens: {sorted(pt)}")
    print(f"  hits: {sorted(hits)}")
    for h in sorted(hits):
        print(f"    {h:14} idf={idf(h,df,n):.2f} df={df.get(h,0)}/{n} path_tf={ptf.get(h,0)} path_w={pw_w(h):.2f}")
    print(f"  precision={prec:.3f} recall={rec:.3f} F1={f1:.3f} bonus={bonus:.3f} -> total~{f1+bonus:.3f}")

# worst FPs against the gambit checkpoint (aa11bb22)
gambit=pool[0]
for p in ["continue the retainer deliverables","continue the clients work",
          "continue the competitor monitor for beta client","continue the company brain",
          "retainer deliverables competitor monitor reports",
          "continue working on the gambit competitor monitor"]:
    analyze(p, gambit)
# references -> miss-chief (aa55ff66 = pool[4])
analyze("continue the references work", pool[4])
