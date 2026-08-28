import json
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


def test_every_current_faction_person_is_uniquely_routeable_from_rosters_without_saved_index():
    from shinobi_runtime.martial_world.live_state import person_route
    from shinobi_runtime.store.repository import RepositoryStore

    assert not (ROOT/'state/martial-world/person-routes.json').exists()
    assert not (ROOT/'state/martial-world/person-routes').exists()
    repository=RepositoryStore(ROOT)
    checked=0
    faction_ids=set()
    for roster_file in sorted((ROOT/'state/martial-world/people').glob('*.json')):
        roster=json.loads(roster_file.read_text())
        fid=roster['faction_ref']
        assert roster_file.name==f'{fid}.json'
        for ordinal,p in enumerate(roster['people']):
            pid=p['person_id']
            assert pid not in faction_ids
            faction_ids.add(pid)
            assert person_route(repository,pid)==(fid,ordinal)
            checked+=1
    independent=load('state/martial-world/independent-people.json')['people']
    civic=load('state/martial-world/civic-people.json')['people']
    assert not (faction_ids & {p['person_id'] for p in independent})
    assert not (faction_ids & {p['person_id'] for p in civic})
    assert checked>0



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


def test_local_travel_uses_canonical_base_speed_and_has_no_fake_procedure_overhead(monkeypatch):
    import copy
    import json
    import shinobi_runtime.martial_world.local_travel as local_travel

    cfg = json.loads((ROOT / "game/data/martial-world/local-geography.json").read_text())
    assert "procedure_overhead_minutes" not in cfg
    baseline = local_travel.base_walking_speed_kph()
    changed_cfg = copy.deepcopy(cfg)
    changed_cfg["walking_speed_kph"] = baseline * 2
    monkeypatch.setattr(local_travel, "_local_geography", lambda: changed_cfg)
    assert local_travel.base_walking_speed_kph() == baseline * 2
    command_source = (ROOT / "runtime/shinobi_runtime/commands/jianghu_travel_team.py").read_text()
    assert "4.8 * walking_milli" not in command_source
    assert "base_walking_speed_kph() * walking_milli" in command_source
