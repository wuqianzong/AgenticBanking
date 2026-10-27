import json,glob,csv,sys
sys.path.insert(0,".")
from backend.app.services.inbound_safety_service import find_credential_rule_ids as F
H="research/2026-10-04_v51_holdout"
msgs=[]
for f in glob.glob(H+"/runs/*/results/*_results.jsonl")+glob.glob(H+"/planted/results/*_results.jsonl")+glob.glob(H+"/planted/v2/results/*_results.jsonl"):
    for l in open(f):
        r=json.loads(l)
        for i,t in enumerate(r["turns"]): msgs.append((r["case_id"],i+1,t["message"]))
print("run turns",len(msgs))
hits=[(c,i,m,F(m)) for c,i,m in msgs if F(m)]
for h in hits: print("HIT",h[0],h[1],h[3],"|",h[2][:90])
r3=[m for m in msgs if m[0].startswith("h4_r3_")]
print("R3 caught",sum(bool(F(m[2])) for m in r3),"/",len(r3), "missed:",[m[2] for m in r3 if not F(m[2])])
b77=[]
for f in ("research/2026-10-04_v51_holdout/data/banking77_train.csv","research/2026-08-19_retrieval_query_refinement/data/banking77_test.csv"):
    for row in csv.reader(open(f)):
        if row and row[0]!="text": b77.append(row[0])
fp=[(t,F(t)) for t in b77 if F(t)]
print("BANKING77 messages",len(b77),"flagged",len(fp))
for t,h in fp[:60]: print("  FP?",h,"|",t[:120])
