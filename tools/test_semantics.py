from pathlib import Path
import json, sys, glob, math
R=Path(__file__).resolve().parents[1]
GAME='sword' if (R/'state/char-roster').exists() else 'shinobi'
errs=[]
def err(x): errs.append(x)
def rj(p):
    p=R/p if isinstance(p,str) else p
    try:return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:err(f'json:{p.relative_to(R)}:{e}');return {}

def all_routes(m=None):
    m=m or rj('data/runtime/repository-map.json')
    out=dict(m.get('routes',{}))
    for rel in m.get('route_shards',{}).values():
        out.update(rj(rel).get('routes',{}))
    return out

def ids_in(dirrel):
    out=set()
    q=R/dirrel
    if not q.exists(): return out
    for p in q.glob('*.json'):
        d=rj(p)
        for k in ('id','doctrine_id','training_id'):
            if isinstance(d.get(k),str):out.add(d[k]);break
    return out

def all_loadouts():
    d=rj('data/loadouts.json'); return set(d.get('ids', d.get('record_index',{})))

router=rj('data/runtime/rule-router.json')
types=rj('data/organization/troop-types.json').get('types',{})
loads=all_loadouts()
if GAME=='shinobi':
    docs=ids_in('data/organization/doctrine-records'); trains=ids_in('data/organization/training-records')
else:
    docs=ids_in('data/mil/doctrine-records'); trains=ids_in('data/mil/training-records')

# Home establishment series are valid homogeneous cold units.
_seen_series=set(); _seen_patterns=set()
for p in (R/'state/org/home-establishments').glob('*.json'):
    d=rj(p); rec=d.get('record',{})
    for s in rec.get('unit_series',[]):
        sid=s.get('series_id',p.name)
        pat=s.get('stable_unit_id_pattern')
        if sid in _seen_series: err(f'duplicate_establishment_series_id:{sid}')
        else: _seen_series.add(sid)
        if pat:
            if pat in _seen_patterns: err(f'duplicate_establishment_unit_pattern:{pat}')
            else: _seen_patterns.add(pat)
        typ=s.get('troop_type'); load=s.get('loadout_standard'); doc=s.get('doctrine'); tr=s.get('training')
        if typ not in types: err(f'establishment_unknown_troop_type:{sid}:{typ}')
        if load not in loads: err(f'establishment_unknown_loadout:{sid}:{load}')
        if doc not in docs: err(f'establishment_unknown_doctrine:{sid}:{doc}')
        if tr not in trains: err(f'establishment_unknown_training:{sid}:{tr}')
        if any(k in s for k in ('troop_type_distribution','loadout_distribution','loadout_standard_distribution','doctrine_distribution','training_distribution','commander_distribution')):
            err(f'establishment_mixed_standard:{sid}')
        if 'command' in str(typ).lower(): err(f'command_as_troop_type:{sid}:{typ}')

# Materialized homogeneous units.
for p in (R/'state/unit').glob('*.json') if (R/'state/unit').exists() else []:
    d=rj(p); uid=d.get('id',p.name); typ=d.get('troop_type')
    if typ not in types:err(f'unit_unknown_troop_type:{uid}:{typ}')
    load=d.get('loadout_standard')
    if not load:err(f'unit_missing_loadout_standard:{uid}')
    elif load not in loads:err(f'unit_unknown_loadout:{uid}:{load}')
    for k in ('troop_type_distribution','loadout_distribution','loadout_standard_distribution','doctrine_distribution','training_distribution','commander_distribution'):
        if k in d:err(f'unit_mixed_standard:{uid}:{k}')
    if 'command' in str(typ).lower():err(f'command_as_unit:{uid}:{typ}')
    if d.get('doctrine') not in docs:err(f'unit_unknown_doctrine:{uid}:{d.get("doctrine")}')
    if d.get('training') not in trains:err(f'unit_unknown_training:{uid}:{d.get("training")}')
    if GAME=='shinobi':
        for refkey in ('stats_ref','battle_kernel_ref'):
            ref=d.get(refkey)
            if not ref or not (R/ref).exists():err(f'unit_missing_ref:{uid}:{refkey}:{ref}')

# Support classes never contribute default line frontage unless their troop type is explicitly line combat.
classes=rj('data/mechanics/support.json').get('combat_classes',{})
class_defaults=rj('data/organization/troop-types.json').get('class_defaults',{})
for typ,spec in types.items():
    cls=spec.get('combat_class')
    if cls in ('service_support','noncombat_support') and class_defaults.get(cls,{}).get('frontage_eligible') is True:
        err(f'support_frontage_default_true:{typ}:{cls}')
    if cls=='service_support' and class_defaults.get(cls,{}).get('offensive_contact_eligible') is True:
        err(f'service_support_offense_default_true:{typ}')

