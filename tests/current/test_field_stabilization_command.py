import copy

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.jianghu_extended import JianghuExtendedCommandsMixin
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.martial_world.physical_presence import same_effective_location
from shinobi_runtime.api.contracts import CommandRejectedError
import pytest


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


class _StrandedRepo(_Repo):
    def __init__(self, ledger, *, separated=()):
        super().__init__(ledger)
        self.route = {
            "schema": "jianghu-route-operations-state-1.0",
            "movements": {
                "move.stranded": {
                    "status": "stranded",
                    "movement_kind": "player_strategic_travel",
                    "route_ref": "route.test",
                    "participant_refs": ["medic.test", "patient.test"],
                    "separated_person_refs": list(separated),
                }
            },
            "contacts": {},
        }

    def read_json(self, path):
        if path == "state/martial-world/route-operations.json":
            return copy.deepcopy(self.route)
        if path in {"state/martial-world/combats.json", "state/martial-world/custody.json"}:
            raise FileNotFoundError(path)
        return super().read_json(path)


class _StrandedFieldMedicineHarness(_FieldMedicineHarness):
    def __init__(self, *, separated=()):
        super().__init__()
        self.repository = _StrandedRepo(self.repository.ledger, separated=separated)

    def _same_effective_location(self, left_ref, right_ref):
        people = {row["person_id"]: row for row in self.roster["people"]}
        return same_effective_location(
            self.repository.read_json, left_ref, right_ref,
            left_person=people[left_ref], right_person=people[right_ref],
        )


def test_stranded_same_cohort_can_receive_field_stabilization_without_route_soft_lock():
    harness = _StrandedFieldMedicineHarness()
    command = CommandEnvelope(
        campaign_id="test", request_id="field-stabilize-stranded", actor_id="medic.test",
        command_type="jianghu_medicine_resolution", expected_revision=1,
        submitted_at="2026-09-10T00:00:00Z",
        payload={"action": "field_stabilize", "subject_ref": "patient.test"}, mode="gameplay",
    )
    result = harness._jianghu_medicine_resolution(
        command, {}, CampaignTime.parse("SE-0061-09-27T21:22:50")
    )
    assert result["code"] == "jianghu_field_stabilization_completed"


def test_stranded_separated_escapee_cannot_be_treated_remotely_by_main_party():
    harness = _StrandedFieldMedicineHarness(separated=("patient.test",))
    command = CommandEnvelope(
        campaign_id="test", request_id="field-stabilize-separated", actor_id="medic.test",
        command_type="jianghu_medicine_resolution", expected_revision=1,
        submitted_at="2026-09-10T00:00:00Z",
        payload={"action": "field_stabilize", "subject_ref": "patient.test"}, mode="gameplay",
    )
    with pytest.raises(CommandRejectedError) as caught:
        harness._jianghu_medicine_resolution(
            command, {}, CampaignTime.parse("SE-0061-09-27T21:22:50")
        )
    assert caught.value.code == "jianghu_field_medicine_subject_not_present"
