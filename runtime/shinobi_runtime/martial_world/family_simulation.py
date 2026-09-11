"""Sparse deterministic family throughput and life-course transitions.

Only current household facts and unresolved pregnancy obligations persist. A
pregnancy schedules one exact birth frontier; no monthly fertility history or
life-event diary is written.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .family_life import child_identity, courtship_eligible, due_birth_at, marriage_eligible
from .life_course import natural_lifespan_years
from .people import apply_age_development, deterministic_body_mass_kg, deterministic_name
from .manpower import is_faction_member

_ROOT = Path(__file__).resolve().parents[3]
_MW = _ROOT / "game/data/martial-world"


def _cfg() -> Mapping[str, Any]:
    return json.loads((_MW / "family-life.json").read_text(encoding="utf-8"))


def _age(person: Mapping[str, Any], year: int) -> int:
    return max(0, int(year) - int(person.get("birth_year", year)))


def _usable(person: Mapping[str, Any]) -> bool:
    health = person.get("health", {}) if isinstance(person.get("health"), Mapping) else {}
    return health.get("status") != "dead"


def _fertility_slot(marriage_ref: str, cycle_months: int) -> int:
    value = int.from_bytes(hashlib.sha256((marriage_ref + ":fertility").encode("utf-8")).digest()[:8], "big")
    return value % max(1, int(cycle_months))


def _child_ref(marriage_ref: str, conception_at: str) -> str:
    digest = hashlib.sha256((marriage_ref + "\0" + conception_at).encode("utf-8")).hexdigest()[:24]
    return f"mw.child.{digest}"



def advance_npc_relationships(
    family: Mapping[str, Any], social: Mapping[str, Any], *, faction_ref: str,
    roster_people: Sequence[Mapping[str, Any]], at_iso: str, player_ref: str | None = None,
    residence_ref: str | None = None, exclude_refs: Sequence[str] = (),
    site_rows: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Advance sparse deterministic NPC courtship/marriage.

    Existing reciprocal social evidence is preferred. Ordinary demographic
    family formation may also select one compatible pair from actual local
    faction members when the faction is below its long-run partnered share.
    Only that causal pair receives relationship/courtship state; the runtime
    never materializes an all-pairs social graph.
    """
    family_after = copy.deepcopy(dict(family))
    social_after = copy.deepcopy(dict(social))
    relationships = social_after.get("relationships", {}) if isinstance(social_after.get("relationships"), Mapping) else {}
    courtships = social_after.setdefault("courtships", {})
    marriages = family_after.setdefault("marriages", {})
    households = family_after.setdefault("households", {})
    if not isinstance(courtships, dict) or not isinstance(marriages, dict) or not isinstance(households, dict):
        raise ValueError("jianghu family/social owner invalid")
    excluded = {str(ref) for ref in exclude_refs if isinstance(ref, str)}
    all_people = {
        str(p.get("person_id")): p for p in roster_people
        if isinstance(p, Mapping) and isinstance(p.get("person_id"), str) and _usable(p)
    }
    # Commitments temporarily remove a person from relationship progression but
    # do not destroy an existing courtship.  Keep existence and availability
    # separate so travel/deployment cannot look like death or disappearance.
    people = {ref: p for ref, p in all_people.items() if ref not in excluded}

    def physical_place(person: Mapping[str, Any]) -> str:
        # Roster hydration supplies the faction home site when no explicit
        # location deviation exists.  Normalize local sites to their parent
        # place so different buildings in one settlement remain co-located,
        # while travel to another settlement cannot progress a relationship.
        ref = str(person.get("location_ref") or residence_ref or person.get("home_place_ref") or "")
        if ref and isinstance(site_rows, Mapping):
            site = site_rows.get(ref)
            if isinstance(site, Mapping) and isinstance(site.get("parent_place_ref"), str):
                return str(site["parent_place_ref"])
        return ref

    def co_located(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        a_place, b_place = physical_place(a), physical_place(b)
        return bool(a_place and b_place and a_place == b_place)

    married_refs: set[str] = set()
    for row in marriages.values():
        if not isinstance(row, Mapping) or row.get("status") != "married":
            continue
        for ref in row.get("spouse_refs", []):
            if isinstance(ref, str): married_refs.add(ref)
    now = datetime.fromisoformat(at_iso)
    married: list[str] = []
    started: list[str] = []

    # Existing courtships resolve first, but never on the same timestamp they
    # began.  This preserves a real causal interval and avoids instant marriage.
    for pair_ref in sorted(list(courtships)):
        raw = courtships.get(pair_ref)
        if not isinstance(raw, Mapping) or raw.get("status") != "active":
            continue
        refs = [str(x) for x in raw.get("person_refs", []) if isinstance(x, str)]
        if len(refs) != 2:
            courtships.pop(pair_ref, None)
            continue
        if any(ref in married_refs for ref in refs):
            courtships.pop(pair_ref, None)
            continue
        # Courtships are a global social owner while this monthly reducer is
        # faction-local. A pair belonging to another faction is therefore not
        # missing or invalid merely because neither person is in this roster.
        # Only the faction that currently contains both participants may
        # progress that courtship; global death/transition cleanup owns removal
        # when an exact person truly ceases to be a valid participant.
        if any(ref not in all_people for ref in refs):
            continue
        if any(ref not in people for ref in refs) or (player_ref and player_ref in refs):
            continue
        try:
            started_at = datetime.fromisoformat(str(raw.get("started_at", "")))
        except ValueError:
            started_at = now
        if started_at >= now:
            continue
        a, b = people[refs[0]], people[refs[1]]
        if not co_located(a, b):
            continue
        ab = relationships.get(f"{refs[0]}|{refs[1]}", {}) if isinstance(relationships, Mapping) else {}
        ba = relationships.get(f"{refs[1]}|{refs[0]}", {}) if isinstance(relationships, Mapping) else {}
        affection = min(int(ab.get("affection", 0)) if isinstance(ab, Mapping) else 0, int(ba.get("affection", 0)) if isinstance(ba, Mapping) else 0)
        trust = min(int(ab.get("trust", 0)) if isinstance(ab, Mapping) else 0, int(ba.get("trust", 0)) if isinstance(ba, Mapping) else 0)
        age_a, age_b = _age(a, now.year), _age(b, now.year)
        if not marriage_eligible(age_a=age_a, age_b=age_b, mutual_consent=(trust >= 25 and affection >= 30), relationship_stage="courtship"):
            continue
        mid = "marriage:" + hashlib.sha256(pair_ref.encode("utf-8")).hexdigest()[:20]
        marriages[mid] = {
            "spouse_refs": sorted(refs), "status": "married", "faction_ref": faction_ref,
            "started_at": at_iso,
        }
        head = min(refs, key=lambda ref: (-_age(people[ref], now.year), ref))
        hid = "household:" + hashlib.sha256((faction_ref + "|" + pair_ref).encode("utf-8")).hexdigest()[:20]
        households.setdefault(hid, {
            "faction_ref": faction_ref, "head_ref": head, "member_refs": sorted(refs),
            "residence_ref": residence_ref or str(people[head].get("location_ref") or ""), "status": "active",
        })
        # Marriage is now the authoritative current family fact.  Keeping the
        # resolved courtship beside it would turn social state into an
        # append-only relationship-history ledger, so the temporary owner is
        # removed at the same frontier that creates the marriage.
        courtships.pop(pair_ref, None)
        married_refs.update(refs); married.append(mid)

    # Prefer already-materialized reciprocal social evidence.
    reciprocal_pairs: set[tuple[str, str]] = set()
    if isinstance(relationships, Mapping):
        for key in relationships:
            if not isinstance(key, str) or "|" not in key:
                continue
            a, b = key.split("|", 1)
            if a == b or a not in people or b not in people:
                continue
            if f"{b}|{a}" in relationships:
                reciprocal_pairs.add(tuple(sorted((a, b))))
    active_courtship_refs: set[str] = set()
    for raw in courtships.values():
        if not isinstance(raw, Mapping) or raw.get("status") != "active":
            continue
        refs = raw.get("person_refs", [])
        if isinstance(refs, list):
            active_courtship_refs.update(str(ref) for ref in refs if isinstance(ref, str))

    for a_ref, b_ref in sorted(reciprocal_pairs):
        if player_ref and player_ref in {a_ref, b_ref}:
            continue
        if a_ref in married_refs or b_ref in married_refs or a_ref in active_courtship_refs or b_ref in active_courtship_refs:
            continue
        pair_ref = "|".join((a_ref, b_ref))
        a, b = people[a_ref], people[b_ref]
        if not co_located(a, b):
            continue
        ab, ba = relationships.get(f"{a_ref}|{b_ref}", {}), relationships.get(f"{b_ref}|{a_ref}", {})
        if not courtship_eligible(
            age_a=_age(a, now.year), age_b=_age(b, now.year),
            affection_ab=int(ab.get("affection", 0)) if isinstance(ab, Mapping) else 0,
            affection_ba=int(ba.get("affection", 0)) if isinstance(ba, Mapping) else 0,
            trust_ab=int(ab.get("trust", 0)) if isinstance(ab, Mapping) else 0,
            trust_ba=int(ba.get("trust", 0)) if isinstance(ba, Mapping) else 0,
        ):
            continue
        courtships[pair_ref] = {"person_refs": [a_ref, b_ref], "status": "active", "started_at": at_iso}
        started.append(pair_ref)
        break

    # Sparse demographic family formation closes the old structural starvation:
    # ordinary people do not require a pre-existing persistent relationship edge
    # merely to have a chance to meet a compatible faction peer.  We materialize
    # at most one pair per faction/month and only while below a configured target.
    if not started:
        demo = _cfg().get("demographic_family_formation", {})
        if not isinstance(demo, Mapping):
            demo = {}
        min_age = int(_cfg().get("courtship", {}).get("minimum_age", 16))
        max_gap = max(0, int(demo.get("maximum_age_gap_years", 18)))
        # The configured target is a share of the faction's adult cohort, not
        # a share of whoever happens to remain unmarried this month.  Using the
        # shrinking unmarried pool as the denominator makes a nominal 50%
        # paired-adult target converge around one third instead.  Temporary
        # commitments also must not shrink the demographic target, so the
        # cohort is counted from all locally owned usable adults while only
        # currently available unpaired adults may become new candidates.
        adult_refs = [
            ref for ref, person in all_people.items()
            if (not player_ref or ref != player_ref) and _age(person, now.year) >= min_age
            and person.get("sex") in {"male", "female"}
        ]
        eligible_refs = [
            ref for ref in adult_refs
            if ref in people and ref not in married_refs and ref not in active_courtship_refs
        ]
        target_share = int(demo.get("target_paired_adult_share_milli", 500))
        if faction_ref in set(str(x) for x in demo.get("low_family_faction_refs", []) if isinstance(x, str)):
            target_share = int(demo.get("low_family_target_share_milli", 80))
        bounded_share = max(0, min(1000, target_share))
        desired_paired_adults = len(adult_refs) * bounded_share // 1000
        current_paired_adults = sum(
            1 for ref in adult_refs if ref in married_refs or ref in active_courtship_refs
        )
        deficit = (max(0, desired_paired_adults - current_paired_adults) + 1) // 2
        if deficit > 0 and len(eligible_refs) >= 2:
            gate = min(
                int(demo.get("monthly_max_start_permille", 550)),
                int(demo.get("monthly_base_start_permille", 120))
                + deficit * int(demo.get("monthly_deficit_bonus_permille_per_pair", 12)),
            )
            gate_roll = int.from_bytes(hashlib.sha256(f"courtship-gate|{faction_ref}|{now.year}|{now.month}".encode()).digest()[:8], "big") % 1000
            if gate_roll < max(0, gate):
                parentage = family_after.get("parentage", {}) if isinstance(family_after.get("parentage"), Mapping) else {}
                def close_kin(a_ref: str, b_ref: str) -> bool:
                    a_par = set(parentage.get(a_ref, {}).get("parent_refs", [])) if isinstance(parentage.get(a_ref), Mapping) else set()
                    b_par = set(parentage.get(b_ref, {}).get("parent_refs", [])) if isinstance(parentage.get(b_ref), Mapping) else set()
                    return b_ref in a_par or a_ref in b_par or bool(a_par & b_par)
                candidates: list[tuple[int, str, str]] = []
                for i, a_ref in enumerate(sorted(eligible_refs)):
                    a = people[a_ref]
                    for b_ref in sorted(eligible_refs)[i+1:]:
                        b = people[b_ref]
                        if not co_located(a, b):
                            continue
                        if a.get("sex") == b.get("sex") or close_kin(a_ref, b_ref):
                            continue
                        if abs(_age(a, now.year) - _age(b, now.year)) > max_gap:
                            continue
                        score = int.from_bytes(hashlib.sha256(f"pair-affinity|{faction_ref}|{a_ref}|{b_ref}".encode()).digest()[:8], "big") % 1000
                        candidates.append((-score, a_ref, b_ref))
                if candidates:
                    candidates.sort()
                    _neg, a_ref, b_ref = candidates[0]
                    pair_ref = "|".join(sorted((a_ref, b_ref)))
                    # Courtship itself is the first material social event.  These
                    # bounded current values are enough for marriage review next
                    # month without creating a fabricated pre-history.
                    relationships[f"{a_ref}|{b_ref}"] = {"familiarity": 25, "trust": 25, "affection": 30, "respect": 10}
                    relationships[f"{b_ref}|{a_ref}"] = {"familiarity": 25, "trust": 25, "affection": 30, "respect": 10}
                    courtships[pair_ref] = {"person_refs": sorted((a_ref, b_ref)), "status": "active", "started_at": at_iso}
                    started.append(pair_ref)
    return {
        "family_after": family_after, "social_after": social_after,
        "courtships_started": started, "marriages_created": married,
    }

def review_conceptions(
    family: Mapping[str, Any], *, faction_ref: str, roster_people: Sequence[Mapping[str, Any]],
    at_iso: str, player_ref: str | None = None, exclude_refs: Sequence[str] = (),
    world_people: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create causally eligible deterministic pregnancies owned by one monthly faction review.

    Same-faction and cross-faction spouses are resolved from exact identities.
    Existing marriages are the consent/relationship authority. The simulation
    never creates a marriage, never autonomously conceives for a player spouse,
    and never stores failed fertility checks.
    """
    out = copy.deepcopy(dict(family))
    marriages = out.setdefault("marriages", {})
    if not isinstance(marriages, dict):
        raise ValueError("jianghu family marriages invalid")
    excluded = {str(ref) for ref in exclude_refs if isinstance(ref, str)}
    source_people = world_people if world_people is not None else roster_people
    if isinstance(source_people, Mapping):
        # Monthly settlement can supply an already-built exact-person index.
        # Consume it directly instead of rebuilding an 11k-person dictionary
        # for every faction conception review.
        people = source_people
    else:
        people = {
            str(p.get("person_id")): p for p in source_people
            if isinstance(p, Mapping) and isinstance(p.get("person_id"), str)
            and str(p.get("person_id")) not in excluded
        }
    at = datetime.fromisoformat(at_iso)
    cfg = _cfg()
    birth_cfg = cfg.get("birth", {}) if isinstance(cfg.get("birth"), Mapping) else {}
    conception_cfg = cfg.get("conception", {}) if isinstance(cfg.get("conception"), Mapping) else {}
    minimum_age = int(birth_cfg.get("minimum_maternal_age", 16))
    maximum_age = int(birth_cfg.get("maximum_maternal_age", 48))
    cycle_months = max(1, int(conception_cfg.get("deterministic_cycle_months", 18)))
    recovery_days = max(0, int(conception_cfg.get("minimum_days_after_birth", 365)))
    month_index = at.year * 12 + at.month - 1
    scheduled: list[dict[str, Any]] = []
    conceived: list[str] = []

    for marriage_ref in sorted(marriages):
        raw = marriages.get(marriage_ref)
        if not isinstance(raw, Mapping):
            continue
        marriage = copy.deepcopy(dict(raw))
        if marriage.get("status") != "married":
            continue
        affiliated_factions: list[str] = []
        single_faction = marriage.get("faction_ref")
        if isinstance(single_faction, str) and single_faction:
            affiliated_factions.append(single_faction)
        cross_factions = marriage.get("faction_refs", [])
        if isinstance(cross_factions, list):
            affiliated_factions.extend(str(x) for x in cross_factions if isinstance(x, str) and x)
        affiliated_factions = sorted(set(affiliated_factions))
        if faction_ref not in affiliated_factions:
            continue
        refs = marriage.get("spouse_refs", [])
        if not isinstance(refs, list) or len(refs) != 2 or any(not isinstance(x, str) for x in refs):
            continue
        if any(ref in excluded for ref in refs):
            continue
        if player_ref and player_ref in refs:
            continue
        if isinstance(marriage.get("pregnancy"), Mapping):
            continue
        a, b = people.get(refs[0]), people.get(refs[1])
        if not isinstance(a, Mapping) or not isinstance(b, Mapping) or not _usable(a) or not _usable(b):
            continue
        female = a if a.get("sex") == "female" else b if b.get("sex") == "female" else None
        male = a if a.get("sex") == "male" else b if b.get("sex") == "male" else None
        if female is None or male is None:
            continue
        # Marriage is not physical co-location. Spouses in different factions
        # may conceive when actually together, while deployed/separated spouses
        # do not conceive merely because a relationship record exists.
        female_location = str(female.get("location_ref") or "")
        male_location = str(male.get("location_ref") or "")
        female_faction = str(female.get("faction_ref") or "")
        male_faction = str(male.get("faction_ref") or "")
        is_cross_owner = world_people is not None and female_faction != male_faction
        if female_location and male_location and female_location != male_location:
            continue
        # Sparse same-faction people commonly omit their default home location;
        # that sparse home-default representation still means they share the faction
        # household environment. Cross-owner spouses have no such common-home
        # implication, so conception requires an explicit matching location.
        if is_cross_owner and (not female_location or female_location != male_location):
            continue
        # Exactly one monthly owner reviews a cross-faction marriage. Prefer the
        # mother's current faction because the newborn is routed there by the
        # birth frontier; stable faction order is the fallback when the mother has no faction owner.
        conception_owner = female_faction if female_faction in affiliated_factions else (affiliated_factions[0] if affiliated_factions else "")
        if conception_owner != faction_ref:
            continue
        maternal_age = _age(female, at.year)
        if maternal_age < minimum_age or maternal_age > maximum_age:
            continue
        last_birth = marriage.get("last_birth_at")
        if isinstance(last_birth, str):
            try:
                if at < datetime.fromisoformat(last_birth) + timedelta(days=recovery_days):
                    continue
            except ValueError as exc:
                raise ValueError("jianghu marriage last_birth_at invalid") from exc
        if month_index % cycle_months != _fertility_slot(marriage_ref, cycle_months):
            continue
        conception_at = at_iso
        due_at = due_birth_at(conception_at=conception_at)
        child_ref = _child_ref(marriage_ref, conception_at)
        marriage["pregnancy"] = {
            "mother_ref": str(female["person_id"]),
            "father_ref": str(male["person_id"]),
            "conceived_at": conception_at,
            "due_at": due_at,
            "child_ref": child_ref,
        }
        marriages[marriage_ref] = marriage
        scheduled.append({
            "event_id": f"family_birth_due:{child_ref}",
            "kind": "family_birth_due",
            "due_at": due_at,
            "owner_ref": faction_ref,
            "marriage_ref": marriage_ref,
            "child_ref": child_ref,
        })
        conceived.append(child_ref)
    return {"family_after": out, "one_off_events": scheduled, "conceived_refs": conceived}


def _adult_attribute_potential(aptitudes: Mapping[str, Any]) -> dict[str, int]:
    cfg = _cfg().get("childhood", {})
    if not isinstance(cfg, Mapping):
        cfg = {}
    base = max(0, int(cfg.get("adult_attribute_floor_base", 20)))
    weight = max(0, int(cfg.get("aptitude_attribute_weight_milli", 400)))
    physical = max(0, int(aptitudes.get("physical", 100)))
    cognitive = max(0, int(aptitudes.get("cognitive", 100)))
    return {
        "strength": base + physical * weight // 1000,
        "speed": base + physical * weight // 1000,
        "dexterity": base + physical * weight // 1000,
        "endurance": base + physical * weight // 1000,
        "perception": base + cognitive * weight // 1000,
        "intelligence": base + cognitive * weight // 1000,
        "willpower": base + cognitive * weight // 1000,
    }


def newborn_person(
    *, child_ref: str, mother: Mapping[str, Any], father: Mapping[str, Any], birth_at: str,
    existing_names: set[str], residence_ref: str | None,
) -> dict[str, Any]:
    sex = "male" if int.from_bytes(hashlib.sha256((child_ref + ":sex").encode()).digest()[:1], "big") % 2 == 0 else "female"
    identity = child_identity(child_id=child_ref, parent_a=mother, parent_b=father, birth_at=birth_at, sex=sex)
    name = None
    for attempt in range(64):
        proposal = deterministic_name(stable=f"{child_ref}:{attempt}", sex=sex)
        if proposal not in existing_names:
            name = proposal
            break
    if name is None:
        raise ValueError("jianghu child name space exhausted")
    aptitudes = copy.deepcopy(dict(identity["aptitudes"]))
    developed = apply_age_development(
        age=0,
        attributes=_adult_attribute_potential(aptitudes),
        martial_skills={"sword": 0, "spear": 0, "bow": 0, "unarmed": 0, "stealth_scouting": 0, "command": 0, "hidden_weapons": 0},
        professional_skills={"medicine": 0, "administration": 0, "commerce": 0, "crafting": 0, "instruction": 0},
        qi=0, qi_control=0,
    )
    at = datetime.fromisoformat(birth_at)
    person: dict[str, Any] = {
        "person_id": child_ref,
        "name": name,
        "birth_year": at.year,
        "sex": sex,
        "body_mass_kg": deterministic_body_mass_kg(stable=child_ref, sex=sex, age=0),
        "appearance": int(identity["appearance"]),
        "aptitudes": aptitudes,
        "attributes": developed["attributes"],
        "martial_skills": developed["martial_skills"],
        "professional_skills": developed["professional_skills"],
        "qi": 0,
        "qi_control": 0,
        # Faction family children belong to the same membership ladder. Grade
        # is rank only; age/development gates prevent juvenile deployment.
        "membership_grade": "probationary",
        "personal_cash": 0,
    }
    if residence_ref:
        person["location_ref"] = residence_ref
    return person


def resolve_birth(
    family: Mapping[str, Any], *, marriage_ref: str, child_ref: str, faction_ref: str,
    roster_people: Sequence[Mapping[str, Any]], birth_at: str,
    existing_world_names: set[str] | None = None,
) -> dict[str, Any]:
    out = copy.deepcopy(dict(family))
    marriages = out.setdefault("marriages", {})
    marriage = marriages.get(marriage_ref) if isinstance(marriages, Mapping) else None
    if not isinstance(marriage, Mapping) or marriage.get("faction_ref") != faction_ref:
        return {"family_after": out, "people_after": list(roster_people), "birth": None}
    pregnancy = marriage.get("pregnancy")
    if not isinstance(pregnancy, Mapping) or pregnancy.get("child_ref") != child_ref:
        return {"family_after": out, "people_after": list(roster_people), "birth": None}
    if datetime.fromisoformat(birth_at) < datetime.fromisoformat(str(pregnancy.get("due_at"))):
        raise ValueError("jianghu birth before due date")
    people = [copy.deepcopy(dict(p)) for p in roster_people if isinstance(p, Mapping)]
    by_ref = {str(p.get("person_id")): p for p in people if isinstance(p.get("person_id"), str)}
    mother = by_ref.get(str(pregnancy.get("mother_ref")))
    father = by_ref.get(str(pregnancy.get("father_ref")))
    marriage_after = copy.deepcopy(dict(marriage))
    marriage_after.pop("pregnancy", None)
    if not isinstance(mother, Mapping) or not isinstance(father, Mapping) or not _usable(mother):
        marriages[marriage_ref] = marriage_after
        return {"family_after": out, "people_after": people, "birth": None}
    if child_ref in by_ref:
        raise ValueError("jianghu duplicate child identity")
    households = out.setdefault("households", {})
    household_ref = next((hid for hid, row in households.items() if isinstance(row, Mapping) and row.get("faction_ref") == faction_ref and str(mother.get("person_id")) in row.get("member_refs", [])), None) if isinstance(households, Mapping) else None
    residence = None
    if isinstance(household_ref, str) and isinstance(households.get(household_ref), Mapping):
        residence = households[household_ref].get("residence_ref")
    existing_names = {str(p.get("name")) for p in people if isinstance(p.get("name"), str)}
    if existing_world_names:
        existing_names.update(str(name) for name in existing_world_names if isinstance(name, str) and name)
    child = newborn_person(
        child_ref=child_ref, mother=mother, father=father, birth_at=birth_at,
        existing_names=existing_names,
        residence_ref=str(residence) if isinstance(residence, str) else str(mother.get("location_ref") or "") or None,
    )
    people.append(child)
    parentage = out.setdefault("parentage", {})
    if isinstance(parentage, dict):
        parentage[child_ref] = {"parent_refs": [str(mother["person_id"]), str(father["person_id"])]}
    claims = out.setdefault("succession_claims", {})
    if isinstance(claims, dict):
        parent_refs = {str(mother["person_id"]), str(father["person_id"])}
        parent_is_leader = any(
            "leader" in (p.get("standing_offices", []) if isinstance(p.get("standing_offices"), list) else [])
            for p in (mother, father)
        )
        parent_is_claimant = any(
            isinstance(row, Mapping) and row.get("faction_ref") == faction_ref
            and str(row.get("person_ref")) in parent_refs
            for row in claims.values()
        )
        if parent_is_leader or parent_is_claimant:
            existing_priorities = [
                max(0, int(row.get("priority", 0))) for row in claims.values()
                if isinstance(row, Mapping) and row.get("faction_ref") == faction_ref
            ]
            claim_ref = f"claim:{child_ref}"
            claims.setdefault(claim_ref, {
                "faction_ref": faction_ref, "person_ref": child_ref,
                "priority": (max(existing_priorities) if existing_priorities else 0) + 1,
                "basis": "lineal_descendant",
            })
    if isinstance(household_ref, str) and isinstance(households, dict):
        row = copy.deepcopy(dict(households[household_ref])); members = row.setdefault("member_refs", [])
        if isinstance(members, list) and child_ref not in members:
            members.append(child_ref)
        households[household_ref] = row
    marriage_after["last_birth_at"] = birth_at
    marriages[marriage_ref] = marriage_after
    return {"family_after": out, "people_after": people, "birth": child}


def _annual_body_mass_step_kg(cfg: Mapping[str, Any], age: int) -> int:
    raw = cfg.get("maximum_annual_body_mass_gain_kg", {}) if isinstance(cfg, Mapping) else {}
    if isinstance(raw, Mapping):
        if age <= 2:
            return max(1, int(raw.get("age_0_2", 8)))
        if age <= 12:
            return max(1, int(raw.get("age_3_12", 5)))
        return max(1, int(raw.get("age_13_17", 8)))
    return max(1, int(raw or 6))


def _approach_maturation_floor(current: int, target: int, maximum_gain: int) -> int:
    """Approach a deterministic developmental floor without snapping authored truth.

    The annual life-course scheduler runs every year.  Existing authored children
    may intentionally differ from the generic identity seed, so maturation may
    raise current physical truth toward the age floor but never replace it with
    an unrelated generated value in one birthday.
    """
    current = max(0, int(current)); target = max(0, int(target))
    if current >= target:
        return current
    return min(target, current + max(1, int(maximum_gain)))


def advance_annual_life_course(
    roster_people: Sequence[Mapping[str, Any]], *, year: int, player_ref: str | None = None,
    exclude_death_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Mature children and resolve deterministic natural death once per year."""
    out: list[dict[str, Any]] = []
    matured: list[str] = []
    died: list[str] = []
    cfg = _cfg().get("childhood", {})
    death_exclusions = {str(x) for x in exclude_death_refs}
    for raw in roster_people:
        if not isinstance(raw, Mapping):
            continue
        person = copy.deepcopy(dict(raw)); ref = str(person.get("person_id") or "")
        age = _age(person, year)
        if age < 18 and person.get("sex") in {"male", "female"}:
            target_mass = deterministic_body_mass_kg(stable=ref, sex=str(person["sex"]), age=age)
            current_mass = max(1, int(person.get("body_mass_kg", target_mass)))
            new_mass = _approach_maturation_floor(
                current_mass, target_mass, _annual_body_mass_step_kg(cfg, age)
            )
            if new_mass != current_mass:
                person["body_mass_kg"] = new_mass; matured.append(ref)
            potential = _adult_attribute_potential(person.get("aptitudes", {}) if isinstance(person.get("aptitudes"), Mapping) else {})
            floor = apply_age_development(age=age, attributes=potential, martial_skills={}, professional_skills={}, qi=0, qi_control=0)["attributes"]
            attrs = copy.deepcopy(dict(person.get("attributes", {}))) if isinstance(person.get("attributes"), Mapping) else {}
            changed = False
            maximum_attribute_gain = max(1, int(cfg.get("maximum_annual_attribute_floor_gain", 7))) if isinstance(cfg, Mapping) else 7
            for key, value in floor.items():
                current_value = max(0, int(attrs.get(key, 0)))
                matured_value = _approach_maturation_floor(current_value, int(value), maximum_attribute_gain)
                if matured_value != current_value:
                    attrs[key] = matured_value; changed = True
            if changed:
                person["attributes"] = attrs
                if ref not in matured: matured.append(ref)
        health = copy.deepcopy(dict(person.get("health", {}))) if isinstance(person.get("health"), Mapping) else {}
        if health.get("status") != "dead" and ref != player_ref and ref not in death_exclusions:
            injuries = health.get("injuries", []) if isinstance(health.get("injuries"), list) else []
            severity = sum(max(0, int(row.get("severity", 0))) for row in injuries if isinstance(row, Mapping))
            health_milli = max(500, 1000 - min(500, severity * 5))
            lifespan = natural_lifespan_years(
                person_id=ref, qi=max(0, int(person.get("qi", 0))),
                qi_control=max(0, int(person.get("qi_control", 0))), health_milli=health_milli,
            )
            if age >= lifespan:
                health["status"] = "dead"; health["consciousness"] = 0
                person["health"] = health; person["standing_offices"] = []; died.append(ref)
        out.append(person)
    return {"people_after": out, "matured_refs": sorted(set(matured)), "died_refs": sorted(set(died))}


def apply_recognized_succession(
    family: Mapping[str, Any], *, faction_ref: str, roster_people: Sequence[Mapping[str, Any]], year: int,
) -> dict[str, Any]:
    """Fill a vacant hereditary leader office from current recognized claims.

    Claims do not grant stats and cannot displace a living leader. If no lawful
    adult martial claimant exists, the vacancy remains for the ordinary
    institutional office selector.
    """
    people = [copy.deepcopy(dict(p)) for p in roster_people if isinstance(p, Mapping)]
    living = {
        str(p.get("person_id")): p for p in people
        if isinstance(p.get("person_id"), str) and _usable(p)
    }
    if any(
        "leader" in (p.get("standing_offices", []) if isinstance(p.get("standing_offices"), list) else [])
        for p in living.values()
    ):
        return {"people_after": people, "successor_ref": None}
    claims = family.get("succession_claims", {}) if isinstance(family, Mapping) else {}
    candidates = [
        row for row in claims.values()
        if isinstance(row, Mapping) and row.get("faction_ref") == faction_ref
        and isinstance(row.get("person_ref"), str) and str(row.get("person_ref")) in living
        and _age(living[str(row.get("person_ref"))], int(year)) >= 16
        and is_faction_member(living[str(row.get("person_ref"))])
    ] if isinstance(claims, Mapping) else []
    candidates.sort(key=lambda row: (int(row.get("priority", 9999)), str(row.get("person_ref"))))
    if not candidates:
        return {"people_after": people, "successor_ref": None}
    successor_ref = str(candidates[0]["person_ref"])
    for idx, person in enumerate(people):
        if person.get("person_id") != successor_ref:
            continue
        offices = [
            str(x) for x in person.get("standing_offices", [])
            if isinstance(x, str) and x != "heir"
        ]
        if "leader" not in offices:
            offices.append("leader")
        person["standing_offices"] = sorted(set(offices)); people[idx] = person
        break
    return {"people_after": people, "successor_ref": successor_ref}


__all__ = [
    "advance_annual_life_course", "apply_recognized_succession", "newborn_person",
    "advance_npc_relationships", "resolve_birth", "review_conceptions",
]
