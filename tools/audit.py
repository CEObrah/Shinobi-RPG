#!/usr/bin/env python3
from __future__ import annotations
import json,re,sys
from pathlib import Path
try: import jsonschema
except Exception: jsonschema=None
ROOT=Path(__file__).resolve().parents[1]
errors=[]
_JSON_CACHE={}
def err(x):errors.append(x)
def rj(p):
 q=Path(p)
 key=str(q.resolve())
 if key in _JSON_CACHE:return _JSON_CACHE[key]
 try:
  data=json.loads(q.read_text(encoding='utf-8'))
  _JSON_CACHE[key]=data
  return data
 except Exception as e:err(f'json:{q.relative_to(ROOT)}:{e}');return None
# parse files, path budgets
for p in ROOT.rglob('*'):
 if '.git' in p.parts or not p.is_file():continue
 rel=p.relative_to(ROOT)
 if len(str(rel))>120:err(f'path_too_long:{rel}')
 if len(p.name)>64:err(f'filename_too_long:{rel}')
 if p.suffix=='.json':rj(p)
 elif p.suffix=='.jsonl':
  for i,line in enumerate(p.read_text(encoding='utf-8').splitlines(),1):
   if line.strip():
    try:json.loads(line)
    except Exception as e:err(f'jsonl:{rel}:{i}:{e}')
meta=rj(ROOT/'state/meta.json') or {}
if meta.get('schema')!='meta':err('meta_schema')
# schema enforcement
registry=rj(ROOT/'schemas/registry.json') or {}
def walk(x):
 if isinstance(x,dict):
  yield x
  for v in x.values():yield from walk(v)
 elif isinstance(x,list):
  for v in x:yield from walk(v)
for p in ROOT.rglob('*.json'):
 if 'schemas' in p.parts:continue
 d=rj(p)
 if d is None:continue
 for o in walk(d):
  s=o.get('schema') if isinstance(o,dict) else None
  if not isinstance(s,str):continue
  target=registry.get(s)
  if not target:err(f'unmapped_schema:{s}:{p.relative_to(ROOT)}');continue
  if jsonschema:
   spec=rj(ROOT/'schemas'/target)
   if spec:
    try:jsonschema.validate(o,spec)
    except Exception as e:err(f'schema:{s}:{p.relative_to(ROOT)}:{getattr(e,"message",e)}')
# top-level IDs
ids={}
for p in (ROOT/'state').rglob('*.json'):
 d=rj(p)
 if not isinstance(d,dict):continue
 oid=d.get('id') or d.get('owner_id') or d.get('formation_id') or d.get('company_id')
 if oid:
  if oid in ids:err(f'duplicate_id:{oid}:{ids[oid]}:{p.relative_to(ROOT)}')
  else:ids[oid]=str(p.relative_to(ROOT))



def sharded_records(_index_rel,_field):
 _idx=rj(ROOT/_index_rel) or {}; _out={}
 if isinstance(_idx.get(_field),dict):_out.update(_idx.get(_field,{}))
 for _sh in _idx.get('shards',[]):
  _path=_sh.get('path') if isinstance(_sh,dict) else _sh
  if _path:_out.update((rj(ROOT/_path) or {}).get(_field,{}))
 if _field=='loadouts' and isinstance(_idx.get('ids'),list) and _idx.get('path_template'):
  for _id in _idx['ids']:
   _path=_idx['path_template'].replace('{loadout_id}',_id)
   _d=rj(ROOT/_path) or {}
   if isinstance(_d.get('loadout'),dict):_out[_id]=_d['loadout']
 for _id,_path in _idx.get('record_index',{}).items():
  _d=rj(ROOT/_path) or {}
  if _field=='doctrines' and isinstance(_d.get('doctrine'),dict):_out[_id]=_d['doctrine']
  elif _field=='profiles' and isinstance(_d.get('profile'),dict):_out[_id]=_d['profile']
  elif _field=='loadouts' and isinstance(_d.get('loadout'),dict):_out[_id]=_d['loadout']
 return _out

def technique_effect_profiles():
 idx=rj(ROOT/'data/mechanics/technique-effects-manifest.json') or {}
 out={}
 for tid,rel in idx.get('effect_profiles',{}).items():
  d=rj(ROOT/rel) or {}; ep=d.get('effect_profile')
  if isinstance(ep,dict): out[tid]=ep
 return out

def ocular_eyes():
 oc=rj(ROOT/'state/medical/ocular-registry.json') or {}
 out=[]
 if isinstance(oc.get('eyes'),list): out.extend(oc.get('eyes',[]))
 for _owner,_ref in (oc.get('owner_index') or {}).items():
  out.extend((rj(ROOT/_ref) or {}).get('eyes',[]))
 stockrefs=[]
 if isinstance(oc.get('obito_stockpile'),dict): stockrefs.append(oc['obito_stockpile'].get('inventory_ref'))
 for _meta in (oc.get('stockpiles') or {}).values():
  if isinstance(_meta,dict): stockrefs.append(_meta.get('inventory_ref'))
 for ref in [x for x in stockrefs if x]:
  sd=rj(ROOT/ref) or {}
  if isinstance(sd.get('eyes'),list): out.extend(sd['eyes']); continue
  if sd.get('schema')=='ocular-stockpile-batch':
   proto=sd.get('prototype',{})
   for a,b in sd.get('available_ordinal_ranges',[]):
    for n in range(int(a),int(b)+1):
     e=dict(proto); e['eye_id']=sd['id_template'].format(ordinal=n); e['original_owner_id']=sd['original_owner_template'].format(ordinal=n); out.append(e)
 return out

# individual body/birth/appearance and no authoritative age caches
people=[ROOT/'state/player.json']+list((ROOT/'state/char').glob('*.json'))+list((ROOT/'state/person').rglob('*.json'))
for p in people:
 d=rj(p) or {}
 for k in ('age','age_current','age_at_anchor'):
  if k in d:err(f'age_cache_present:{p.relative_to(ROOT)}:{k}')
 if not d.get('birth_date'):err(f'missing_birth_date:{p.relative_to(ROOT)}')
 b=d.get('body')
 if not isinstance(b,dict):err(f'missing_body:{p.relative_to(ROOT)}');continue
 for k in ('adult_height_cm','growth_end_age','current_weight_kg','frame'):
  if b.get(k) is None:err(f'missing_body_field:{p.relative_to(ROOT)}:{k}')
 if b.get('growth_end_age')!=18:err(f'growth_end_not_18:{p.relative_to(ROOT)}')
 if not (0 <= d.get('appearance',-1) <= 100):err(f'appearance:{p.relative_to(ROOT)}')
# Campaign age is derived from birth_date + current meta time; standard audit does not lock opening ages/heights.
# Team records may change through legal play. Validate only structural references when a saved team exists.
team=rj(ROOT/'state/team/fujin.json') or {}
if team:
 _tm=[x for x in [team.get('jonin_instructor')]+list(team.get('genin',[])) if x]
 if len(_tm)!=len(set(_tm)):err('team_duplicate_member')

# techniques: direct record paths; manifest is maintenance enumeration only
_tmanifest=rj(ROOT/'data/tech/manifest.json') or {}; index=_tmanifest.get('techniques',{})
alltech={}
for tid,rel in index.items():
 d=rj(ROOT/rel) or {}
 if tid in alltech:err(f'duplicate_technique:{tid}')
 if d.get('method_id')!=tid:err(f'technique_record_id_mismatch:{tid}:{rel}')
 alltech[tid]=d
 if d.get('effect_profile_path')!=f'data/mechanics/technique-effects/profiles/{tid}.json':err(f'technique_effect_path:{tid}')
 if not d.get('mechanical_base_path') or not (ROOT/d.get('mechanical_base_path')).exists():err(f'technique_primitive_path:{tid}')
