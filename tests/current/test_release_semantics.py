import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from shinobi_runtime.martial_world.people import person_lite
from shinobi_runtime.martial_world.commitments import reserve_resources
from shinobi_runtime.martial_world.government import allocate_response

ROOT=Path(__file__).resolve().parents[2]
_VERIFY_SPEC=importlib.util.spec_from_file_location('verify_jianghu_semantics',ROOT/'tools/verify_jianghu_semantics.py')
assert _VERIFY_SPEC is not None and _VERIFY_SPEC.loader is not None
_VERIFY_MODULE=importlib.util.module_from_spec(_VERIFY_SPEC)
_VERIFY_SPEC.loader.exec_module(_VERIFY_MODULE)
institutional_membership_obligation_errors=_VERIFY_MODULE.institutional_membership_obligation_errors
equipment_authority_errors=_VERIFY_MODULE.equipment_authority_errors
deployment_equipment_authority_errors=_VERIFY_MODULE.deployment_equipment_authority_errors
strategic_operation_intent_errors=_VERIFY_MODULE.strategic_operation_intent_errors
route_controller_authority_errors=_VERIFY_MODULE.route_controller_authority_errors
coalition_causality_errors=_VERIFY_MODULE.coalition_causality_errors
def load(rel): return json.loads((ROOT/rel).read_text())


def test_full_semantic_release_gate_passes():
    run=subprocess.run([sys.executable,str(ROOT/'tools/verify_jianghu_semantics.py')],cwd=ROOT,capture_output=True,text=True)
    assert run.returncode==0,run.stdout+run.stderr


def test_semantic_gate_rejects_stale_or_incompatible_war_coalition():
    current={"a","b","c","enemy"}
    relations={
        "edges":[
            {"from_faction":"a","to_faction":"enemy","hostility":80},
            {"from_faction":"b","to_faction":"enemy","hostility":80},
            {"from_faction":"a","to_faction":"b","trust":20},
            {"from_faction":"b","to_faction":"a","trust":20},
            {"from_faction":"c","to_faction":"enemy","hostility":0},
            {"from_faction":"a","to_faction":"c","hostility":20},
        ],
        "coalitions":{
            "coalition.bad":{
                "member_faction_refs":["a","b","c"],
                "target_faction_ref":"enemy",
                "purpose":"mutual_war_pressure",
                "formed_at":"0061-01-01T00:00:00",
            }
        },
    }
    errors, metrics=coalition_causality_errors(relations=relations,current_faction_refs=current)
    assert metrics=={"coalitions":1,"coalition_memberships":3}
    assert any("not currently at war" in error for error in errors)
    assert any("mutually incompatible" in error for error in errors)


def test_character_rules_do_not_duplicate_240_faction_policies():
    assert 'faction_demography' not in load('game/data/martial-world/character-system.json')
    assert len(load('game/data/martial-world/faction-identities.json')['identities'])==240


def test_generated_person_obeys_authored_demography_age_and_name():
    identity=load('game/data/martial-world/faction-identities.json')['identities']['shaolin']
    person=person_lite(world_seed='test',faction_id='shaolin',headquarters='shaolin_temple',ordinal=9999,training=identity['training_curriculum'],recruitment_policy=identity['admission_policy'])
    assert person['sex']=='male'
    assert 61-person['birth_year']>=identity['admission_policy']['minimum_entry_age']
    assert not person['name'].lower().startswith('recruit ')
    assert 'shield' not in json.dumps(person).lower()


def test_master_disciple_special_state_is_removed():
    assert not (ROOT / "state/martial-world/lineages.json").exists()
    assert not (ROOT / "game/data/martial-world/master-disciple.json").exists()
    operations = (ROOT / "runtime/shinobi_runtime/api/operations.py").read_text(encoding="utf-8")
    assert "'lineage'" not in operations


def test_commitment_authority_rejects_double_booking():
    state={'schema':'jianghu-commitment-state-1.0','commitments':{}}
    first=reserve_resources(state,resources=[('person','p','owner')],actor_ref='actor',owner_ref='owner',activity_ref='a',activity_kind='test',started_at='0061-01-01T00:00:00',location_ref='site')
    try:
        reserve_resources(first,resources=[('person','p','owner')],actor_ref='actor',owner_ref='owner',activity_ref='b',activity_kind='test',started_at='0061-01-01T00:00:00',location_ref='site')
    except ValueError:
        pass
    else:
        raise AssertionError('double booking was accepted')


def test_government_response_consumes_finite_capacity():
    capacity={'militia':2,'standard':1,'elite':0}
    a=allocate_response(100,capacity)
    b=allocate_response(100,a['capacity_after'])
    used=int(a['allocated']['exact_headcount'])+int(b['allocated']['exact_headcount'])
    assert used<=3


