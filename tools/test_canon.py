#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    jsonschema = None

from test_templates import validate_doc


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "game/data/canon/manifest.json"
MANIFEST_SCHEMA_PATH = ROOT / "game/schemas/canon-continuity-manifest.schema.json"
MANIFEST_TEMPLATE_PATH = ROOT / "runtime/contracts/templates/canon-continuity-manifest.template.json"
SHARD_SCHEMA_PATH = ROOT / "game/schemas/canon-history-shard.schema.json"
SHARD_TEMPLATE_PATH = ROOT / "runtime/contracts/templates/canon-history-shard.template.json"
LANES = ("public", "restricted", "secret")
EXPECTED_ROUTE_NAMES = {
    "canon_manifest",
    "canon_history_known_id",
    "canon_history_public",
    "canon_history_restricted",
    "canon_history_secret",
}
CLASS_MEDIUM = {
    "primary_manga": "manga",
    "official_supplement": "official_supplement",
    "anime_compatible": "anime",
}
EXPECTED_ACCESS_RULES = {
    "public": "ambient_reference_not_automatic_personal_knowledge",
    "restricted": "requires_saved_access_or_knowledge_route",
    "secret": "world_causality_only_until_saved_discovery",
}
EXPECTED_SOURCE_RULES = {
    "primary_manga": (1, "establish_with_approved_volume_chapter_locator"),
    "official_supplement": (2, "fill_only_nonconflicting_gaps_with_explicit_approval"),
    "anime_compatible": (3, "supplement_only_when_explicitly_approved_and_nonconflicting"),
}
EXPECTED_FORBIDDEN_AUTHORITIES = {
    "model_memory",
    "wiki",
    "fanon",
    "unverified_summary",
}
EXPECTED_GUARDS = {
    "campaign_state_precedence",
    "unbound_anchor_forbids_events",
    "event_seed_requires_primary_locator",
    "future_outcomes_forbidden",
    "model_memory_forbidden",
    "paraphrase_only",
    "visibility_is_not_character_knowledge",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def structural_errors(label: str, document: dict, schema: dict, template: dict) -> list[str]:
    errors = []
    if jsonschema is not None:
        errors.extend(
            f"{label}:schema:{error.message}"
            for error in jsonschema.Draft202012Validator(schema).iter_errors(document)
        )
    validate_doc(label, document, template, errors)
    return errors


def locator_errors(locator: dict, catalog: dict[str, dict], label: str) -> list[str]:
    errors = []
    required_keys = {"work_id", "locator_kind", "volume", "chapter", "episode", "section"}
    if set(locator) != required_keys:
        errors.append(f"{label}:locator_shape:{sorted(locator)}")
    work_id = locator.get("work_id")
    source = catalog.get(work_id)
    if source is None:
        return [f"{label}:unknown_source:{work_id}"]
    if source.get("approval_status") != "approved":
        errors.append(f"{label}:source_not_approved:{work_id}")
    source_class = source.get("source_class")
    if CLASS_MEDIUM.get(source_class) != source.get("medium"):
        errors.append(f"{label}:source_class_medium_mismatch:{work_id}")

    kind = locator.get("locator_kind")
    populated = {
        key for key in ("volume", "chapter", "episode", "section")
        if locator.get(key) is not None
    }
    expected = {
        "volume_chapter": {"volume", "chapter"},
        "episode": {"episode"},
        "section": {"section"},
    }.get(kind)
    if expected is None or populated != expected:
        errors.append(f"{label}:malformed_{kind}_locator:{work_id}")
    if source_class == "primary_manga" and kind != "volume_chapter":
        errors.append(f"{label}:primary_source_without_volume_chapter:{work_id}")
    return errors


def semantic_errors(manifest: dict, shards: dict[str, dict]) -> list[str]:
    errors = []
    continuity_id = manifest.get("continuity_id")
    if manifest.get("schema") != "canon-continuity-manifest":
        errors.append("manifest_schema_mismatch")
    if continuity_id != "continuity.naruto.manga_primary_anime_compatible":
        errors.append(f"continuity_id_mismatch:{continuity_id}")
    if manifest.get("campaign_authority") is not False:
        errors.append("manifest_must_not_be_campaign_authority")

    guards = manifest.get("runtime_guards", {})
    if set(guards) != EXPECTED_GUARDS or any(guards.get(key) is not True for key in EXPECTED_GUARDS):
        errors.append("runtime_guard_policy_mismatch")

    source_policy = manifest.get("source_policy", {})
    for source_class, (precedence, fact_rule) in EXPECTED_SOURCE_RULES.items():
        policy = source_policy.get(source_class, {})
        if policy.get("precedence") != precedence or policy.get("fact_rule") != fact_rule:
            errors.append(f"source_precedence_policy_mismatch:{source_class}")
    if set(source_policy.get("forbidden_authorities", [])) != EXPECTED_FORBIDDEN_AUTHORITIES:
        errors.append("forbidden_authority_policy_mismatch")

    catalog: dict[str, dict] = {}
    for source in manifest.get("source_catalog", []):
        if set(source) != {"work_id", "title", "medium", "source_class", "approval_status"}:
            errors.append(f"source_record_shape:{source.get('work_id')}")
        work_id = source.get("work_id")
        if work_id in catalog:
            errors.append(f"duplicate_source_id:{work_id}")
        catalog[work_id] = source
        if source.get("source_class") not in CLASS_MEDIUM:
            errors.append(f"unsupported_source_class:{work_id}:{source.get('source_class')}")
        if source.get("approval_status") not in {"approved", "pending", "rejected"}:
            errors.append(f"unsupported_approval_status:{work_id}:{source.get('approval_status')}")
        expected_medium = CLASS_MEDIUM.get(source.get("source_class"))
        if expected_medium != source.get("medium"):
            errors.append(f"source_class_medium_mismatch:{work_id}")

    anchor = manifest.get("anchor", {})
    anchor_locators = anchor.get("source_locators", [])
    if anchor.get("binding_status") not in {"unbound", "verified"}:
        errors.append(f"unsupported_anchor_status:{anchor.get('binding_status')}")
    if anchor.get("binding_status") == "unbound" and (
        anchor.get("campaign_time") is not None
        or anchor.get("canon_event_id") is not None
        or anchor_locators
    ):
        errors.append("unbound_anchor_has_binding_data")
    if anchor.get("binding_status") == "verified":
        if not anchor.get("campaign_time"):
            errors.append("verified_anchor_missing_campaign_time")
        if not anchor.get("canon_event_id"):
            errors.append("verified_anchor_missing_event_id")
    for position, locator in enumerate(anchor_locators):
        errors.extend(locator_errors(locator, catalog, f"anchor:{position}"))
    if anchor.get("binding_status") == "verified":
        if not any(
            catalog.get(locator.get("work_id"), {}).get("source_class") == "primary_manga"
            and locator.get("locator_kind") == "volume_chapter"
            for locator in anchor_locators
        ):
            errors.append("verified_anchor_without_primary_volume_chapter_locator")

    routed_paths: dict[str, str] = {}
    for lane in LANES:
        lane_route = manifest.get("history_routes", {}).get(lane, {})
        if lane_route.get("access_rule") != EXPECTED_ACCESS_RULES[lane]:
            errors.append(f"lane_access_rule_mismatch:{lane}")
        lane_paths = lane_route.get("shards", [])
        if not lane_paths:
            errors.append(f"lane_without_shard:{lane}")
        for path in lane_paths:
            if path in routed_paths:
                errors.append(f"shard_routed_twice:{path}:{routed_paths[path]}:{lane}")
            routed_paths[path] = lane
            if path not in shards:
                errors.append(f"routed_shard_missing:{lane}:{path}")
    for path in shards:
        if path not in routed_paths:
            errors.append(f"unrouted_shard:{path}")

    events: dict[str, tuple[str, str, dict]] = {}
    shard_ids = set()
    for path, shard in shards.items():
        lane = routed_paths.get(path)
        shard_id = shard.get("shard_id")
        if shard.get("schema") != "canon-history-shard":
            errors.append(f"shard_schema_mismatch:{path}")
        if shard.get("campaign_authority") is not False:
            errors.append(f"shard_must_not_be_campaign_authority:{path}")
        if shard_id in shard_ids:
            errors.append(f"duplicate_shard_id:{shard_id}")
        shard_ids.add(shard_id)
        if shard.get("continuity_id") != continuity_id:
            errors.append(f"continuity_mismatch:{path}")
        if shard.get("lane") != lane:
            errors.append(f"lane_path_mismatch:{path}:{shard.get('lane')}:{lane}")
        if lane and not str(shard_id).startswith(f"canon.history.{lane}."):
            errors.append(f"shard_id_lane_mismatch:{path}:{shard_id}:{lane}")
        for event in shard.get("events", []):
            event_id = event.get("id")
            required_event_keys = {
                "id",
                "title",
                "paraphrase",
                "support_kind",
                "anchor_relation",
                "predecessor_refs",
                "subject_refs",
                "place_refs",
                "source_locators",
            }
            if set(event) != required_event_keys:
                errors.append(f"event_shape:{event_id}")
            if event_id in events:
                errors.append(f"duplicate_event_id:{event_id}")
            events[event_id] = (path, lane or "", event)
            if event.get("support_kind") not in {"directly_depicted", "explicitly_stated"}:
                errors.append(f"unsupported_support_kind:{event_id}:{event.get('support_kind')}")
            if not isinstance(event.get("title"), str) or not event.get("title"):
                errors.append(f"event_missing_title:{event_id}")
            if not isinstance(event.get("paraphrase"), str) or not event.get("paraphrase"):
                errors.append(f"event_missing_paraphrase:{event_id}")
            if event.get("anchor_relation") != "before":
                errors.append(f"event_not_pre_anchor:{event_id}")
            primary_locator = False
            for position, locator in enumerate(event.get("source_locators", [])):
                errors.extend(locator_errors(locator, catalog, f"event:{event_id}:{position}"))
                source = catalog.get(locator.get("work_id"), {})
                if (
                    source.get("source_class") == "primary_manga"
                    and source.get("approval_status") == "approved"
                    and locator.get("locator_kind") == "volume_chapter"
                ):
                    primary_locator = True
            if not primary_locator:
                errors.append(f"event_without_approved_primary_volume_chapter_locator:{event_id}")

    if anchor.get("binding_status") != "verified" and events:
        errors.append("unbound_anchor_contains_history_events")

    index = manifest.get("event_index", {})
    if set(index) != set(events):
        for event_id in sorted(set(events) - set(index)):
            errors.append(f"event_missing_from_index:{event_id}")
        for event_id in sorted(set(index) - set(events)):
            errors.append(f"index_dangling_event:{event_id}")
    for event_id, entry in index.items():
        actual = events.get(event_id)
        if actual and (entry.get("path"), entry.get("lane")) != actual[:2]:
            errors.append(
                f"event_index_mismatch:{event_id}:"
                f"{entry.get('path')}:{entry.get('lane')}:{actual[0]}:{actual[1]}"
            )

    for event_id, (_, _, event) in events.items():
        for predecessor in event.get("predecessor_refs", []):
            if predecessor == event_id:
                errors.append(f"self_predecessor:{event_id}")
            elif predecessor not in events:
                errors.append(f"unknown_predecessor:{event_id}:{predecessor}")
            elif {
                "public": 0,
                "restricted": 1,
                "secret": 2,
            }[events[predecessor][1]] > {
                "public": 0,
                "restricted": 1,
                "secret": 2,
            }[events[event_id][1]]:
                errors.append(f"visibility_predecessor_leak:{event_id}:{predecessor}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(event_id: str) -> None:
        if event_id in visited:
            return
        if event_id in visiting:
            errors.append(f"chronology_cycle:{event_id}")
            return
        visiting.add(event_id)
        event = events[event_id][2]
        for predecessor in event.get("predecessor_refs", []):
            if predecessor in events:
                visit(predecessor)
        visiting.remove(event_id)
        visited.add(event_id)

    for event_id in events:
        visit(event_id)
    return errors


def filesystem_errors(manifest: dict, shards: dict[str, dict]) -> list[str]:
    errors = []
    for lane in LANES:
        disk_paths = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / f"game/data/canon/history/{lane}").glob("*.json")
        }
        routed_paths = set(manifest["history_routes"][lane]["shards"])
        if disk_paths != routed_paths:
            errors.append(
                f"lane_manifest_file_mismatch:{lane}:"
                f"disk={sorted(disk_paths)}:routed={sorted(routed_paths)}"
            )
    if set(shards) != {
        path
        for lane in LANES
        for path in manifest["history_routes"][lane]["shards"]
    }:
        errors.append("loaded_shard_set_mismatch")

    repository_map = read_json(ROOT / "runtime/contracts/repository-map.json")
    if repository_map.get("route_shards", {}).get("canon") != "runtime/contracts/repository-routes/canon.json":
        errors.append("canon_route_shard_not_registered")
    for route_name in EXPECTED_ROUTE_NAMES:
        if repository_map.get("route_index", {}).get(route_name) != "canon":
            errors.append(f"canon_route_index_missing:{route_name}")

    route_shard = read_json(ROOT / "runtime/contracts/repository-routes/canon.json")
    if not EXPECTED_ROUTE_NAMES.issubset(set(route_shard.get("routes", {}))):
        errors.append("canon_route_shard_missing_history_routes")
    if route_shard.get("routes", {}).get("canon_history_known_id", {}).get("i") != [
        "game/data/canon/manifest.json"
    ]:
        errors.append("canon_known_id_route_not_manifest_first")
    for lane in LANES:
        route = route_shard.get("routes", {}).get(f"canon_history_{lane}", {})
        if route.get("i"):
            errors.append(f"canon_lane_route_leaks_global_index:{lane}")
        if route.get("g") != [f"game/data/canon/history/{lane}/*.json"]:
            errors.append(f"canon_lane_glob_mismatch:{lane}")

    directory_map = read_json(ROOT / "runtime/contracts/directory-map.json")
    expected_directories = {"game/data/canon", "game/data/canon/history"} | {
        f"game/data/canon/history/{lane}" for lane in LANES
    }
    for directory in expected_directories:
        if directory_map.get("dirs", {}).get(directory) != "mapped":
            errors.append(f"canon_directory_unmapped:{directory}")
    return errors


