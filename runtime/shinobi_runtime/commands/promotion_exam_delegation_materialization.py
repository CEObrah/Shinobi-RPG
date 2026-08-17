"""Conserved representation upgrade for hosted-exam home-village delegates.

A hosted exam may need an exact candidate even when the home village's Genin
exist only inside its aggregate shinobi-service population.  This module turns
an already-existing anonymous service member into an exact Genin without
changing physical population or aggregate rank headcount.  The selection is a
home-village institutional representation step, not a new birth, recruitment,
or promotion.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache
from shinobi_runtime.sim.events import CampaignTime

_POPULATION = "state/population/registry.json"
_CHAR_INDEX = "state/index/owners/char.json"
_OWNER_INDEX = "state/index/owners.json"
_FORCE_INDEX = "state/index/owners/force.json"
_CAREER = "state/reg/shinobi-career-pipeline.json"

_FIRST_NAMES = (
    "Ren", "Aya", "Toma", "Mika", "Jun", "Sora", "Nao", "Rin",
    "Daichi", "Hana", "Rei", "Kota", "Emi", "Yori", "Akira", "Mio",
)
_LAST_NAMES = (
    "Arai", "Mori", "Seki", "Ishida", "Kano", "Hoshino", "Ueda", "Nakai",
    "Asano", "Kido", "Mizuno", "Fujita", "Ono", "Tani", "Kume", "Sano",
)
_VILLAGE_PACKAGE = {
    "konoha": "PKG_KONOHA_FIELD_CORE",
    "suna": "PKG_SUNA_FIELD_CORE",
    "iwa": "PKG_IWA_FIELD_CORE",
    "kumo": "PKG_KUMO_FIELD_CORE",
    "kiri": "PKG_KIRI_FIELD_CORE",
    "ame": "PKG_AME_FIELD_CORE",
    "kusa": "PKG_KUSA_FIELD_CORE",
    "taki": "PKG_TAKI_FIELD_CORE",
    "oto": "PKG_OTO_FIELD_CORE",
    "yuga": "PKG_YUGA_FIELD_CORE",
}
_VILLAGE_DOMAIN = {
    "konoha": "fire", "suna": "wind", "iwa": "earth", "kumo": "lightning",
    "kiri": "water", "ame": "water", "kusa": "earth", "taki": "water",
    "oto": "wind", "yuga": "water",
}


def _record(planner: Any, path: str, record_writes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    staged = record_writes.get(path)
    if staged is not None:
        return staged
    try:
        loaded = planner.repository.read_json(path)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_delegation_population_invalid") from exc
    if not isinstance(loaded, dict):
        raise CommandRejectedError("promotion_exam_delegation_population_invalid")
    staged = copy.deepcopy(loaded)
    record_writes[path] = staged
    return staged


def _stable_digest(cycle_id: str, village: str, slot: int) -> bytes:
    return hashlib.sha256(f"promotion-exam-delegate|{cycle_id}|{village}|{slot}".encode("utf-8")).digest()


def _jitter(digest: bytes, index: int, base: int, spread: int = 5, *, low: int = 1, high: int = 100) -> int:
    delta = int(digest[index % len(digest)] % (spread * 2 + 1)) - spread
    return max(low, min(high, int(base) + delta))


def _force_capability(planner: Any, delegation: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    force_ref = delegation.get("force_ref")
    if not isinstance(force_ref, str) or not force_ref:
        raise CommandRejectedError("promotion_exam_rules_invalid")
    try:
        force_index = planner.repository.read_json(_FORCE_INDEX)
        force_path = force_index["owners"][force_ref]
        force = planner.repository.read_json(force_path)
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise CommandRejectedError("promotion_exam_delegation_force_invalid") from exc
    if force.get("id") != force_ref or force.get("owner_ref") != delegation.get("selection_authority_ref"):
        raise CommandRejectedError("promotion_exam_delegation_force_invalid")
    troop_pools = force.get("troop_pools")
    if not isinstance(troop_pools, list):
        raise CommandRejectedError("promotion_exam_delegation_force_invalid")
    preferred = [row for row in troop_pools if isinstance(row, Mapping) and row.get("role") == "field_ready"]
    candidates = preferred or [row for row in troop_pools if isinstance(row, Mapping)]
    for row in candidates:
        capability_ref = row.get("capability_ref")
        if not isinstance(capability_ref, str) or not capability_ref:
            continue
        try:
            capability = planner.repository.read_json(capability_ref)
        except (FileNotFoundError, ValueError):
            continue
        ranks = capability.get("rank_distribution") if isinstance(capability, Mapping) else None
        genin = ranks.get("genin", 0) if isinstance(ranks, Mapping) else 0
        if isinstance(genin, int) and not isinstance(genin, bool) and genin > 0:
            loadout = row.get("loadout")
            return dict(capability), str(loadout) if isinstance(loadout, str) else "loadout_minor_village_field"
    raise CommandRejectedError("promotion_exam_delegation_genin_capability_missing")


def _exact_genin(
    *,
    ref: str,
    village: str,
    home_place_ref: str,
    at: CampaignTime,
    digest: bytes,
    capability: Mapping[str, Any],
    loadout_ref: str,
    calibration: Mapping[str, Any],
) -> dict[str, Any]:
    label = village.upper() if village == "oto" else village.title()
    first = _FIRST_NAMES[digest[0] % len(_FIRST_NAMES)]
    last = _LAST_NAMES[digest[1] % len(_LAST_NAMES)]
    age = 14 + digest[2] % 5
    birth_year = max(1, at.year - age)
    month = 1 + digest[3] % 12
    day = 1 + digest[4] % 28
    stats = capability.get("stats") if isinstance(capability, Mapping) else None
    attrs0 = stats.get("attributes") if isinstance(stats, Mapping) else {}
    chakra0 = stats.get("chakra") if isinstance(stats, Mapping) else {}
    skills0 = stats.get("skills") if isinstance(stats, Mapping) else {}
    methods0 = stats.get("methods") if isinstance(stats, Mapping) else {}
    combat = int(attrs0.get("combat", 50))
    awareness = int(attrs0.get("awareness", 50))
    endurance = int(attrs0.get("endurance", 50))
    movement = int(skills0.get("movement", combat))
    tactics = int(skills0.get("tactics", awareness))
    team = int(skills0.get("team_coordination", combat))
    discipline = int(stats.get("discipline", 50)) if isinstance(stats, Mapping) else 50
    morale = int(stats.get("morale", 50)) if isinstance(stats, Mapping) else 50
    # Field-ready capability describes the whole pool, not Genin specifically.
    # Exactified Genin remain below those aggregate means on average, but use
    # the same open capability scale as exact characters. Calibration is live
    # promotion-exam policy, not a hidden materializer constant.
    offset = calibration.get("force_baseline_offset")
    spread = calibration.get("spread")
    minimum = calibration.get("minimum")
    maximum = calibration.get("maximum")
    if (
        isinstance(offset, bool) or not isinstance(offset, int)
        or isinstance(spread, bool) or not isinstance(spread, int) or spread < 0
        or isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0
        or isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= minimum
    ):
        raise CommandRejectedError("promotion_exam_rules_invalid")

    def g(base: int, i: int, floor: int = 20) -> int:
        low = max(int(minimum), floor)
        return _jitter(
            digest, i, max(low, base + int(offset)), int(spread),
            low=low, high=int(maximum),
        )
    attrs = {
        "agility": g(movement, 5), "awareness": g(awareness, 6),
        "composure": g(discipline, 7), "coordination": g(combat, 8),
        "endurance": g(endurance, 9), "intelligence": g(tactics, 10),
        "presence": g(morale, 11), "strength": g(combat, 12), "toughness": g(endurance, 13),
    }
    control = int(chakra0.get("control", 50)); output = int(chakra0.get("output", 50))
    chakra = {
        "casting_stability": g(control, 14), "control": g(control, 15),
        "efficiency": g(control, 16), "hand_seal_speed": g(movement, 17),
        "multitasking": g(tactics, 18), "output": g(output, 19),
        "sensing": g(int(methods0.get("sensory", awareness)), 20), "suppression": g(control, 21),
    }
    method = lambda key, i: g(int(methods0.get(key, combat)), i, 15)
    martial = {
        "bow": method("bow", 22), "grappling": method("unarmed", 23),
        "heavy_weapon": method("heavy_weapon", 24), "movement": g(movement, 25),
        "polearm": method("polearm", 26), "staff": method("polearm", 27),
        "stealth": g(tactics, 28), "sword": method("sword", 29),
        "thrown_tools": method("thrown_tools", 30), "unarmed": method("unarmed", 31),
    }
    operational = {
        "infiltration": g(tactics, 7), "investigation": g(awareness, 8),
        "leadership": g(team, 9), "medicine": method("medical", 10),
        "survival": g(endurance, 11), "tactics": g(tactics, 12),
        "team_coordination": g(team, 13), "tracking": g(awareness, 14),
        "traps": method("traps", 15),
    }
    domains = {k: 0 for k in (
        "barrier", "earth", "fire", "genjutsu", "ice_release", "lightning", "medical", "raw_chakra", "sealing", "sensory", "summoning", "water", "wind", "yang", "yin"
    )}
    focus = _VILLAGE_DOMAIN.get(village, "raw_chakra")
    ninjutsu = int(methods0.get("ninjutsu", output))
    domains[focus] = g(ninjutsu, 30, 15)
    domains["raw_chakra"] = g(output, 31, 15)
    domains["genjutsu"] = g(int(methods0.get("genjutsu", control)), 3, 10)
    domains["sensory"] = g(int(methods0.get("sensory", awareness)), 4, 10)
    domains["sealing"] = g(int(methods0.get("sealing", control)), 5, 10)
    health_capacity = 72 + attrs["toughness"] // 2
    fatigue_capacity = 52 + attrs["endurance"] // 2
    chakra_capacity = 55 + chakra["output"] // 2 + chakra["efficiency"] // 4
    academy_pkg = f"PKG_{village.upper()}_ACADEMY_CORE"
    field_pkg = _VILLAGE_PACKAGE.get(village)
    packages = [academy_pkg] + ([field_pkg] if field_pkg else [])
    return {
        "schema": "shinobi_character",
        "owner_id": ref,
        "owner_type": "character",
        "name": f"{first} {last}",
        "birth_date": f"SE-{birth_year:04d}-{month:02d}-{day:02d}",
        "body": {"adult_height_cm": 158 + digest[5] % 25, "growth_end_age": 18, "current_weight_kg": 48 + digest[6] % 22, "frame": "athletic"},
        "appearance": 40 + digest[7] % 41,
        "repertoire": {
            "packages": packages,
            "package_mastery": {pkg: _jitter(digest, 8+i, 48, 7, low=25, high=70) for i, pkg in enumerate(packages)},
            "latent_or_locked_techniques": [], "bloodlines": [], "method_mastery": {},
        },
        "life_status": "alive",
        "official_rank_or_status": "genin",
        "village_or_affiliation": label,
        "career_state": {"current_rank_or_status": "genin", "current_assignment_or_office": "home-village Genin service", "promotion_eligible": True, "retirement_eligible": False},
        "condition": {"disabilities": [], "illness": None, "injuries": [], "poison": None, "readiness": "ready"},
        "current_location_id": home_place_ref,
        "life_course_state": {
            "annual_reviews": 0, "career_changes": 0, "career_stage": "active_service",
            "deployment": {"home_location_id": home_place_ref, "return_at": None, "status": "home_or_saved_assignment"},
            "injury_events": [], "location_changes": 0,
            "location_history": [{"at": str(at), "location_id": home_place_ref, "reason": "exact representation established for home-village Chunin Exam selection"}],
            "persistent_injuries": 0,
            "rank_history": [{"at": str(at), "rank": "genin", "reason": "pre-existing aggregate Genin service represented exactly for home-village selection"}],
            "relationship_events": [], "status_history": [],
        },
        "attributes": attrs, "chakra_dimensions": chakra, "domain_proficiencies": domains,
        "martial_skills": martial, "operational_skills": operational,
        "resources": {
            "chakra": {"current": chakra_capacity, "capacity": chakra_capacity},
            "fatigue": {"current": 0, "capacity": fatigue_capacity},
            "health": {"current": health_capacity, "capacity": health_capacity},
            "strain": {"current": 0, "safe_capacity": max(40, fatigue_capacity - 10)},
        },
        "equipment_loadout_id": loadout_ref,
        "equipment_mode": "loadout_plus_exceptions",
        "resolution_tier": "operational_campaign_lite",
        "canon_status": "campaign_generated_from_aggregate_population",
        "roles": ["genin", "home_village_exam_delegate"],
        "background": {"origin": label, "career": "Genin", "service_history": "Selected by home-village authority from the already-existing promotion-eligible aggregate Genin service cohort."},
    }

def exactify_home_village_genin(
    planner: Any,
    *,
    delegation: Mapping[str, Any],
    cycle_id: str,
    at: CampaignTime,
    record_writes: dict[str, dict[str, Any]],
    count: int,
    calibration: Mapping[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise CommandRejectedError("promotion_exam_delegation_size_invalid")
    if count == 0:
        return []
    village = delegation.get("service_village")
    pool_id = delegation.get("service_pool_ref")
    home_place = delegation.get("home_place_ref")
    authority_ref = delegation.get("selection_authority_ref")
    force_ref = delegation.get("force_ref")
    if any(not isinstance(v, str) or not v for v in (village, pool_id, home_place, authority_ref, force_ref)):
        raise CommandRejectedError("promotion_exam_rules_invalid")

    registry = _record(planner, _POPULATION, record_writes)
    career = _record(planner, _CAREER, record_writes)
    char_index = _record(planner, _CHAR_INDEX, record_writes)
    owner_index = _record(planner, _OWNER_INDEX, record_writes)
    pools = registry.get("pools")
    transfers = registry.get("transfers")
    owners = char_index.get("owners")
    if not isinstance(pools, dict) or not isinstance(transfers, list) or not isinstance(owners, dict):
        raise CommandRejectedError("promotion_exam_delegation_population_invalid")
    pool = pools.get(pool_id)
    if (
        not isinstance(pool, dict)
        or pool.get("category") != "shinobi_service"
        or pool.get("owner_ref") != authority_ref
        or pool.get("linked_force_ref") != force_ref
    ):
        raise CommandRejectedError("promotion_exam_delegation_population_invalid")
    villages = career.get("villages")
    village_career = villages.get(str(village).lower()) if isinstance(villages, Mapping) else None
    rank_counts = village_career.get("rank_counts") if isinstance(village_career, Mapping) else None
    genin_count = rank_counts.get("genin") if isinstance(rank_counts, Mapping) else None
    if (
        not isinstance(village_career, Mapping)
        or village_career.get("service_pool_ref") != pool_id
        or village_career.get("force_ref") != force_ref
        or isinstance(genin_count, bool)
        or not isinstance(genin_count, int)
        or genin_count < count
    ):
        raise CommandRejectedError("promotion_exam_delegation_genin_population_insufficient")
    capability, loadout_ref = _force_capability(planner, delegation)
    representation = pool.get("representation")
    if not isinstance(representation, dict):
        raise CommandRejectedError("promotion_exam_delegation_population_invalid")
    anonymous = representation.get("anonymous_count")
    rostered = representation.get("rostered_count")
    rostered_refs = representation.get("rostered_person_refs")
    total = pool.get("count")
    if (
        isinstance(anonymous, bool) or not isinstance(anonymous, int) or anonymous < count
        or isinstance(rostered, bool) or not isinstance(rostered, int) or rostered < 0
        or not isinstance(rostered_refs, list)
        or isinstance(total, bool) or not isinstance(total, int) or total < 0
        or anonymous + rostered != total
    ):
        raise CommandRejectedError("promotion_exam_delegation_population_insufficient")

    created: list[tuple[str, dict[str, Any]]] = []
    existing_paths = set(owners.values())
    slot = 0
    while len(created) < count:
        digest = _stable_digest(cycle_id, village.lower(), slot)
        token = digest.hex()[:12]
        ref = f"char.exam_{village.lower()}_{token}"
        path = f"state/char/exam-{village.lower()}-{token}.json"
        slot += 1
        if ref in owners or path in existing_paths or path in record_writes:
            continue
        exact = _exact_genin(
            ref=ref,
            village=village.lower(),
            home_place_ref=home_place,
            at=at,
            digest=digest,
            capability=capability,
            loadout_ref=loadout_ref,
            calibration=calibration,
        )
        record_writes[path] = exact
        owners[ref] = path
        existing_paths.add(path)
        created.append((ref, exact))

    refs = [ref for ref, _ in created]
    representation["anonymous_count"] = anonymous - count
    representation["rostered_count"] = rostered + count
    representation["rostered_person_refs"] = sorted([*rostered_refs, *refs])
    pool["last_changed_at"] = str(at)
    owner_count = owner_index.get("owner_count")
    if isinstance(owner_count, bool) or not isinstance(owner_count, int) or owner_count < 0:
        raise CommandRejectedError("promotion_exam_delegation_population_invalid")
    owner_index["owner_count"] = owner_count + count
    transfer_id = "exam_delegate_exactification." + hashlib.sha256(
        f"{cycle_id}|{village}|{'|'.join(refs)}".encode("utf-8")
    ).hexdigest()[:24]
    transfers.append({
        "id": transfer_id,
        "at": str(at),
        "source_pool_id": pool_id,
        "destination_ref": str(delegation["delegation_ref"]),
        "requested_count": count,
        "accepted": count,
        "rejected": 0,
        "authority_ref": authority_ref,
        "authority_basis": "home_village_chunin_exam_selection",
        "policy_ref": "promotion_exam.hosted_common_delegation",
        "method": "representation_exactification_from_existing_genin_service",
        "accepted_profile": {
            "numeric_distributions": {},
            "category_counts": {"shinobi_service": count},
            "dimension_counts": {},
            "tags": ["representation_exactification", "chunin_exam_delegate"],
        },
        "materialized_person_ids": refs,
        "source_removed": 0,
        "destination_added": 0,
        "selection_note": "Home-village selection exactified already-existing anonymous Genin. Physical population and aggregate rank headcount are unchanged.",
    })
    trim = getattr(planner, "_trim_population_transfer_history", None)
    if callable(trim):
        trim(transfers)
    if representation["anonymous_count"] + representation["rostered_count"] != total:
        raise CommandRejectedError("promotion_exam_delegation_population_conservation_failed")
    return created


__all__ = ["exactify_home_village_genin"]
