"""Common schema and deterministic experiment logic for AOS A1 V3.
Python 3.10+, standard library. All jobs run in the original educational scheduler.
"""
from __future__ import annotations
import csv, io, json, hashlib, re
from collections import Counter
from a1_core import baseline_rows, jobs_for, simulate, csv_runs, valid_token, job_csv

VERSION='AOS-A1-V5-1.0'
METRICS=('avg_response','avg_turnaround','avg_waiting','makespan','context_switches','migrations')
FIELDS={'arrival_change','placement','concentration','quantum','migration_penalty'}
COLUMNS=['run_id','condition_id','seed_index','workload_id','policy','placement','arrival_change','concentration','quantum','migration_penalty','job_count','total_cpu_demand','demand_c0','demand_c1','demand_c2','demand_c3','makespan','avg_response','avg_turnaround','avg_waiting','context_switches','migrations']

def canonical_json(obj):return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def hash_obj(obj):return hashlib.sha256(canonical_json(obj).encode()).hexdigest()

def fail(msg):raise ValueError(msg)
def string(x, name, lo=1, hi=1000):
    if not isinstance(x,str) or not (lo<=len(x.strip())<=hi):fail(f'{name}: use text of length {lo}–{hi}')

def validate_plan(plan,token):
    valid_token(token)
    if not isinstance(plan,dict):fail('plan must be JSON object')
    if set(plan)!={'schema','student_experiment_id','conditions','contrasts','design_reason'}:fail('plan keys incorrect')
    if plan['schema']!=VERSION or plan['student_experiment_id']!=token:fail('plan version or experiment ID incorrect')
    string(plan['design_reason'],'design_reason',40,1600)
    cond=plan['conditions']
    if not isinstance(cond,list) or not (4<=len(cond)<=6):fail('need 4–6 conditions')
    ids=[]
    for i,c in enumerate(cond,1):
        if not isinstance(c,dict) or set(c)!={'id',*FIELDS}:fail('each condition must use the exact specified keys')
        if c['id']!=f'C{i:02d}':fail('condition IDs must be C01, C02 ... in order')
        ids.append(c['id'])
        if c['arrival_change'] not in ('none','compress','stretch'):fail('invalid arrival_change')
        if c['placement'] not in ('balanced','skewed','custom_skew'):fail('invalid placement')
        if c['placement']=='custom_skew':
            if type(c['concentration']) is not int or not 5<=c['concentration']<=10:fail('custom_skew requires integer concentration 5–10')
        elif c['concentration'] is not None:fail('concentration must be null unless custom_skew')
        if type(c['quantum']) is not int or c['quantum'] not in (1,2,4,8):fail('quantum must be 1/2/4/8')
        if type(c['migration_penalty']) is not int or c['migration_penalty'] not in (0,1,2,4,6):fail('migration_penalty must be 0/1/2/4/6')
    anchor={'id':'C01','arrival_change':'none','placement':'balanced','concentration':None,'quantum':2,'migration_penalty':0}
    if cond[0]!=anchor:fail('C01 must be the fixed reference condition')
    second=dict(anchor,id='C02',placement='skewed')
    if cond[1]!=second:fail('C02 must change placement to skewed and keep other settings fixed')
    if len({tuple((k,c[k]) for k in sorted(FIELDS)) for c in cond})!=len(cond):fail('duplicate conditions')
    contrasts=plan['contrasts']
    if not isinstance(contrasts,list) or not (3<=len(contrasts)<=5):fail('need 3–5 contrasts')
    changes=[]; seen=set(); byid={c['id']:c for c in cond}
    for i,x in enumerate(contrasts,1):
        if not isinstance(x,dict) or set(x)!={'id','condition_a','condition_b','changed_factor','why_this_test'}:fail('contrast keys incorrect')
        if x['id']!=f'K{i:02d}':fail('contrast IDs must be K01, K02 ... in order')
        string(x['why_this_test'],'contrast why_this_test',20,800)
        a,b=x['condition_a'],x['condition_b']
        if a not in byid or b not in byid or a==b or (a,b) in seen:fail('contrast condition IDs invalid or duplicated')
        seen.add((a,b)); diffs=[k for k in FIELDS if byid[a][k]!=byid[b][k]]
        # Placement/concentration constitute ONE factor: static placement pattern.
        if diffs==['concentration','placement'] or set(diffs)=={'concentration','placement'}:diffs=['placement']
        if len(diffs)!=1 or diffs[0]!=x['changed_factor']:fail(f'{x["id"]}: contrast must change exactly one factor; found {diffs}')
        changes.append(diffs[0])
    if 'placement' not in changes or 'migration_penalty' not in changes or not ({'arrival_change','quantum'} & set(changes)):
        fail('contrasts must include placement, migration_penalty, and arrival_change or quantum')
    return True