packs=(rj(ROOT/'data/tech/packages.json') or {}).get('packages',{})
for pid,pv in packs.items():
 for t in pv.get('methods',[]):
  if t not in alltech:err(f'package_undefined_technique:{pid}:{t}')
bl=(rj(ROOT/'data/tech/bloodlines.json') or {}).get('bloodlines',{})
for p in people:
 d=rj(p) or {}; rep=d.get('repertoire',{})
 for t in rep.get('field_usable_techniques',[])+rep.get('latent_or_locked_techniques',[]):
  if t not in alltech:err(f'undefined_repertoire_technique:{p.relative_to(ROOT)}:{t}')
 for pid in rep.get('packages',[]):
  if pid not in packs:err(f'undefined_package:{p.relative_to(ROOT)}:{pid}')
 for b in rep.get('bloodlines',[]):
  if b not in bl:err(f'undefined_bloodline:{p.relative_to(ROOT)}:{b}')
haku=rj(ROOT/'state/char/haku.json') or {}; mei=rj(ROOT/'state/char/mei-arakawa.json') or {}
if 'bloodline.ice_release' not in haku.get('repertoire',{}).get('bloodlines',[]):err('haku_missing_shared_ice_release')
if 'bloodline.ice_release' not in mei.get('repertoire',{}).get('bloodlines',[]):err('mei_missing_shared_ice_release')
if 'demonic_mirroring_ice_crystals' in mei.get('repertoire',{}).get('field_usable_techniques',[]):err('mei_should_not_auto_learn_haku_mirrors')
for req in ['listening_skin','flowing_veil','crosscurrent_zone','pressure_nails','binding_current','empty_step','returning_current','thousand_edge_field','silent_chamber','severing_wake','rulers_domain','court_of_ten_thousand_invisible_blades','sheltering_current','clear_air_court','breaking_step','mind_anchor_protocol','carrying_veil']:
 if req not in alltech:err(f'missing_invisible_court:{req}')
if (ROOT/'data/jutsu.json').exists():err('obsolete_jutsu_catalog_present')
# item/loadout registry
itemidx=(rj(ROOT/'data/items/index.json') or {}).get('items',{})
items={}
for fn in sorted(set(itemidx.values())):
 d=rj(ROOT/'data/items'/fn) or {}; items.update(d)
loads=sharded_records('data/loadouts.json','loadouts')
def resolve_load(lid,seen=None):
 seen=seen or set()
 if lid in seen:err(f'loadout_cycle:{lid}');return
 if lid not in loads:err(f'undefined_loadout:{lid}');return
 seen.add(lid)
 par=loads[lid].get('inherits')
 if par:resolve_load(par,seen)
 for it in loads[lid].get('items',[]):
  if it.get('item_id') not in items:err(f'loadout_undefined_item:{lid}:{it.get("item_id")}')
for lid in loads:resolve_load(lid)
mechanic_keys={'damage','impact','penetration','reach_m','mass_kg','protection','integrity_max'}
for p in (ROOT/'state').rglob('*.json'):
 d=rj(p)
 if d is None:continue
 for o in walk(d):
  if not isinstance(o,dict):continue
  for k in ('equipment_loadout_id','equipment_standard','loadout'):
   v=o.get(k)
   if isinstance(v,str) and v not in loads:err(f'state_undefined_loadout:{p.relative_to(ROOT)}:{v}')
  if isinstance(o.get('loadout_distribution'),dict):
   for lid in o['loadout_distribution']:
    if lid not in loads:err(f'state_undefined_loadout:{p.relative_to(ROOT)}:{lid}')
  iid=o.get('item_id') or o.get('item_profile_id')
  if iid:
   if iid not in items:err(f'state_undefined_item:{p.relative_to(ROOT)}:{iid}')
   if mechanic_keys.intersection(o.keys()):err(f'local_item_mechanics:{p.relative_to(ROOT)}:{iid}')
if any('scabbard' in x.lower() for x in items):err('ordinary_scabbard_item_present')
armor=items.get('armor_house_court',{})
if not (armor.get('protection')==6 and armor.get('integrity_max')==140 and armor.get('speed_penalty')==0 and armor.get('vision_penalty')==0 and armor.get('hand_seal_penalty')==0):err('court_armor_values')
# Force conservation + demographics
for p in (ROOT/'state/force').glob('*.json'):
 d=rj(p) or {}
 if 'total' in d:
  pool=sum(int(c.get('count',0)) for c in d.get('troop_pools',[])); claims=sum(int(c.get('count',0)) for c in d.get('unit_claims',[]))
  if pool+claims!=int(d.get('total',0)):err(f'force_conservation:{d.get("id")}')
 for c in d.get('troop_pools',[]):
  _cap=rj(ROOT/c.get('capability_ref','')) or {}
  if 'demographics' not in _cap:err(f'unit_missing_demographics:{c.get("id")}')
# Temporal frontier
def tkey(s):
 m=re.match(r'SE-(\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)',s or '')
 return tuple(map(int,m.groups())) if m else None
front=rj(ROOT/'state/time/frontier.json') or {}; world=tkey(meta.get('time')); covered=set()
_covreq=rj(ROOT/'data/runtime/coverage-requirements.json') or {}
def _process_coverage(_p):
 _cov=list(_p.get('coverage',[]))
 _ref=_p.get('coverage_ref')
 if _ref:
  _d=rj(ROOT/_ref) or {}
  if _d.get('process_id')!=_p.get('id'):err(f'process_coverage_ref_mismatch:{_p.get("id")}:{_ref}')
  _cov=list(_d.get('owner_ids',[]))
 return _cov
for p in front.get('processes',[]):
 covered.update(_process_coverage(p))
 if p.get('status')=='active' and p.get('settlement_mode')!='triggered':
  nd=tkey(p.get('next_due'))
  if not nd or not world or nd<=world:err(f'overdue_or_missing_next_due:{p.get("id")}:{p.get("next_due")}')
for oid in _covreq.get('required_owner_ids',[]):
 if oid not in covered:err(f'uncovered_owner:{oid}')
# Personal/issued policy
house=rj(ROOT/'state/house/tang.json') or {}
if house.get('personal_force_model')!='aggregate_cohorts_with_sparse_sword_manor_notables':err('house_resolution_policy')
# Player build validation may enforce persistent abilities, never a mutable opening career status.
pl=rj(ROOT/'state/player.json') or {}
if 'bloodline.guardian_current' not in pl.get('repertoire',{}).get('bloodlines',[]):err('wei_missing_guardian_current')

# Dedicated reputation authority.
_repidx=rj(ROOT/'state/reputation/index.json') or {}
if _repidx.get('schema')!='reputation-index' or _repidx.get('authority') is not False:err('reputation_index_invalid')
_reps=list((ROOT/'state/reputation/subjects').glob('*.json'))
if len(_reps)!=_repidx.get('subject_count'):err(f'reputation_subject_count:{len(_reps)}:{_repidx.get("subject_count")}')
for _p in _reps:
 _d=rj(_p) or {}
 if _d.get('schema')!='reputation-subject' or _d.get('authority') is not True:err(f'reputation_subject_invalid:{_p.name}')
 for _aud,_ref in _d.get('audience_profiles',{}).items():
  if not (ROOT/_ref).exists():err(f'reputation_audience_missing:{_p.name}:{_aud}:{_ref}')
if not (ROOT/'data/mechanics/reputation.json').exists():err('reputation_mechanics_missing')

