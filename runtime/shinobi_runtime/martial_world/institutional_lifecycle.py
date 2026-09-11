"""Bounded deterministic monthly institutional progression for Jianghu factions.

This module turns already-saved capabilities, health, membership, social trust and
institutional staffing needs into current consequences. It never grants stats,
creates people, or keeps a decision-history diary.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Mapping, Sequence

from .health import compact_current_wounds, recovery_advance, settle_physiology, wound_requires_persistence
from .membership import grade_eligibility, select_office_candidate
from .manpower import is_faction_member
from .duties import derive_duty_assignments
from .institutional_offices import ORDERED_CORE_OFFICES, required_institutional_offices
from .medicine import administer_dose, medicine_category, stabilize_wounds, treat_poison_burden, toxicity_consequences, wound_treatment_score
from .character_rules import martial_discipline_keys
from .poison import clear_poison_burden, combined_poison_burdens

_GRADES = ("probationary", "junior", "full", "senior", "elite", "elder")
_GRADE_MIN_SERVICE = {"probationary":0,"junior":30,"full":365,"senior":1095,"elite":1825,"elder":3650}
_MARTIAL = martial_discipline_keys()

def _alive(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead" and int(health.get("consciousness", 100)) > 0


def _primary_discipline(faction: Mapping[str, Any]) -> str:
    training = faction.get("training", {}) if isinstance(faction.get("training"), Mapping) else {}
    return max(_MARTIAL, key=lambda k: (int(training.get(k, 0)), -_MARTIAL.index(k)))


def _service_days(person: Mapping[str, Any], year: int) -> int:
    birth = int(person.get("birth_year", year))
    default_join = birth + 16
    joined = int(person.get("joined_year", default_join))
    joined = max(birth, joined)
    return max(0, (int(year) - joined) * 365)


def _office_service_score_days(person: Mapping[str, Any], year: int) -> int:
    """Tenure remains real elapsed service; completed missions add bounded merit only for office selection."""
    tenure = _service_days(person, year)
    record = person.get("institutional_service", {}) if isinstance(person.get("institutional_service"), Mapping) else {}
    completed = max(0, int(record.get("completed_missions", 0)))
    successful = max(0, int(record.get("successful_missions", 0)))
    commands = max(0, int(record.get("commands_completed", 0)))
    merit = min(720, completed * 7 + successful * 14 + commands * 30)
    return tenure + merit


def _office_key(value: object) -> str:
    return str(value).split(":", 1)[0]


def _trust(social: Mapping[str, Any], a: str, b: str) -> int:
    rows = social.get("relationships", {}) if isinstance(social.get("relationships"), Mapping) else {}
    ab = rows.get(f"{a}|{b}", {}) if isinstance(rows, Mapping) else {}
    ba = rows.get(f"{b}|{a}", {}) if isinstance(rows, Mapping) else {}
    av = int(ab.get("trust", 0)) if isinstance(ab, Mapping) else 0
    bv = int(ba.get("trust", 0)) if isinstance(ba, Mapping) else 0
    if av and bv:
        return min(av, bv)
    return max(av, bv)


def institutional_status(
    faction: Mapping[str, Any], roster: Mapping[str, Any], *, year: int,
    social: Mapping[str, Any], unavailable_refs: Sequence[str] = (),
) -> dict[str, int]:
    people = roster.get("people", []) if isinstance(roster, Mapping) else []
    if not isinstance(people, list):
        people = []
    living = [p for p in people if isinstance(p, Mapping) and _alive(p)]
    unavailable = {str(x) for x in unavailable_refs}
    available = [p for p in living if str(p.get("person_id", "")) not in unavailable]
    # Existing offices remain current authority during temporary unavailability;
    # custody/deployment blocks new selection rather than silently deposing an
    # officeholder.
    occupied = {_office_key(o) for p in living for o in (p.get("standing_offices", []) if isinstance(p.get("standing_offices"), list) else [])}
    required_offices = required_institutional_offices(faction, roster)
    office_vacancies = sum(1 for office, _ in required_offices if office not in occupied)
    return {"office_vacancies": office_vacancies}




def apply_autonomous_clinical_treatment(
    faction: Mapping[str, Any], roster: Mapping[str, Any], inventory: Mapping[str, Any], *,
    at_iso: str, unavailable_refs: Sequence[str] = (), treatment_stations: int = 0,
    prepare_patient: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Use real faction medicine on real current casualties within finite capacity."""
    out_roster=copy.deepcopy(dict(roster)); out_inventory=copy.deepcopy(dict(inventory))
    people=out_roster.get("people",[])
    if not isinstance(people,list) or max(0,int(treatment_stations))<=0:
        return {"roster":out_roster,"inventory":out_inventory,"treated_refs":[],"doses_used":0,"physiology_rebases":{}}
    blocked={str(x) for x in unavailable_refs}
    facility=str(faction.get("local_site_ref") or faction.get("headquarters") or "")
    physicians=[]
    for p in people:
        if not isinstance(p,Mapping) or not _alive(p) or str(p.get("person_id") or "") in blocked:
            continue
        if facility and str(p.get("location_ref") or "")!=facility:
            continue
        prof=p.get("professional_skills",{}) if isinstance(p.get("professional_skills"),Mapping) else {}
        skill=max(0,int(prof.get("medicine",0)))
        if skill>0: physicians.append((-skill,str(p.get("person_id") or ""),p))
    physicians.sort(key=lambda row:(row[0],row[1]))
    if not physicians:
        return {"roster":out_roster,"inventory":out_inventory,"treated_refs":[],"doses_used":0,"physiology_rebases":{}}
    doctor=physicians[0][2]
    dattrs=doctor.get("attributes",{}) if isinstance(doctor.get("attributes"),Mapping) else {}
    dprof=doctor.get("professional_skills",{}) if isinstance(doctor.get("professional_skills"),Mapping) else {}
    med_skill=max(0,int(dprof.get("medicine",0)))
    medicines=copy.deepcopy(dict(out_inventory.get("medicines",{}))) if isinstance(out_inventory.get("medicines"),Mapping) else {}
    equipment=copy.deepcopy(dict(out_inventory.get("equipment",{}))) if isinstance(out_inventory.get("equipment"),Mapping) else {}
    kit_available=max(0,int(equipment.get("tool_physicians_kit",0)))>0
    supply_available=max(0,int(equipment.get("supply_medical_bundle",0)))>0
    facility_level=max(0,int((faction.get("buildings",{}) if isinstance(faction.get("buildings"),Mapping) else {}).get("infirmary_apothecary",0)))
    environment=1000+facility_level*40
    candidates=[]
    for idx,p in enumerate(people):
        if not isinstance(p,Mapping) or not _alive(p): continue
        pid=str(p.get("person_id") or "")
        if pid in blocked: continue
        if facility and str(p.get("location_ref") or "") != facility: continue
        health=p.get("health",{}) if isinstance(p.get("health"),Mapping) else {}
        wounds=health.get("injuries",[]) if isinstance(health.get("injuries"),list) else []
        burdens=p.get("poison_burdens",{}) if isinstance(p.get("poison_burdens"),Mapping) else {}
        pending=p.get("pending_poison_burdens",{}) if isinstance(p.get("pending_poison_burdens"),Mapping) else {}
        poison_total=sum(combined_poison_burdens(burdens,pending).values())
        severity=sum(max(0,int(w.get("severity",0))) for w in wounds if isinstance(w,Mapping))
        if poison_total<=0 and severity<=0: continue
        candidates.append((-(poison_total*10+severity),pid,idx))
    candidates.sort()
    treated=[]; doses=0; physiology_rebases: dict[str, dict[str, Any]] = {}
    for _priority,pid,idx in candidates[:max(0,int(treatment_stations))]:
        p=copy.deepcopy(dict(people[idx]))
        prepared = prepare_patient(pid, p) if prepare_patient is not None else {
            "person_after": p, "recovery_carry_minutes": 0, "poison_clearance_carry_minutes": 0,
            "event_id": f"person_physiology_due:{pid}",
        }
        prepared_person = prepared.get("person_after") if isinstance(prepared, Mapping) else None
        if not isinstance(prepared_person, Mapping):
            raise ValueError("autonomous clinical treatment patient rebase invalid")
        p=copy.deepcopy(dict(prepared_person)); health=copy.deepcopy(dict(p.get("health",{}))) if isinstance(p.get("health"),Mapping) else {}
        burdens=copy.deepcopy(dict(p.get("poison_burdens",{}))) if isinstance(p.get("poison_burdens"),Mapping) else {}
        pending=copy.deepcopy(dict(p.get("pending_poison_burdens",{}))) if isinstance(p.get("pending_poison_burdens"),Mapping) else {}
        combined=combined_poison_burdens(burdens,pending)
        recipe=None; poison_result=None; poison_ref=None
        if combined:
            scored=[]
            attrs=p.get("attributes",{}) if isinstance(p.get("attributes"),Mapping) else {}
            for med_ref,qty in medicines.items():
                if int(qty)<=0: continue
                try:
                    if medicine_category(str(med_ref))!="antidote": continue
                except KeyError:
                    continue
                for pref,burden in combined.items():
                    if int(burden)<=0: continue
                    try:
                        tr=treat_poison_burden(
                            burden=int(burden),medicine=med_skill,intelligence=int(dattrs.get("intelligence",0)),
                            perception=int(dattrs.get("perception",0)),poison_ref=str(pref),medicine_ref=str(med_ref),
                            patient_endurance=int(attrs.get("endurance",0)),patient_qi=int(p.get("qi",0)),
                            patient_qi_control=int(p.get("qi_control",0)),facility_level=facility_level,treatment_minutes=20,
                        )
                    except KeyError:
                        continue
                    scored.append((int(tr.get("burden_cleared",0)),str(med_ref),str(pref),tr))
            if scored:
                _cleared,recipe,poison_ref,poison_result=max(scored,key=lambda row:(row[0],row[1],row[2]))
        if recipe is None:
            wounds=health.get("injuries",[]) if isinstance(health.get("injuries"),list) else []
            has_bone=any(isinstance(w,Mapping) and (int(w.get("fracture",0))>0 or int(w.get("tendon_damage",0))>0) for w in wounds)
            preferred=("bone","wound","internal") if has_bone else ("wound","internal","bone")
            for category in preferred:
                options=[]
                for med_ref,qty in medicines.items():
                    if int(qty)<=0: continue
                    try:
                        if medicine_category(str(med_ref))==category: options.append(str(med_ref))
                    except KeyError:
                        continue
                if options:
                    recipe=sorted(options)[0]; break
        if recipe is None:
            continue
        try:
            dose=administer_dose(recipe,at=at_iso,inventory=medicines,person_state=p.get("medicine_state"))
        except (KeyError,ValueError):
            continue
        medicines=dose["inventory_after"]; p["medicine_state"]=dose["medicine_state_after"]
        category=medicine_category(recipe)
        if category in {"wound","bone","internal"}:
            treatment=wound_treatment_score(
                medicine=med_skill,dexterity=int(dattrs.get("dexterity",0)),intelligence=int(dattrs.get("intelligence",0)),
                perception=int(dattrs.get("perception",0)),physician_kit=kit_available,medical_supply=supply_available,
                environment_milli=environment,treatment_minutes=30,patient_condition_milli=1000,
            )
            health=stabilize_wounds(health,treatment_score_value=int(treatment["treatment_score"]),advanced_procedure_enabled=bool(treatment["advanced_procedure_enabled"]),medical_supply_available=bool(treatment["medical_supply_available"]))
            if supply_available and int(equipment.get("supply_medical_bundle",0))>0:
                equipment["supply_medical_bundle"]=int(equipment.get("supply_medical_bundle",0))-1
                supply_available=int(equipment.get("supply_medical_bundle",0))>0
        elif category=="antidote" and poison_result is not None and poison_ref is not None:
            cleared_state=clear_poison_burden(
                active=burdens,pending=pending,poison_ref=str(poison_ref),
                amount=int(poison_result.get("burden_cleared",0)),
            )
            burdens=dict(cleared_state["active_after"]); pending=dict(cleared_state["pending_after"])
            if burdens: p["poison_burdens"]=burdens
            else: p.pop("poison_burdens",None)
            if pending: p["pending_poison_burdens"]=pending
            else: p.pop("pending_poison_burdens",None)
        consequences=toxicity_consequences(p.get("medicine_state"),at=at_iso)
        p["fatigue_milli"]=max(0,int(p.get("fatigue_milli",0))+int(consequences["fatigue_burden_points"]))
        health["toxicity_milli"]=int(consequences["toxicity_milli"]); health["shock"]=max(int(health.get("shock",0)),int(consequences["shock_contribution"]))
        p["health"]=health; people[idx]=p; treated.append(pid); doses+=1
        physiology_rebases[pid] = {
            "event_id": str(prepared.get("event_id") or f"person_physiology_due:{pid}") if isinstance(prepared, Mapping) else f"person_physiology_due:{pid}",
            "recovery_carry_minutes": max(0, int(prepared.get("recovery_carry_minutes", 0))) if isinstance(prepared, Mapping) else 0,
            "poison_clearance_carry_minutes": max(0, int(prepared.get("poison_clearance_carry_minutes", 0))) if isinstance(prepared, Mapping) else 0,
        }
    out_roster["people"]=people; out_inventory["medicines"]=medicines; out_inventory["equipment"]=equipment
    return {"roster":out_roster,"inventory":out_inventory,"treated_refs":treated,"doses_used":doses,"physiology_rebases":physiology_rebases}

