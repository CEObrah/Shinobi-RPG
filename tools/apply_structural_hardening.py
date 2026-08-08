#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def write_json(rel: str, value) -> None:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_templates() -> dict[str, dict]:
    index = read_json("data/runtime/template-index.json")
    out: dict[str, dict] = {}
    for shard_rel in index.get("shards", {}).values():
        out.update(read_json(shard_rel).get("templates", {}))
    return out


def decode_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise SystemExit(f"invalid template pointer:{pointer}")
    return [segment.replace("~1", "/").replace("~0", "~") for segment in pointer[1:].split("/")]


def encode_segment(segment: str) -> str:
    return segment.replace("~", "~0").replace("/", "~1")


def placeholder(type_spec):
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    if "object" in types:
        return {}
    if "array" in types:
        return []
    return None


def put(root: dict, pointer: str, value) -> None:
    segments = decode_pointer(pointer)
    if not segments or "*" in segments:
        return
    current = root
    for idx, segment in enumerate(segments):
        last = idx == len(segments) - 1
        if not isinstance(current, dict):
            return
        if last:
            current.setdefault(segment, value)
            return
        current.setdefault(segment, {})
        if isinstance(current[segment], list):
            return
        if current[segment] is None:
            current[segment] = {}
        if not isinstance(current[segment], dict):
            return
        current = current[segment]


def blank_from_template(template: dict) -> dict:
    type_contracts = template.get("type_contracts", {})
    object_contracts = template.get("object_contracts", {})
    array_contracts = template.get("array_contracts", {})
    if not isinstance(type_contracts, dict) or not isinstance(object_contracts, dict) or not isinstance(array_contracts, dict):
        raise SystemExit(f"missing structural contracts:{template.get('target_schema')}")
    root: dict = {}
    for pointer, type_spec in sorted(type_contracts.items(), key=lambda item: (item[0].count("/"), item[0])):
        if pointer and "*" not in decode_pointer(pointer):
            put(root, pointer, placeholder(type_spec))
    for pointer in sorted(array_contracts, key=lambda p: (p.count("/"), p)):
        if "*" not in decode_pointer(pointer):
            put(root, pointer, [])
    for pointer, contract in sorted(object_contracts.items(), key=lambda item: (item[0].count("/"), item[0])):
        if "*" in decode_pointer(pointer):
            continue
        if pointer:
            put(root, pointer, {})
        for key in contract.get("allowed_keys", []):
            child = f"{pointer}/{encode_segment(key)}" if pointer else f"/{encode_segment(key)}"
            if child in type_contracts:
                value = placeholder(type_contracts[child])
            elif child in array_contracts:
                value = []
            elif child in object_contracts:
                value = {}
            else:
                value = None
            put(root, child, value)
    return root


# Exact semantic owner routing. This is intentionally explicit; no prefix-based
# inference is used to grant write authority.
EXISTING_ADDITIONS = {
    "health_medical": [
        "biological-implant-state",
        "ocular-owner-shard",
        "ocular-registry-index",
        "ocular-stockpile-batch",
        "seal-state-registry",
    ],
    "command": ["command-assignments", "command-group-index"],
    "family": ["family-index", "kinship-index"],
    "forces_institutions": [
        "covenant",
        "home_establishment_index",
        "house",
        "house_charter",
        "house_units",
        "manpower-capability",
        "process",
        "stock",
    ],
    "techniques_special": ["jinchuriki-registry", "puppet-registry", "summon-registry"],
    "time_process": ["life-course-registry"],
    "reputation": ["reputation-index"],
    "teams_formations": ["team", "team-doctrine-registry"],
}

AUTHORITY_ADDITIONS = {
    "command": ["state/org/assignments.json"],
    "forces_institutions": ["state/manpower-capability/"],
    "time_process": ["state/reg/life-course-registry.json"],
}

