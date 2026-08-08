#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def write_json(rel: str, data):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(rel: str, text: str):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def add_unique(seq, value, index=None):
    if value in seq:
        return
    if index is None:
        seq.append(value)
    else:
        seq.insert(index, value)


def remove_if_present(seq, value):
    while value in seq:
        seq.remove(value)


def flatten_numeric(value, prefix=""):
    out = {}
    if isinstance(value, dict):
        for key, child in value.items():
            p = f"{prefix}.{key}" if prefix else key
            out.update(flatten_numeric(child, p))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out[prefix] = float(value)
    return out


def exact_string_refs(value, targets, found):
    if isinstance(value, dict):
        for child in value.values():
            exact_string_refs(child, targets, found)
    elif isinstance(value, list):
        for child in value:
            exact_string_refs(child, targets, found)
    elif isinstance(value, str) and value in targets:
        found.add(value)


def age_at(birth_date: str, world_time: str):
    try:
        by, bm, bd = map(int, birth_date.removeprefix("SE-").split("-"))
        date = world_time.split("T", 1)[0].removeprefix("SE-")
        wy, wm, wd = map(int, date.split("-"))
        return wy - by - (1 if (wm, wd) < (bm, bd) else 0)
    except Exception:
        return None


def summary(values):
    if not values:
        return None
    vals = [float(v) for v in values]
    return {
        "count": len(vals),
        "mean": round(sum(vals) / len(vals), 6),
        "sd": round(statistics.pstdev(vals), 6) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 6),
        "max": round(max(vals), 6),
    }


def profile_for(records, world_time):
    numeric = {}
    categories = {}

    def cat(key):
        if not key:
            return
        categories[key] = categories.get(key, 0) + 1

    for person in records:
        for key, val in flatten_numeric(person.get("stats", {}), "stats").items():
            numeric.setdefault(key, []).append(val)
        for key, val in flatten_numeric(person.get("aptitude", {}), "aptitude").items():
            numeric.setdefault(key, []).append(val)
        body = person.get("body") or {}
        for key in ("adult_height_cm", "current_weight_kg", "growth_end_age"):
            val = body.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                numeric.setdefault(f"body.{key}", []).append(float(val))
        app = person.get("appearance")
        if isinstance(app, (int, float)) and not isinstance(app, bool):
            numeric.setdefault("appearance", []).append(float(app))
        age = age_at(person.get("birth_date", ""), world_time)
        if age is not None:
            numeric.setdefault("age_years", []).append(float(age))
        cat(f"rank:{person.get('rank')}")
        cat(f"frame:{body.get('frame')}")
        health = person.get("health") or {}
        cat(f"health:{health.get('status')}")
        cat(f"assignment:{person.get('assignment')}")
        cat(f"origin:{person.get('origin')}")
        for duty in person.get("duties") or []:
            cat(f"duty:{duty}")
        for qual in person.get("qualifications") or []:
            cat(f"qualification:{qual}")
        rep = person.get("repertoire") or {}
        for pkg in rep.get("packages") or []:
            cat(f"package:{pkg}")

    return {
        "representation": "house_cohort",
        "numeric_distributions": {k: summary(v) for k, v in sorted(numeric.items()) if summary(v) is not None},
        "category_counts": dict(sorted(categories.items())),
        "development": {
            "resolved_through": world_time,
            "credits": {},
            "model": "representation_neutral_house_cohort",
        },
        "provenance": [
            "compressed_from_existing_house_tang_person_lite_without_capability_or_headcount_gain",
            "individual_identity_removed_only_when_no_saved_external_reference_or_notability_trigger_required persistence",
        ],
    }