# owner index basic verification
ind=rj(ROOT/'state/index/owners.json') or {}
if ind.get('schema')!='owner_index':err('owner_index_schema')
if ind.get('authority') is not False:err('index_must_be_non_authoritative')
_owner_total=0
for _prefix,_shrel in ind.get('prefix_index',{}).items():
 _sh=rj(ROOT/_shrel) or {}
 if _sh.get('prefix')!=_prefix or _sh.get('authority') is not False:err(f'owner_index_shard_header:{_prefix}')
 for oid,rel in _sh.get('owners',{}).items():
  _owner_total+=1
  if not (ROOT/rel).exists():err(f'index_missing_file:{oid}:{rel}')
if _owner_total!=ind.get('owner_count'):err(f'owner_index_count:{_owner_total}:{ind.get("owner_count")}')
# Character, force, and development invariants
# aptitude and persistent exact-character depth
for pth in people:
 d=rj(pth) or {}
 ap=d.get('aptitude')
 if not isinstance(ap,dict):err(f'missing_aptitude:{pth.relative_to(ROOT)}')
 else:
  for k in ('physical_learning','technical_learning','tactical_learning','academic_learning','social_learning'):
   if not isinstance(ap.get(k),(int,float)) or not 0<=ap.get(k)<=200:err(f'bad_aptitude:{pth.relative_to(ROOT)}:{k}')
for pth in (ROOT/'state/char').glob('*.json'):
 d=rj(pth) or {}
 if 'character_profile' in d:err(f'deprecated_character_profile:{pth.name}')
 if 'personality_signature' in d:err(f'deprecated_personality_signature:{pth.name}')
 if 'audit_notes' in d:err(f'maintenance_notes_in_character:{pth.name}')
 if 'relationships' in d and not isinstance(d.get('relationships'),list):err(f'bad_relationships:{pth.name}')
 if isinstance(d.get('behavior'),dict):
  _ct=d['behavior'].get('core_traits')
  if _ct is not None and not isinstance(_ct,(list,str)):err(f'bad_behavior_traits:{pth.name}')
 if not isinstance(d.get('goal_state'),dict):err(f'missing_goal_state:{pth.name}')
 if not isinstance(d.get('background'),dict):err(f'missing_background:{pth.name}')
 if not isinstance(d.get('knowledge_state'),dict) and not isinstance(d.get('knowledge_profile_ref'),str):err(f'missing_knowledge_authority:{pth.name}')
 if not isinstance(d.get('development'),dict):err(f'missing_development:{pth.name}')
# Tang family aptitude is intentionally extraordinary
for rel in ('state/player.json','state/char/zhu.json','state/char/linh.json','state/char/kai.json'):
 d=rj(ROOT/rel) or {}; ap=d.get('aptitude',{})
 if min(ap.get('technical_learning',0),ap.get('chakra_learning',0))<185:err(f'tang_aptitude_too_low:{rel}')
linh=rj(ROOT/'state/char/linh.json') or {}
if linh.get('operational_skills',{}).get('medicine',0)<190 or linh.get('domain_proficiencies',{}).get('medical',0)<190:err('linh_medical_specialist_missing')
if linh.get('aptitude',{}).get('medical_learning')!=200:err('linh_medical_aptitude')
# new exact roster
for fn in ('ensui-nara.json','hoheto-hyuga.json','fu-yamanaka.json','torune-aburame.json','hana-inuzuka.json','sai.json','yamato.json','yugito-nii.json','mabui.json','atsui.json','roshi.json','han-jinchuriki.json','fu-takigakure.json','shibuki.json','maki.json'):
 if not (ROOT/'state/char'/fn).exists():err(f'missing_required_character:{fn}')
# OOC future wishes must never be canonical state. Physical eye authority is unique and evolution-safe.
oc0=rj(ROOT/'state/medical/ocular-registry.json') or {}; inst=ocular_eyes()
if len({x.get('eye_id') for x in inst})!=len(inst):err('duplicate_ocular_implant_id')
for _e in inst:
 if not isinstance(_e.get('vision_integrity'),(int,float)) or not 0<=_e.get('vision_integrity')<=200:err(f'bad_eye_integrity:{_e.get("eye_id")}')
 if not isinstance(_e.get('integration'),(int,float)) or not 0<=_e.get('integration')<=200:err(f'bad_eye_integration:{_e.get("eye_id")}')

# all force troop pools and tactical units carry aptitude and resolution scale
for pth in (ROOT/'state/force').glob('*.json'):
 d=rj(pth) or {}
 for u in d.get('troop_pools',[]):
  _cap=rj(ROOT/u.get('capability_ref','')) or {}
  if not isinstance(_cap.get('aptitude_distribution'),dict):err(f'unit_pool_missing_aptitude:{pth.name}:{u.get("id")}')
  if not (u.get('accounting_scale') or u.get('resolution_scale')):err(f'unit_pool_missing_scale:{pth.name}:{u.get("id")}')
  if not u.get('troop_type'):err(f'unit_pool_missing_troop_type:{pth.name}:{u.get("id")}')
  if 'combat_class' in u:err(f'unit_pool_duplicate_combat_class:{pth.name}:{u.get("id")}')
  if u.get('accounting_only') is not True:err(f'unit_pool_must_be_accounting_only:{pth.name}:{u.get("id")}')
for pth in (ROOT/'state/unit').glob('*.json'):
 d=rj(pth) or {}
 if not (isinstance(d.get('aptitude_distribution'),dict) or isinstance(d.get('aptitude_profile_ref'),str)):err(f'unit_missing_aptitude:{pth.name}:{d.get("id")}')
 if not d.get('resolution_scale'):err(f'unit_missing_scale:{pth.name}:{d.get("id")}')
 if not isinstance(d.get('personnel'),dict) or int(d.get('personnel',{}).get('count',-1))<0:err(f'unit_bad_personnel:{pth.name}:{d.get("id")}')
 if not d.get('troop_type'):err(f'unit_missing_troop_type:{pth.name}:{d.get("id")}')
# generic Konoha ANBU units contain no ordinary Genin and use ANBU loadout
for pth in (ROOT/'state/unit').glob('konoha-anbu-*--*.json'):
 d=rj(pth) or {}
 if 'genin' in d.get('rank_distribution',{}):err(f'anbu_generic_genin:{pth.name}:{d.get("id")}')
 if d.get('loadout_standard')!='loadout_anbu':err(f'anbu_loadout:{pth.name}:{d.get("id")}')
# village strategic field-ready profiles must be differentiated
field_sigs=[]
for pth in (ROOT/'state/force').glob('*.json'):
 d=rj(pth) or {}
 if str(d.get('id','')).startswith('force_') and 'civil' not in str(d.get('id','')):
  for c in d.get('troop_pools',[]):
   if c.get('role')=='field_ready':
    _cap=rj(ROOT/c.get('capability_ref','')) or {};field_sigs.append(json.dumps(_cap.get('stats',{}),sort_keys=True))
if len(field_sigs)>=8 and len(set(field_sigs))<7:err('strategic_force_profiles_still_cloned')
# tactical morale/cohesion/readiness must not be universal constants
ucr=[]
for pth in (ROOT/'state/unit').glob('*.json'):
 d=rj(pth) or {}; ucr.append((d.get('morale'),d.get('cohesion'),d.get('readiness')))
if len(set(ucr))<15:err('unit_readiness_values_insufficiently_differentiated')


# Representation-neutral development model
_dev=rj(ROOT/'data/development/model.json') or {}
_eff=_dev.get('representation_efficiency',{})
if set(_eff.keys())!=set(('exact','individual_lite','unit','house_cohort')):err('development_representation_classes')
if any(v!=1.0 for v in _eff.values()):err(f'development_compression_bonus:{_eff}')
if not _dev.get('capacity_rules',{}).get('instructor_time_conserved'):err('development_instructor_capacity_not_conserved')
if not _dev.get('capacity_rules',{}).get('facility_capacity_conserved'):err('development_facility_capacity_not_conserved')
if _dev.get('promotion_rule',{}).get('mode')!='qualified_subset_transfer':err('development_promotion_mode')
if not _dev.get('batching_rule',{}).get('batch_equivalence_required'):err('development_batch_equivalence_missing')
_dt=rj(ROOT/'tests/development-fairness.json') or {}
if len(_dt.get('tests',[]))<6:err('development_fairness_regressions_missing')
if not (ROOT/'rules/transplants.md').exists():err('transplant_rules_missing')