NEW_CONTRACTS = {
    "campaign_runtime": {
        "schema": "system-contract.v1",
        "system_id": "campaign_runtime",
        "authority": False,
        "authority_paths": ["state/meta.json", "state/scene.json"],
        "owner_templates": ["meta", "scene"],
        "read_first": ["current meta", "current scene only when scene-local state matters"],
        "write_order": ["resolve authoritative causal changes first", "advance meta time/revision only inside the canonical transaction", "write scene as the player-facing current situation after authoritative owners settle"],
        "invariants": ["Meta owns campaign clock/revision identity; scene does not override world owners.", "Scene records the current observable/decision surface, not hidden world truth.", "No campaign time or revision changes outside a validated gameplay transaction."],
        "validators": ["tools/test_transaction_integrity.py", "tools/test_runtime.py", "tools/test_templates.py", "tools/test_structural_determinism.py"],
    },
    "derived_indexes": {
        "schema": "system-contract.v1",
        "system_id": "derived_indexes",
        "authority": False,
        "authority_paths": ["state/index/", "state/reg/factions.json"],
        "owner_templates": ["faction-registry-index", "owner-index-shard", "owner_index", "unit-path-index-shard", "unit-routing-index", "unit_owner_index"],
        "read_first": ["authoritative owner(s) that changed", "only the derived index being rebuilt"],
        "write_order": ["write authoritative owner change first", "rebuild affected discovery index deterministically", "validate every indexed path/id against authority"],
        "invariants": ["Indexes are discovery aids and never gameplay truth.", "No gameplay fact may be authored first in an index.", "A missing or stale index never licenses invention; direct known owner paths remain authoritative."],
        "validators": ["tools/audit.py", "tools/test_routing.py", "tools/test_unit_model.py", "tools/test_structural_determinism.py"],
    },
    "world_state": {
        "schema": "system-contract.v1",
        "system_id": "world_state",
        "authority": False,
        "authority_paths": ["state/world/", "state/place/", "state/reg/factions/", "state/reg/world-events.json"],
        "owner_templates": ["faction-owner", "place", "shinobi-world-registry", "world-event-registry"],
        "read_first": ["exact world/place/faction owner causally involved", "only referenced process/mechanics needed to resolve change"],
        "write_order": ["validate cause, authority, elapsed time and information limits", "write authoritative world owner", "rebuild affected indexes and process consequences"],
        "invariants": ["Offscreen world state changes only through registered processes or resolved causal actions.", "World ownership/control/resources are never inferred from labels or indexes.", "Player knowledge remains separate from world truth."],
        "validators": ["tools/test_living_world.py", "tools/test_runtime.py", "tools/test_templates.py", "tools/test_structural_determinism.py"],
    },
    "player_combat_detail": {
        "schema": "system-contract.v1",
        "system_id": "player_combat_detail",
        "authority": False,
        "authority_paths": ["state/player-detail/"],
        "owner_templates": ["player-combat-style"],
        "read_first": ["player combat-detail owner", "referenced static combat/technique package only when execution requires it"],
        "write_order": ["require explicit player-authored doctrine change", "validate referenced mechanics and legal technique access", "persist only the declared doctrine/detail change"],
        "invariants": ["Permanent player doctrine is player-controlled intent and is never invented or silently normalized.", "Combat style cannot grant techniques, stats, equipment, knowledge, authority, or resources.", "Temporary tactical choices do not rewrite permanent doctrine unless the player explicitly makes them standing policy."],
        "validators": ["tools/test_mechanics.py", "tools/test_semantics.py", "tools/test_templates.py", "tools/test_structural_determinism.py"],
    },
}


