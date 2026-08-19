"""Fresh blind comparison: direct vs complete angles vs FAQ scope patterns."""
from __future__ import annotations

import argparse, asyncio, csv, json, random, time
from pathlib import Path

from run_blind_banking77_pattern_validation import (
    PROJECT_ROOT, MAPPINGS, _scope_key, _scope_parent_texts, _pattern_prompt,
    _parse_json, _call_remote, _judge_prompt, _randomised_evidence,
)
from benchmark_retrieval import _combined_clean_query, _get_or_build_generic_collection, _run_single_query_case, _run_query_view_rrf_case
from backend.app.orchestration.rag_engine import RAGEngine

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
SOURCE = RESEARCH_ROOT / "data" / "banking77_test.csv"
DEV = RESEARCH_ROOT / "fixtures" / "banking77_horizon_retrieval_eval_v1.json"
PRIOR_BLIND = RESEARCH_ROOT / "results" / "blind_banking77_pattern_validation_v1.json"
OUT = RESEARCH_ROOT / "results" / "threeway_narrative_validation_v1.json"
MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
SEED = 20260821
REMOTE_PAUSE_SECONDS = 4.2


def _fallback_model(model: str) -> str:
    """Use the other permitted Gemini model once if the preferred call fails."""
    return MODELS[1] if model == MODELS[0] else MODELS[0]

def fresh_cases():
    used = {x["customer_query"] for x in json.load(open(DEV))}
    if PRIOR_BLIND.exists(): used |= {x["customer_query"] for x in json.load(open(PRIOR_BLIND))["cases"]}
    rows = list(csv.DictReader(open(SOURCE)))
    rng = random.Random(SEED); result=[]
    for category, m in MAPPINGS.items():
        pool=[r["text"].strip() for r in rows if r["category"]==category and r["text"].strip() not in used]
        for i,q in enumerate(rng.sample(pool,10),1):
            result.append({"id":f"threeway_{category}_{i:02d}","source_intent":category,"customer_query":q,"direct_query":q,"scope_pairs":m["scope_pairs"],"expected_parent_id":m["acceptable_parent_ids"][0]})
    return result

def angle_prompt(case):
    return f'''COMPLETE QUERY-VARIANT GENERATION\nCustomer message: {case["customer_query"]}\nRetrieval scope: {json.dumps(case["scope_pairs"])}\nWrite exactly three concise search queries: procedural emphasis, requirements/policy emphasis, and product-mechanism emphasis. Every query MUST preserve the entire customer need, product references, constraints, and requested outcome. Do not add facts, services, or bank procedures. Output JSON only: {{"procedural":"...","policy":"...","conceptual":"..."}}'''

def build_query_variants(cases):
    scopes={_scope_key(c["scope_pairs"]):c["scope_pairs"] for c in cases}; patterns={}
    for n,(key,scope) in enumerate(scopes.items()):
        p=_parse_json(_call_remote(_pattern_prompt(scope,_scope_parent_texts(scope)),MODELS[n%2]))
        patterns[key]=[str(x).strip() for x in p["patterns"] if str(x).strip()]
    angles={}
    for n,c in enumerate(cases):
        angles[c["id"]]=_parse_json(_call_remote(angle_prompt(c),MODELS[n%2]))
        time.sleep(REMOTE_PAUSE_SECONDS)
    return patterns,angles

async def retrieve(cases,patterns,angles):
    e=RAGEngine(collection=_get_or_build_generic_collection(False)); out={}
    for c in cases:
        anchor=c["customer_query"]; a=angles[c["id"]]
        out[c["id"]]={
          "direct":await _run_single_query_case(e,c,anchor),
          "complete_three_angle":await _run_query_view_rrf_case(e,c,[anchor,f"{anchor}; {a['procedural']}",f"{anchor}; {a['policy']}",f"{anchor}; {a['conceptual']}"]),
          "faq_scope_pattern":await _run_query_view_rrf_case(e,c,[anchor,*[f"{anchor}; {x}" for x in patterns[_scope_key(c['scope_pairs'])]]]),
        }
    return out