# Mechanical closure and cleanup invariants
if (ROOT/'schemas/compat').exists(): err('compat_schema_directory_present')
# no obsolete compatibility metadata in gameplay state
for pth in (ROOT/'state').rglob('*.json'):
 d=rj(pth) or {}
 for o in walk(d):
  if isinstance(o,dict):
   for k in o:
    if k in ('legacy_owner_ids','legacy_size_preserved_not_template','migration_alias','migration_aliases','old_id','former_id'): err(f'obsolete_runtime_key:{pth.relative_to(ROOT)}:{k}')
# Hyūga display names use macron; machine IDs remain ASCII
for pth in list((ROOT/'state').rglob('*.json'))+list((ROOT/'data').rglob('*.json')):
 txt=pth.read_text(encoding='utf-8')
 if 'Hyuga' in txt: err(f'hyuga_display_spelling:{pth.relative_to(ROOT)}')
 _old_riku_name='Tsu'+'chida'; _old_riku_id='riku_'+'tsu'+'chida'
 if _old_riku_name in txt or _old_riku_id in txt: err(f'old_riku_identity:{pth.relative_to(ROOT)}')
# Tang family current equipment/career may change. Validate stable aptitude and single equipment authority only.
for rel in ('state/player.json','state/char/zhu.json','state/char/linh.json','state/char/kai.json'):
 d=rj(ROOT/rel) or {}
 if d.get('aptitude',{}).get('dojutsu_learning')!=200:err(f'tang_family_dojutsu_aptitude:{rel}')
 if d.get('equipment_manifest'):err(f'duplicate_family_equipment_authority:{rel}')
pl=rj(ROOT/'state/player.json') or {}
# Every field-usable exact technique has explicit owner mastery
for pth in [ROOT/'state/player.json']+list((ROOT/'state/char').glob('*.json')):
 d=rj(pth) or {}; rep=d.get('repertoire',{}); mm=rep.get('method_mastery',{})
 for tid in rep.get('field_usable_techniques',[]):
  if tid not in mm:err(f'missing_method_mastery:{pth.name}:{tid}')
# Every technique has a defined primitive and nonempty physical/mechanical profile
pr=rj(ROOT/'data/mechanics/technique-primitives.json') or {}; _prim_paths=pr.get('records',{}); prim={_id:(rj(ROOT/_rel) or {}).get('primitive',{}) for _id,_rel in _prim_paths.items()}
for tid,rec in alltech.items():
 if rec.get('mechanical_base') not in prim:err(f'undefined_primitive:{tid}:{rec.get("mechanical_base")}')
 if not rec.get('physical_profile'):err(f'empty_mechanical_profile:{tid}')
# Eye registry is the sole physical ocular authority; character sheets keep user-side mastery only.
oc=rj(ROOT/'state/medical/ocular-registry.json') or {}; eyes=ocular_eyes()
if len({x.get('eye_id') for x in eyes})!=len(eyes):err('duplicate_eye_id')
rr=rj(ROOT/'state/char/riku-hyuga.json') or {}
if rr.get('name')!='Riku Hyūga' or rr.get('owner_id')!='char.riku_hyuga':err('riku_hyuga_identity')
for _e in eyes:
 if _e.get('native_to_current_owner') and _e.get('current_owner_id') and _e.get('preservation_state')=='living' and _e.get('integration')!=200:
  err(f'native_eye_not_fully_integrated:{_e.get("eye_id")}')

# mechanics regression suite exists
# mechanics regression suite exists
if not (ROOT/'tests/mechanics.json').exists():err('mechanics_regression_missing')

# Final mechanical-closure release gate
_required_mechanics=('biju.json','body.json','chakra.json','clone-barrier.json','core.json','dojutsu.json','effect-resolvers.json','elements.json','encumbrance.json','genjutsu.json','guardian-current.json','injury.json','medical.json','morale.json','narrative-recall.json','perception.json','sealing.json','special-systems.json','stats.json','technique-effects.json','technique-primitives.json','timing.json','training.json','travel.json','unique-items.json')
_sreg=rj(ROOT/'schemas/registry.json') or {}
for _fn in _required_mechanics:
 _p=ROOT/'data/mechanics'/_fn
 if not _p.exists():err(f'missing_mechanics_authority:{_fn}');continue
 _d=rj(_p) or {}; _sid=_d.get('schema')
 if not _sid or _sid not in _sreg:err(f'unregistered_mechanics_schema:{_fn}:{_sid}')
# Unified exact-character resources: one canonical shape for all exact people.
for _pth in [ROOT/'state/player.json']+list((ROOT/'state/char').glob('*.json')):
 _d=rj(_pth) or {}; _r=_d.get('resources',{})
 if set(_r)!=set(('chakra','health','fatigue','strain')):err(f'noncanonical_resource_domains:{_pth.name}:{sorted(_r)}');continue
 for _k in ('chakra','health','fatigue'):
  if set(_r.get(_k,{}))!=set(('capacity','current')):err(f'noncanonical_resource_shape:{_pth.name}:{_k}')
 if set(_r.get('strain',{}))!=set(('safe_capacity','current')):err(f'noncanonical_resource_shape:{_pth.name}:strain')
# Character equipment resolves from current loadout + owner deltas; do not cache a second compiled copy.
for _pth in (ROOT/'state/char').glob('*.json'):
 _d=rj(_pth) or {}
 if 'compiled_equipment' in _d:err(f'duplicate_compiled_equipment_cache:{_pth.name}')
_ui_mech=rj(ROOT/'data/mechanics/unique-items.json') or {}
if 'opening_named_items' in _ui_mech:err('duplicate_named_item_current_state_in_mechanics')

# Dōjutsu character state stores operator-side skill only; physical eyes are resolved from ocular registry at runtime.
for _pth in [ROOT/'state/player.json']+list((ROOT/'state/char').glob('*.json')):
 _d=rj(_pth) or {}; _ds=_d.get('dojutsu_state')
 if not _ds:continue
 for _bad in ('eye_ids','eye_integration','vision_integrity_by_eye','integration','vision_integrity'):
  if _bad in _ds:err(f'duplicate_physical_eye_cache:{_pth.name}:{_bad}')
 if not isinstance(_ds.get('ocular_control'),(int,float)) or not 0<=_ds.get('ocular_control',-1)<=200:err(f'dojutsu_bad_ocular_control:{_pth.name}')
 if not isinstance(_ds.get('feature_mastery'),dict) or not _ds.get('feature_mastery'):err(f'dojutsu_missing_feature_mastery:{_pth.name}')

# Every technique primitive is a real resolver, not a label.
# Every technique primitive is a real resolver, not a label.
for _pid,_prec in prim.items():
 if not _prec.get('resolver') or not _prec.get('specific_resolution'):err(f'primitive_not_closed:{_pid}')
 if _prec.get('shared_chakra_authority')!='data/mechanics/chakra.json':err(f'primitive_chakra_authority:{_pid}')
 if _prec.get('shared_timing_authority')!='data/mechanics/core.json':err(f'primitive_timing_authority:{_pid}')
