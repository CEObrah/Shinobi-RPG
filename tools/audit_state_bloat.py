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
STATIC_FACTION_KEYS={'admission_policy','membership_ladder','office_structure_ref','leader_title','martial_specializations','martial_neglects','qi_emphasis','weapons','training_curriculum','doctrine','economic_niches','public_reputation','allies','rivals','operating_routes','display_titles','apothecary_policy'}
ALLOWED_LARGE_ARRAY_SUFFIXES={
 'people','edges','rows','events','registrations','bracket','wounds','subject_refs','owner_refs','event_kinds','sites','routes','faction_refs','participant_refs','escort_refs','raider_refs',
}
BOUNDED_ARRAY_LIMITS={}
BANNED_STATE_PATHS={
 'state/martial-world/commitments.json',
 'state/martial-world/person-routes.json',
 'state/martial-world/world-history.json',
 'state/martial-world/library-holdings.json',
}
BANNED_OWNER_KEYS={
 'current_environment','cycles_settled','escort_policy_version',
 'treasury_cash_after_start','material_stock_after_start',
 'failed_contacts','last_contact_at','last_deployment',
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
 world_seed=json.loads((ROOT/'game/data/martial-world/world-seed.json').read_text())
 world_factions=world_seed.get('martial_factions',{}) if isinstance(world_seed,dict) else {}
 life_service_factions={
  str(fid) for fid,row in world_factions.items()
  if isinstance(row,dict) and row.get('membership_tenure')=='life_service'
 } if isinstance(world_factions,dict) else set()
 for rel in sorted(BANNED_STATE_PATHS):
  if (ROOT/rel).exists(): errors.append(f'{rel}: derived/telemetry owner must not persist')
 if (ROOT/'state/martial-world/person-routes').exists(): errors.append('state/martial-world/person-routes: derived route shard directory must not persist')
 independents_path=ROOT/'state/martial-world/independent-people.json'
 if independents_path.exists():
  independents=json.loads(independents_path.read_text())
  for row in independents.get('people',[]) if isinstance(independents,dict) else []:
   if not isinstance(row,dict): continue
   former=str(row.get('former_faction_ref') or '')
   if former in life_service_factions:
    errors.append(f'independent person {row.get("person_id","?")}: former life-service member escaped ordinary faction ownership: {former}')
 for p in (ROOT/'state').rglob('*.json'):
  rel=p.relative_to(ROOT).as_posix(); doc=json.loads(p.read_text())
  if rel.startswith('state/martial-world/factions/'):
   bad=STATIC_FACTION_KEYS & set(doc)
   if bad: errors.append(f'{rel}: static faction identity copied into hot state: {sorted(bad)}')
   if 'population' in doc: errors.append(f'{rel}: derived living population persisted')
   holdings=doc.get('holdings',{}) if isinstance(doc.get('holdings'),dict) else {}
   for key in ('cultivated_land_mu','urban_estate_area_m2'):
    if key in holdings: errors.append(f'{rel}: derived/duplicate holdings field persisted: {key}')
   epoch=doc.get('training_epoch',{}) if isinstance(doc.get('training_epoch'),dict) else {}
   for key in ('curriculum_ref','history','current_environment'):
    if key in epoch: errors.append(f'{rel}: derived training epoch field persisted: {key}')
  for path,node in walk(doc):
   if path:
    key=path[-1]
    if key in BANNED_KEYS: errors.append(f'{rel}:{".".join(path)}: banned development/history key')
    if key in BANNED_OWNER_KEYS: errors.append(f'{rel}:{".".join(path)}: derived/receipt key must not persist')
    if key in {'training_curriculum','admission_policy','combat_doctrine_definition'}: counts['static_policy_copies']+=1
   if isinstance(node,list) and len(node)>100:
    suffix=path[-1] if path else ''
    pointer=f'{rel}:{".".join(path)}'
    explicit_limit=BOUNDED_ARRAY_LIMITS.get(pointer)
    allowed=suffix in ALLOWED_LARGE_ARRAY_SUFFIXES or (explicit_limit is not None and len(node)<=explicit_limit)
    oversized.append({'path':pointer,'length':len(node),'allowed_kind':allowed,'explicit_limit':explicit_limit})
    if explicit_limit is not None and len(node)>explicit_limit:
     errors.append(f'{pointer}: bounded semantic array exceeded limit {explicit_limit}: {len(node)}')
    elif not allowed:
     errors.append(f'{pointer}: unexplained append-style array length {len(node)}')
 scheduler=json.loads((ROOT/'state/martial-world/scheduler.json').read_text())
 for class_id,row in scheduler.get('recurring',{}).items():
  for owner in row.get('owner_refs',[]):
   if str(owner).startswith('mw.person.') or str(owner) in {'pc_wei_tang','char.zhu','char.ling','char.kai'}:
    errors.append(f'per-person scheduler owner: {class_id}/{owner}')
 counts['scheduler_owner_refs']=sum(len(r.get('owner_refs',[])) for r in scheduler.get('recurring',{}).values())
 counts['scheduler_classes']=len(scheduler.get('recurring',{}))
 allowed_classes={'faction_monthly','region_monthly','faction_annual','route_daily'}
 recurring_classes=set(scheduler.get('recurring',{}))
 if recurring_classes != allowed_classes:
  errors.append(f'scheduler recurring class mismatch: {sorted(recurring_classes)}')
 banned_oneoffs={'annual_faction_life_review','agriculture_harvest_due'}
 for event_ref,row in scheduler.get('one_off',{}).items():
  if isinstance(row,dict) and str(row.get('kind') or row.get('event_kind') or '') in banned_oneoffs:
   kind=str(row.get('kind') or row.get('event_kind') or '')
   errors.append(f'scheduler persists derived one-off {kind}: {event_ref}')
 # Projects persist current work only, not transaction receipts or duplicate IDs.
 projects=json.loads((ROOT/'state/martial-world/projects.json').read_text()).get('projects',{})
 for project_ref,row in projects.items():
  if not isinstance(row,dict): continue
  for key in ('treasury_cash_after_start','material_stock_after_start','quote','commitment_ref'):
   if key in row: errors.append(f'project {project_ref}: receipt/derived field persisted: {key}')
  if row.get('project_ref') == project_ref: errors.append(f'project {project_ref}: duplicate registry ID persisted inside row')
  if row.get('completed') is False: errors.append(f'project {project_ref}: false default completed flag persisted')
 # Custody stores current physical restraint, consequential institutional
 # knowledge, and an actual live ransom demand only. Response/action status is
 # derived from deployments, treasuries and current people rather than copied.
 custody_path=ROOT/'state/martial-world/custody.json'
 if custody_path.exists():
  custody_state=json.loads(custody_path.read_text())
  for row in custody_state.get('records',[]):
   if not isinstance(row,dict): continue
   if row.get('status') in {'released','escaped','rescued','executed'}:
    errors.append(f'custody {row.get("custody_id","?")}: terminal custody history must not persist')
   for bad in ('response_status','ransom_status','ransom_payer_faction_ref','active_rescue_operation_ref','captured_from_movement_ref','resolved_at','rescued_by_ref'):
    if bad in row: errors.append(f'custody {row.get("custody_id","?")}: derived/history field persisted: {bad}')

 # Market cycles are execution counters, not current market facts.
 for mp in (ROOT/'state/martial-world/markets').glob('*.json'):
  md=json.loads(mp.read_text())
  if 'cycles_settled' in md: errors.append(f'{mp.relative_to(ROOT)}: engine cycle counter persisted')
 # Route/contract owners must not carry reservation implementation references.
 for rel in ('state/martial-world/contracts/index.json','state/martial-world/route-operations.json'):
  rp=ROOT/rel
  if not rp.exists(): continue
  rd=json.loads(rp.read_text())
  for path,node in walk(rd):
   if path and path[-1] in {'commitment_ref','commitment_refs','escort_policy_version'}:
    errors.append(f'{rel}:{".".join(path)}: obsolete implementation reference persisted')
 # Keyed registries own their row identity; do not persist the same ID twice.
 contracts_path=ROOT/'state/martial-world/contracts/index.json'
 if contracts_path.exists():
  contracts_state=json.loads(contracts_path.read_text())
  active=contracts_state.get('active',{}) if isinstance(contracts_state,dict) else {}
  if isinstance(active,dict):
   for contract_ref,row in active.items():
    if not isinstance(row,dict): continue
    if row.get('contract_id')==contract_ref: errors.append(f'contract {contract_ref}: duplicate registry ID persisted inside row')
    objective=row.get('objective',{}) if isinstance(row.get('objective'),dict) else {}
    for bad in ('source_region','destination_region','distance_km_tenths','expected_travel_hours','terrain','road_quality','estimated_toll_cash','cargo_mass_kg','cargo_value_cash','minimum_escort_count','threat_score','transport_mode','wagon_count','pack_animal_count','draft_animal_count','civilian_crew_count'):
     if bad in objective: errors.append(f'contract {contract_ref}: derived route/logistics snapshot persisted: {bad}')
 deployments_path=ROOT/'state/martial-world/deployments.json'
 if deployments_path.exists():
  deployment_state=json.loads(deployments_path.read_text())
  deployment_rows=deployment_state.get('deployments',{}) if isinstance(deployment_state,dict) else {}
  if isinstance(deployment_rows,dict):
   for deployment_ref,row in deployment_rows.items():
    if isinstance(row,dict) and row.get('deployment_ref')==deployment_ref:
     errors.append(f'deployment {deployment_ref}: duplicate registry ID persisted inside row')
 # Escort movements commit exact real people; no aggregate strength/outcome shortcuts are save truth.
 route_path=ROOT/'state/martial-world/route-operations.json'
 if route_path.exists():
  route_state=json.loads(route_path.read_text())
  movements=route_state.get('movements',{}) if isinstance(route_state,dict) else {}
  if isinstance(movements,dict):
   for movement_ref,row in movements.items():
    if not isinstance(row,dict): continue
    if row.get('movement_ref')==movement_ref: errors.append(f'route movement {movement_ref}: duplicate registry ID persisted inside row')
    for bad in ('escort_strength','escort_combat_index','combat_roll','outcome_roll','casualty_roll','known_escort_count','source_region','destination_region','cargo_value_cash','purchase_cash','expected_sale_cash','sale_cash','toll_cash','repelled_outlaw_refs','robbed_by_faction_ref','sale_failed'):
     if bad in row: errors.append(f'escort/route {movement_ref}: derived/history field persisted: {bad}')
    participants=[str(x) for x in row.get('participant_refs',[]) if isinstance(x,str)]
    if len(participants)!=len(set(participants)):
     errors.append(f'route movement {movement_ref}: duplicate exact participant_refs')
    escorts=[str(x) for x in row.get('escort_refs',[]) if isinstance(x,str)] if isinstance(row.get('escort_refs'),list) else None
    raiders=[str(x) for x in row.get('raider_refs',[]) if isinstance(x,str)] if isinstance(row.get('raider_refs'),list) else None
    if row.get('movement_kind')=='raid_return':
     if escorts is not None or raiders is not None:
      errors.append(f'raid return {movement_ref}: redundant controller alias persists')
    elif escorts is not None and escorts==participants:
     errors.append(f'route movement {movement_ref}: redundant escort_refs duplicates participant_refs')
    if row.get('movement_kind')=='escort_contract' and row.get('status','active') in {'active','contact_pending'}:
     participants=[str(x) for x in row.get('participant_refs',[]) if isinstance(x,str)]
     explicit_escorts=[str(x) for x in row.get('escort_refs',[]) if isinstance(x,str)] if isinstance(row.get('escort_refs'),list) else None
     escorts=explicit_escorts if explicit_escorts is not None else participants
     if not escorts: errors.append(f'escort movement {movement_ref}: no exact controlling participants')
     if len(escorts)!=len(set(escorts)): errors.append(f'escort movement {movement_ref}: duplicate exact escort refs')
     missing=[x for x in escorts if x not in participants]
     if missing: errors.append(f'escort movement {movement_ref}: escort not present in participant_refs: {missing[0]}')
   contacts=route_state.get('contacts',{}) if isinstance(route_state,dict) else {}
   if isinstance(contacts,dict):
    for contact_ref,row in contacts.items():
     if isinstance(row,dict) and row.get('contact_ref')==contact_ref:
      errors.append(f'route contact {contact_ref}: duplicate registry ID persisted inside row')
 # Shared institutional training should not create ordinary personal accumulator state.
 training_exceptions=0; current_people=0
 for p in (ROOT/'state/martial-world/people').glob('*.json'):
  d=json.loads(p.read_text())
  for person in d.get('people',[]):
   current_people+=1
   if 'standing_duty_ref' in person:
    errors.append(f'{p.relative_to(ROOT)}:{person.get("person_id","?")}: derived duty assignment persisted')
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