def test_structural_template_accepts_intentionally_open_event_payloads():
    from shinobi_runtime.store.template_validation import RegisteredTemplateValidator
    template={
        "required_top_level_keys":["schema","events"],
        "object_contracts":{
            "":{"mode":"closed","allowed_keys":["schema","events"]},
            "/events":{"mode":"open_map"},
            "/events/*":{"mode":"open"},
        },
        "type_contracts":{
            "":["object"],"/schema":["string"],"/events":["object"],"/events/*":["object"],
        },
        "array_contracts":{},
    }
    RegisteredTemplateValidator._validate_document(
        {"schema":"test","events":{"e1":{"kind":"harvest","crop_ref":"staple_grain","planted_mu":12}}},
        template,label="test",
    )


def test_hot_state_has_no_schema_version_or_per_person_scheduler():
    for path in (ROOT/'state').rglob('*.json'):
        assert 'schema_version' not in json.dumps(json.loads(path.read_text()))
    scheduler=load('state/martial-world/scheduler.json')
    assert not any(str(owner).startswith('mw.person.') for row in scheduler['recurring'].values() for owner in row['owner_refs'])


def test_institutional_obligation_authority_rejects_changed_contract_and_tournament_affiliation():
    contracts={
        'active':{
            'contract.bound':{
                'status':'accepted','beneficiary_ref':'house.a','participants':['person.bound'],
            },
        },
    }
    tournaments={
        'tournaments':{
            'tournament.bound':{
                'status':'registration_open',
                'registrations':[{'entrant_ref':'person.bound','faction_ref':'house.a'}],
                'delegations':{'house.a':{'faction_ref':'house.a','leader_refs':['person.bound']}},
            },
        },
    }
    errors=institutional_membership_obligation_errors(
        contracts=contracts,tournaments=tournaments,living_exact_people={'person.bound'},
        people_faction={'person.bound':'house.b'},faction_refs={'house.a','house.b'},
    )
    assert any('contract principal' in row and 'beneficiary house.a' in row for row in errors)
    assert any('tournament entrant' in row and 'sponsor house.a' in row for row in errors)
    assert any('leader_refs' in row and 'sponsor house.a' in row for row in errors)
    assert institutional_membership_obligation_errors(
        contracts=contracts,tournaments=tournaments,living_exact_people={'person.bound'},
        people_faction={'person.bound':'house.a'},faction_refs={'house.a','house.b'},
    ) == []


def test_equipment_provenance_cannot_claim_more_than_holder_physically_carries():
    ledger = {
        "policy_assignments": {},
        "person_loadouts": {"person.holder": {"items": {"weapon_jian": 1}}},
        "provenance_exceptions": {
            "person.holder": {
                "weapon_jian": {"owner_ref": "faction.owner", "quantity": 2, "status": "seized"},
            },
        },
    }
    errors, _metrics = equipment_authority_errors(
        ledger=ledger, loadout_policies={},
        exact_people={"person.holder": {"person_id": "person.holder"}},
        living_exact_people={"person.holder"},
        active_faction_refs={"faction.owner"}, dormant_faction_refs=set(),
        people_faction={"person.holder": "faction.other"},
    )
    assert any("exceeds physical held quantity" in row for row in errors)


def test_operation_issue_authority_rejects_dead_or_detached_live_holders():
    deployments={
        'deployments':{
            'op.bound':{
                'status':'traveling_return','participant_refs':['person.live'],
                'issued_equipment':{
                    'person.live':{'weapon_jian':1},
                    'person.dead':{'weapon_jian':1},
                    'person.detached':{'weapon_jian':1},
                },
                'issued_equipment_baseline':{
                    'person.live':{'weapon_jian':0},
                    'person.dead':{'weapon_jian':0},
                    'person.detached':{'weapon_jian':0},
                    'person.stale':{'weapon_jian':0},
                },
            },
        },
    }
    errors=deployment_equipment_authority_errors(
        deployments=deployments,
        exact_people={'person.live','person.dead','person.detached','person.stale'},
        living_exact_people={'person.live','person.detached','person.stale'},
    )
    assert any('dead exact person person.dead' in row for row in errors)
    assert any('not a current participant person.detached' in row for row in errors)
    assert any('issued_equipment_baseline has holders outside issued_equipment' in row for row in errors)
    assert deployment_equipment_authority_errors(
        deployments={'deployments':{'op.ok':{
            'status':'traveling_return','participant_refs':['person.live'],
            'issued_equipment':{'person.live':{'weapon_jian':1}},
            'issued_equipment_baseline':{'person.live':{'weapon_jian':0}},
            'issued_equipment_claim_baseline':{'person.live':{'weapon_jian':0}},
        }}},
        exact_people={'person.live'},living_exact_people={'person.live'},
    ) == []

    separated=deployment_equipment_authority_errors(
        deployments={'deployments':{'op.separated':{
            'status':'traveling_return','participant_refs':['person.live'],
            'physical_movement_ref':'move.return',
            'issued_equipment':{'person.live':{'weapon_jian':1}},
            'issued_equipment_baseline':{'person.live':{'weapon_jian':0}},
            'issued_equipment_claim_baseline':{'person.live':{'weapon_jian':0}},
        }}},
        exact_people={'person.live'},living_exact_people={'person.live'},
        route_operations={'movements':{'move.return':{'participant_refs':[],'status':'active'}}},
    )
    assert any('not on linked physical movement person.live' in row for row in separated)
    assert deployment_equipment_authority_errors(
        deployments={'deployments':{'op.linked':{
            'status':'traveling_return','participant_refs':['person.live'],
            'physical_movement_ref':'move.return',
            'issued_equipment':{'person.live':{'weapon_jian':1}},
            'issued_equipment_baseline':{'person.live':{'weapon_jian':0}},
            'issued_equipment_claim_baseline':{'person.live':{'weapon_jian':0}},
        }}},
        exact_people={'person.live'},living_exact_people={'person.live'},
        route_operations={'movements':{'move.return':{'participant_refs':['person.live'],'status':'active'}}},
    ) == []