def generate(token,plan):
    validate_plan(plan,token)
    rows=[];workloads={}
    for condition in plan['conditions']:
        for seed in (0,1,2):
            c=condition
            jobs=jobs_for(token,seed,c['placement'],c['arrival_change'],c['concentration'])
            workload=job_csv(jobs)
            workloads[f'{c["id"]}_seed{seed}.csv']=workload
            for policy in ('global','per_core'):
                r=simulate(jobs,seed,c['placement'],policy,c['quantum'],c['migration_penalty'])
                d={k:r[k] for k in ['seed_index','workload_id','policy','placement','quantum','migration_penalty','job_count','total_cpu_demand','demand_c0','demand_c1','demand_c2','demand_c3','makespan','avg_response','avg_turnaround','avg_waiting','context_switches','migrations']}
                rows.append(dict(run_id=f'{c["id"]}_s{seed}_{policy}',condition_id=c['id'],arrival_change=c['arrival_change'],concentration=c['concentration'] if c['concentration'] is not None else '',**d))
    assert len(rows)==len(plan['conditions'])*6
    return rows,workloads

def csv_text(rows):
    o=io.StringIO(newline=''); w=csv.DictWriter(o,fieldnames=COLUMNS,lineterminator='\n');w.writeheader();w.writerows(rows);return o.getvalue()

def mean(rows,cid,policy,metric):
    vals=[float(r[metric]) for r in rows if r['condition_id']==cid and r['policy']==policy]
    if len(vals)!=3:fail('mean missing exactly three seeds')
    return sum(vals)/3

