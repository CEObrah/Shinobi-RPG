#!/usr/bin/env python3
"""Measure hot-state size and reject known Jianghu bloat producer patterns."""
from __future__ import annotations
import argparse, collections, json
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
BANNED_KEYS={
 'schema_version','gameplay_version','rules_version','migration_version','baseline_version','state_version',
 'migration_reason','repair_proof','cleanup_receipt','old_baseline','previous_bug_description',
 'version_history','revision_history','migration_history','repair_history','scheduler_runs',
 'autonomous_action_attempts','decision_history','mutation_history','status_history',
 'combat_targeting_doctrine','representation_note','materialized_from',
}
STATIC_FACTION_KEYS={'admission_policy','membership_ladder','office_structure_ref','leader_title','martial_specializations','martial_neglects','qi_emphasis','weapons','training_curriculum','doctrine','economic_niches','public_reputation','allies','rivals','operating_routes','display_titles'}
ALLOWED_LARGE_ARRAY_SUFFIXES={
 'people','edges','rows','events','registrations','bracket','wounds','subject_refs','owner_refs','event_kinds','sites','routes',
}

def walk(v:Any,path=()):
 yield path,v
 if isinstance(v,dict):
  for k,x in v.items(): yield from walk(x,path+(str(k),))
 elif isinstance(v,list):
  for i,x in enumerate(v): yield from walk(x,path+(str(i),))

def snapshot(root:Path):
 state=root/'state'; files=[]
 if not state.exists(): return {'exists':False}
 for p in state.rglob('*.json'):
  files.append((p.relative_to(root).as_posix(),p.stat().st_size))
 files.sort(key=lambda x:(-x[1],x[0]))
 return {'exists':True,'file_count':len(files),'total_bytes':sum(x[1] for x in files),'largest_files':[{'path':p,'bytes':n} for p,n in files[:25]]}



def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--before-root'); ap.add_argument('--json',dest='out'); args=ap.parse_args()
 errors=[]; warnings=[]; counts=collections.Counter(); oversized=[]
 for p in (ROOT/'state').rglob('*.json'):
  rel=p.relative_to(ROOT).as_posix(); doc=json.loads(p.read_text())
  if rel.startswith('state/martial-world/factions/'):
   bad=STATIC_FACTION_KEYS & set(doc)
   if bad: errors.append(f'{rel}: static faction identity copied into hot state: {sorted(bad)}')
  for path,node in walk(doc):
   if path:
    key=path[-1]
    if key in BANNED_KEYS: errors.append(f'{rel}:{".".join(path)}: banned development/history key')
    if key in {'training_curriculum','admission_policy','combat_doctrine_definition'}: counts['static_policy_copies']+=1
   if isinstance(node,list) and len(node)>100:
    suffix=path[-1] if path else ''
    oversized.append({'path':f'{rel}:{".".join(path)}','length':len(node),'allowed_kind':suffix in ALLOWED_LARGE_ARRAY_SUFFIXES})
    if suffix not in ALLOWED_LARGE_ARRAY_SUFFIXES:
     errors.append(f'{rel}:{".".join(path)}: unexplained append-style array length {len(node)}')
 scheduler=json.loads((ROOT/'state/martial-world/scheduler.json').read_text())
 for class_id,row in scheduler.get('recurring',{}).items():
  for owner in row.get('owner_refs',[]):
   if str(owner).startswith('mw.person.') or str(owner) in {'pc_wei_tang','char.zhu','char.ling','char.kai'}:
    errors.append(f'per-person scheduler owner: {class_id}/{owner}')
 counts['scheduler_owner_refs']=sum(len(r.get('owner_refs',[])) for r in scheduler.get('recurring',{}).values())
 counts['scheduler_classes']=len(scheduler.get('recurring',{}))
 # Shared institutional training should not create ordinary personal accumulator state.
 training_exceptions=0; current_people=0
 for p in (ROOT/'state/martial-world/people').glob('*.json'):
  d=json.loads(p.read_text())
  for person in d.get('people',[]):
   current_people+=1
   if 'training_state' in person: training_exceptions+=1
 counts['people']=current_people; counts['personal_training_exceptions']=training_exceptions
 if training_exceptions>max(100,current_people//20): errors.append(f'personal training state is no longer sparse: {training_exceptions}/{current_people}')
 after=snapshot(ROOT); before=snapshot(Path(args.before_root).resolve()) if args.before_root else None
 result={'status':'PASS' if not errors else 'FAIL','after':after,'before':before,'counts':dict(counts),'large_arrays':oversized,'errors':errors,'warnings':warnings}
 if before and before.get('exists'):
  result['comparison']={'bytes_removed':before['total_bytes']-after['total_bytes'],'percent_change':round((after['total_bytes']-before['total_bytes'])*100/max(1,before['total_bytes']),2),'files_change':after['file_count']-before['file_count']}
 if args.out:
  q=Path(args.out); q=q if q.is_absolute() else ROOT/q; q.parent.mkdir(parents=True,exist_ok=True); q.write_text(json.dumps(result,indent=2)+'\n')
 print('STATE BLOAT AUDIT',result['status'])
 print(json.dumps({k:v for k,v in result.items() if k not in {'large_arrays','errors','warnings'}},indent=2))
 print('large_arrays',len(oversized))
 for e in errors[:100]: print('ERROR:',e)
 return 0 if not errors else 1
if __name__=='__main__': raise SystemExit(main())
