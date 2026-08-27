from datetime import datetime

from shinobi_runtime.martial_world.clinical_physiology import (
    prepare_patient_for_treatment,
    rebase_treated_patient_wakes,
)
from shinobi_runtime.martial_world.institutional_lifecycle import apply_autonomous_clinical_treatment
from shinobi_runtime.martial_world.physiology_frontier import next_physiology_event, settle_person_physiology_event


def _doctor():
    return {
        "person_id": "doctor", "location_ref": "site.test", "health": {"status": "ready"},
        "attributes": {"dexterity": 100, "intelligence": 100, "perception": 100},
        "professional_skills": {"medicine": 120},
    }


def _patient():
    return {
        "person_id": "patient", "location_ref": "site.test", "body_mass_kg": 70,
        "attributes": {"endurance": 60, "willpower": 60},
        "health": {
            "status": "injured", "blood_lost_ml": 250, "shock": 10, "consciousness": 100,
            "injuries": [{
                "zone": "forearm", "created_at": "0061-10-12T08:00:00", "cut": 80,
                "pierce": 0, "blunt": 0, "penetration": 0, "severity": 80,
                "bleeding_ml_per_min": 0, "fracture": 0, "tendon_damage": 0,
                "nerve_damage": 0, "organ_trauma": 0, "pain": 60,
                "function_loss_pct": 15, "treated": False, "healing_progress_milli": 0,
            }],
        },
    }


def test_autonomous_treatment_rebases_existing_patient_wake_before_dose():
    patient = _patient()
    old = next_physiology_event("patient", patient, now="0061-10-12T08:00:00")
    assert old is not None
    at = datetime.fromisoformat("0061-10-13T21:15:00")
    schedule = {"one_off": {old["event_id"]: old}}

    def prepare(pid, person):
        return prepare_patient_for_treatment(pid, person, schedule=schedule, pending_events=[], at=at)

    result = apply_autonomous_clinical_treatment(
        {"local_site_ref": "site.test", "buildings": {"infirmary_apothecary": 1}, "infrastructure": {}},
        {"people": [_doctor(), patient]},
        {"medicines": {"wound_salve": 1}, "equipment": {"tool_physicians_kit": 1, "supply_medical_bundle": 1}},
        at_iso=at.isoformat(), treatment_stations=1, prepare_patient=prepare,
    )
    assert result["treated_refs"] == ["patient"]
    after = next(row for row in result["roster"]["people"] if row["person_id"] == "patient")
    assert after["medicine_state"]["last_settled_at"] == at.isoformat()
    assert result["physiology_rebases"]["patient"]["event_id"] == "person_physiology_due:patient"

    def load_person(_ref):
        return (None, None, None, None, after)

    rebased = rebase_treated_patient_wakes(
        result["physiology_rebases"], schedule=schedule, pending_events=[], at=at, load_person=load_person,
    )
    assert old["event_id"] not in rebased["schedule_after"]["one_off"]
    replacement = next(row for row in rebased["pending_events_after"] if row["event_id"] == old["event_id"])
    assert replacement["last_settled_at"] == at.isoformat()
    settled = settle_person_physiology_event(after, replacement, at=replacement["due_at"])
    assert settled["person_after"]["medicine_state"]["last_settled_at"] == replacement["due_at"]


def test_same_frontier_pending_replacement_beats_stale_scheduler_event():
    patient = _patient()
    old = next_physiology_event("patient", patient, now="0061-10-12T08:00:00")
    at = datetime.fromisoformat("0061-10-13T21:15:00")
    already = settle_person_physiology_event(patient, old, at=at)
    pending = already["next_event"]
    assert pending is not None and pending["last_settled_at"] == at.isoformat()
    prepared = prepare_patient_for_treatment(
        "patient", already["person_after"], schedule={"one_off": {old["event_id"]: old}}, pending_events=[pending], at=at,
    )
    assert prepared["person_after"]["health"] == already["person_after"]["health"]
    assert prepared["recovery_carry_minutes"] == pending["recovery_carry_minutes"]


def test_autonomous_antidote_treats_pending_pre_onset_poison():
    from shinobi_runtime.martial_world.poison import combined_poison_burdens

    patient = {
        'person_id': 'patient.poison', 'location_ref': 'site.test', 'body_mass_kg': 70,
        'qi': 50, 'qi_control': 50,
        'attributes': {'endurance': 60, 'willpower': 60},
        'health': {'status': 'ready', 'consciousness': 100, 'injuries': []},
        'pending_poison_burdens': {
            'cardiotoxic': {
                'poison_ref': 'cardiotoxic', 'burden': 40,
                'activates_at': '0061-10-13T21:15:25',
                'peaks_at': '0061-10-13T21:17:00', 'stage': 'onset',
            },
        },
    }
    result = apply_autonomous_clinical_treatment(
        {'local_site_ref': 'site.test', 'buildings': {'infirmary_apothecary': 3}, 'infrastructure': {}},
        {'people': [_doctor(), patient]},
        {'medicines': {'blood_cardiac_antidote': 1}, 'equipment': {'tool_physicians_kit': 1}},
        at_iso='0061-10-13T21:15:00', treatment_stations=1,
    )

    assert result['treated_refs'] == ['patient.poison']
    after = next(row for row in result['roster']['people'] if row['person_id'] == 'patient.poison')
    remaining = combined_poison_burdens(after.get('poison_burdens', {}), after.get('pending_poison_burdens', {})).get('cardiotoxic', 0)
    assert remaining < 40
    assert result['inventory']['medicines'].get('blood_cardiac_antidote', 0) == 0
