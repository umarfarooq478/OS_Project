"""Deterministic external workload generator and experiment specification for AOS A1 v2.
This module does not change the supplied scheduler engine. Python 3.10+ only.
"""
from __future__ import annotations
from dataclasses import asdict
import csv
import hashlib
import io
import json
import random
import re
from pathlib import Path
from aos_scheduler_framework import Job, simulate_multicore_rr

VERSION = 'AOS-A1-RESEARCH-v2.0'
CORE_COUNT = 4
N_JOBS = 12
Q = 2
SEED_INDICES = (0, 1, 2)
RUN_FIELDS = ['seed_index','workload_id','kind','policy','placement','quantum','migration_penalty',
              'job_count','total_cpu_demand','demand_c0','demand_c1','demand_c2','demand_c3',
              'makespan','avg_response','avg_turnaround','avg_waiting','context_switches','migrations']
JOB_FIELDS = ['jid','arrival','burst','priority','tickets','affinity']

def valid_token(token:str)->str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{3,29}',token):
        raise ValueError('Your assigned experimental ID must contain 4–30 letters, numbers, underscore or hyphen.')
    return token

def make_seed(token: str, idx: int)->int:
    valid_token(token)
    return int.from_bytes(hashlib.sha256(f'{VERSION}|{token}|{idx}'.encode()).digest()[:8],'big')

def base_jobs(token:str,idx:int)->list[Job]:
    r=random.Random(make_seed(token,idx))
    return [Job(f'J{i:02d}',arrival=r.randrange(0,8),burst=r.randrange(2,10)) for i in range(N_JOBS)]

def balanced_assignment(jobs:list[Job])->dict[str,int]:
    # Idealized OFFLINE placement: knows the entire CPU burst in advance.
    totals=[0]*CORE_COUNT
    where={}
    for j in sorted(jobs,key=lambda x:(-x.burst,x.jid)):
        c=min(range(CORE_COUNT),key=lambda c:(totals[c],c))
        where[j.jid]=c
        totals[c]+=j.burst
    return where

def affinity_assignment(jobs:list[Job],kind:str,concentration:int|None=None)->dict[str,int]:
    if kind=='balanced':return balanced_assignment(jobs)
    if kind not in ('skewed','custom_skew'):raise ValueError(kind)
    n=9 if kind=='skewed' else concentration
    if not isinstance(n,int) or not (5<=n<=10):raise ValueError('custom skew concentration must be integer 5–10')
    # Assignment by job id is deliberate; this changes placement, not job demand/arrivals.
    ids=[j.jid for j in sorted(jobs,key=lambda j:j.jid)]
    return {jid:(0 if i<n else 1+(i-n)%3) for i,jid in enumerate(ids)}