def validate_research(q,lit,hyp,plan,find,contr,limits,rec,rows):
    """Structural and factual checks. Free-form reasoning goes to calibrated AI/human review."""
    flags=[]; errors=[]
    def err(m):errors.append(m)
    def txt(d,key,lo=12,hi=1200):
        try:string(d[key],key,lo,hi)
        except (KeyError,ValueError,TypeError):err(f'{key}: missing/too short/too long')
    if not isinstance(q,dict):return ['research_question.json is not an object'],[]
    for field in ['question','why_it_matters','scope_boundary']:txt(q,field)
    # Short valid field labels such as 'quantum' and 'makespan' are acceptable.
    for field in ['independent_variable','dependent_metric']:txt(q,field,1,1200)
    if q.get('dependent_metric') not in METRICS:err('question dependent_metric not recognized')
    if not isinstance(lit,list) or len(lit)!=3:err('literature.csv must contain exactly R01/R02/R03')
    else:
        for i,ref in enumerate(lit,1):
            if ref.get('source_id')!=f'R{i:02d}':err('literature IDs must be R01, R02, R03')
            for field in ['title','year','source_type','doi_or_url','location','finding','link_to_experiment']:
                if not str(ref.get(field,'')).strip():err(f'{ref.get("source_id")}.{field} missing')
        if '10.1145/2901318.2901326' not in lit[0].get('doi_or_url',''):err('R01 must be the required Lozi et al paper')
        if lit[1].get('source_type')!='paper':err('R02 must be a peer-reviewed paper')
        if lit[2].get('source_type')!='documentation' or 'kernel.org' not in lit[2].get('doi_or_url',''):err('R03 must be official kernel documentation')
        if lit[1].get('doi_or_url','').strip()==lit[0].get('doi_or_url','').strip():err('R02 must be a different paper')
        flags.append('R02 bibliographic accuracy and all page/section citations require independent verification')
    hh=hyp.get('hypotheses') if isinstance(hyp,dict) else None
    if not isinstance(hh,list) or len(hh)!=2:err('hypotheses.json: exactly two hypotheses needed'); hh=[]
    for i,h in enumerate(hh,1):
        if h.get('id')!=f'H{i:02d}':err('hypothesis IDs must be H01,H02')
        for field in ['condition','prediction','mechanism','would_disprove']:txt(h,field)
        if h.get('metric') not in METRICS:err('hypothesis metric unsupported')
        if not isinstance(h.get('source_ids'),list) or not set(h['source_ids']).issubset({'R01','R02','R03'}) or not h['source_ids']:err('hypothesis source_ids invalid')
        planned=h.get('planned_contrast_ids')
        legal={k['id'] for k in plan['contrasts']}
        if not isinstance(planned,list) or not planned or len(planned)!=len(set(planned)) or not set(planned).issubset(legal):err('hypothesis '+str(h.get('id'))+' must name valid Stage 1 planned_contrast_ids')
    ff=find.get('findings') if isinstance(find,dict) else None
    if not isinstance(ff,list) or not (3<=len(ff)<=5):err('findings: need 3–5 entries');ff=[]
    byid={c['id']:c for c in plan['conditions']}
    seen_findings=set()
    for i,f in enumerate(ff,1):
        if f.get('id')!=f'F{i:02d}':err('finding IDs must be F01 ...')
        for field in ['claim','mechanism','why_this_matters']:txt(f,field)
        metric=f.get('metric'); a,b=f.get('condition_a'),f.get('condition_b')
        pa,pb=f.get('policy_a'),f.get('policy_b')
        op=f.get('observed_relation')
        if metric not in METRICS:err(f'{f.get("id")} metric invalid');continue
        if a not in byid or b not in byid or pa not in ('global','per_core') or pb not in ('global','per_core'):err(f'{f.get("id")} references invalid condition/policy');continue
        if op not in ('lower','higher','equal'):err(f'{f.get("id")} invalid observed_relation');continue
        if a==b and pa==pb:err(f'{f.get("id")} must compare two distinct experiment/policy results');continue
        signature=(a,pa,b,pb,metric)
        if signature in seen_findings:err(f'{f.get("id")} repeats the same numerical comparison');continue
        seen_findings.add(signature)
        ma,mb=mean(rows,a,pa,metric),mean(rows,b,pb,metric)
        expected='equal' if abs(ma-mb)<1e-7 else 'lower' if ma<mb else 'higher'
        if expected!=op:err(f'{f.get("id")} numeric relation wrong: {a}/{pa}={ma:.3f}, {b}/{pb}={mb:.3f}, expected {expected}')
        if f.get('hypothesis_id') not in ('H01','H02'):err(f'{f.get("id")} hypothesis reference invalid')
        refs=f.get('source_ids');
        if not isinstance(refs,list) or not refs or not set(refs).issubset({'R01','R02','R03'}):err(f'{f.get("id")} literature source IDs invalid')
    cc=contr.get('checks') if isinstance(contr,dict) else None
    if not isinstance(cc,list) or len(cc)!=2:err('contradictions.json needs two checks, one per hypothesis');cc=[]
    for i,c in enumerate(cc,1):
        if c.get('hypothesis_id')!=f'H{i:02d}':err('contradiction hypothesis IDs invalid')
        for field in ['what_the_results_show','different_explanation','next_experiment']:txt(c,field)
        if c.get('status') not in ('supported_in_tests','challenged','inconclusive'):err('contradiction status invalid')
        refs=c.get('finding_ids')
        if not isinstance(refs,list) or not refs or not set(refs).issubset({x.get('id') for x in ff}):err('contradiction finding IDs missing/invalid')
        # Link this Stage 2 interpretation directly to the original Stage 1 hypothesis/plan.
        planned=hh[i-1].get('planned_contrast_ids',[]) if i<=len(hh) else []
        tested=c.get('tested_contrast_ids')
        if not isinstance(tested,list) or not tested or not set(tested).issubset(set(planned)):
            err('H'+f'{i:02d}'+': tested_contrast_ids must refer to its Stage 1 planned contrasts')
        else:
            direct=False
            for contrast in plan['contrasts']:
                if contrast['id'] not in tested:continue
                cids={contrast['condition_a'],contrast['condition_b']}
                for f in ff:
                    if f.get('id') not in (refs if isinstance(refs,list) else []):continue
                    if f.get('hypothesis_id')!=f'H{i:02d}':continue
                    if {f.get('condition_a'),f.get('condition_b')}==cids and f.get('policy_a')==f.get('policy_b'):
                        direct=True;break
            if not direct:err('H'+f'{i:02d}'+': cite a finding comparing the two conditions in one of its planned contrasts using the same policy')
        txt(c,'comparison_with_original_prediction',25,1400)
    ll=limits.get('limitations') if isinstance(limits,dict) else None
    if not isinstance(ll,list) or len(ll)<2:err('limitations.json needs at least two limitations');ll=[]
    for i,l in enumerate(ll,1):
        if l.get('id')!=f'L{i:02d}':err('limitation IDs L01, L02 etc')
        if l.get('kind') not in ('model','experiment'):err('limitation kind must be model or experiment')
        for field in ['limitation','effect_on_conclusion','better_test']:txt(l,field)
    if not {'model','experiment'}.issubset({x.get('kind') for x in ll}):err('need both model and experiment limitations')
    if not isinstance(rec,dict):err('recommendation.json must be an object')
    else:
        for field in ['decision','when_it_applies','when_not_to_use_it','reason','next_test']:txt(rec,field)
        if rec.get('policy') not in ('global','per_core','depends'):err('recommendation policy invalid')
        if rec.get('metric') not in METRICS:err('recommendation metric invalid')
        if not isinstance(rec.get('finding_ids'),list) or not rec['finding_ids'] or not set(rec['finding_ids']).issubset({x.get('id') for x in ff}):err('recommendation finding IDs invalid')
        if not isinstance(rec.get('limitation_ids'),list) or not rec['limitation_ids'] or not set(rec['limitation_ids']).issubset({x.get('id') for x in ll}):err('recommendation limitation IDs invalid')
    return errors,flags