# Command mechanics reference case and ownership-agnostic hierarchy.
cmd=rj('data/mechanics/command.json')
if cmd.get('schema')!='hierarchical_command_mechanics.v4':err('command_schema')
if [120,10000] not in cmd.get('comfortable_direct_personnel_anchors',[]):err('command_anchor_120_personnel')
if [120,8] not in cmd.get('comfortable_direct_command_slots_anchors',[]):err('command_anchor_120_slots')
text=json.dumps(cmd).lower()
for phrase in ('ownership','subordinate command node','direct personnel','direct command'):
    if phrase not in text:err(f'command_contract_missing:{phrase}')
ex=cmd.get('worked_example',{})
if '10000 direct personnel' not in ex.get('superior_after','') or '7 direct command slots' not in ex.get('superior_after',''):
    err('command_delegation_reference_case')

# Partition/refit deterministic authority.
part=rj('data/mechanics/unit-partition.json')
if part.get('schema')!='unit_partition_mechanics.v1':err('unit_partition_schema')
pt=json.dumps(part).lower()
for phrase in ('largest remainder','neutral','merge','weighted','receipt'):
    if phrase not in pt:err(f'partition_contract_missing:{phrase}')

# Transaction registry must be v2 and conserve future receipts.
tx=rj('state/org/unit-transactions.json')
if tx.get('schema')!='unit-transaction-registry.v2':err('unit_transaction_schema')

# Force pools/accounting cannot masquerade as formations.
if GAME=='sword':
    for p in (R/'state/force-pool').glob('*.json'):
        d=rj(p)
        if d.get('accounting_only') is not True:err(f'force_pool_not_accounting_only:{p.name}')
        if str(d.get('schema','')).startswith('formation'):err(f'force_pool_as_formation:{p.name}')

# Ocular routing/batching preserves unique assets without loading every stored eye.
oc=rj('state/medical/ocular-registry.json')
if oc.get('schema')!='ocular-registry-index.v2':err('ocular_index_v2')
for owner,rel in oc.get('owner_index',{}).items():
    if not (R/rel).exists():err(f'ocular_owner_shard_missing:{owner}')
sp=((oc.get('stockpiles') or {}).get('obito_stockpile') or {}).get('inventory_ref')
if not sp or not (R/sp).exists():err('ocular_stockpile_route_missing')
else:
    sd=rj(sp)
    if sd.get('schema')!='ocular-stockpile-batch.v1':err('ocular_stockpile_batch_schema')
    n=sum(int(b)-int(a)+1 for a,b in sd.get('available_ordinal_ranges',[]))
    if n!=sd.get('unique_asset_count'):err('ocular_stockpile_batch_count')
    if 'lowest available ordinal' not in str(sd.get('allocation_rule','')).lower():err('ocular_stockpile_deterministic_allocation')

# Exact characters need enough behavior context without role-label filler.
bpi=rj('data/people/behavior-profile-index.json')
bprofiles=bpi.get('profiles',{})
for fp in (R/'state/char').glob('*.json'):
    d=json.load(open(fp,encoding='utf-8')); name=fp.name; oid=d.get('owner_id')
    cp=d.get('compact_personality')
    if cp and set(cp).issubset({'discipline','morale','traits'}):
        err(f'compact_personality_role_filler_forbidden:{name}')
    if not (d.get('behavior') or cp or oid in bprofiles):
        err(f'exact_character_no_behavior_anchor_or_profile:{name}')
    if d.get('runtime_status')=='active_shared_process_participant' and not (d.get('behavior') or oid in bprofiles):
        err(f'active_exact_character_no_behavior_context:{name}')
_exact_oids={json.load(open(x,encoding='utf-8')).get('owner_id') for x in (R/'state/char').glob('*.json')}
for oid,rel in bprofiles.items():
    pp=R/rel
    if not pp.exists(): err(f'behavior_profile_missing:{oid}:{rel}'); continue
    pd=json.load(open(pp,encoding='utf-8'))
    if pd.get('person_id')!=oid or pd.get('authority') is not False: err(f'behavior_profile_invalid:{oid}')
    if oid not in _exact_oids: err(f'behavior_profile_orphan:{oid}')

# Human map must explain both read and write/deepening behavior.
hmap=(R/'REPOSITORY_MAP.md').read_text(encoding='utf-8')
for phrase in ('Minimum-context','Updating a unit','Updating an NPC','Large battle'):
    if phrase.lower() not in hmap.lower():err(f'human_map_missing:{phrase}')


# Reputation architecture and relationship separation.
repidx=rj('state/reputation/index.json')
if repidx.get('schema')!='reputation-index.v1' or repidx.get('authority') is not False:err('reputation_index_schema')
_reps=list((R/'state/reputation/subjects').glob('*.json'))
if len(_reps)!=repidx.get('subject_count'):err(f'reputation_subject_count:{len(_reps)}:{repidx.get("subject_count")}')
for _p in _reps:
    _d=rj(_p)
    if _d.get('schema')!='reputation-subject.v1' or _d.get('authority') is not True:err(f'reputation_subject_schema:{_p.name}')
    for _aud,_ref in _d.get('audience_profiles',{}).items():
        if not (R/_ref).exists():err(f'reputation_audience_ref_missing:{_p.name}:{_aud}:{_ref}')