def intrinsic_notability(person):
    reasons = []
    numeric_stats = list(flatten_numeric(person.get("stats", {})).values())
    if numeric_stats and max(numeric_stats) >= 160:
        reasons.append("legendary_current_capability")

    apt = [
        float(v)
        for k, v in (person.get("aptitude") or {}).items()
        if k != "source" and isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if apt:
        if sum(apt) / len(apt) >= 160:
            reasons.append("exceptional_mean_aptitude")
        if max(apt) >= 180:
            reasons.append("prodigy_aptitude_dimension")

    rep = person.get("repertoire") or {}
    if rep.get("bloodlines"):
        reasons.append("rare_bloodline")
    if rep.get("field_usable_techniques") or rep.get("latent_or_locked_techniques"):
        reasons.append("individual_technique_state")
    if person.get("offices"):
        reasons.append("individual_office")
    if person.get("relationships"):
        reasons.append("persistent_relationship_state")
    history = person.get("history") or {}
    if history.get("promotion") or history.get("service"):
        reasons.append("individual_service_history")
    health = person.get("health") or {}
    if health.get("status") not in (None, "healthy") or (health.get("fatigue") or 0) != 0:
        reasons.append("individual_health_exception")
    if str(person.get("current_goal") or "").strip():
        reasons.append("individual_goal")
    if person.get("assignment") not in (None, "manor_rotation"):
        reasons.append("nonroutine_assignment")
    routine_qualifications = {"real_katana_use"}
    if set(person.get("qualifications") or []) - routine_qualifications:
        reasons.append("uncommon_qualification")
    role_words = ("commander", "captain", "chief", "head", "leader", "quartermaster", "armorer", "smith", "medical", "healer", "specialist", "steward")
    duties = [str(x).lower() for x in person.get("duties") or []]
    if any(any(word in duty for word in role_words) for duty in duties):
        reasons.append("consequential_individual_duty")
    priority = str(person.get("narration_priority") or "").lower()
    if priority in {"high", "major", "recurring", "spotlight"}:
        reasons.append("saved_narrative_priority")
    return reasons


def create_population_structure():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Population Registry",
        "type": "object",
        "required": ["schema", "id", "pools", "transfers"],
        "properties": {
            "schema": {"const": "population-registry"},
            "id": {"type": "string"},
            "pools": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": ["owner_ref", "category", "count", "status", "provenance", "profile", "last_changed_at"],
                    "properties": {
                        "owner_ref": {"type": "string"},
                        "category": {"type": "string"},
                        "count": {"type": "integer", "minimum": 0},
                        "status": {"enum": ["active", "exhausted", "closed"]},
                        "provenance": {"type": "string"},
                        "profile": {"$ref": "#/$defs/profile"},
                        "last_changed_at": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            "transfers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "id", "at", "source_pool_id", "destination_ref", "applicants", "accepted", "rejected",
                        "method", "accepted_profile", "materialized_person_ids", "source_removed", "destination_added"
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "at": {"type": "string"},
                        "source_pool_id": {"type": "string"},
                        "destination_ref": {"type": "string"},
                        "applicants": {"type": "integer", "minimum": 0},
                        "accepted": {"type": "integer", "minimum": 0},
                        "rejected": {"type": "integer", "minimum": 0},
                        "method": {"type": "string"},
                        "accepted_profile": {"$ref": "#/$defs/profile"},
                        "materialized_person_ids": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                        "source_removed": {"type": "integer", "minimum": 0},
                        "destination_added": {"type": "integer", "minimum": 0},
                        "selection_note": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "$defs": {
            "profile": {
                "type": "object",
                "required": ["numeric_distributions", "category_counts", "tags"],
                "properties": {
                    "numeric_distributions": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "required": ["count", "mean", "sd", "min", "max"],
                            "properties": {
                                "count": {"type": "integer", "minimum": 0},
                                "mean": {"type": "number"},
                                "sd": {"type": "number", "minimum": 0},
                                "min": {"type": "number"},
                                "max": {"type": "number"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "category_counts": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }
    write_json("schemas/population-registry.schema.json", schema)

    template = {
        "schema": "file-template.v1",
        "template_id": "template.population-registry",
        "target_schema": "population-registry",
        "source_schema": "schemas/population-registry.schema.json",
        "scope": "mutable_state",
        "current_directories": ["state/population"],
        "unknown_key_policy": "reject",
        "required_top_level_keys": ["schema", "id", "pools", "transfers"],
        "object_contracts": {
            "": {"mode": "closed", "allowed_keys": ["schema", "id", "pools", "transfers"], "canonical_order": ["schema", "id", "pools", "transfers"]},
            "/pools": {"mode": "open_map"},
            "/pools/*": {"mode": "closed", "allowed_keys": ["owner_ref", "category", "count", "status", "provenance", "profile", "last_changed_at"], "canonical_order": ["owner_ref", "category", "count", "status", "provenance", "profile", "last_changed_at"]},
            "/pools/*/profile": {"mode": "closed", "allowed_keys": ["numeric_distributions", "category_counts", "tags"], "canonical_order": ["numeric_distributions", "category_counts", "tags"]},
            "/pools/*/profile/numeric_distributions": {"mode": "open_map"},
            "/pools/*/profile/numeric_distributions/*": {"mode": "closed", "allowed_keys": ["count", "mean", "sd", "min", "max"], "canonical_order": ["count", "mean", "sd", "min", "max"]},
            "/pools/*/profile/category_counts": {"mode": "open_map"},
            "/transfers/*": {"mode": "closed", "allowed_keys": ["id", "at", "source_pool_id", "destination_ref", "applicants", "accepted", "rejected", "method", "accepted_profile", "materialized_person_ids", "source_removed", "destination_added", "selection_note"], "canonical_order": ["id", "at", "source_pool_id", "destination_ref", "applicants", "accepted", "rejected", "method", "accepted_profile", "materialized_person_ids", "source_removed", "destination_added", "selection_note"]},
            "/transfers/*/accepted_profile": {"mode": "closed", "allowed_keys": ["numeric_distributions", "category_counts", "tags"], "canonical_order": ["numeric_distributions", "category_counts", "tags"]},
            "/transfers/*/accepted_profile/numeric_distributions": {"mode": "open_map"},
            "/transfers/*/accepted_profile/numeric_distributions/*": {"mode": "closed", "allowed_keys": ["count", "mean", "sd", "min", "max"], "canonical_order": ["count", "mean", "sd", "min", "max"]},
            "/transfers/*/accepted_profile/category_counts": {"mode": "open_map"},
        },
        "type_contracts": {
            "": ["object"], "/schema": ["string"], "/id": ["string"], "/pools": ["object"], "/pools/*": ["object"],
            "/pools/*/owner_ref": ["string"], "/pools/*/category": ["string"], "/pools/*/count": ["integer"], "/pools/*/status": ["string"], "/pools/*/provenance": ["string"], "/pools/*/profile": ["object"], "/pools/*/last_changed_at": ["string"],
            "/pools/*/profile/numeric_distributions": ["object"], "/pools/*/profile/numeric_distributions/*": ["object"], "/pools/*/profile/numeric_distributions/*/count": ["integer"], "/pools/*/profile/numeric_distributions/*/mean": ["number"], "/pools/*/profile/numeric_distributions/*/sd": ["number"], "/pools/*/profile/numeric_distributions/*/min": ["number"], "/pools/*/profile/numeric_distributions/*/max": ["number"], "/pools/*/profile/category_counts": ["object"], "/pools/*/profile/category_counts/*": ["integer"], "/pools/*/profile/tags": ["array"], "/pools/*/profile/tags/*": ["string"],
            "/transfers": ["array"], "/transfers/*": ["object"], "/transfers/*/id": ["string"], "/transfers/*/at": ["string"], "/transfers/*/source_pool_id": ["string"], "/transfers/*/destination_ref": ["string"], "/transfers/*/applicants": ["integer"], "/transfers/*/accepted": ["integer"], "/transfers/*/rejected": ["integer"], "/transfers/*/method": ["string"], "/transfers/*/accepted_profile": ["object"], "/transfers/*/materialized_person_ids": ["array"], "/transfers/*/materialized_person_ids/*": ["string"], "/transfers/*/source_removed": ["integer"], "/transfers/*/destination_added": ["integer"], "/transfers/*/selection_note": ["null", "string"],
            "/transfers/*/accepted_profile/numeric_distributions": ["object"], "/transfers/*/accepted_profile/numeric_distributions/*": ["object"], "/transfers/*/accepted_profile/numeric_distributions/*/count": ["integer"], "/transfers/*/accepted_profile/numeric_distributions/*/mean": ["number"], "/transfers/*/accepted_profile/numeric_distributions/*/sd": ["number"], "/transfers/*/accepted_profile/numeric_distributions/*/min": ["number"], "/transfers/*/accepted_profile/numeric_distributions/*/max": ["number"], "/transfers/*/accepted_profile/category_counts": ["object"], "/transfers/*/accepted_profile/category_counts/*": ["integer"], "/transfers/*/accepted_profile/tags": ["array"], "/transfers/*/accepted_profile/tags/*": ["string"],
        },
        "array_contracts": {
            "/pools/*/profile/tags": {"item_types": ["string"]},
            "/transfers": {"item_types": ["object"]},
            "/transfers/*/materialized_person_ids": {"item_types": ["string"]},
            "/transfers/*/accepted_profile/tags": {"item_types": ["string"]},
        },
        "writing_rules": [
            "Population pools are aggregate reservoirs and recruitment provenance, not hidden person sheets.",
            "Recruitment conserves accepted people from source pool to destination; rejected applicants remain in the source population.",
            "Mass recruitment never creates person-lite records. Only the explicit Sword Manor sparse-notable policy may materialize a recruitment subset as person-lite.",
            "Labels such as hunter, farmer, villager, academy trainee or veteran are categories inside this registry when causal, never mandatory separate files.",
        ],
    }
    write_json("data/runtime/templates/population-registry.template.json", template)
    write_json("data/runtime/blank-owners/population-registry.blank.json", {"id": None, "pools": {}, "schema": None, "transfers": []})

    contract = {
        "schema": "system-contract.v1",
        "system_id": "population_recruitment",
        "authority": False,
        "authority_paths": ["state/population/", "state/house/", "state/force/", "state/unit/", "state/person/"],
        "owner_templates": ["population-registry", "house", "force", "unit", "person-lite"],
        "read_first": [
            "the one source population pool actually recruited from",
            "the destination House/force/unit only",
            "selection mechanics and source/destination capability state only when the recruitment test changes composition",
        ],
        "write_order": [
            "resolve applicant count and selection from the source pool without pre-creating individuals",
            "remove only accepted personnel from the source population; rejected applicants remain in the source pool",
            "update the aggregate destination cohort/force/unit once with accepted count and selected composition",
            "for house.tang only, materialize a sparse proven standout subset as person-lite and subtract that subset from the aggregate accepted cohort",
            "record one conserved population transfer and rebuild only affected derived indexes",
        ],
        "invariants": [
            "Recruitment never creates personnel from nothing.",
            "Mass force, village, country, clan, mercenary and civilian-security recruitment remains aggregate and cannot automatically emit person-lite owners.",
            "Sword Manor is the only recruitment path allowed to create sparse person-lite standouts, and only when house.tang personal_force_model authorizes it.",
            "A descriptive source category such as hunter or villager is stored inside a pool/profile when causally relevant and does not require its own file.",
            "Selection changes the accepted population profile; eight selected from fifty are not treated as eight unselected random civilians.",
            "Compression cannot improve aptitude, age/body composition, capability, experience, survival, equipment, promotion or training outcomes.",
        ],
        "validators": ["tools/test_population_model.py", "tools/test_state_consolidation.py", "tools/test_templates.py", "tools/test_development.py"],
    }
    write_json("data/runtime/system-contracts/population_recruitment.json", contract)

    rules = """# Population, Recruitment, and Materialization

## One recruitment law
All recruitment starts from a real aggregate source population or manpower pool and ends in a real destination cohort, unit, force, institution, or House. Accepted headcount is conserved. Rejected applicants remain in the source population. A recruitment event may sample many candidates, but only accepted people leave the source pool.

Source categories such as rural hunter, farmer, villager, academy trainee, clan trainee, veteran, mercenary, refugee, artisan, or laborer are created only when that distinction changes selection, capability, aptitude, body/age composition, experience, availability, or later outcomes. They are profile/category records inside the consolidated population registry, not one file per category and never one hidden person sheet per civilian.

Selection must alter the accepted profile. If fifty applicants are tested and eight pass a demanding sword/chakra examination, the accepted eight inherit the selection-conditioned capability/aptitude/body distribution rather than the unfiltered source distribution.

## Mass recruitment stays aggregate
For Konoha, other hidden villages, countries, national forces, clans, mercenary organizations, civilian security and other non-Sword-Manor institutions, recruitment is aggregate. Recruiting one thousand people updates the source population and destination aggregate state; it does not create one thousand person-lite records or a sparse personal roster as a side effect.

A specific person from those forces may still be materialized later when command, combat, injury, relationship, knowledge, political, narrative, or other individual state becomes consequential. That is a separate materialization event, not recruitment bookkeeping.

## Sword Manor sparse-notable exception
House Tang may use a higher-fidelity personal-force model only while `state/house/tang.json` declares `personal_force_model = aggregate_cohorts_with_sparse_sword_manor_notables`.

Sword Manor recruits enter aggregate House cohorts by default. A recruit may be materialized immediately as person-lite only when the recruitment itself establishes a durable individual distinction, such as legendary current capability, exceptional/prodigy aptitude, rare bloodline or technique state, unusual qualification, command/office responsibility, unique injury/equipment, persistent relationship/history, or another saved fact that makes cohort averaging unfair. There is no quota and no automatic materialization merely for being accepted.

Existing or future Sword Manor person-lite owners are the sparse exceptions. Ordinary House members live in aggregate cohort counts and capability/aptitude/body distributions. Group training changes the cohort once, not every anonymous member record.

## Representation transitions
Population/cohort to person-lite is **materialization**: identify an existing member of the source cohort, conserve headcount, and instantiate only facts supported by the source distribution plus the causal evidence that made the individual notable. Person-lite to exact character is **expansion**: the same person gains deeper individual state with no free capability.

No representation transition grants skill, aptitude, bloodline, equipment, experience, health, office, relationships, knowledge, or survival advantage.
"""
    write_text("rules/population.md", rules)

    registry_path = ROOT / "state/population/registry.json"
    if not registry_path.exists():
        write_json("state/population/registry.json", {"schema": "population-registry", "id": "registry.population", "pools": {}, "transfers": []})


def register_population_structure():
    pidx = read_json("data/runtime/template-index-shards/p.json")
    pidx["templates"]["population-registry"] = {
        "path": "data/runtime/templates/population-registry.template.json",
        "source_schema": "schemas/population-registry.schema.json",
        "scope": "mutable_state",
    }
    write_json("data/runtime/template-index-shards/p.json", pidx)

    bidx = read_json("data/runtime/blank-owner-index.json")
    bidx["owners"]["population-registry"] = "data/runtime/blank-owners/population-registry.blank.json"
    write_json("data/runtime/blank-owner-index.json", bidx)

    sidx = read_json("data/runtime/system-contract-index.json")
    sidx["systems"]["population_recruitment"] = "data/runtime/system-contracts/population_recruitment.json"
    write_json("data/runtime/system-contract-index.json", sidx)

    rr = read_json("data/runtime/rule-router.json")
    rr["domains"]["population_recruitment"] = ["rules/population.md"]
    write_json("data/runtime/rule-router.json", rr)

    repo_map = read_json("data/runtime/repository-map.json")
    repo_map["route_index"]["population_recruitment"] = "world"
    write_json("data/runtime/repository-map.json", repo_map)

    world = read_json("data/runtime/repository-routes/world.json")
    world["routes"]["population_recruitment"] = {
        "r": ["state/population/registry.json"],
        "w": ["state/population/registry.json"],
        "domain": "population_recruitment",
        "note": "Recruitment is aggregate for mass forces. Sword Manor alone may emit sparse proven person-lite standouts under its saved personal-force model.",
    }
    write_json("data/runtime/repository-routes/world.json", world)


def extend_person_lite_structure():
    tpl = read_json("data/runtime/templates/person-lite.template.json")
    root = tpl["object_contracts"][""]
    add_unique(root["allowed_keys"], "materialization_reason")
    if "materialization_reason" not in root["canonical_order"]:
        idx = root["canonical_order"].index("narration_priority") + 1 if "narration_priority" in root["canonical_order"] else None
        add_unique(root["canonical_order"], "materialization_reason", idx)
    tpl["type_contracts"]["/materialization_reason"] = ["string"]
    write_json("data/runtime/templates/person-lite.template.json", tpl)

    blank = read_json("data/runtime/blank-owners/person-lite.blank.json")
    blank["materialization_reason"] = None
    write_json("data/runtime/blank-owners/person-lite.blank.json", blank)

    schema = read_json("schemas/person-lite.schema.json")
    schema.setdefault("properties", {})["materialization_reason"] = {"type": "string"}
    write_json("schemas/person-lite.schema.json", schema)

    contract = read_json("data/runtime/system-contracts/characters.json")
    add_unique(contract["invariants"], "Sword Manor person-lite owners are sparse exceptions and carry a nonempty materialization_reason; mass recruitment elsewhere never creates person-lite automatically.")
    write_json("data/runtime/system-contracts/characters.json", contract)


def extend_house_structure():
    tpl = read_json("data/runtime/templates/house.template.json")
    root = tpl["object_contracts"][""]
    add_unique(root["allowed_keys"], "aggregate_member_count")
    if "aggregate_member_count" not in root["canonical_order"]:
        idx = root["canonical_order"].index("member_ids") + 1 if "member_ids" in root["canonical_order"] else None
        add_unique(root["canonical_order"], "aggregate_member_count", idx)
    tpl["type_contracts"]["/aggregate_member_count"] = ["integer"]

    unit = tpl["object_contracts"]["/permanent_units/*"]
    for key in ("aggregate_count", "cohort_profile"):
        add_unique(unit["allowed_keys"], key)
    if "aggregate_count" not in unit["canonical_order"]:
        idx = unit["canonical_order"].index("members") + 1
        add_unique(unit["canonical_order"], "aggregate_count", idx)
    if "cohort_profile" not in unit["canonical_order"]:
        idx = unit["canonical_order"].index("aggregate_count") + 1
        add_unique(unit["canonical_order"], "cohort_profile", idx)
    tpl["type_contracts"]["/permanent_units/*/aggregate_count"] = ["integer"]
    tpl["type_contracts"]["/permanent_units/*/cohort_profile"] = ["null", "object"]

    tpl["object_contracts"]["/permanent_units/*/cohort_profile"] = {
        "mode": "closed",
        "allowed_keys": ["representation", "numeric_distributions", "category_counts", "development", "provenance"],
        "canonical_order": ["representation", "numeric_distributions", "category_counts", "development", "provenance"],
    }
    tpl["object_contracts"]["/permanent_units/*/cohort_profile/numeric_distributions"] = {"mode": "open_map"}
    tpl["object_contracts"]["/permanent_units/*/cohort_profile/numeric_distributions/*"] = {
        "mode": "closed",
        "allowed_keys": ["count", "mean", "sd", "min", "max"],
        "canonical_order": ["count", "mean", "sd", "min", "max"],
    }
    tpl["object_contracts"]["/permanent_units/*/cohort_profile/category_counts"] = {"mode": "open_map"}
    tpl["object_contracts"]["/permanent_units/*/cohort_profile/development"] = {
        "mode": "closed",
        "allowed_keys": ["resolved_through", "credits", "model"],
        "canonical_order": ["resolved_through", "credits", "model"],
    }
    tpl["object_contracts"]["/permanent_units/*/cohort_profile/development/credits"] = {"mode": "open_map"}

    tc = tpl["type_contracts"]
    tc.update({
        "/permanent_units/*/cohort_profile/representation": ["string"],
        "/permanent_units/*/cohort_profile/numeric_distributions": ["object"],
        "/permanent_units/*/cohort_profile/numeric_distributions/*": ["object"],
        "/permanent_units/*/cohort_profile/numeric_distributions/*/count": ["integer"],
        "/permanent_units/*/cohort_profile/numeric_distributions/*/mean": ["number"],
        "/permanent_units/*/cohort_profile/numeric_distributions/*/sd": ["number"],
        "/permanent_units/*/cohort_profile/numeric_distributions/*/min": ["number"],
        "/permanent_units/*/cohort_profile/numeric_distributions/*/max": ["number"],
        "/permanent_units/*/cohort_profile/category_counts": ["object"],
        "/permanent_units/*/cohort_profile/category_counts/*": ["integer"],
        "/permanent_units/*/cohort_profile/development": ["object"],
        "/permanent_units/*/cohort_profile/development/resolved_through": ["string"],
        "/permanent_units/*/cohort_profile/development/credits": ["object"],
        "/permanent_units/*/cohort_profile/development/credits/*": ["number"],
        "/permanent_units/*/cohort_profile/development/model": ["string"],
        "/permanent_units/*/cohort_profile/provenance": ["array"],
        "/permanent_units/*/cohort_profile/provenance/*": ["string"],
    })
    tpl["array_contracts"]["/permanent_units/*/cohort_profile/provenance"] = {"item_types": ["string"]}
    add_unique(tpl["writing_rules"], "House Tang ordinary personnel may be represented as aggregate cohort counts/profiles inside the House owner; members arrays contain only persistent exact/person-lite identities.")
    add_unique(tpl["writing_rules"], "Group development updates a cohort profile once. Do not fan out routine training writes across anonymous cohort members.")
    write_json("data/runtime/templates/house.template.json", tpl)

    blank = read_json("data/runtime/blank-owners/house.blank.json")
    blank["aggregate_member_count"] = None
    write_json("data/runtime/blank-owners/house.blank.json", blank)

    contract = read_json("data/runtime/system-contracts/forces_institutions.json")
    add_unique(contract["invariants"], "House member_ids contains persistent identity-bearing members only; ordinary Sword Manor personnel are conserved in aggregate cohort counts under the House owner.")
    add_unique(contract["invariants"], "Routine House cohort development is one aggregate write; do not create or update one person owner per ordinary member.")
    write_json("data/runtime/system-contracts/forces_institutions.json", contract)


def extend_development_structure():
    model = read_json("data/development/model.json")
    model["representation_efficiency"]["house_cohort"] = 1.0
    write_json("data/development/model.json", model)

    tpl = read_json("data/runtime/templates/development-model.template.json")
    rep = tpl["object_contracts"]["/representation_efficiency"]
    add_unique(rep["allowed_keys"], "house_cohort")
    add_unique(rep["canonical_order"], "house_cohort")
    tpl["type_contracts"]["/representation_efficiency/house_cohort"] = ["number"]
    write_json("data/runtime/templates/development-model.template.json", tpl)

    contract = read_json("data/runtime/system-contracts/training_development.json")
    add_unique(contract["authority_paths"], "state/house/")
    add_unique(contract["owner_templates"], "house")
    add_unique(contract["read_first"], "for a Sword Manor anonymous cohort, load state/house/tang.json once rather than every former member record")
    add_unique(contract["write_order"], "for a House aggregate cohort, update its capability distribution and embedded residual credits once; persistent standouts settle individually only for hours they actually share")
    add_unique(contract["invariants"], "House cohort compression is representation-neutral and cannot make anonymous members train faster than equivalent individually resolved people.")
    add_unique(contract["invariants"], "A shared House training interval is not copied into one write per anonymous member; cohort state owns the common development and sparse person-lite exceptions own only individual divergence.")
    write_json("data/runtime/system-contracts/training_development.json", contract)

    rules_path = ROOT / "rules/training.md"
    text = rules_path.read_text(encoding="utf-8")
    marker = "For aggregate units, the same development law applies. Residual credits remain unit-specific and target-specific; capability changes update the authoritative multidimensional distribution and invalidate/rebuild the derived battle kernel. Qualified-subset promotion remains a conserved integer personnel transfer and cannot be fabricated from a fractional bank. Routine training cannot manufacture field, combat, or command experience."
    addition = marker + "\n\nSword Manor anonymous cohorts use the same law as aggregate units but keep their compact distribution and residual credits inside the House owner. A shared training block updates the cohort once. It must not fan out identical writes across every anonymous disciple. Persistent Sword Manor person-lite/exact standouts remain separate people and settle only their own attended hours; they do not receive both the cohort gain and a duplicate individual gain."
    if marker in text and "Sword Manor anonymous cohorts use the same law" not in text:
        text = text.replace(marker, addition)
    write_text("rules/training.md", text)


def update_runtime_and_docs():
    path = ROOT / "RUNTIME.md"
    text = path.read_text(encoding="utf-8")
    section = """## Population and recruitment invariants

Recruitment conserves real aggregate population/manpower. Candidate categories such as hunter, villager, farmer, academy trainee, veteran, mercenary or specialist exist only when causally relevant and live as compact profile records, not separate files or hidden person sheets. Selection changes the accepted population profile.

Mass recruitment for villages, countries, forces, clans, mercenaries and civilian security remains aggregate and never creates person-lite records as a side effect. Sword Manor is the sole recruitment exception: while House Tang's saved personal-force model authorizes it, only proven standout recruits may materialize as sparse person-lite individuals; ordinary accepted recruits enter aggregate House cohorts. Group training/development updates the aggregate cohort once, while persistent standouts retain only their individual divergence. Materialization and exact-character expansion conserve the same person and grant no free capability.

"""
    if "## Population and recruitment invariants" not in text:
        marker = "## Information and determinism\n"
        text = text.replace(marker, section + marker)
    write_text("RUNTIME.md", text)

    rmap = ROOT / "REPOSITORY_MAP.md"
    doc = rmap.read_text(encoding="utf-8")
    if "Population / recruitment" not in doc:
        doc += "\n\n## Population / recruitment\n\nUse the `population_recruitment` route. Load `state/population/registry.json` plus only the one destination owner. Recruitment is aggregate for mass forces. Sword Manor may materialize only sparse proven standouts under its saved personal-force model. Do not enumerate person files to process ordinary recruitment or cohort training.\n"
    write_text("REPOSITORY_MAP.md", doc)


def migrate_house_people():
    house = read_json("state/house/tang.json")
    meta = read_json("state/meta.json")
    world_time = meta["time"]
    person_dir = ROOT / "state/person/ht"
    person_paths = sorted(person_dir.glob("*.json")) if person_dir.exists() else []
    people = {}
    path_by_id = {}
    for path in person_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != "person-lite" or not data.get("id"):
            raise RuntimeError(f"Unexpected House Tang person file: {path}")
        people[data["id"]] = data
        path_by_id[data["id"]] = path

    target_ids = set(people)
    external_refs = {pid: set() for pid in target_ids}
    excluded = {
        "state/house/tang.json",
        "state/house/units.json",
        "state/index/owners/ht.json",
        "state/time/coverage/process_house_tang_people_weekly.json",
    }
    for path in (ROOT / "state").rglob("*.json"):
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        if rel in excluded or rel.startswith("state/person/ht/"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        found = set()
        exact_string_refs(data, target_ids, found)
        for pid in found:
            external_refs[pid].add(rel)

    survivors = {}
    collapsed = {}
    for pid, person in people.items():
        reasons = intrinsic_notability(person)
        if external_refs[pid]:
            reasons.append("preexisting_external_state_reference")
        if reasons:
            person["materialization_reason"] = "; ".join(sorted(set(reasons)))
            survivors[pid] = person
        else:
            collapsed[pid] = person

    old_member_ids = list(house.get("member_ids", []))
    house["member_ids"] = [pid for pid in old_member_ids if pid not in target_ids or pid in survivors]
    house["personal_force_model"] = "aggregate_cohorts_with_sparse_sword_manor_notables"

    assigned_seen = set()
    total_aggregate = 0
    for unit in house.get("permanent_units", []):
        original_members = list(unit.get("members", []))
        ordinary = [pid for pid in original_members if pid in collapsed]
        named = [pid for pid in original_members if pid not in collapsed]
        unit["members"] = named
        unit["aggregate_count"] = len(ordinary)
        total_aggregate += len(ordinary)
        assigned_seen.update(ordinary)
        unit["cohort_profile"] = profile_for([collapsed[pid] for pid in ordinary], world_time) if ordinary else None

    missing_collapsed = set(collapsed) - assigned_seen
    if missing_collapsed:
        raise RuntimeError(f"Cannot collapse unclassified House members: {sorted(missing_collapsed)}")
    house["aggregate_member_count"] = total_aggregate
    write_json("state/house/tang.json", house)

    for pid, person in survivors.items():
        path_by_id[pid].write_text(json.dumps(person, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for pid in collapsed:
        path_by_id[pid].unlink()

    ht_index = read_json("state/index/owners/ht.json")
    ht_index["owners"] = {
        pid: str(path_by_id[pid].relative_to(ROOT)).replace("\\", "/")
        for pid in sorted(survivors)
    }
    write_json("state/index/owners/ht.json", ht_index)

    projection = ROOT / "state/house/units.json"
    if projection.exists():
        projection.unlink()

    coverage = read_json("state/time/coverage/process_house_tang_people_weekly.json")
    base = ["house.tang", "pc_wei_tang", "char.zhu", "char.linh", "char.kai"]
    coverage["owner_ids"] = base + sorted(survivors)
    write_json("state/time/coverage/process_house_tang_people_weekly.json", coverage)

    runtime = read_json("state/runtime.json")
    receipt = runtime.get("completed_reviews", {}).get("process_house_tang_people_weekly")
    if not isinstance(receipt, dict):
        raise RuntimeError("Missing House Tang process receipt")
    receipt["coverage_count"] = len(coverage["owner_ids"])
    receipt["result"] = "Current House Tang exact/notable people plus aggregate House cohorts are the authoritative settlement base at this frontier; ordinary former person-lite members were compression-migrated without changing headcount or capability distribution."
    write_json("state/runtime.json", runtime)

    owners = read_json("state/index/owners.json")
    count = 0
    for shard in sorted((ROOT / "state/index/owners").glob("*.json")):
        data = json.loads(shard.read_text(encoding="utf-8"))
        if isinstance(data.get("owners"), dict):
            count += len(data["owners"])
    owners["owner_count"] = count
    write_json("state/index/owners.json", owners)

    print("House Tang population migration")
    print("  prior_person_lite=", len(people))
    print("  retained_notables=", len(survivors))
    print("  aggregated_ordinary=", len(collapsed))
    for pid in sorted(survivors):
        print("   KEEP", pid, survivors[pid].get("name"), "=>", survivors[pid].get("materialization_reason"))


def update_tests_and_ci():
    path = ROOT / "tools/test_state_consolidation.py"
    text = path.read_text(encoding="utf-8")
    old = """# Compatibility House projection is allowed only as a derived exact projection.\nprojection_path = ROOT / \"state/house/units.json\"\nif projection_path.exists():\n    projection = read_json(projection_path)\n    source_ref = projection.get(\"source_ref\")\n    if projection.get(\"authority\") is not False or projection.get(\"derived_cache\") is not True:\n        errors.append(\"state/house/units.json must be a non-authoritative derived cache\")\n    if source_ref != \"state/house/tang.json\":\n        errors.append(\"state/house/units.json must source state/house/tang.json\")\n    source = read_json(ROOT / source_ref) if source_ref and (ROOT / source_ref).exists() else {}\n    for key in (\n        \"permanent_units\",\n        \"unassigned_members\",\n        \"externally_assigned_members\",\n        \"formation_library_ref\",\n        \"reconstitution_policy_ref\",\n    ):\n        if projection.get(key) != source.get(key):\n            errors.append(f\"state/house/units.json drift: {key} != House owner {key}\")\n    if projection.get(\"note\") != source.get(\"standing_readiness_order\"):\n        errors.append(\"state/house/units.json drift: note != House standing_readiness_order\")\n\nowner_shard = read_json(ROOT / \"state/index/owners/house.json\")\nif \"house.tang.units\" in owner_shard.get(\"owners\", {}):\n    errors.append(\"derived House unit projection must not be registered as an owner\")\n"""
    new = """# Redundant House unit projection was removed; House organization/cohorts live in the House owner.\nprojection_path = ROOT / \"state/house/units.json\"\nif projection_path.exists():\n    errors.append(\"redundant House unit projection must not be recreated: state/house/units.json\")\n\nowner_shard = read_json(ROOT / \"state/index/owners/house.json\")\nif \"house.tang.units\" in owner_shard.get(\"owners\", {}):\n    errors.append(\"removed House unit projection must not be registered as an owner\")\n"""
    if old in text:
        text = text.replace(old, new)
    anchor = """    if len(unit_members) != len(set(unit_members)):\n        errors.append(f\"{path.relative_to(ROOT)}: a person appears in more than one permanent House unit\")\n"""
    addition = anchor + """    aggregate_sum = 0\n    for unit in units:\n        aggregate_count = unit.get(\"aggregate_count\")\n        if not isinstance(aggregate_count, int) or aggregate_count < 0:\n            errors.append(f\"{path.relative_to(ROOT)}: unit {unit.get('id')} aggregate_count must be a nonnegative integer\")\n            continue\n        aggregate_sum += aggregate_count\n        profile = unit.get(\"cohort_profile\")\n        if aggregate_count == 0 and profile is not None:\n            errors.append(f\"{path.relative_to(ROOT)}: unit {unit.get('id')} zero aggregate_count must have null cohort_profile\")\n        if aggregate_count > 0 and (not isinstance(profile, dict) or profile.get(\"representation\") != \"house_cohort\"):\n            errors.append(f\"{path.relative_to(ROOT)}: unit {unit.get('id')} aggregate personnel require house_cohort profile\")\n    if house.get(\"aggregate_member_count\") != aggregate_sum:\n        errors.append(f\"{path.relative_to(ROOT)}: aggregate_member_count drift {house.get('aggregate_member_count')} != {aggregate_sum}\")\n    if house.get(\"personal_force_model\") != \"aggregate_cohorts_with_sparse_sword_manor_notables\":\n        errors.append(f\"{path.relative_to(ROOT)}: House Tang must use aggregate cohorts with sparse Sword Manor notables\")\n"""
    if anchor in text and "aggregate_member_count drift" not in text:
        text = text.replace(anchor, addition)
    write_text("tools/test_state_consolidation.py", text)

    dev = ROOT / "tools/test_development.py"
    dtext = dev.read_text(encoding="utf-8")
    if "assert 'house_cohort' in eff" not in dtext:
        dtext = dtext.replace("assert set(eff.values())=={1.0}, eff", "assert set(eff.values())=={1.0}, eff\nassert 'house_cohort' in eff and eff['house_cohort']==1.0")
    write_text("tools/test_development.py", dtext)

    pop_test = r'''#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []

def read(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))

def err(msg):
    errors.append(msg)

registry = read("state/population/registry.json")
if registry.get("schema") != "population-registry":
    err("population registry schema")
for pool_id, pool in registry.get("pools", {}).items():
    if not isinstance(pool.get("count"), int) or pool["count"] < 0:
        err(f"bad population pool count:{pool_id}")
for transfer in registry.get("transfers", []):
    applicants = transfer.get("applicants")
    accepted = transfer.get("accepted")
    rejected = transfer.get("rejected")
    if not all(isinstance(x, int) and x >= 0 for x in (applicants, accepted, rejected)):
        err(f"bad transfer counts:{transfer.get('id')}")
        continue
    if accepted + rejected != applicants:
        err(f"selection conservation:{transfer.get('id')}")
    if transfer.get("source_removed") != accepted or transfer.get("destination_added") != accepted:
        err(f"transfer headcount conservation:{transfer.get('id')}")
    mids = transfer.get("materialized_person_ids", [])
    if len(mids) > accepted:
        err(f"materialized subset exceeds accepted:{transfer.get('id')}")
    if mids and transfer.get("destination_ref") != "house.tang":
        err(f"mass recruitment illegally created person-lite:{transfer.get('id')}:{transfer.get('destination_ref')}")

house = read("state/house/tang.json")
if house.get("personal_force_model") != "aggregate_cohorts_with_sparse_sword_manor_notables":
    err("House Tang personal force model")
aggregate = 0
for unit in house.get("permanent_units", []):
    n = unit.get("aggregate_count")
    if not isinstance(n, int) or n < 0:
        err(f"bad House cohort count:{unit.get('id')}")
        continue
    aggregate += n
    profile = unit.get("cohort_profile")
    if n > 0:
        if not isinstance(profile, dict) or profile.get("representation") != "house_cohort":
            err(f"missing House cohort profile:{unit.get('id')}")
        else:
            dev = profile.get("development") or {}
            if dev.get("model") != "representation_neutral_house_cohort":
                err(f"bad House cohort development model:{unit.get('id')}")
            if not isinstance(dev.get("credits"), dict):
                err(f"bad House cohort credits:{unit.get('id')}")
    elif profile is not None:
        err(f"zero-count House unit has profile:{unit.get('id')}")
if aggregate != house.get("aggregate_member_count"):
    err(f"House aggregate total drift:{aggregate}:{house.get('aggregate_member_count')}")

if (ROOT / "state/house/units.json").exists():
    err("redundant state/house/units.json exists")

ht_files = sorted((ROOT / "state/person/ht").glob("*.json")) if (ROOT / "state/person/ht").exists() else []
ht_ids = set()
for path in ht_files:
    person = json.loads(path.read_text(encoding="utf-8"))
    pid = person.get("id")
    ht_ids.add(pid)
    if person.get("schema") != "person-lite" or person.get("resolution") != "individual_lite":
        err(f"bad Sword Manor notable owner:{path.name}")
    if not str(person.get("materialization_reason") or "").strip():
        err(f"Sword Manor person-lite lacks materialization_reason:{pid}")

ht_index = read("state/index/owners/ht.json").get("owners", {})
if set(ht_index) != ht_ids:
    err(f"Sword Manor owner index drift:index={sorted(ht_index)} files={sorted(ht_ids)}")
for pid, rel in ht_index.items():
    if not (ROOT / rel).exists():
        err(f"Sword Manor owner route missing:{pid}:{rel}")

coverage = read("state/time/coverage/process_house_tang_people_weekly.json").get("owner_ids", [])
if "house.tang" not in coverage:
    err("House cohort owner absent from House process coverage")
for pid in ht_ids:
    if pid not in coverage:
        err(f"Sword Manor notable absent from House process coverage:{pid}")

runtime = read("state/runtime.json")
receipt = runtime.get("completed_reviews", {}).get("process_house_tang_people_weekly", {})
if receipt.get("coverage_count") != len(coverage):
    err("House process receipt coverage drift")

rules = (ROOT / "rules/population.md").read_text(encoding="utf-8")
for phrase in ("Mass recruitment stays aggregate", "Sword Manor sparse-notable exception", "does not create one thousand person-lite"):
    if phrase not in rules:
        err(f"population rule missing:{phrase}")

if errors:
    print(f"POPULATION MODEL FAIL {len(errors)}")
    for e in errors:
        print("-", e)
    raise SystemExit(1)
print(f"POPULATION MODEL OK house_aggregate={aggregate} sword_manor_notables={len(ht_ids)} transfers={len(registry.get('transfers', []))}")
'''
    write_text("tools/test_population_model.py", pop_test)

    audit = ROOT / ".github/workflows/audit.yml"
    atext = audit.read_text(encoding="utf-8")
    line = "      - run: python tools/test_population_model.py\n"
    if line not in atext:
        anchor = "      - run: python tools/test_state_consolidation.py\n"
        atext = atext.replace(anchor, anchor + line)
    write_text(".github/workflows/audit.yml", atext)


def main():
    create_population_structure()
    register_population_structure()
    extend_person_lite_structure()
    extend_house_structure()
    extend_development_structure()
    update_runtime_and_docs()
    migrate_house_people()
    update_tests_and_ci()
    print("Population/materialization migration prepared successfully")


if __name__ == "__main__":
    main()