# Current invariants file only; no compatibility invariant filename.
if not (ROOT/'tests/invariants.json').exists():err('current_invariants_missing')
_obsolete_inv=ROOT/'tests'/('v'+'36-invariants.json')
if _obsolete_inv.exists():err('obsolete_invariants_file_present')
# Ocular storage and biological implant references are evolution-safe.
_obs=((oc.get('stockpiles') or {}).get('obito_stockpile') or oc.get('obito_stockpile') or {}); _oref=_obs.get('inventory_ref')
if not _oref or not (ROOT/_oref).exists():err('obito_inventory_ref_missing')
else:
 _oinv=rj(ROOT/_oref) or {}
 if _oinv.get('regeneration_forbidden') is not True:err('ocular_inventory_regeneration_not_forbidden')
 if isinstance(_oinv.get('eyes'),list):
  if len({x.get('eye_id') for x in _oinv.get('eyes',[])})!=len(_oinv.get('eyes',[])):err('ocular_inventory_duplicate_eye')
 elif _oinv.get('schema')=='ocular-stockpile-batch':
  _n=sum(int(b)-int(a)+1 for a,b in _oinv.get('available_ordinal_ranges',[]))
  if _n!=int(_oinv.get('unique_asset_count',-1)):err('ocular_batch_count_mismatch')
  if not _oinv.get('id_template') or not _oinv.get('allocation_rule'):err('ocular_batch_materialization_contract_missing')
 else:err('ocular_inventory_shape_unknown')
_bio=rj(ROOT/'state/medical/biological-implants.json') or {}; _arm=None
for _x in _bio.get('instances',[]):
 if _x.get('id')=='bio.arm.danzo.modified_right':_arm=_x
if _arm:
 _contains=_arm.get('contains',[])
 if len(_contains)!=len(set(_contains)):err('danzo_arm_duplicate_eye_ref')
 for _eid in _contains:
  if _eid not in {x.get('eye_id') for x in eyes}:err(f'danzo_arm_eye_missing_from_ocular_registry:{_eid}')

# Technique effect-profile closure
_te=rj(ROOT/'data/mechanics/technique-effects.json') or {}; _ep=technique_effect_profiles()
_er=rj(ROOT/'data/mechanics/effect-resolvers.json') or {}; _ers=_er.get('resolvers',{})
if len(_ep)!=len(alltech):err(f'effect_profile_count:{len(_ep)}:{len(alltech)}')
for _tid,_t in alltech.items():
 if _t.get('effect_profile_ref')!=_tid:err(f'technique_effect_ref:{_tid}:{_t.get("effect_profile_ref")}')
 _e=_ep.get(_tid)
 if not _e:err(f'missing_effect_profile:{_tid}');continue
 if _e.get('resolver') not in _ers:err(f'undefined_effect_resolver:{_tid}:{_e.get("resolver")}')
 _pp=_t.get('physical_profile',{})
 if _pp.get('effect_profile_ref')!=_tid:err(f'physical_effect_ref:{_tid}')
 if set(_pp)==set(('effect_strength','primitive_resolver_required')):err(f'generic_fallback_profile:{_tid}')
# Unique item modules have one structured numerical authority.
_ui=rj(ROOT/'data/mechanics/unique-items.json') or {}; _mods=_ui.get('unique_modules',{})
_ni=rj(ROOT/'state/reg/named-items.json') or {}
for _it in _ni.get('named_items',[]):
 _mid=_it.get('unique_module_id')
 if _mid and _mid not in _mods:err(f'named_item_module_unresolved:{_it.get("id")}:{_mid}')

# Structured travel and no duplicate Markdown JSON authorities
_routes=rj(ROOT/'state/world/routes-and-settlements.json') or {}
for _rt in _routes.get('payload',{}).get('routes',[]):
 _band=_rt.get('travel_days_band',[])
 if len(_band)!=2:err(f'route_band:{_rt.get("id")}');continue
 if _rt.get('reference_travel_days')!=sum(_band)/2:err(f'route_reference_time:{_rt.get("id")}')
 if _rt.get('travel_mechanics_ref')!='data/mechanics/travel.json':err(f'route_mechanics_ref:{_rt.get("id")}')
for _fn in ('effects.md','encumbrance.md','geometry.md','world.md','unique-items.md'):
 _txt=(ROOT/'rules'/_fn).read_text(encoding='utf-8')
 if '```json' in _txt:err(f'duplicate_json_authority_in_rule:{_fn}')


# Living-world content and runtime-hardening invariants
_fac=rj(ROOT/'state/reg/factions.json') or {}; _factions=[(rj(ROOT/_p) or {}).get('faction',{}) for _p in _fac.get('record_index',{}).values()] if _fac.get('record_index') else _fac.get('factions',[])
if len(_factions)<15:err(f'living_faction_count:{len(_factions)}')
for _f in _factions:
 if not _f.get('goals') or not _f.get('resources') or not _f.get('constraints'):err(f'thin_faction:{_f.get("id")}')
 if 'pursue goal within current resources' in str(_f.get('current_plan','')):err(f'generic_faction_plan:{_f.get("id")}')
# Unassigned patterns are cold content, not active state.
_ma=rj(ROOT/'data/content/mission-archetypes.json') or {}; _mt=_ma.get('archetypes',[])
_wea=rj(ROOT/'data/content/world-event-archetypes.json') or {}; _wa=_wea.get('archetypes',[])
if len(_mt)<20:err(f'mission_archetype_count:{len(_mt)}')
if len(_wa)<12:err(f'world_event_archetype_count:{len(_wa)}')
_mr=rj(ROOT/'state/reg/missions-contracts-projects.json') or {}
if 'mission_templates' in _mr or 'mission_assignment_rules' in _mr:err('mission_design_discussion_in_active_state')
_wr=rj(ROOT/'state/reg/world-events.json') or {}
if _wr.get('events'):err('dormant_world_event_templates_in_active_state')
# Scene contains decision context only; no cached menus.
_sc=rj(ROOT/'state/scene.json') or {}
for _bad in ('decision_packages','action_packages','next_action'):
 if _bad in _sc:err(f'cached_scene_choice:{_bad}')
# Process IDs are unique regardless of whether a one-shot assignment later completes or is removed.
_pids=[_p.get('id') for _p in front.get('processes',[]) if _p.get('id')]
if len(_pids)!=len(set(_pids)):err('duplicate_frontier_process_id')
# Eye transfer is deterministic when eligibility/access conditions are met.
_med=rj(ROOT/'data/mechanics/medical.json') or {}; _pol=_med.get('ocular_transfer_policy',{})
for _k in ('extraction','proper_preservation','implantation'):
 if '100_percent' not in str(_pol.get(_k,'')):err(f'ocular_transfer_not_deterministic:{_k}')
if _pol.get('random_rejection')!='none' or _pol.get('random_eye_damage')!='none':err('ocular_random_failure_not_removed')
# Root primary complex is precommitted hidden world truth.
_rp=rj(ROOT/'state/place/root-primary-complex.json') or {}
if len(_rp.get('zones',[]))<8 or len(_rp.get('connections',[]))<8 or len(_rp.get('security_elements',[]))<5:err('root_facility_not_fleshed')
if not isinstance(_rp.get('player_knowledge',{}).get('facility_known'),bool):err('root_facility_knowledge_state_missing')
for _pth in list((ROOT/'state/char').glob('*.json'))+list((ROOT/'state/person/world').glob('*.json')):
 if 'loc_root_headquarters' in _pth.read_text(encoding='utf-8'):err(f'stale_root_location:{_pth.name}')
# Support people are compact but individualized.
for _pth in (ROOT/'state/person/world').glob('*.json'):
 _d=rj(_pth) or {}
 if not _d.get('history',{}).get('service'):err(f'thin_support_history:{_pth.name}')
 if 'while protecting personal and institutional interests' in str(_d.get('current_goal','')):err(f'generic_support_goal:{_pth.name}')