def main() -> None:
    template_entries = load_templates()
    mutable: dict[str, dict] = {}
    for schema_id, entry in template_entries.items():
        template = read_json(entry["path"])
        if template.get("scope") == "mutable_state":
            mutable[schema_id] = template

    # Update existing system contracts only from the explicit routing map.
    system_index = read_json("data/runtime/system-contract-index.json")
    systems = system_index.setdefault("systems", {})
    for system_id, additions in EXISTING_ADDITIONS.items():
        rel = systems[system_id]
        contract = read_json(rel)
        owners = set(contract.get("owner_templates", []))
        owners.update(additions)
        contract["owner_templates"] = sorted(owners)
        paths = list(contract.get("authority_paths", []))
        for path in AUTHORITY_ADDITIONS.get(system_id, []):
            if path not in paths:
                paths.append(path)
        contract["authority_paths"] = paths
        write_json(rel, contract)

    # Register narrow missing authority domains.
    for system_id, contract in NEW_CONTRACTS.items():
        rel = f"data/runtime/system-contracts/{system_id}.json"
        write_json(rel, contract)
        systems[system_id] = rel
    system_index["systems"] = dict(sorted(systems.items()))
    write_json("data/runtime/system-contract-index.json", system_index)

    # Generate one fact-free blank authoring skeleton per mutable owner type.
    blank_map = {}
    blank_dir = ROOT / "data/runtime/blank-owners"
    blank_dir.mkdir(parents=True, exist_ok=True)
    for schema_id, template in sorted(mutable.items()):
        rel = f"data/runtime/blank-owners/{schema_id}.blank.json"
        write_json(rel, blank_from_template(template))
        blank_map[schema_id] = rel
    write_json("data/runtime/blank-owner-index.json", {
        "purpose": "Fact-free structural authoring skeletons for mutable owners. Values are placeholders, never campaign defaults.",
        "owners": blank_map,
    })

    # Tighten the canonical runtime rule without carrying migration history.
    runtime_path = ROOT / "RUNTIME.md"
    runtime = runtime_path.read_text(encoding="utf-8")
    runtime = runtime.replace(
        "Structural writes use one exact cold file template plus the relevant system update contract.",
        "Structural writes use one exact cold file template, its registered blank owner skeleton, and the relevant system update contract. Existing owners and examples never define structure.",
    )
    old = "One fact has one authoritative owner. Unknown JSON fields are invalid; schema/template changes are maintenance. Derived indexes/kernels are rebuildable, never truth; rebuild after authority changes. Never infer mutable rank, roster, ownership, mastery, injuries, force size, relationships, appointments, or player plans from documentation. Only this repository is authority; never import another game repository's state, mechanics, examples, IDs, or assumptions."
    new = "One fact has one authoritative owner. Every mutable owner type has a registered structural template, a registered blank owner skeleton, and at least one registered system update contract. Create owners from the blank skeleton and fill only fields permitted by the template under that system contract. Existing owners, neighboring files, examples, indexes, chat memory, and model inference are never structural authority. A field absent from the template is illegal. Adding, removing, renaming, or changing a field/type is maintenance: update the formal schema, structural template, blank skeleton, and every affected system contract first, then validate before gameplay can write it. Blank skeletons define shape only and never supply campaign facts, defaults, resources, capability, relationships, or intent. If a template, blank skeleton, or contract is missing or ambiguous, persistence stops; do not improvise. Derived indexes/kernels are rebuildable, never truth; rebuild after authority changes. Never infer mutable rank, roster, ownership, mastery, injuries, force size, relationships, appointments, or player plans from documentation. Only this repository is authority; never import another game repository's state, mechanics, examples, IDs, or assumptions."
    if old not in runtime:
        raise SystemExit("RUNTIME maintenance paragraph changed; refusing blind rewrite")
    runtime = runtime.replace(old, new)
    runtime_path.write_text(runtime, encoding="utf-8")

    # Route structural writes through the new blank-skeleton registry.
    repo_map = read_json("data/runtime/repository-map.json")
    repo_map["blank_owner_index"] = "data/runtime/blank-owner-index.json"
    invariants = repo_map.get("retrieval_invariants", [])
    old_inv = "Before any structural write, load the target file template and relevant system update contract."
    new_inv = "Before any structural write, load the target file template, registered blank owner skeleton, and relevant system update contract; existing owners/examples never define structure."
    if old_inv in invariants:
        invariants[invariants.index(old_inv)] = new_inv
    elif new_inv not in invariants:
        raise SystemExit("repository-map structural invariant changed; refusing blind rewrite")
    route = repo_map.get("routes", {}).get("write_template_lookup")
    if not isinstance(route, dict):
        raise SystemExit("missing write_template_lookup route")
    route["i"] = ["data/runtime/template-index.json", "data/runtime/blank-owner-index.json"]
    route["note"] = "Before creating or structurally changing JSON, resolve target schema to its cold structural template and fact-free blank skeleton. No unregistered fields; missing or ambiguous structure stops persistence."
    write_json("data/runtime/repository-map.json", repo_map)

    # Make structural determinism part of normal CI.
    audit_path = ROOT / ".github/workflows/audit.yml"
    audit = audit_path.read_text(encoding="utf-8")
    marker = "      - run: python tools/test_templates.py\n"
    addition = marker + "      - run: python tools/test_structural_determinism.py\n"
    if "python tools/test_structural_determinism.py" not in audit:
        if marker not in audit:
            raise SystemExit("audit workflow template marker missing")
        audit = audit.replace(marker, addition)
        audit_path.write_text(audit, encoding="utf-8")

    # Prove explicit contract coverage before handing off to validators.
    coverage = set()
    system_index = read_json("data/runtime/system-contract-index.json")
    for rel in system_index["systems"].values():
        contract = read_json(rel)
        coverage.update(schema for schema in contract.get("owner_templates", []) if schema in mutable)
    missing = sorted(set(mutable) - coverage)
    if missing:
        raise SystemExit(f"mutable owners still lack system contract coverage:{missing}")

    print(json.dumps({
        "mutable_owner_types": len(mutable),
        "blank_skeletons_generated": len(blank_map),
        "contract_covered": len(coverage),
        "new_system_contracts": sorted(NEW_CONTRACTS),
    }, indent=2))


if __name__ == "__main__":
    main()