def save(data, path):
    path.parent.mkdir(parents=True,exist_ok=True); json.dump(data,open(path,'w'),indent=2)

async def retrieve_one(data, case):
    engine=RAGEngine(collection=_get_or_build_generic_collection(False)); anchor=case['customer_query']; a=data['angles'][case['id']]
    results={
      'direct':await _run_single_query_case(engine,case,anchor),
      'complete_three_angle':await _run_query_view_rrf_case(engine,case,[anchor,f"{anchor}; {a['procedural']}",f"{anchor}; {a['policy']}",f"{anchor}; {a['conceptual']}"]),
      'faq_scope_pattern':await _run_query_view_rrf_case(engine,case,[anchor,*[f"{anchor}; {x}" for x in data['patterns'][_scope_key(case['scope_pairs'])]]]),
    }
    case['strategies']={name:{'selected_parent_ids':r['selected_parent_ids'],'judgment':{}} for name,r in results.items()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=OUT);p.add_argument('--stage',choices=['init','patterns','angles','retrieve','judge'],default='judge');p.add_argument('--max-items',type=int,default=5);args=p.parse_args()
    if args.output.exists(): data=json.load(open(args.output))
    else:
        data={"metadata":{"seed":SEED,"method":"fresh three-way blind validation; all augmented views retain direct anchor"},"patterns":{},"angles":{},"cases":[{**c,"model":MODELS[i%2],"strategies":{}} for i,c in enumerate(fresh_cases())]}; save(data,args.output)
    if args.stage=='init': return
    if args.stage=='patterns':
        scopes={_scope_key(c['scope_pairs']):c['scope_pairs'] for c in data['cases']}
        for key,scope in scopes.items():
            if key in data['patterns']: continue
            n=len(data['patterns'])
            try:
                generated=_parse_json(_call_remote(_pattern_prompt(scope,_scope_parent_texts(scope)),MODELS[n%2]))
            except Exception:
                generated=_parse_json(_call_remote(_pattern_prompt(scope,_scope_parent_texts(scope)),MODELS[(n+1)%2]))
            data['patterns'][key]=[str(x).strip() for x in generated['patterns'] if str(x).strip()]; save(data,args.output); print('pattern',n+1)
            if n+1>=args.max_items:return
        return
    if args.stage=='angles':
        done=0
        for c in data['cases']:
            if c['id'] in data['angles']: continue
            try:
                generated = _parse_json(_call_remote(angle_prompt(c), c['model']))
            except Exception:
                generated = _parse_json(_call_remote(angle_prompt(c), _fallback_model(c['model'])))
            data['angles'][c['id']] = generated
            save(data,args.output); done+=1; print('angle',done,c['id']); time.sleep(REMOTE_PAUSE_SECONDS)
            if done>=args.max_items:return
        return
    if args.stage=='retrieve':
        done=0
        for c in data['cases']:
            if c['strategies']: continue
            asyncio.run(retrieve_one(data,c)); save(data,args.output); done+=1; print('retrieved',done,c['id'])
            if done>=args.max_items:return
        return
    done=0
    for c in data['cases']:
        for name,s in c['strategies'].items():
            if s['judgment'].get('answerability'): continue
            evidence=_randomised_evidence(c['id'],name,s['selected_parent_ids'])
            try:
                model = c['model']
                judgment = _parse_json(_call_remote(_judge_prompt(c['customer_query'], evidence), model))
                s['judgment'] = {**judgment, "model": model}
            except Exception:
                try:
                    model = _fallback_model(c['model'])
                    judgment = _parse_json(_call_remote(_judge_prompt(c['customer_query'], evidence), model))
                    s['judgment'] = {**judgment, "model": model}
                except Exception as fallback_exc:
                    s['judgment'] = {"error": str(fallback_exc), "model": model}
            json.dump(data,open(args.output,'w'),indent=2); done+=1; print(f"judged {done}: {c['id']} {name}"); time.sleep(REMOTE_PAUSE_SECONDS)
            if done>=args.max_items: return
if __name__=='__main__': main()
