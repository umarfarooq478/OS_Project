"""AOS A1 v5 student tool. Standard Python 3.10+. Never sends files to a server."""
from __future__ import annotations
import argparse, csv, hashlib, io, json, shutil, sys, zipfile
from pathlib import Path
from a1_core import baseline_rows,csv_runs,valid_token
from v3_schema import VERSION,validate_plan,generate,csv_text,hash_obj,validate_research
from docx_check import validate as validate_note
ROOT=Path(__file__).resolve().parents[1]
REQUIRED={'research/research_question.json','research/literature.csv','research/hypotheses.json','experiments/experiment_plan.json','analysis/findings.json','analysis/contradictions.json','analysis/limitations.json','recommendation.json','research/stage1_research_note.docx','analysis/stage2_discussion_note.docx'}
DESIGN=('research/research_question.json','research/hypotheses.json','experiments/experiment_plan.json','research/literature.csv','research/stage1_research_note.docx')

def save(p,contents):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(contents,encoding='utf-8',newline='')
def load_json(p):return json.loads(p.read_text(encoding='utf-8'))
def read_research(folder):
    d={n:load_json(folder/n) for n in REQUIRED if n.endswith('.json')}
    with (folder/'research/literature.csv').open(encoding='utf-8',newline='') as f:d['research/literature.csv']=list(csv.DictReader(f))
    return d

def evidence(token,plan):
    base,base_w=baseline_rows(token)
    custom,custom_w=generate(token,plan)
    files={'evidence/baseline_runs.csv':csv_runs(base),'evidence/custom_runs.csv':csv_text(custom)}
    files.update({f'evidence/workloads/baseline_{n}':v for n,v in base_w.items()})
    files.update({f'evidence/workloads/custom_{n}':v for n,v in custom_w.items()})
    return files,base,custom

def run(folder,token):
    plan=load_json(folder/'experiments/experiment_plan.json');validate_plan(plan,token)
    files,_,custom=evidence(token,plan)
    for n,t in files.items():save(folder/n,t)
    hashes={k:hashlib.sha256(v.encode('utf-8')).hexdigest() for k,v in sorted(files.items())}
    manifest={'schema':VERSION,'student_experiment_id':token,'plan_hash':hash_obj(plan),'files':hashes}
    save(folder/'evidence/manifest.json',json.dumps(manifest,indent=2)+'\n')
    print(f'Completed {len(custom)} custom runs and 15 baseline runs. Output: {folder}/evidence')

def check_stage1(folder,token,require_snapshot=True):
    errors=[]
    try:
        plan=load_json(folder/'experiments/experiment_plan.json');validate_plan(plan,token)
        h=load_json(folder/'research/hypotheses.json');q=load_json(folder/'research/research_question.json')
        with (folder/'research/literature.csv').open(encoding='utf-8',newline='') as fh:lit=list(csv.DictReader(fh))
        if [r.get('source_id') for r in lit]!=['R01','R02','R03']:errors.append('research/literature.csv must have R01/R02/R03')
        for ref in lit:
            if any(not str(ref.get(k,'')).strip() or 'WRITE YOUR' in str(ref.get(k,'')) for k in ('title','year','source_type','doi_or_url','location','finding','link_to_experiment')):errors.append('incomplete literature entry '+str(ref.get('source_id')))
        if len(h.get('hypotheses',[]))!=2:errors.append('exactly two predictions H01/H02 needed')
        for i,item in enumerate(h.get('hypotheses',[])):
            if item.get('id')!=f'H{i+1:02d}':errors.append('hypothesis ID incorrect')
            ids=item.get('planned_contrast_ids')
            if not isinstance(ids,list) or not ids or not set(ids).issubset({k['id'] for k in plan['contrasts']}):errors.append('hypothesis '+str(item.get('id'))+' needs valid planned_contrast_ids')
            for k in ('condition','prediction','mechanism','would_disprove','metric'):
                if not str(item.get(k,'')).strip() or 'WRITE YOUR' in str(item.get(k,'')):errors.append('hypothesis field incomplete: '+k)
        for k in ('question','why_it_matters','scope_boundary','independent_variable','dependent_metric'):
            if not str(q.get(k,'')).strip() or 'WRITE YOUR' in str(q.get(k,'')):errors.append('research question field incomplete: '+k)
        validate_note((folder/'research/stage1_research_note.docx').read_bytes(),'stage1')
        if require_snapshot:
            snap=load_json(folder/'design_snapshot.json')
            if snap.get('schema')!=VERSION or snap.get('student_experiment_id')!=token:errors.append('snapshot identity mismatch')
            for n in DESIGN:
                if snap.get('design_files',{}).get(n)!=hashlib.sha256((folder/n).read_bytes()).hexdigest():errors.append('Stage 1 file changed after freeze: '+n)
    except (ValueError,KeyError,TypeError,FileNotFoundError,json.JSONDecodeError) as exc:errors.append('Stage 1 file error: '+str(exc))
    return errors

def freeze(folder,token):
    errs=check_stage1(folder,token,require_snapshot=False)
    if errs:raise ValueError('Fix Stage 1 files before freeze: '+'; '.join(errs[:6]))
    plan=load_json(folder/'experiments/experiment_plan.json')
    if 'WRITE YOUR' in (folder/'research/literature.csv').read_text(encoding='utf-8') or 'REPLACE THIS' in (folder/'research/literature.csv').read_text(encoding='utf-8'):
        raise ValueError('complete literature.csv before freeze')
    for n in DESIGN:
        if n.endswith('.docx') or n.endswith('.csv'):continue
        if 'WRITE YOUR' in (folder/n).read_text(encoding='utf-8'):
            raise ValueError('complete the design template before freeze: '+n)
        d=load_json(folder/n)
        if n=='research/research_question.json' and d.get('dependent_metric') not in ('avg_response','avg_turnaround','avg_waiting','makespan','context_switches','migrations'):
            raise ValueError('complete the research question before freezing')
        if n=='research/hypotheses.json' and len(d.get('hypotheses',[]))!=2:raise ValueError('complete both hypotheses before freezing')
    hashes={n:hashlib.sha256((folder/n).read_bytes()).hexdigest() for n in DESIGN}
    snapshot={'schema':VERSION,'student_experiment_id':token,'design_files':hashes}
    save(folder/'design_snapshot.json',json.dumps(snapshot,indent=2)+'\n')
    print('Stage 1 design recorded. Upload SIX files to the Stage 1 LMS slot: the five original Stage 1 files (including the Word research plan note) and design_snapshot.json.');return snapshot

