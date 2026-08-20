"""Re-score the saved 80-case angle ablation using reconstructed blind labels.

No LLM calls.  The saved Gemini judgments identify evidence indexes after a
deterministic shuffle; this script reverses that shuffle and retains a parent
only when at least two of the three independently presented evidence sets
marked it relevant.  Multiple accepted parents are preserved.
"""
from __future__ import annotations

import argparse, json, random, sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from chromadb.utils import embedding_functions
from scipy.stats import pointbiserialr, spearmanr

ROOT = Path(__file__).resolve().parents[3]
RESEARCH = Path(__file__).resolve().parents[1]
JUDGED = RESEARCH / "results" / "threeway_narrative_validation_v1.json"
ABLATION = RESEARCH / "results" / "threeway_angle_ablation_v1.json"
LABELS = RESEARCH / "fixtures" / "threeway_judged_acceptable_parents_v1.json"
OUTPUT = RESEARCH / "results" / "threeway_judged_target_dispersion_rerun_v1.json"
CHILDREN = ROOT / "backend/data/etl/history/generic_chunking/artifacts/ready_for_ingestion.json"
DB = ROOT / "backend/data/knowledge_base.db"
VOTES_REQUIRED, TOP_K = 2, 8

def key(scope): return json.dumps(scope, sort_keys=True, separators=(",",":"))
def mrr(rank): return 0 if rank is None else 1/rank
def get_rank(ids, targets): return next((i for i,p in enumerate(ids,1) if p in targets), None)
def corr(x,y,fn):
    value,p=fn(x,y); return {"coefficient":float(value),"p_value":float(p)}

def reconstruct():
    data=json.loads(JUDGED.read_text()); cases=[]
    for case in data['cases']:
        votes=Counter(); trace=[]
        for strategy_name, strategy in case['strategies'].items():
            judgment=strategy['judgment']
            ids=list(strategy['selected_parent_ids'])
            random.Random(f"20260820:{case['id']}:{strategy_name}").shuffle(ids)
            mapped=[ids[index-1] for index in judgment.get('relevant_evidence_indexes', []) if isinstance(index,int) and 1 <= index <= len(ids)]
            if judgment.get('answerability') == 'full': votes.update(mapped)
            trace.append({"strategy":strategy_name,"answerability":judgment.get('answerability'),"reconstructed_relevant_parent_ids":mapped})
        accepted=sorted(parent for parent,count in votes.items() if count >= VOTES_REQUIRED)
        cases.append({"id":case['id'],"customer_query":case['customer_query'],"scope_pairs":case['scope_pairs'],"source_intent":case['source_intent'],"old_category_parent_id":case['expected_parent_id'],"acceptable_parent_ids":accepted,"votes":dict(votes),"trace":trace})
    payload={"metadata":{"source":str(JUDGED),"method":"Reverse deterministic evidence shuffle; retain FAQ parent judged relevant in >=2 full-answerability strategy presentations.","important_limit":"These labels remain pooled from the evaluated retrieval outputs; they are stronger than category labels but not independent relevance annotations."},"cases":cases}
    LABELS.write_text(json.dumps(payload,indent=2)); return payload

def indices(cases, children_path, db_path, embedder):
    con=sqlite3.connect(db_path)
    try: rows=con.execute('SELECT parent_id, product_tags, intent_families FROM knowledge_parents').fetchall()
    finally: con.close()
    tax={p:(json.loads(prod),json.loads(intent)) for p,prod,intent in rows}; scopes={key(c['scope_pairs']):c['scope_pairs'] for c in cases}; grouped={k:[] for k in scopes}; seen=set()
    for child in json.loads(children_path.read_text())['child_data']:
        meta=child.get('metadata',{}); parent=meta.get('parent_id')
        if parent not in tax: continue
        item=(parent,child.get('text',''),meta.get('chunk_type','generic'))
        if item in seen: continue
        seen.add(item); products,intents=tax[parent]
        for k,scope in scopes.items():
            if any(pair['product'] in products and intent in pair['intents'] for pair in scope for intent in intents): grouped[k].append((parent,child['text']))
    return {k:{'parents':[p for p,_ in docs],'vectors':np.asarray(embedder([t for _,t in docs]),dtype=float)} for k,docs in grouped.items()}