def fixture_bundle(manifest: dict, shards: dict[str, dict]) -> tuple[dict, dict[str, dict]]:
    fixture_manifest = copy.deepcopy(manifest)
    fixture_shards = copy.deepcopy(shards)
    locator = {
        "work_id": "canon.source.fixture",
        "locator_kind": "volume_chapter",
        "volume": "fixture-volume",
        "chapter": "fixture-chapter",
        "episode": None,
        "section": None,
    }
    fixture_manifest["source_catalog"] = [
        {
            "work_id": "canon.source.fixture",
            "title": "Synthetic validator fixture",
            "medium": "manga",
            "source_class": "primary_manga",
            "approval_status": "approved",
        }
    ]
    fixture_manifest["anchor"] = {
        "campaign_time": "SE-0000-01-01T00:00:00",
        "binding_status": "verified",
        "canon_event_id": "canon.anchor.fixture",
        "source_locators": [copy.deepcopy(locator)],
    }
    event = {
        "id": "canon.event.fixture",
        "title": "Synthetic validator event",
        "paraphrase": "Synthetic structural and semantic test record.",
        "support_kind": "directly_depicted",
        "anchor_relation": "before",
        "predecessor_refs": [],
        "subject_refs": [],
        "place_refs": [],
        "source_locators": [copy.deepcopy(locator)],
    }
    public_path = fixture_manifest["history_routes"]["public"]["shards"][0]
    fixture_shards[public_path]["events"] = [event]
    fixture_manifest["event_index"] = {
        event["id"]: {
            "path": public_path,
            "lane": "public",
        }
    }
    return fixture_manifest, fixture_shards