def check(folder,token):
    problems=[]
    for n in REQUIRED:
        if not (folder/n).is_file():problems.append('missing '+n)
    if problems:return problems,[]
    d=read_research(folder)
    for name,kind in [('research/stage1_research_note.docx','stage1'),('analysis/stage2_discussion_note.docx','stage2')]:
        try:validate_note((folder/name).read_bytes(),kind)
        except ValueError as e:problems.append(str(e))
    plan=d['experiments/experiment_plan.json']
    try:validate_plan(plan,token)
    except Exception as e:return ['invalid plan: '+str(e)],[]
    for n in DESIGN:
        p=folder/'design_snapshot.json'
        if not p.exists():problems.append('missing design_snapshot.json');break
        try:
            snapshot=load_json(p)
            if snapshot['design_files'][n]!=hashlib.sha256((folder/n).read_bytes()).hexdigest():problems.append('design file changed after freeze: '+n)
            if snapshot['student_experiment_id']!=token or snapshot['schema']!=VERSION:problems.append('design snapshot identity error')
        except Exception:problems.append('invalid design_snapshot.json');break
    files,_,custom=evidence(token,plan)
    for n,t in files.items():
        if not (folder/n).is_file() or (folder/n).read_text(encoding='utf-8')!=t:problems.append('missing or wrong evidence: '+n)
    try:
        manifest=load_json(folder/'evidence/manifest.json')
        expected={'schema':VERSION,'student_experiment_id':token,'plan_hash':hash_obj(plan),'files':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in sorted(files.items())}}
        if manifest!=expected:problems.append('manifest does not match expected experiments')
    except Exception:problems.append('missing/invalid evidence manifest')
    err,flags=validate_research(d['research/research_question.json'],d['research/literature.csv'],d['research/hypotheses.json'],plan,d['analysis/findings.json'],d['analysis/contradictions.json'],d['analysis/limitations.json'],d['recommendation.json'],custom)
    problems.extend(err)
    # placeholders, if any, are never accepted as completed work
    for n in REQUIRED:
        if n.endswith('.docx'):continue
        text=(folder/n).read_text(encoding='utf-8')
        if 'WRITE YOUR' in text or 'REPLACE THIS' in text or '<YOUR ' in text:
            problems.append('incomplete template text: '+n)
    return problems,flags

def make_pack(folder, zipout):
    """Include exactly the checked submission files, not every file a student puts in a folder.

    This prevents the former bug where 'check' passed but 'pack' included extra
    notes/scripts that the instructor checker rejected as unexpected entries.
    """
    plan=load_json(folder/'experiments/experiment_plan.json')
    token=plan['student_experiment_id']
    generated,_,_=evidence(token,plan)
    allowed=sorted(REQUIRED | {'design_snapshot.json','evidence/manifest.json'} | set(generated))
    with zipfile.ZipFile(zipout,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name in allowed:
            file=folder/name
            if not file.is_file():
                raise ValueError('required submission file missing: '+name)
            z.write(file,arcname=name)
    print('Submission saved:',zipout)

def main():
    p=argparse.ArgumentParser(description='AOS A1 V5: prepare/check/pack research experiments')
    p.add_argument('action',choices=['init','freeze','check-stage1','run','check','pack']);p.add_argument('--token',required=True);p.add_argument('--work',default='my_a1');p.add_argument('--zip',default='AOS_A1_submission.zip')
    args=p.parse_args();token=valid_token(args.token);folder=Path(args.work)
    try:
        if args.action=='init':
            if folder.exists():raise ValueError('work folder already exists')
            shutil.copytree(ROOT/'templates',folder)
            f=folder/'experiments/experiment_plan.json';obj=load_json(f);obj['student_experiment_id']=token;save(f,json.dumps(obj,indent=2)+'\n')
            print('Created',folder,'Open the JSON and CSV templates and fill in your own work.');return
        if not folder.is_dir():raise ValueError('work folder not found: run init first')
        if args.action=='freeze':freeze(folder,token)
        if args.action=='check-stage1':
            errs=check_stage1(folder,token)
            if errs:raise ValueError('Stage 1 check failed: '+'; '.join(errs[:8]))
            print('STAGE 1 FILE CHECK PASS. Research quality will be graded separately from the original LMS submission.')
        if args.action=='run':run(folder,token)
        if args.action=='check':
            errors,flags=check(folder,token)
            for x in errors:print('ERROR:',x)
            for x in flags:print('REVIEW:',x)
            if errors:raise SystemExit(2)
            print('STRUCTURE AND NUMERICAL EVIDENCE PASS. Research quality still needs grading.')
        if args.action=='pack':
            errors,flags=check(folder,token)
            if errors:raise ValueError('Run check and fix first. Problems: '+', '.join(errors[:6]))
            make_pack(folder,Path(args.zip))
    except (ValueError,FileNotFoundError,json.JSONDecodeError) as e:
        raise SystemExit('ERROR: '+str(e))
if __name__=='__main__':main()