_mech=rj('data/mechanics/reputation.json')
if _mech.get('schema')!='reputation-mechanics.v1':err('reputation_mechanics_schema')
_dims=rj('data/reputation/dimensions.json')
if _dims.get('schema')!='reputation-dimensions.v1':err('reputation_dimensions_schema')
_auds=rj('data/reputation/audience-segments.json')
if _auds.get('schema')!='reputation-audience-segments.v1':err('reputation_audience_segments_schema')
_routes=router.get('domains',{})
for _dom in ('reputation_query','recognition_check','reputation_event'):
    if _dom not in _routes:err(f'reputation_router_domain_missing:{_dom}')
_maproutes=all_routes()
for _r in ('reputation_subject','recognition_check','reputation_event'):
    if _r not in _maproutes:err(f'reputation_map_route_missing:{_r}')
_relpath=R/('state/reg/relationships-knowledge.json' if GAME=='shinobi' else 'state/rel/relationships-knowledge.json')
if not _relpath.exists():err('relationships_knowledge_registry_missing')
else:
    _reld=rj(_relpath); _blob=json.dumps(_reld).lower()
    if 'reputation_events' in _reld or 'reputation foundation' in _blob:err('reputation_leaked_into_relationship_registry')
_oldrel=R/('state/reg/relationships-knowledge-reputation.json' if GAME=='shinobi' else 'state/rel/relationships-knowledge-reputation.json')
if _oldrel.exists():err('deprecated_relationship_reputation_registry_present')

# Exact-character goals have one writable authority: structured goal_state.
for _p in (R/'state/char').glob('*.json'):
    _d=rj(_p)
    if 'goals' in _d:err(f'legacy_exact_character_goal_mirror:{_p.name}:goals')
    if not isinstance(_d.get('goal_state'),dict):err(f'exact_character_goal_state_missing:{_p.name}')

# Registered randomness has one reproducible stream construction; model variation is never RNG.
_core=rj('data/mechanics/core.json'); _rng=_core.get('rng',{})
if _rng.get('algorithm')!='sha256_counter_u64':err('rng_algorithm')
if 'world_seed' not in str(_rng.get('seed_source','')) or 'transaction_id' not in str(_rng.get('seed_source','')) or 'named_rng_stream' not in str(_rng.get('seed_source','')):err('rng_seed_source')
if not (_rng.get('record_seed_and_draw_index') is True or _rng.get('must_record_seed_and_draw_index') is True):err('rng_receipt_requirement')
if _rng.get('model_sampling_forbidden') is not True:err('rng_model_sampling_forbidden')

# Router/map must expose the current architecture and stay compact enough to be useful.
mapd=rj('data/runtime/repository-map.json'); routes=all_routes(mapd)
for k in ('command_capacity','unit_partition','mass_battle','npc_development','unit_development','equipment'):
    if k not in routes:err(f'map_route_missing:{k}')
if (R/'data/runtime/repository-map.json').stat().st_size>14000:print(f"CONTEXT ADVISORY: repository-map.json is {(R/'data/runtime/repository-map.json').stat().st_size} bytes")
if (R/'RUNTIME.md').stat().st_size>8000:print(f"CONTEXT ADVISORY: RUNTIME.md is {(R/'RUNTIME.md').stat().st_size} bytes")


# Sparse family/life-course authority must be routed and separate from relationships/reputation.
_fidx=rj('state/family/index.json')
if _fidx.get('schema')!='family-index.v1' or _fidx.get('authority') is not False:err('family_index_schema')
_fm=rj('data/mechanics/family.json')
if _fm.get('schema')!='family-mechanics.v1':err('family_mechanics_schema')
for _dom in ('family_query','family_transition','family_succession'):
    if _dom not in router.get('domains',{}):err(f'family_router_domain_missing:{_dom}')
for _r in ('family_person','family_transition','family_succession','family_event'):
    if _r not in routes:err(f'family_map_route_missing:{_r}')

# No retired organizational term survives outside the regression scanner itself.
retired='co'+'hort'
for p in R.rglob('*'):
    if not p.is_file() or '__pycache__' in p.parts or p.suffix in ('.pyc','.zip'): continue
    if retired in p.name.lower():err(f'retired_term_filename:{p.relative_to(R)}')
    if p.name=='test_unit_model.py': continue
    try:t=p.read_text(encoding='utf-8').lower()
    except:continue
    if retired in t:err(f'retired_term_text:{p.relative_to(R)}')

# Process sharding contract on Sword.
if GAME=='sword':
    pc=rj('state/reg/registry-process-contracts.json')
    if pc.get('schema')!='process-contract-registry':err('process_contract_index_schema')
    recs=list((R/'state/reg/process-contracts').glob('*.json'))
    if len(recs)!=pc.get('record_count'):err('process_contract_record_count')
    if not (R/'rules/character-runtime.md').exists():err('character_runtime_rule_missing')

if errs:
    print('SEMANTIC TEST FAILED')
    for x in errs[:300]: print('-',x)
    sys.exit(1)
print('SEMANTIC TEST OK')
print(f'game={GAME} troop_types={len(types)} loadouts={len(loads)} doctrine_records={len(docs)} training_records={len(trains)}')