def settle_institutional_offices(
    faction: Mapping[str, Any], roster: Mapping[str, Any], *, year: int,
    social: Mapping[str, Any], player_ref: str | None = None,
    unavailable_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Prune obsolete formal offices and fill current vacancies deterministically.

    This is the single appointment authority used by the monthly institutional
    lifecycle and immediate death/succession closure.  Formal office is a
    standing authority, not a time reservation; callers decide which exact
    people are ineligible for a new appointment through ``unavailable_refs``.
    """
    out = copy.deepcopy(dict(roster))
    people = out.get("people")
    if not isinstance(people, list):
        raise ValueError("jianghu roster people invalid")
    unavailable = {str(x) for x in unavailable_refs if isinstance(x, str) and x}

    required_names = {office for office, _skill in required_institutional_offices(faction, out)}
    for idx, raw in enumerate(people):
        if not isinstance(raw, Mapping):
            continue
        offices = raw.get("standing_offices", [])
        if not isinstance(offices, list) or not offices:
            continue
        kept = [] if not _alive(raw) else [
            str(office) for office in offices
            if _office_key(office) not in ORDERED_CORE_OFFICES or _office_key(office) in required_names
        ]
        if kept != offices:
            person = copy.deepcopy(dict(raw))
            person["standing_offices"] = kept
            people[idx] = person

    living = [person for person in people if isinstance(person, Mapping) and _alive(person)]
    by_id = {
        str(person["person_id"]): idx
        for idx, person in enumerate(people)
        if isinstance(person, Mapping) and isinstance(person.get("person_id"), str)
    }
    occupied = {
        _office_key(office)
        for person in living
        for office in (person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else [])
    }
    service = {
        str(person["person_id"]): _office_service_score_days(person, year)
        for person in living if isinstance(person.get("person_id"), str)
    }
    leader_refs = [
        str(person["person_id"]) for person in living
        if isinstance(person.get("person_id"), str)
        and "leader" in (person.get("standing_offices", []) if isinstance(person.get("standing_offices"), list) else [])
    ]
    trust = {
        str(person["person_id"]): (
            max((_trust(social, leader_ref, str(person["person_id"])) for leader_ref in leader_refs), default=0)
            if str(person["person_id"]) not in leader_refs else 100
        )
        for person in living if isinstance(person.get("person_id"), str)
    }
    eligible_candidates = [
        person for person in living
        if is_faction_member(person)
        and str(person.get("membership_grade")) in {"full", "senior", "elite", "elder"}
        and person.get("person_id") != player_ref
        and str(person.get("person_id", "")) not in unavailable
    ]
    appointments: list[dict[str, str]] = []
    for office, skill in required_institutional_offices(faction, out):
        if office in occupied or not eligible_candidates:
            continue
        candidate = select_office_candidate(
            eligible_candidates, relevant_skill_key=skill, service_days=service, trust=trust,
        )
        if candidate is None:
            continue
        idx = by_id[candidate]
        person = copy.deepcopy(dict(people[idx]))
        offices = list(person.get("standing_offices", [])) if isinstance(person.get("standing_offices"), list) else []
        offices.append(office)
        person["standing_offices"] = sorted(set(str(x) for x in offices))
        people[idx] = person
        occupied.add(office)
        appointments.append({"office": office, "person_ref": candidate})

    out["people"] = people
    return {"roster": out, "appointments": appointments}


def advance_institution(
    faction: Mapping[str, Any], roster: Mapping[str, Any], *, year: int,
    social: Mapping[str, Any], player_ref: str | None = None,
    unavailable_refs: Sequence[str] = (), infirmary_beds: int = 0, month: int = 1,
) -> dict[str, Any]:
    """Apply bounded current consequences and return after-images plus summary."""
    out = copy.deepcopy(dict(roster))
    people = out.get("people")
    if not isinstance(people, list):
        raise ValueError("jianghu roster people invalid")
    unavailable = {str(x) for x in unavailable_refs}
    primary = _primary_discipline(faction)
    promoted: list[str] = []
    recovered: list[str] = []
    died: list[str] = []

    # Monthly recovery consumes no history. Infirmary beds are real: the most
    # serious current casualties receive the full institutional recovery
    # interval, while overflow receives only ordinary unsupported recovery.
    severity_rows=[]
    for raw in people:
        if not isinstance(raw,Mapping):continue
        wounds=raw.get("health",{}).get("injuries",[]) if isinstance(raw.get("health"),Mapping) else []
        if not isinstance(wounds,list) or not wounds:continue
        severity=sum(max(0,int(w.get("severity",0))) for w in wounds if isinstance(w,Mapping))
        pid=str(raw.get("person_id") or "")
        severity_rows.append((-severity,pid))
    severity_rows.sort()
    admitted={pid for _sev,pid in severity_rows[:max(0,int(infirmary_beds))] if pid}
    for idx, raw in enumerate(people):
        if not isinstance(raw, Mapping):
            continue
        p = copy.deepcopy(dict(raw))
        health = copy.deepcopy(dict(p.get("health", {}))) if isinstance(p.get("health"), Mapping) else {}
        wounds = health.get("injuries", [])
        if isinstance(wounds, list) and wounds:
            physiology = settle_physiology(
                body_mass_kg=float(p.get("body_mass_kg", 70)),
                wounds=[w for w in wounds if isinstance(w, Mapping)],
                blood_lost_ml=max(0, int(health.get("blood_lost_ml", 0))),
                elapsed_seconds=0,
                endurance=int(p.get("attributes", {}).get("endurance", 0)) if isinstance(p.get("attributes"), Mapping) else 0,
                willpower=int(p.get("attributes", {}).get("willpower", 0)) if isinstance(p.get("attributes"), Mapping) else 0,
            )
            if physiology.get("lethal_state") in {"dead", "dying"}:
                health["status"] = "dead"
                health["consciousness"] = 0
                health["shock"] = max(int(health.get("shock", 0)), int(physiology.get("shock", 0)))
                health["blood_lost_ml"] = max(int(health.get("blood_lost_ml", 0)), int(physiology.get("blood_lost_ml", 0)))
                p["health"] = health
                people[idx] = p
                if isinstance(p.get("person_id"), str):
                    died.append(str(p["person_id"]))
                continue
            recovery_hours=(30*24 if str(p.get("person_id") or "") in admitted else 20*24)
            advanced = compact_current_wounds([recovery_advance(w, elapsed_hours=recovery_hours) for w in wounds if isinstance(w, Mapping)])
            remaining = [
                w for w in advanced
                if int(w.get("healing_progress_milli", 0)) < 100000 or wound_requires_persistence(w)
            ]
            acute_remaining = [w for w in remaining if not bool(w.get("healed"))]
            if remaining != wounds:
                health["injuries"] = remaining
                if not acute_remaining and health.get("status") not in {"dead"}:
                    health["status"] = "ready"
                    health["consciousness"] = max(1, int(health.get("consciousness", 100)))
                p["health"] = health
                people[idx] = p
                if isinstance(p.get("person_id"), str):
                    recovered.append(str(p["person_id"]))

    # One-grade-at-a-time promotion. Current grade is never a source of stats.
    living_member_count = sum(1 for p in people if isinstance(p, Mapping) and _alive(p))
    elder_cap = max(1, living_member_count // 50) if living_member_count >= 25 else 0
    elder_count = sum(1 for p in people if isinstance(p, Mapping) and p.get("membership_grade") == "elder" and _alive(p))
    for idx, raw in enumerate(people):
        if len(promoted) >= 12 or not isinstance(raw, Mapping):
            continue
        p = copy.deepcopy(dict(raw))
        pid = p.get("person_id")
        if not isinstance(pid, str) or pid == player_ref or pid in unavailable or not is_faction_member(p) or not _alive(p):
            continue
        grade = str(p.get("membership_grade", "probationary"))
        if grade not in _GRADES or grade == "elder":
            continue
        target = _GRADES[_GRADES.index(grade) + 1]
        check = grade_eligibility(
            p, target_grade=target, service_days=_service_days(p, year), primary_discipline=primary,
            discipline_clean=True, elder_open_seat=(elder_count < elder_cap),
        )
        if check["eligible"]:
            p["membership_grade"] = target
            people[idx] = p
            promoted.append(pid)
            if target == "elder":
                elder_count += 1

    # Formal office maintenance is shared with immediate battle-death closure,
    # so monthly progression and exact succession cannot disagree about who may
    # fill a current institutional vacancy.
    office_result = settle_institutional_offices(
        faction, out, year=year, social=social, player_ref=player_ref,
        unavailable_refs=sorted(unavailable),
    )
    out = office_result["roster"]
    people = out["people"]
    appointments = office_result["appointments"]

    # Routine work is derived for this monthly frontier only. It is not a
    # persistent person role. Exact workers still matter to production,
    # medicine, trade and administration, but their assignment is recomputed
    # from current need, skill, health and availability whenever required.
    duty_projection = derive_duty_assignments(
        faction,
        [p for p in people if isinstance(p, Mapping)],
        year=year,
        month=month,
        unavailable_refs=sorted(unavailable),
        protected_refs=([player_ref] if isinstance(player_ref, str) and player_ref else ["pc_wei_tang"]),
    )

    return {
        "roster": out,
        "summary": {
            "promoted_refs": promoted,
            "appointments": appointments,
            "recovered_refs": recovered,
            "died_refs": died,
            "duty_shortages": duty_projection["shortages"],
            "duty_assigned_counts": duty_projection["assigned_counts"],
        },
    }


__all__ = ["advance_institution", "apply_autonomous_clinical_treatment", "institutional_status", "settle_institutional_offices"]