# Runtime efficiency: no dead ref archaeology, no daily dormant-event polling, compact contracts.
if (ROOT/'data/ref').exists():err('dead_data_ref_directory')
if len(front.get('processes',[]))>16:err(f'frontier_process_bloat:{len(front.get("processes",[]))}')
# Canonical state must not contain OOC wishlists or duplicate owner schedule clocks.
_forbidden_ooc_keys={'preferred_allocation_if_assets_are_recovered','desired_roster','wishlist','future_roster','player_preference','ooc_plan','desired_recruit','story_plan','possible_future_team','user_wants'}
_forbidden_ooc_values={'planned_player_objective_not_started','inactive_player_goal'}
for _pth in (ROOT/'state').rglob('*.json'):
 _d=rj(_pth) or {}
 for _o in walk(_d):
  if not isinstance(_o,dict):continue
  if _forbidden_ooc_keys.intersection(_o):err(f'ooc_player_wishlist_state:{_pth.relative_to(ROOT)}')
  if any(v in _forbidden_ooc_values for v in _o.values() if isinstance(v,str)):err(f'ooc_player_wishlist_state:{_pth.relative_to(ROOT)}')
  if 'next_review_at' in _o:err(f'duplicate_owner_schedule_clock:{_pth.relative_to(ROOT)}')
# Process policy routing and autonomous-world linkage must resolve.
_pc=rj(ROOT/'data/runtime/process-policies.json') or {}
_profiles=set((_pc.get('routine_profiles') or {}).keys())
_templates=set((_pc.get('policy_templates') or {}).keys())
for _rr in _pc.get('resolution_rules',[]):
 _pr=_rr.get('routine_profile_id')
 if _pr and _pr not in _profiles:err(f'undefined_process_routine_profile:{_pr}')
 _tp=_rr.get('template_id')
 if _tp and _tp not in _templates:err(f'undefined_process_policy_template:{_tp}')
_autoref=_pc.get('autonomous_world_contract')
if not _autoref or not (ROOT/_autoref).exists():err('process_autonomous_world_contract_missing')
# OOC future-wishlist artifacts are not canonical owners.
if (ROOT/'state/objective').exists():
 _oj=list((ROOT/'state/objective').rglob('*.json'))
 if _oj:err('generic_player_wishlist_objective_layer_present')
for _badrel in ('state/team/wei-anbu-special-force-plan.json','state/objective/tang-root-raid.json'):
 if (ROOT/_badrel).exists():err(f'ooc_future_plan_file_present:{_badrel}')

# Narrator recognition and time-labelled choice contract exists.
_voice=(ROOT/'VOICE.md').read_text(encoding='utf-8')
for _phrase in ('Repository memory is not player memory','estimated in-world','medium','long'):
 if _phrase not in _voice:err(f'narrator_contract_missing:{_phrase}')
# Release packaging provenance belongs outside the live gameplay tree.
if (ROOT/'data/provenance.json').exists():err('release_provenance_in_live_gameplay_tree')
if (ROOT/'schemas/release-provenance.schema.json').exists():err('release_provenance_schema_in_live_gameplay_tree')


# Normalized character/interface/Invisible Court invariants.
_forbidden_maintenance_keys={'audit_notes','developer_notes','migration_notes','balance_notes','recalibration_notes','version_change_notes','release_notes','conversion_notes'}
for _pth in (ROOT/'state').rglob('*.json'):
 _d=rj(_pth) or {}
 for _o in walk(_d):
  if not isinstance(_o,dict):continue
  for _k in _forbidden_maintenance_keys:
   if _k in _o:err(f'maintenance_key_in_state:{_pth.relative_to(ROOT)}:{_k}')
  for _r in _o.get('relationships',[]) if isinstance(_o.get('relationships'),list) else []:
   if isinstance(_r,dict) and _r.get('source')=='simulation_fill_noncanonical_acquaintance':err(f'synthetic_acquaintance_in_state:{_pth.relative_to(ROOT)}')
_treg=rj(ROOT/'state/team/team-doctrine-registry.json') or {}
if 'doctrine_template' in _treg:err('team_template_still_in_state')
if _treg.get('doctrine_template_ref')!='data/team/doctrine-template.json':err('team_template_ref_missing')
_ttpl=rj(ROOT/'data/team/doctrine-template.json') or {}
if not isinstance((_ttpl.get('template') or {}).get('tendencies'),dict):err('team_tendency_template_missing')
_iface=(ROOT/'PLAYER_INTERFACE.md').read_text(encoding='utf-8') if (ROOT/'PLAYER_INTERFACE.md').exists() else ''
for _phrase in ('OOC:','FORM UNIT','TEAM SETUP','FORMATION SETUP'):
 if _phrase not in _iface:err(f'player_interface_missing:{_phrase}')
_rd=alltech.get('rulers_domain') or {}
if _rd.get('geometry',{}).get('radius_m')!=10:err('rulers_domain_radius')
if _rd.get('physical_profile',{}).get('does_not_freeze') is not True:err('rulers_domain_freeze_guard')
if _rd.get('physical_profile',{}).get('wind_channel_cost')!=4:err('rulers_domain_channel_cost')
if 'rulers_domain' not in pl.get('unique_methods',[]):err('wei_missing_rulers_domain')
if pl.get('repertoire',{}).get('method_mastery',{}).get('rulers_domain')!=80:err('wei_rulers_domain_mastery')
_cs=rj(ROOT/'state/player-detail/combat-style.json') or {}
if _cs.get('combat_style',{}).get('detail_ref')!='data/combat/invisible-court-tactics.json':err('invisible_court_detail_ref')


# Central relationship and reusable knowledge reference integrity.
_relreg=rj(ROOT/'state/reg/relationships-knowledge.json') or {}
_reledges={}
for _rsh in (ROOT/'state/reg/relationship-edges').glob('*.json'):
 _rsd=rj(_rsh) or {}
 _src=_rsd.get('source_id')
 for _eid,_edge in _rsd.get('relationship_edges',{}).items():
  if _eid in _reledges:err(f'duplicate_relationship_edge:{_eid}')
  if _edge.get('source_id')!=_src:err(f'relationship_shard_wrong_source:{_rsh.name}:{_eid}')
  _reledges[_eid]=_edge
_ridx=rj(ROOT/'state/reg/relationship-edge-index.json') or {}
if _ridx.get('edge_count')!=len(_reledges):err(f'relationship_edge_count:{len(_reledges)}:{_ridx.get("edge_count")}')
for _eid,_path in _ridx.get('edge_index',{}).items():
 if _eid not in _reledges or not (ROOT/_path).exists():err(f'relationship_edge_index_dangling:{_eid}:{_path}')
_kprofiles=(rj(ROOT/'data/people/knowledge-profiles.json') or {}).get('profiles',{})
for _pth in (ROOT/'state/char').glob('*.json'):
 _d=rj(_pth) or {}
 if 'relationships' in _d:err(f'exact_relationships_not_centralized:{_pth.name}')
 for _ref in _d.get('relationship_refs',[]):
  _edge=_reledges.get(_ref)
  if not isinstance(_edge,dict):err(f'missing_relationship_ref:{_pth.name}:{_ref}')
  elif _edge.get('source_id')!=_d.get('owner_id'):err(f'relationship_ref_wrong_source:{_pth.name}:{_ref}')
 _kp=_d.get('knowledge_profile_ref')
 if _kp and _kp not in _kprofiles:err(f'missing_knowledge_profile:{_pth.name}:{_kp}')
for _eid,_edge in _reledges.items():
 if not isinstance(_edge,dict) or _edge.get('id')!=_eid:err(f'bad_relationship_edge_id:{_eid}')