def dispersion(index, query, selected, embedder):
    q=np.asarray(embedder([query])[0],dtype=float); vectors=index['vectors']; scores=vectors@q/(np.linalg.norm(vectors,axis=1)*np.linalg.norm(q)); best={}
    for i,parent in enumerate(index['parents']): best[parent]=max(best.get(parent,-np.inf),float(scores[i]))
    values=[best[p] for p in selected if p in best]
    return float(np.std(values)) if len(values)>=2 else None

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--children',type=Path,default=CHILDREN); parser.add_argument('--knowledge-db',type=Path,default=DB); args=parser.parse_args()
    labels=reconstruct()['cases']; ablation={x['id']:x for x in json.loads(ABLATION.read_text())['cases']}; labelled=[c for c in labels if c['acceptable_parent_ids'] and c['id'] in ablation]
    embedder=embedding_functions.DefaultEmbeddingFunction(); ix=indices(labelled,args.children,args.knowledge_db,embedder); records=[]
    arms=('direct_anchor','direct_plus_procedural','direct_plus_policy','direct_plus_conceptual','direct_plus_all_three')
    for n,case in enumerate(labelled,1):
        strategies=ablation[case['id']]['strategies']; ranks={arm:get_rank(strategies[arm]['selected_parent_ids'],set(case['acceptable_parent_ids'])) for arm in arms}; direct_ids=strategies['direct_anchor']['selected_parent_ids']; d=dispersion(ix[key(case['scope_pairs'])],case['customer_query'],direct_ids,embedder)
        gains={arm:mrr(rank)-mrr(ranks['direct_anchor']) for arm,rank in ranks.items() if arm!='direct_anchor'}
        records.append({**case,"direct_parent_dispersion":d,"ranks":ranks,"mrr_deltas":gains})
        print(f'{n}/{len(labelled)} {case["id"]}')
    valid=[r for r in records if r['direct_parent_dispersion'] is not None]; ds=[r['direct_parent_dispersion'] for r in valid]; best=[max(r['mrr_deltas'][arm] for arm in ('direct_plus_procedural','direct_plus_policy','direct_plus_conceptual')) for r in valid]
    q1,q3=np.quantile(ds,[.25,.75]); low=[g for d,g in zip(ds,best) if d<=q1]; high=[g for d,g in zip(ds,best) if d>=q3]
    summary={"labelled_case_count":len(records),"original_case_count":80,"direct":{},"arms":{},"oracle_single_angle":{"mean_mrr_delta":float(np.mean(best)),"improved_count":sum(g>0 for g in best),"spearman_dispersion_vs_gain":corr(ds,best,spearmanr),"point_biserial_dispersion_vs_any_improvement":corr(ds,[g>0 for g in best],pointbiserialr),"low_quartile":{"count":len(low),"mean_gain":float(np.mean(low))},"high_quartile":{"count":len(high),"mean_gain":float(np.mean(high))}}}
    for arm in arms:
        ranks=[r['ranks'][arm] for r in records]; summary['arms'][arm]={"recall_at_4":sum(x is not None for x in ranks)/len(ranks),"mrr_at_4":float(np.mean([mrr(x) for x in ranks]))}
    for arm in ('direct_plus_procedural','direct_plus_policy','direct_plus_conceptual','direct_plus_all_three'):
        gains=[r['mrr_deltas'][arm] for r in valid]; summary['arms'][arm].update({"mean_mrr_delta":float(np.mean(gains)),"spearman_dispersion_vs_gain":corr(ds,gains,spearmanr)})
    payload={"metadata":{"purpose":"Rerun of saved angle-ablation scoring with reconstructed, conservative Gemini-derived acceptable parent sets; no new LLM calls.","label_rule":"relevant in >=2 full-answerability shuffled evidence presentations","important_limit":"Labels are only available for 46/80 cases and derive from pooled retrieval outputs."},"cases":records,"summary":summary}; OUTPUT.write_text(json.dumps(payload,indent=2)); print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
