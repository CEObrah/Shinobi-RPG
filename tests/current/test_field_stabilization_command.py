import copy

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_extended import JianghuExtendedCommandsMixin
from shinobi_runtime.sim.events import CampaignTime


class _Repo:
    def __init__(self, ledger):
        self.ledger = copy.deepcopy(ledger)

    def read_json(self, path):
        if path == "state/martial-world/equipment-ledger.json":
            return copy.deepcopy(self.ledger)
        raise KeyError(path)


class _FieldMedicineHarness(JianghuExtendedCommandsMixin):
    def __init__(self):
        self.path = "state/test-roster.json"
        actor = {
            "person_id": "medic.test", "location_ref": "road.test",
            "attributes": {"dexterity": 80, "intelligence": 80, "perception": 80},
            "professional_skills": {"medicine": 85},
            "health": {"status": "ready", "injuries": [], "blood_lost_ml": 0, "shock": 0, "consciousness": 100},
        }
        patient = {
            "person_id": "patient.test", "location_ref": "road.test",
            "attributes": {}, "professional_skills": {},
            "health": {
                "status": "wounded", "blood_lost_ml": 100, "shock": 30, "consciousness": 90,
                "injuries": [{
                    "wound_id": "wound.test", "zone": "left_arm", "severity": 55,
                    "bleeding_ml_per_min": 180, "pain": 40, "organ_trauma": 0,
                    "structure_damage": 45, "stabilized": False,
                }],
            },
        }
        self.roster = {"schema": "jianghu-faction-roster-1.0", "faction_ref": "faction.test", "people": [actor, patient]}
        self.repository = _Repo({
            "schema": "jianghu-equipment-ledger-1.0", "policy_assignments": {},
            "person_loadouts": {
                "medic.test": {"items": {"tool_physicians_kit": 1, "supply_medical_bundle": 2}, "condition_milli": {}},
                "patient.test": {"items": {}, "condition_milli": {}},
            },
        })

    def _person(self, person_ref):
        for index, row in enumerate(self.roster["people"]):
            if row["person_id"] == person_ref:
                return self.path, copy.deepcopy(self.roster), index, copy.deepcopy(row)
        raise AssertionError(person_ref)

    def _active_combat_person_refs(self):
        return set()

    def _same_effective_location(self, left_ref, right_ref):
        return True

    def _time_plan_exact(self, command, meta, current_time, *, seconds):
        return {"seconds": seconds, "at": str(current_time)}

    @staticmethod
    def _time_after_record(time_plan, path, fallback):
        return copy.deepcopy(dict(fallback))

    def _combine_time_plan(self, command, time_plan, *, extra_records, code, result):
        return {"records": extra_records, "code": code, "result": result, "time_plan": time_plan}


def test_post_combat_field_stabilization_consumes_real_supply_and_reduces_bleeding():
    harness = _FieldMedicineHarness()
    command = CommandEnvelope(
        campaign_id="test", request_id="field-stabilize", actor_id="medic.test",
        command_type="jianghu_medicine_resolution", expected_revision=1,
        submitted_at="2026-09-04T00:00:00Z",
        payload={"action": "field_stabilize", "subject_ref": "patient.test"}, mode="gameplay",
    )
    result = harness._jianghu_medicine_resolution(command, {}, CampaignTime.parse("SE-0061-09-27T21:15:00"))
    assert result["code"] == "jianghu_field_stabilization_completed"
    assert result["result"]["care_scope"] == "emergency_field_stabilization_not_infirmary_care"
    assert result["result"]["elapsed_seconds"] >= 10
    assert result["result"]["bleeding_ml_per_min_after"] <= result["result"]["bleeding_ml_per_min_before"]
    ledger = result["records"]["state/martial-world/equipment-ledger.json"]
    assert ledger["person_loadouts"]["medic.test"]["items"]["supply_medical_bundle"] == 1