def test_strategic_operation_intent_rejects_legacy_lethal_outlaw_raid():
    factions={
        'faction.band':{'faction_id':'faction.band','type':'outlaw_faction'},
        'faction.target':{'faction_id':'faction.target','type':'martial_school'},
    }
    bad={'deployments':{
        'op.legacy':{
            'status':'traveling_outbound','operation_kind':'faction_raid',
            'faction_ref':'faction.band','target_faction_ref':'faction.target',
            'targeting_intent':'lethal',
        },
    }}
    errors=strategic_operation_intent_errors(deployments=bad,factions=factions)
    assert any('lacks a lawful nonlethal raid objective' in row for row in errors)

    contradictory={'deployments':{
        'op.bad':{
            'status':'traveling_outbound','operation_kind':'faction_raid',
            'faction_ref':'faction.band','operation_intent':'extortion','targeting_intent':'lethal',
        },
    }}
    errors=strategic_operation_intent_errors(deployments=contradictory,factions=factions)
    assert any('explicitly targets lethal combat' in row for row in errors)

    good={'deployments':{
        'op.good':{
            'status':'traveling_outbound','operation_kind':'faction_raid',
            'faction_ref':'faction.band','operation_intent':'extortion','targeting_intent':'disable',
        },
    }}
    assert strategic_operation_intent_errors(deployments=good,factions=factions) == []


def test_route_controller_authority_rejects_live_carried_only_movement():
    rescued='person.rescued'
    dead_escort='person.dead-escort'
    bad={'movements':{'move.bad':{
        'movement_kind':'faction_operation_travel','status':'active',
        'participant_refs':[rescued],'escort_refs':[],
        'protected_person_refs':[rescued],'rescued_refs':[rescued],
    }}}
    errors=route_controller_authority_errors(route_operations=bad)
    assert any('carried-only movement has no potential controller' in row for row in errors)

    waiting={'movements':{'move.wait':{
        'movement_kind':'faction_operation_travel','status':'awaiting_return_logistics',
        'participant_refs':['person.wounded',rescued],'escort_refs':[],
        'protected_person_refs':[rescued],'rescued_refs':[rescued],
    }}}
    assert route_controller_authority_errors(route_operations=waiting) == []

    controlled={'movements':{'move.ok':{
        'movement_kind':'faction_operation_travel','status':'active',
        'participant_refs':['person.escort',rescued],'escort_refs':['person.escort'],
        'protected_person_refs':[rescued],'rescued_refs':[rescued],
    }}}
    assert route_controller_authority_errors(route_operations=controlled) == []


def test_removed_paper_formal_offices_do_not_survive_in_current_policy_or_runtime():
    forbidden = {"chief_apothecary", "master_weaponsmith", "discipline_instructor"}
    structure = json.loads((ROOT / "game/data/martial-world/faction-structure.json").read_text())
    assert forbidden.isdisjoint(structure.get("offices", {}))
    for rel in (
        "runtime/shinobi_runtime/martial_world/duties.py",
        "runtime/shinobi_runtime/martial_world/training.py",
        "runtime/shinobi_runtime/martial_world/retinues.py",
        "runtime/shinobi_runtime/commands/jianghu_extended.py",
        "game/data/martial-world/house-tang-seed.json",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not any(label in text for label in forbidden), rel


