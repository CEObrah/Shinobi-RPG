#!/usr/bin/env python3
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
errs=[]
def err(x): errs.append(x)
def read(rel):
    try:return json.loads((ROOT/rel).read_text(encoding='utf-8'))
    except Exception as exc: err(f'json:{rel}:{exc}'); return {}

meta=read('state/meta.json')
world_time=meta.get('time')
# Exact people are real full owners but do not carry general scheduler mirrors.
exact_paths=[ROOT/'state/player.json']+sorted((ROOT/'state/char').glob('*.json'))+sorted((ROOT/'state/person/ht').glob('*.json'))
exact_ids=set()
for path in exact_paths:
    d=json.loads(path.read_text(encoding='utf-8'))
    oid=d.get('owner_id') or d.get('id')
    if oid: exact_ids.add(oid)
    for forbidden in ('coverage_ref','schedule_profile','runtime'):
        if forbidden in d: err(f'exact_person_scheduler_mirror:{path.relative_to(ROOT)}:{forbidden}')

# Sword Manor invariant: 32 formal members, 5 exact + 27 sparse persistent cores.
house=read('state/house/tang.json')
cores_doc=read('state/person-core/house-tang.json')
cores=cores_doc.get('people',{})
member_ids=house.get('member_ids',[])
if len(member_ids)!=32 or len(set(member_ids))!=32: err(f'house_member_count:{len(member_ids)}')
if house.get('rostered_member_count')!=27: err(f'house_rostered_member_count:{house.get("rostered_member_count")}')
if len(cores)!=27: err(f'house_core_count:{len(cores)}')
if set(cores)-set(member_ids): err('house_core_not_member')
exact_house=[mid for mid in member_ids if mid not in cores]
if len(exact_house)!=5: err(f'house_exact_member_count:{len(exact_house)}:{exact_house}')
for mid in exact_house:
    if mid not in exact_ids: err(f'house_exact_member_missing_owner:{mid}')

allowed_core={
    'id','name','aliases','pronouns','birth_date','birth_date_source','origin','life_status',
    'location_ref','cohort_ref','cohort_slot','role_profile_ref','duty_tags','resolved_through',
    'identity_cues','component_refs','provenance','affiliation_ref'
}
heavy={'stats','health','aptitude','repertoire','body','history','personality','inventory','knowledge','relationships','goals','career'}
cohort_refs=set()
for core_id,core in cores.items():
    if core.get('id')!=core_id: err(f'core_key_id_drift:{core_id}')
    unknown=set(core)-allowed_core
    if unknown: err(f'core_unknown_fields:{core_id}:{sorted(unknown)}')
    if heavy.intersection(core): err(f'core_heavy_state:{core_id}:{sorted(heavy.intersection(core))}')
    if core.get('resolved_through')!=world_time: err(f'core_cursor_drift:{core_id}:{core.get("resolved_through")}:{world_time}')
    if core.get('life_status')!='alive': err(f'opening_core_not_alive:{core_id}:{core.get("life_status")}')
    cref=core.get('cohort_ref')
    if not isinstance(cref,str) or not cref: err(f'core_missing_cohort:{core_id}')
    else: cohort_refs.add(cref)
    if 'coverage_ref' in core: err(f'core_scheduler_mirror:{core_id}')

# Every roster core must be represented exactly once by a House cohort.
cohort_members={}
for cohort in house.get('cohorts',[]):
    uid=cohort.get('id')
    for core_id in cohort.get('roster_refs',[]):
        if core_id in cohort_members: err(f'core_in_multiple_cohorts:{core_id}:{cohort_members[core_id]}:{uid}')
        cohort_members[core_id]=uid
    count=cohort.get('aggregate_count')
    refs=cohort.get('roster_refs',[])
    if refs and count!=len(refs): err(f'cohort_headcount_drift:{uid}:{count}:{len(refs)}')
for core_id,core in cores.items():
    if cohort_members.get(core_id)!=core.get('cohort_ref'): err(f'core_cohort_membership_drift:{core_id}:{core.get("cohort_ref")}:{cohort_members.get(core_id)}')

# No scheduler event may poll a roster identity just because it exists.
scheduler=read('state/time/causal-scheduler.json')
blob=json.dumps(scheduler,sort_keys=True)
for core_id in cores:
    if core_id in blob: err(f'roster_core_in_scheduler:{core_id}')

if errs:
    print('PERSON MODEL TEST FAILED')
    for e in errs: print('-',e)
    sys.exit(1)
print('PERSON MODEL TEST OK')
print(f'exact_people={len(exact_ids)} house_members={len(member_ids)} exact_house={len(exact_house)} roster_cores={len(cores)} cohorts={len(cohort_refs)}')