_rd=alltech.get('rulers_domain') or {}
_base=_rd.get('physical_profile',{}).get('wind_channel_cost',999)
_focus=(_rd.get('physical_profile',{}).get('focused_restraint') or {}).get('additional_wind_channel_cost',999)
_corr=(_rd.get('physical_profile',{}).get('ally_safe_corridors') or {}).get('additional_wind_channel_cost_per_coherent_corridor',999)
_avail=(pl.get('derived_runtime_limits') or {}).get('available_wind_channels_unfatigued',0)
if _base+_focus>_avail:err('rulers_domain_focus_exceeds_player_channels')
if _base+_focus+_corr>_avail:err('rulers_domain_one_corridor_focus_exceeds_player_channels')




# Tactical containers are teams, not a second combat-cell organization.
for _pth in (ROOT/'state/team/tactical').glob('*.json'):
 _d=rj(_pth) or {}
 if str(_d.get('id','')).startswith('cell_') or ' Cell' in str(_d.get('name','')):err(f'deprecated_tactical_cell_identity:{_pth.name}')
 if not (_d.get('commander') or isinstance(_d.get('commander_slot'),dict)):err(f'tactical_team_missing_command_authority:{_pth.name}')


# Exact-character single-authority and tactical command-personnel invariants.
if (ROOT/'data/latent-identities.json').exists() or (ROOT/'data/latent-identities').exists():err('deprecated_latent_identity_catalog_present')
for _fn in ('kosuke-maruboshi.json','zaji.json','tekuno-kanden.json','hayama-shirakumo.json','suzaku-nara.json','daikoku-funeno.json','suzume.json'):
 if not (ROOT/'state/char'/ _fn).exists():err(f'obscure_konoha_exact_missing:{_fn}')
_unit_by_id={}
for _pth in (ROOT/'state/unit').glob('*.json'):
 _d=rj(_pth) or {}; _unit_by_id[_d.get('id')]=_d
 if _d.get('troop_type')=='command_staff':err(f'deprecated_command_staff_unit:{_pth.name}')
 if _d.get('troop_type')=='communications' and _d.get('role')=='command':err(f'communications_misrepresented_as_command:{_pth.name}')
for _pth in (ROOT/'state/team/tactical').glob('*.json'):
 _d=rj(_pth) or {}; _cp=_d.get('command_personnel') or {}
 if not isinstance(_cp,dict) or int(_cp.get('count',0))<1:err(f'tactical_team_missing_conserved_command_personnel:{_pth.name}')
 if _cp.get('includes_commander_slot') is not True:err(f'tactical_team_commander_not_conserved:{_pth.name}')
 _n=int(_cp.get('count',0)); _missing=[]
 for _uid in _d.get('unit_refs',[]):
  _u=_unit_by_id.get(_uid)
  if not _u:_missing.append(_uid)
  else:_n+=int((_u.get('personnel') or {}).get('count',0))
 if _missing:err(f'tactical_team_dangling_unit_refs:{_pth.name}:{_missing[:3]}')
 if _n!=int(_d.get('personnel_total',-1)):err(f'tactical_team_personnel_conservation:{_pth.name}:{_n}:{_d.get("personnel_total")}')

# Sharded home-establishment loader
def home_records():
 idx=rj(ROOT/'state/org/home-establishments.json') or {}
 out=[]
 if isinstance(idx.get('records'),list): return idx.get('records',[])
 for _oid,_path in idx.get('record_index',{}).items():
  sh=rj(ROOT/_path) or {}; rec=sh.get('record')
  if isinstance(rec,dict):out.append(rec)
 if out:return out
 for ent in idx.get('entries',[]):
  sh=rj(ROOT/ent.get('path','')) or {}; rec=sh.get('record')
  if isinstance(rec,dict):out.append(rec)
 return out

# Home-establishment and return invariants
_hrs=home_records(); _he={'records':_hrs}
if len(_hrs)<25:err(f'home_establishment_count:{len(_hrs)}')
_howners={x.get('owner_id') for x in _hrs}
for _pth in (ROOT/'state/force').glob('*.json'):
 _d=rj(_pth) or {}; _oid=_d.get('id')
 if 'total' in _d and _oid not in _howners:err(f'force_missing_home_establishment:{_oid}')
 if 'total' in _d and not _d.get('formation_library_ref'):err(f'force_missing_formation_library:{_oid}')
for _r in _hrs:
 for _s in _r.get('unit_series',[]):
  _n=int(_s.get('unit_count',0));_nom=int(_s.get('nominal_strength',0));_fin=int(_s.get('final_unit_strength',0))
  if _n<1 or _nom<1 or _fin<1 or _fin>_nom:err(f'bad_unit_series:{_r.get("id")}:{_s.get("series_id")}')
_hu=rj(ROOT/'state/house/tang.json') or {}
if 'pc_wei_tang' not in _hu.get('unassigned_members',[]):err('wei_personal_organization_predefined')
if len(_hu.get('permanent_units',[]))<5:err('house_tang_institutional_units_missing')
for _fn in ('kosuke-maruboshi.json','zaji.json','tekuno-kanden.json','hayama-shirakumo.json','suzaku-nara.json','daikoku-funeno.json','suzume.json'):
 if not (ROOT/'state/char'/ _fn).exists():err(f'obscure_konoha_exact_missing:{_fn}')


# Establishment standing-procedure coverage
_he={'records':home_records()}
for _r in _he.get('records',[]):
 if not _r.get('formation_library_ref'):err(f'establishment_missing_formation_library:{_r.get("owner_id")}')
 if not _r.get('reconstitution_policy_ref'):err(f'establishment_missing_reconstitution:{_r.get("owner_id")}')
 if not _r.get('standing_procedure_ref'):err(f'establishment_missing_standing_procedure:{_r.get("owner_id")}')



# homogeneous-unit and unit-standard invariant
_ttypes=(rj(ROOT/'data/organization/troop-types.json') or {}).get('types',{})
for pth in (ROOT/'state/unit').glob('*.json'):
 d=rj(pth) or {}
 count=int((d.get('personnel') or {}).get('count',0))
 tt=d.get('troop_type')
 if not tt:err(f'unit_missing_troop_type:{pth.name}')
 elif tt not in _ttypes:err(f'unit_undefined_troop_type:{pth.name}:{tt}')
 if not isinstance(d.get('loadout_standard'),str):err(f'unit_missing_standard_loadout:{pth.name}')
 if 'loadout_distribution' in d:err(f'deprecated_unit_loadout_distribution:{pth.name}')
 if 'specialization_distribution' in d:err(f'deprecated_unit_specialization_distribution:{pth.name}')
 if 'combat_class' in d:err(f'unit_duplicate_combat_class:{pth.name}:{tt}')
 if count<0:err(f'unit_negative_personnel:{pth.name}:{count}')


# Unit capability payloads are cold and referenced, not duplicated inline.
for _pth in (ROOT/'state/unit').glob('*.json'):
 _d=rj(_pth) or {}
 if 'stats' in _d:err(f'inline_unit_stats_bloat:{_pth.name}')
 _sr=_d.get('stats_ref')
 if not _sr or not (ROOT/_sr).exists():err(f'unit_missing_stats_ref:{_pth.name}:{_sr}')
 else:
  _cap=rj(ROOT/_sr) or {}
  if _cap.get('unit_id')!=_d.get('id'):err(f'unit_stats_ref_wrong_owner:{_pth.name}:{_sr}')

