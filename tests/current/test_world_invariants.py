import hashlib, json
from pathlib import Path

from shinobi_runtime.martial_world.field_command import formation_kind_for_headcount, build_deployment_structure, validate_deployment_structure
from shinobi_runtime.martial_world.recruitment import deterministic_candidate, screening_report
from shinobi_runtime.martial_world.services import service_quote
from shinobi_runtime.martial_world.local_travel import local_travel_quote

ROOT=Path(__file__).resolve().parents[2]


def load(rel): return json.loads((ROOT/rel).read_text())


def test_world_counts_and_no_mercenary_or_branch_type():
    world=load('game/data/martial-world/world-seed.json')
    factions=world['martial_factions']
    assert len(factions)==240
    types={f['type'] for f in factions.values()}
    assert 'mercenary_company' not in types
    assert all('branch' not in f for f in factions.values())
    assert sum(1 for f in factions.values() if f['type']=='outlaw_faction')==70


def test_every_current_faction_person_has_exact_direct_route_and_independents_do_not():
    index=load('state/martial-world/person-routes.json')
    assert index['schema']=='jianghu-person-route-index-1.0'
    checked=0
    faction_ids=set()
    for roster_file in (ROOT/'state/martial-world/people').glob('*.json'):
        roster=json.loads(roster_file.read_text())
        fid=roster['faction_ref']
        assert roster_file.name==f'{fid}.json'
        for ordinal,p in enumerate(roster['people']):
            pid=p['person_id']; faction_ids.add(pid)
            bucket=hashlib.sha256(pid.encode()).hexdigest()[:2]
            shard=load(f'state/martial-world/person-routes/{bucket}.json')
            assert shard['people'][pid]==[fid,ordinal]
            checked+=1
    assert checked==index['person_count']==len(faction_ids)
    independent=load('state/martial-world/independent-people.json')['people']
    assert not (faction_ids & {p['person_id'] for p in independent})
    for person in independent:
        pid=person['person_id']; bucket=hashlib.sha256(pid.encode()).hexdigest()[:2]
        shard=load(f'state/martial-world/person-routes/{bucket}.json')
        assert pid not in shard['people']
    assert checked+len(independent)>=11691


def test_no_hot_state_debug_provenance_fields():
    # Domain data legitimately uses names such as source_ref/source_region for
    # physical origin and demand ownership. Ban repository/debug provenance
    # specifically, rather than every useful field whose name starts "source".
    debug_keys={
        'source','source_file','source_path','source_line','source_sha',
        'source_commit','source_branch','source_url','source_blob','source_locator',
    }
    bad=[]
    for p in (ROOT/'state').rglob('*.json'):
        text=p.read_text()
        data=json.loads(text)
        def walk(v,path=''):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k in debug_keys: bad.append(f'{p}:{path}/{k}')
                    walk(x,path+'/'+k)
            elif isinstance(v,list):
                for i,x in enumerate(v): walk(x,path+f'/{i}')
        walk(data)
    assert not bad


def test_recruit_candidate_is_stable_and_screening_does_not_reroll():
    kwargs=dict(world_seed='seed',origin_population_id='civilian.luoyang',ordinal=123)
    a=deterministic_candidate(**kwargs); b=deterministic_candidate(**kwargs)
    assert a==b
    assert 'origin_population_id' in a and 'origin_ordinal' in a
    assert not any(k.startswith('source') for k in a)
    report=screening_report(a,evaluator_skill=20)
    assert report['true_values_changed'] is False
    assert a==b


def test_small_scale_field_command_has_no_redundant_hq():
    assert formation_kind_for_headcount(5)=='team'
    assert formation_kind_for_headcount(25)=='section'
    assert formation_kind_for_headcount(80)=='wing'
    assert formation_kind_for_headcount(121)=='field_force'
    roster=load('state/martial-world/people/house_tang.json')['people'][:60]
    structure=build_deployment_structure(member_refs=[p['person_id'] for p in roster],records={p['person_id']:p for p in roster})
    assert structure['kind']=='wing'
    assert validate_deployment_structure(structure)
    assert structure['headcount']==60


def test_local_services_have_real_prices_but_no_site_ledgers():
    sites=load('game/data/martial-world/local-sites.json')['sites']
    inn=next(k for k,v in sites.items() if v.get('site_type')=='inn')
    q=service_quote(site_ref=inn,service_ref='simple_room',buyer_age=20)
    assert q['price_cash']>0 and q['duration_minutes']>0
    assert 'ledger_ref' not in sites[inn]


def test_local_travel_is_physical_not_teleportation():
    sites=load('game/data/martial-world/local-sites.json')['sites']
    by_place={}
    for ref,row in sites.items(): by_place.setdefault(row['parent_place_ref'],[]).append(ref)
    a,b=next((rows[0],rows[1]) for rows in by_place.values() if len(rows)>=2)
    q=local_travel_quote(start_site_ref=a,end_site_ref=b)
    assert q['distance_m']>0 and q['walking_minutes']>0