def jobs_for(token:str,idx:int,kind:str='balanced',arrival_change:str='none',concentration:int|None=None)->list[Job]:
    raw=base_jobs(token,idx)
    placement=affinity_assignment(raw,kind,concentration)
    if arrival_change not in ('none','compress','stretch'):raise ValueError(arrival_change)
    return [Job(j.jid,(j.arrival//2 if arrival_change=='compress' else 2*j.arrival if arrival_change=='stretch' else j.arrival),j.burst,
                priority=j.priority,tickets=j.tickets,affinity=placement[j.jid]) for j in raw]

def job_csv(jobs:list[Job])->str:
    f=io.StringIO(newline=''); w=csv.DictWriter(f,fieldnames=JOB_FIELDS,lineterminator='\n');w.writeheader()
    for j in jobs:w.writerow(asdict(j))
    return f.getvalue()

def workload_identifier(csv_text:str)->str:return hashlib.sha256(csv_text.encode('utf-8')).hexdigest()[:16]

def simulate(jobs:list[Job],idx:int,kind:str,policy:str,quantum:int=Q,penalty:int=0)->dict:
    if policy not in ('global','per_core'):raise ValueError('policy')
    r=simulate_multicore_rr(jobs,cores=CORE_COUNT,quantum=quantum,queue_mode=policy,migration_penalty=penalty)
    s=r['summary']; loads=[sum(j.burst for j in jobs if j.affinity==c) for c in range(CORE_COUNT)]
    d=dict(seed_index=idx,workload_id=workload_identifier(job_csv(jobs)),kind='experiment',policy=policy,
           placement=kind,quantum=quantum,migration_penalty=penalty,job_count=len(jobs),
           total_cpu_demand=sum(j.burst for j in jobs),**{f'demand_c{i}':loads[i] for i in range(CORE_COUNT)})
    for k in ('makespan','avg_response','avg_turnaround','avg_waiting','context_switches','migrations'):d[k]=s[k]
    assert len(r['jobs'])==len(jobs)
    assert d['makespan']==max(x['completion'] for x in r['jobs'])
    assert all(x['completion']>=x['arrival']+x['burst'] for x in r['jobs'])
    assert all(x['turnaround']==x['completion']-x['arrival'] for x in r['jobs'])
    assert all(x['waiting']==x['turnaround']-x['burst'] for x in r['jobs'])
    assert d['migrations']==sum(x['migrations'] for x in r['jobs'])
    return d

def baseline_rows(token:str)->tuple[list[dict],dict[str,str]]:
    rows=[]; workloads={}
    for idx in SEED_INDICES:
        bal=jobs_for(token,idx,'balanced');skew=jobs_for(token,idx,'skewed')
        workloads[f'seed{idx}_balanced.csv']=job_csv(bal)
        workloads[f'seed{idx}_skewed.csv']=job_csv(skew)
        assert [(j.jid,j.arrival,j.burst) for j in bal]==[(j.jid,j.arrival,j.burst) for j in skew]
        for penalty in (0,1,4):rows.append(simulate(bal,idx,'balanced','global',Q,penalty))
        for placement,jobs in [('balanced',bal),('skewed',skew)]:rows.append(simulate(jobs,idx,placement,'per_core',Q,0))
    return rows,workloads

def validate_extension(option:str,value:str)->str|int:
    if option=='arrival':
        if value not in ('compress','stretch'):raise ValueError('arrival option requires compress or stretch')
        return value
    if option=='affinity':
        n=int(value)
        if not 5<=n<=8:raise ValueError('affinity option requires 5–8 jobs concentrated on core 0')
        return n
    if option=='quantum':
        n=int(value)
        if n not in (1,4):raise ValueError('quantum option requires 1 or 4')
        return n
    raise ValueError('option must be arrival, affinity, or quantum')

def extension_rows(token:str,option:str,value:str)->tuple[list[dict],dict[str,str]]:
    v=validate_extension(option,value)
    rows=[]; workloads={}
    for idx in SEED_INDICES:
        if option=='affinity':
            jobs=jobs_for(token,idx,'custom_skew',concentration=int(v))
            workloads[f'ext_seed{idx}_custom_skew.csv']=job_csv(jobs)
            rows.append(simulate(jobs,idx,'custom_skew','per_core',Q,0))
        else:
            change=str(v) if option=='arrival' else 'none'
            q=int(v) if option=='quantum' else Q
            b=jobs_for(token,idx,'balanced',arrival_change=change)
            s=jobs_for(token,idx,'skewed',arrival_change=change)
            workloads[f'ext_seed{idx}_balanced.csv']=job_csv(b)
            workloads[f'ext_seed{idx}_skewed.csv']=job_csv(s)
            for penalty in (0,4):rows.append(simulate(b,idx,'balanced','global',q,penalty))
            rows.append(simulate(b,idx,'balanced','per_core',q,0))
            rows.append(simulate(s,idx,'skewed','per_core',q,0))
    return rows,workloads

def csv_runs(rows:list[dict])->str:
    out=io.StringIO(newline=''); w=csv.DictWriter(out,fieldnames=RUN_FIELDS,lineterminator='\n');w.writeheader();w.writerows(rows)
    return out.getvalue()

def manifest(token:str, option:str|None, value:str|None, data:dict[str,str])->dict:
    return dict(schema=VERSION,student_experiment_id=valid_token(token),extension=(dict(option=option,value=str(value)) if option else None),
                files={k:hashlib.sha256(v.encode('utf-8')).hexdigest() for k,v in sorted(data.items())})