# Doctrine/training reference integrity
_docs=sharded_records('data/organization/doctrines.json','doctrines')
_trains=sharded_records('data/organization/training-profiles.json','profiles')
for pth in list((ROOT/'state/unit').glob('*.json'))+list((ROOT/'state/force').glob('*.json')):
 d=rj(pth) or {}
 for o in walk(d):
  if isinstance(o,dict):
   if isinstance(o.get('doctrine'),str) and o['doctrine'] not in _docs:err(f'undefined_doctrine:{pth.name}:{o["doctrine"]}')
   if isinstance(o.get('training'),str) and o['training'] not in _trains:err(f'undefined_training:{pth.name}:{o["training"]}')
# Unit index shards
_ind=rj(ROOT/'state/index/owners.json') or {}
for sh in _ind.get('unit_index_shards',[]):
 sp=ROOT/sh.get('path','')
 sd=rj(sp) or {}
 if len(sd.get('units',{}))!=int(sh.get('count',-1)):err(f'unit_index_shard_count:{sh.get("owner_id")}')
 for uid,rel in sd.get('units',{}).items():
  if not (ROOT/rel).exists():err(f'unit_index_missing_file:{uid}:{rel}')

_tprof=(rj(ROOT/'data/organization/unit-tendency-profiles.json') or {}).get('profiles',{})
_aprof=(rj(ROOT/'data/organization/unit-aptitude-profiles.json') or {}).get('profiles',{})
for pth in (ROOT/'state/unit').glob('*.json'):
 d=rj(pth) or {}
 if d.get('tendency_profile_ref') not in _tprof and not isinstance(d.get('tendencies'),dict):err(f'bad_tendency_profile_ref:{pth.name}')
 if d.get('aptitude_profile_ref') not in _aprof and not isinstance(d.get('aptitude_distribution'),dict):err(f'bad_aptitude_profile_ref:{pth.name}')


# Source manpower pools are accounting-only and keep heavy capability cold.
for _pth in (ROOT/'state/force').glob('*.json'):
 _d=rj(_pth) or {}
 if not isinstance(_d.get('troop_pools'),list):continue
 if _d.get('accounting_only') is not True:err(f'force_source_not_accounting_only:{_pth.name}')
 for _pool in _d.get('troop_pools',[]):
  if _pool.get('accounting_only') is not True:err(f'force_pool_not_accounting_only:{_pth.name}:{_pool.get("id")}')
  if 'resolution_scale' in _pool:err(f'force_pool_combat_scale_reintroduced:{_pth.name}:{_pool.get("id")}')
  for _bad in ('stats','demographics','aptitude_distribution','rank_distribution','tendencies'):
   if _bad in _pool:err(f'inline_force_pool_capability_bloat:{_pth.name}:{_bad}')
  _cr=_pool.get('capability_ref')
  if not _cr or not (ROOT/_cr).exists():err(f'force_pool_missing_capability_ref:{_pth.name}:{_pool.get("id")}')

# Home-series reference integrity
_hloads=loads
_hdocs=sharded_records('data/organization/doctrines.json','doctrines')
_htr=sharded_records('data/organization/training-profiles.json','profiles')
for _r in home_records():
 for _s in _r.get('unit_series',[]):
  if _s.get('loadout_standard') not in _hloads:err(f'home_undefined_loadout:{_r.get("owner_id")}:{_s.get("loadout_standard")}')
  if _s.get('doctrine') not in _hdocs:err(f'home_undefined_doctrine:{_r.get("owner_id")}:{_s.get("doctrine")}')
  if _s.get('training') not in _htr:err(f'home_undefined_training:{_r.get("owner_id")}:{_s.get("training")}')

# Refit targets must resolve to a real loadout and never masquerade as an instant completed refit.
for _pth in (ROOT/'state/unit').glob('*.json'):
 _d=rj(_pth) or {}; _rf=_d.get('refit_state')
 if isinstance(_rf,dict):
  _target=_rf.get('target_loadout_standard')
  if _target not in loads:err(f'unit_refit_unknown_loadout:{_pth.name}:{_target}')
  if _rf.get('progress')==1:err(f'unit_completed_refit_not_promoted:{_pth.name}')

# Unit split/merge transaction receipts must conserve headcount and lineage evidence.
_tx=rj(ROOT/'state/org/unit-transactions.json') or {}
if _tx.get('schema')!='unit-transaction-registry':err('unit_transaction_registry_v2_missing')
_seen_tx=set()
for _r in _tx.get('records',[]):
 _tid=_r.get('id')
 if not _tid or _tid in _seen_tx:err(f'unit_transaction_duplicate_or_missing_id:{_tid}')
 _seen_tx.add(_tid)
 _b=_r.get('before') or {}; _a=_r.get('after') or {}; _c=_r.get('conservation') or {}
 if _r.get('method') not in ('neutral_proportional','explicit_selection','merge_pooling','structural_reorganization'):err(f'unit_transaction_method_missing:{_tid}')
 _ev=_r.get('capability_evidence') or {}
 if _ev.get('partition_authority')!='data/mechanics/unit-partition.json':err(f'unit_transaction_partition_evidence_missing:{_tid}')
 if int(_b.get('personnel_total',-1))!=int(_a.get('personnel_total',-2)):err(f'unit_transaction_personnel_not_conserved:{_tid}')
 if _c.get('personnel_delta')!=0:err(f'unit_transaction_nonzero_personnel_delta:{_tid}:{_c.get("personnel_delta")}')
 if not _b.get('unit_ids') or not _a.get('unit_ids'):err(f'unit_transaction_missing_unit_lineage:{_tid}')


# Derived unit battle kernels must match authoritative full capability hashes and dimensions.
import hashlib as _hashlib
_stat_order=rj(ROOT/'data/stat-order.json') or {}; _axes=len(_stat_order.get('axes',[]))
for _up in (ROOT/'state/unit').glob('*.json'):
 _u=rj(_up) or {}; _kref=_u.get('battle_kernel_ref'); _sref=_u.get('stats_ref')
 if not _kref or not _sref: err(f'unit_missing_resolution_refs:{_up.name}'); continue
 _kp=ROOT/_kref; _sp=ROOT/_sref
 if not _kp.exists(): err(f'unit_kernel_missing:{_up.name}:{_kref}'); continue
 _k=rj(_kp) or {}
 if _k.get('unit_id')!=_u.get('id'): err(f'unit_kernel_owner_mismatch:{_up.name}')
 if len(_k.get('mean_vector',[]))!=_axes or len(_k.get('spread_vector',[]))!=_axes: err(f'unit_kernel_axis_count:{_up.name}')
 if _sp.exists():
  _h=_hashlib.sha256(_sp.read_bytes()).hexdigest()
  if _k.get('source_sha256')!=_h: err(f'unit_kernel_stale:{_up.name}')
# Command mechanics must expose ownership-agnostic two-axis hierarchy at army scale.
_cmd=rj(ROOT/'data/mechanics/command.json') or {}
if _cmd.get('schema')!='hierarchical_command_mechanics': err('command_schema_v4_missing')
if _cmd.get('comfortable_direct_personnel_anchors',[])[-1][1] < 100000: err('command_personnel_scale_too_low')
if 'ownership' not in str(_cmd.get('principle','')).lower(): err('command_ownership_agnostic_missing')
# Unit model requires aggregate resolution and single-loadout boundary.
_um=rj(ROOT/'data/organization/unit-model.json') or {}
_part=rj(ROOT/'data/mechanics/unit-partition.json') or {}
if _part.get('schema')!='unit_partition_mechanics':err('unit_partition_mechanics_missing')
if _um.get('aggregate_combat_authority')!='data/mechanics/unit-resolution.json': err('unit_resolution_authority_missing')
if errors:
 print('AUDIT FAILED');[print('-',e) for e in errors];sys.exit(1)
print('AUDIT OK')
print(f'people={len(people)} techniques={len(alltech)} loadouts={len(loads)} items={len(items)} frontier_processes={len(front.get("processes",[]))}')
