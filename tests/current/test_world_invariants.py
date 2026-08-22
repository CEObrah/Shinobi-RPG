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
    bad=[]
    for p in (ROOT/'state').rglob('*.json'):
        text=p.read_text()
        data=json.loads(text)
        def walk(v,path=''):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k=='source' or k.startswith('source_'): bad.append(f'{p}:{path}/{k}')
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
    refs=next(v for v in by_place.values() if len(v)>=2)
    q=local_travel_quote(start_site_ref=refs[0],end_site_ref=refs[1])
    assert q['distance_m']>=0
    assert q['walking_minutes']>=1


def test_flower_house_has_real_prices_and_adult_service_gate_without_ledger():
    sites=load('game/data/martial-world/local-sites.json')['sites']
    flower=next(k for k,v in sites.items() if v.get('site_type')=='flower_house')
    ordinary=service_quote(site_ref=flower,service_ref='tea_and_performance',buyer_age=16)
    assert ordinary['price_cash']>0
    import pytest
    with pytest.raises(PermissionError):
        service_quote(site_ref=flower,service_ref='private_adult_companionship',buyer_age=17)
    adult=service_quote(site_ref=flower,service_ref='private_adult_companionship',buyer_age=18)
    assert adult['price_cash']>ordinary['price_cash']
    assert 'ledger_ref' not in sites[flower]


def test_hot_factions_reference_static_policy_instead_of_copying_it():
    static=load('game/data/martial-world/world-seed.json')['martial_factions']
    for path in (ROOT/'state/martial-world/factions').glob('*.json'):
        row=json.loads(path.read_text())
        fid=row['faction_id']
        assert path.name==f'{fid}.json'
        if fid in static:
            for key in ('name','type','training','doctrine','recruitment_policy','autonomy_policy','outlaw_subtype','operating_routes','outlaw_policy'):
                assert key not in row or row[key] != static[fid].get(key)
        assert 'roster_ref' not in row and 'inventory_ref' not in row
        assert 'last_review_at' not in row and 'injured_martial' not in row
        assert 'active_projects' not in row and 'active_contracts' not in row


def test_inventory_and_person_defaults_are_sparse():
    for path in (ROOT/'state/martial-world/inventories').glob('*.json'):
        inv=json.loads(path.read_text())
        assert 'last_settled_at' not in inv
        for key in ('equipment','raw_materials','herbs','medicines','transport_assets'):
            assert all(int(v)>0 for v in inv.get(key,{}).values())
            if key in inv: assert inv[key]
    for path in (ROOT/'state/martial-world/people').glob('*.json'):
        roster=json.loads(path.read_text())
        for person in roster['people']:
            assert 'martial_member' not in person
            assert person.get('qi') != 0 and person.get('qi_control') != 0
            assert all(int(v)>0 for v in person.get('martial_skills',{}).values())
            assert all(int(v)>0 for v in person.get('professional_skills',{}).values())


def test_every_climate_profile_has_all_twelve_calendar_months():
    data=json.loads((ROOT/'game/data/martial-world/climate.json').read_text())
    for profile_ref, row in data['profiles'].items():
        means=row.get('monthly_mean_temp_c_tenths',[])
        assert len(means)==12, (profile_ref, len(means))
        assert all(isinstance(value,int) for value in means)
