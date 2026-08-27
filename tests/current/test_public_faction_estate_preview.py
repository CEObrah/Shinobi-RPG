import copy
import json
import shutil
from pathlib import Path

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.planner import RepositoryCommandPlanner
from shinobi_runtime.martial_world.independent_people import compact_independent_person
from shinobi_runtime.store.repository import RepositoryStore

ROOT=Path(__file__).resolve().parents[2]


def _clone(tmp_path, name):
    dst=tmp_path/name
    shutil.copytree(ROOT,dst,ignore=shutil.ignore_patterns('.pytest_cache','__pycache__','*.pyc'))
    return dst


def _write(root, rel, obj):
    (root/rel).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def test_public_found_dynamic_faction_preview_validates_new_closed_owners(tmp_path):
    root=_clone(tmp_path,'found')
    meta=json.loads((root/'state/meta.json').read_text()); player=meta['player_id']
    roster_path=root/'state/martial-world/people/house_tang.json'
    roster=json.loads(roster_path.read_text()); moved=[]; keep=[]
    for row in roster['people']:
        if row.get('person_id') in {player,'char.kai'}: moved.append(copy.deepcopy(row))
        else: keep.append(row)
    assert {x['person_id'] for x in moved}=={player,'char.kai'}
    sites=json.loads((root/'game/data/martial-world/local-sites.json').read_text())['sites']
    site_ref=next(ref for ref,row in sorted(sites.items()) if row.get('site_type')=='guild_hall' and row.get('public_access','public')=='public')
    place_ref=sites[site_ref]['parent_place_ref']
    for row in moved:
        row.pop('membership_grade',None); row['standing_offices']=[]; row['location_ref']=site_ref
    roster['people']=keep; _write(root,'state/martial-world/people/house_tang.json',roster)
    independents=json.loads((root/'state/martial-world/independent-people.json').read_text())
    independents['people'].extend(compact_independent_person(row) for row in moved)
    _write(root,'state/martial-world/independent-people.json',independents)
    scene=json.loads((root/'state/scene.json').read_text()); scene['location_id']=site_ref; scene['present_person_ids']=[player,'char.kai']; scene['visible_person_ids']=[player,'char.kai']; _write(root,'state/scene.json',scene)
    repo=RepositoryStore(root); planner=RepositoryCommandPlanner(repo)
    command=CommandEnvelope(campaign_id=meta['campaign_id'],request_id='request.test.found',actor_id=player,
        command_type='jianghu_faction_lifecycle_resolution',expected_revision=meta['revision'],submitted_at='2026-08-23T00:00:00Z',payload={
            'action':'found','new_faction_ref':'faction.preview_fellowship','name':'Preview Fellowship','faction_type':'society',
            'member_refs':[player,'char.kai'],'startup_cash':1000,'startup_ration_days':0,
            'headquarters_place_ref':place_ref,'headquarters_site_ref':site_ref,'jianghu_camp':'independent',
        })
    preview=planner.preview(command)
    assert preview.status=='ready'
    assert any(ref.endswith('faction.preview_fellowship.json') for ref in preview.affected_refs)