def main() -> int:
    manifest_schema = read_json(MANIFEST_SCHEMA_PATH)
    manifest_template = read_json(MANIFEST_TEMPLATE_PATH)
    shard_schema = read_json(SHARD_SCHEMA_PATH)
    shard_template = read_json(SHARD_TEMPLATE_PATH)
    manifest = read_json(MANIFEST_PATH)
    shards = {
        path: read_json(ROOT / path)
        for lane in LANES
        for path in manifest.get("history_routes", {}).get(lane, {}).get("shards", [])
        if (ROOT / path).exists()
    }
    failures = []

    failures.extend(structural_errors("canon-manifest", manifest, manifest_schema, manifest_template))
    for path, shard in shards.items():
        failures.extend(structural_errors(path, shard, shard_schema, shard_template))
    failures.extend(semantic_errors(manifest, shards))
    failures.extend(filesystem_errors(manifest, shards))

    valid_manifest, valid_shards = fixture_bundle(manifest, shards)
    valid_structural = structural_errors(
        "valid-fixture-manifest", valid_manifest, manifest_schema, manifest_template
    )
    for path, shard in valid_shards.items():
        valid_structural.extend(structural_errors(path, shard, shard_schema, shard_template))
    valid_semantic = semantic_errors(valid_manifest, valid_shards) if not valid_structural else []
    if valid_structural or valid_semantic:
        failures.append(f"valid_fixture_rejected:{valid_structural}:{valid_semantic}")

    unbound_manifest = copy.deepcopy(valid_manifest)
    unbound_manifest["anchor"] = {
        "campaign_time": None,
        "binding_status": "unbound",
        "canon_event_id": None,
        "source_locators": [],
    }
    if "unbound_anchor_contains_history_events" not in semantic_errors(unbound_manifest, valid_shards):
        failures.append("unbound_anchor_fixture_not_rejected")

    pending_manifest = copy.deepcopy(valid_manifest)
    pending_manifest["source_catalog"][0]["approval_status"] = "pending"
    if not any("source_not_approved" in error for error in semantic_errors(pending_manifest, valid_shards)):
        failures.append("pending_source_fixture_not_rejected")

    bad_index_manifest = copy.deepcopy(valid_manifest)
    bad_index_manifest["event_index"]["canon.event.fixture"]["lane"] = "secret"
    if not any("event_index_mismatch" in error for error in semantic_errors(bad_index_manifest, valid_shards)):
        failures.append("wrong_lane_fixture_not_rejected")

    dangling_shards = copy.deepcopy(valid_shards)
    public_path = valid_manifest["history_routes"]["public"]["shards"][0]
    dangling_shards[public_path]["events"][0]["predecessor_refs"] = ["canon.event.missing"]
    if not any("unknown_predecessor" in error for error in semantic_errors(valid_manifest, dangling_shards)):
        failures.append("dangling_chronology_fixture_not_rejected")

    visibility_manifest = copy.deepcopy(valid_manifest)
    visibility_shards = copy.deepcopy(valid_shards)
    secret_path = valid_manifest["history_routes"]["secret"]["shards"][0]
    secret_event = copy.deepcopy(visibility_shards[public_path]["events"][0])
    secret_event["id"] = "canon.event.secret_fixture"
    visibility_shards[secret_path]["events"] = [secret_event]
    visibility_manifest["event_index"][secret_event["id"]] = {
        "path": secret_path,
        "lane": "secret",
    }
    visibility_shards[public_path]["events"][0]["predecessor_refs"] = [secret_event["id"]]
    if not any(
        "visibility_predecessor_leak" in error
        for error in semantic_errors(visibility_manifest, visibility_shards)
    ):
        failures.append("visibility_predecessor_fixture_not_rejected")

    future_shards = copy.deepcopy(valid_shards)
    future_shards[public_path]["events"][0]["anchor_relation"] = "after"
    if not any(
        "event_not_pre_anchor" in error
        for error in semantic_errors(valid_manifest, future_shards)
    ):
        failures.append("future_event_fixture_not_rejected")

    memory_manifest = copy.deepcopy(valid_manifest)
    memory_manifest["source_catalog"][0]["source_class"] = "model_memory"
    if not any(
        "unsupported_source_class" in error
        for error in semantic_errors(memory_manifest, valid_shards)
    ):
        failures.append("model_memory_source_fixture_not_rejected")

    malformed_manifest = copy.deepcopy(valid_manifest)
    malformed_locator = malformed_manifest["anchor"]["source_locators"][0]
    malformed_locator.update(
        {
            "locator_kind": "episode",
            "volume": None,
            "chapter": None,
            "episode": "fixture-episode",
        }
    )
    if not any(
        "primary_source_without_volume_chapter" in error
        for error in semantic_errors(malformed_manifest, valid_shards)
    ):
        failures.append("primary_episode_locator_fixture_not_rejected")

    if failures:
        print(f"CANON CONTINUITY TEST FAILED {len(failures)}")
        for failure in failures[:250]:
            print("-", failure)
        if len(failures) > 250:
            print(f"- ... {len(failures) - 250} more")
        return 1

    current_event_count = sum(len(shard.get("events", [])) for shard in shards.values())
    print("CANON CONTINUITY TEST OK")
    print(
        f"anchor={manifest['anchor']['binding_status']} sources={len(manifest['source_catalog'])} "
        f"shards={len(shards)} events={current_event_count} negative_fixtures=8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
