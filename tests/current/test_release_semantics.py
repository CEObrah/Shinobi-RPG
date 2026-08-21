import copy
import json
import subprocess
import sys
from pathlib import Path

from shinobi_runtime.martial_world.people import person_lite
from shinobi_runtime.martial_world.commitments import reserve_resources
from shinobi_runtime.martial_world.government import allocate_response

ROOT=Path(__file__).resolve().parents[2]
def load(rel): return json.loads((ROOT/rel).read_text())


def test_full_semantic_release_gate_passes():
    run=subprocess.run([sys.executable,str(ROOT/'tools/verify_jianghu_semantics.py')],cwd=ROOT,capture_output=True,text=True)
    assert run.returncode==0,run.stdout+run.stderr


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