def test_public_extinct_estate_claim_preview_validates_controlled_facilities(tmp_path):
    root=_clone(tmp_path,'estate')
    meta=json.loads((root/'state/meta.json').read_text()); player=meta['player_id']
    registry=json.loads((root/'state/martial-world/faction-registry.json').read_text())
    target=next(ref for ref in registry['faction_refs'] if ref!='house_tang')
    registry['faction_refs']=[ref for ref in registry['faction_refs'] if ref!=target]
    registry.setdefault('dormant_estate_refs',[]).append(target); registry['dormant_estate_refs']=sorted(set(registry['dormant_estate_refs']))
    _write(root,'state/martial-world/faction-registry.json',registry)
    fpath=f'state/martial-world/factions/{target}.json'; faction=json.loads((root/fpath).read_text()); faction['status']='extinct'
    faction['holdings']={'rural_land_mu':50,'record_refs':['record.capture.test']}
    faction['enterprise_scale']={'crafting_workshop':{'productive_workstations':7}}
    _write(root,fpath,faction)
    claimant_path='state/martial-world/factions/house_tang.json'
    claimant=json.loads((root/claimant_path).read_text()); claimant['holdings']={'rural_land_mu':20,'record_refs':['record.house.test']}
    _write(root,claimant_path,claimant)
    estate_site=faction['local_site_ref']
    physical_ref='project:test:captured-physical'; scale_ref='project:test:captured-scale'
    _write(root,'state/martial-world/projects.json',{'schema':'jianghu-project-registry-1.0','projects':{
        physical_ref:{
            'project_type':'building_expansion','building_type':'residential_compound','additional_footprint_m2':100,
            'faction_ref':target,'site_ref':estate_site,'started_at':'0061-09-01T00:00:00','last_progress_at':'0061-09-05T00:00:00',
            'elapsed_calendar_days':4,'minimum_calendar_days':10,'general_labor_hours_remaining':500,'skilled_labor_hours_remaining':100,
            'general_worker_refs':['ghost.general'],'skilled_worker_refs':['ghost.skilled'],'management_worker_refs':[],
            'planned_general_worker_count':1,'planned_skilled_worker_count':1,'planned_management_worker_count':0,
            'status':'suspended_extinct','suspended_reason':'faction_extinct','suspended_at':'0061-09-05T00:00:00',
        },
        scale_ref:{
            'project_type':'enterprise_scale_expansion','enterprise_type':'crafting_workshop','scale_key':'productive_workstations',
            'from_value':7,'target_value':8,'faction_ref':target,'site_ref':estate_site,
            'started_at':'0061-09-01T00:00:00','last_progress_at':'0061-09-05T00:00:00','elapsed_calendar_days':4,'minimum_calendar_days':10,
            'general_labor_hours_remaining':0,'skilled_labor_hours_remaining':0,'management_labor_hours_remaining':100,
            'general_worker_refs':[],'skilled_worker_refs':[],'management_worker_refs':['ghost.manager'],
            'planned_general_worker_count':0,'planned_skilled_worker_count':0,'planned_management_worker_count':1,
            'status':'suspended_extinct','suspended_reason':'faction_extinct','suspended_at':'0061-09-05T00:00:00',
        },
    }})
    scheduler=json.loads((root/'state/martial-world/scheduler.json').read_text())
    scheduler.setdefault('one_off',{})[f'autonomous_project_due:{physical_ref}']={'event_id':f'autonomous_project_due:{physical_ref}','kind':'autonomous_project_due','due_at':'0061-09-20T00:00:00','owner_ref':physical_ref,'requires_player_decision':False}
    scheduler['one_off'][f'autonomous_project_due:{scale_ref}']={'event_id':f'autonomous_project_due:{scale_ref}','kind':'autonomous_project_due','due_at':'0061-09-20T00:00:00','owner_ref':scale_ref,'requires_player_decision':False}
    _write(root,'state/martial-world/scheduler.json',scheduler)
    # Put Wei physically at the estate; the preview remains read-only.
    rpath=root/'state/martial-world/people/house_tang.json'; roster=json.loads(rpath.read_text())
    for row in roster['people']:
        if row.get('person_id')==player: row['location_ref']=estate_site; row['standing_offices']=['leader']
    _write(root,'state/martial-world/people/house_tang.json',roster)
    scene=json.loads((root/'state/scene.json').read_text()); scene['location_id']=estate_site; scene['present_person_ids']=[player]; scene['visible_person_ids']=[player]; _write(root,'state/scene.json',scene)
    repo=RepositoryStore(root); planner=RepositoryCommandPlanner(repo)
    command=CommandEnvelope(campaign_id=meta['campaign_id'],request_id='request.test.estate',actor_id=player,
        command_type='jianghu_property_transfer_resolution',expected_revision=meta['revision'],submitted_at='2026-08-23T00:00:00Z',
        payload={'action':'claim_extinct_estate','other_ref':target})
    preview=planner.preview(command)
    assert preview.status=='ready'
    assert f'state/martial-world/factions/house_tang.json' in preview.affected_refs
    plan=planner.plan(command)
    claimant_after=json.loads(plan.writes[claimant_path].decode('utf-8'))
    target_after=json.loads(plan.writes[fpath].decode('utf-8'))
    assert claimant_after['holdings']=={'rural_land_mu':70,'record_refs':['record.capture.test','record.house.test']}
    assert target_after['holdings']=={}
    assert target_after['enterprise_scale']=={}
    assert estate_site in claimant_after['controlled_estates']
    projects_after=json.loads(plan.writes['state/martial-world/projects.json'].decode('utf-8'))['projects']
    adopted=projects_after[physical_ref]
    assert adopted['faction_ref']=='house_tang' and adopted['site_ref']==estate_site
    assert adopted['status']=='staffing_required'
    assert adopted['last_progress_at']=='0061-09-14T09:15:00'
    assert adopted['general_worker_refs']==[] and adopted['skilled_worker_refs']==[]
    assert adopted['planned_general_worker_count']==1 and adopted['planned_skilled_worker_count']==1
    assert scale_ref not in projects_after
    schedule_after=json.loads(plan.writes['state/martial-world/scheduler.json'].decode('utf-8'))
    assert f'autonomous_project_due:{scale_ref}' not in schedule_after['one_off']
    assert schedule_after['one_off'][f'autonomous_project_due:{physical_ref}']['due_at']=='0061-09-15T09:15:00'
