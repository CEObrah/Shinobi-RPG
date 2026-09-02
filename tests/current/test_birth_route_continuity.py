from datetime import datetime

from shinobi_runtime.martial_world.family_frontier import settle_due_births
from shinobi_runtime.martial_world.faction_state import faction_path, roster_path
from shinobi_runtime.martial_world.faction_registry import REGISTRY_PATH
from shinobi_runtime.martial_world.physical_presence import active_route_for_person


def _reader(records):
    def read(path):
        if path not in records:
            raise FileNotFoundError(path)
        return records[path]
    return read


def _person(ref, *, sex, faction, location):
    return {
        'person_id':ref,'name':ref,'birth_year':30,'sex':sex,'appearance':100,
        'aptitudes':{'physical':100,'martial':100,'qi':100,'cognitive':100,'leadership':100},
        'attributes':{'strength':50,'speed':50,'dexterity':50,'endurance':50,'perception':50,'intelligence':50,'willpower':50},
        'martial_skills':{},'professional_skills':{},'qi':0,'qi_control':0,
        'health':{'status':'healthy'},'faction_ref':faction,'location_ref':location,
    }


def test_due_birth_while_traveling_adds_newborn_to_mothers_exact_route_owner():
    due=datetime(61,9,1,12,0,0)
    mother=_person('mother.route',sex='female',faction='a',location='site.origin')
    father=_person('father.route',sex='male',faction='a',location='site.origin')
    family={
        'schema':'jianghu-family-state-1.0',
        'marriages':{'m.route':{
            'status':'married','spouse_refs':['mother.route','father.route'],'faction_ref':'a',
            'pregnancy':{'mother_ref':'mother.route','father_ref':'father.route','conceived_at':'0060-12-01T00:00:00','due_at':due.isoformat(),'child_ref':'child.route'},
        }},
        'parentage':{},'households':{},'succession_claims':{},
    }
    records={
        'state/meta.json':{'player_id':'someone.else'},
        REGISTRY_PATH:{'schema':'jianghu-faction-registry-1.0','faction_refs':['a'],'dormant_estate_refs':[]},
        faction_path('a'):{'schema':'jianghu-faction-state-1.0','faction_id':'a','name':'A','headquarters':'origin','local_site_ref':'site.origin','treasury_cash':0,'member_count':2},
        roster_path('a'):{'schema':'jianghu-person-lite-roster-1.0','faction_ref':'a','people':[mother,father]},
        'state/martial-world/independent-people.json':{'schema':'jianghu-independent-people-1.0','people':[]},
        'state/martial-world/civic-people.json':{'schema':'jianghu-civic-people-1.0','people':[]},
        'state/martial-world/family.json':family,
        'state/martial-world/route-operations.json':{
            'schema':'jianghu-route-operations-state-1.0','contacts':{},'movements':{
                'movement.route.birth':{
                    'movement_ref':'movement.route.birth','status':'active','route_ref':'route.origin.destination',
                    'participant_refs':['mother.route','father.route'],'protected_person_refs':[],
                },
            },
        },
        'state/martial-world/custody.json':{'schema':'jianghu-custody-state-1.0','records':[]},
        'state/martial-world/combats.json':{'schema':'jianghu-combat-state-1.0','combats':{}},
    }
    writes={}
    result=settle_due_births(
        read_json=_reader(records),writes=writes,
        events=[{'event_id':'family_birth_due:child.route','kind':'family_birth_due','due_at':due.isoformat(),'owner_ref':'a','marriage_ref':'m.route','child_ref':'child.route'}],
        at=due,
    )
    assert result['reviews'][0]['result']=='birth'
    assert result['reviews'][0]['birth_presence_kind']=='route'
    movement=writes['state/martial-world/route-operations.json']['movements']['movement.route.birth']
    assert 'child.route' in movement['participant_refs']
    assert 'child.route' in movement['protected_person_refs']
    overlay=dict(records); overlay.update(writes)
    assert active_route_for_person(_reader(overlay),'child.route')[0]=='movement.route.birth'
