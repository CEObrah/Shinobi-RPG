#!/usr/bin/env python3
"""Bind a Shinobi release candidate to its intended full-fresh campaign snapshot."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from shinobi_runtime.martial_world.equipment_state import effective_person_loadout

CONTRACT_REL = Path("runtime/contracts/release-campaign-state.json")


def state_tree_sha256(root: Path) -> str:
    state = root / "state"
    digest = hashlib.sha256()
    files = sorted((p for p in state.rglob("*") if p.is_file()), key=lambda p: p.relative_to(state).as_posix())
    for path in files:
        rel = path.relative_to(state).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _load_people(root: Path, refs: set[str]) -> dict[str, dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}
    for path in (root / "state/martial-world/people").glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        for row in doc.get("people", []) if isinstance(doc, dict) else []:
            if isinstance(row, dict) and row.get("person_id") in refs:
                people[str(row["person_id"])] = row
        if len(people) == len(refs):
            break
    return people


def verify_release_state(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        contract: dict[str, Any] = json.loads((root / CONTRACT_REL).read_text(encoding="utf-8"))
        meta: dict[str, Any] = json.loads((root / "state/meta.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"release campaign state inputs unreadable: {exc}"]

    for actual_key, contract_key in (
        ("campaign_id", "campaign_id"),
        ("player_id", "player_id"),
        ("revision", "revision"),
        ("time", "world_time"),
    ):
        expected = contract.get(contract_key)
        actual = meta.get(actual_key)
        if actual != expected:
            errors.append(f"campaign baseline mismatch {actual_key}: expected {expected!r}, got {actual!r}")

    expected_hash = str(contract.get("state_tree_sha256") or "")
    actual_hash = state_tree_sha256(root)
    if actual_hash != expected_hash:
        errors.append(f"campaign state tree hash mismatch: expected {expected_hash}, got {actual_hash}")

    try:
        combats = json.loads((root / "state/martial-world/combats.json").read_text(encoding="utf-8")).get("combats", {})
        combat_ref = str(contract["active_combat_ref"])
        combat = combats.get(combat_ref)
        if not isinstance(combat, dict):
            errors.append(f"expected active combat missing: {combat_ref}")
            combat = {}
        if combat.get("status") != "active":
            errors.append(f"expected combat not active: {combat.get('status')!r}")
        if int(combat.get("elapsed_ms", -1)) != int(contract["active_combat_elapsed_ms"]):
            errors.append(f"combat elapsed mismatch: expected {contract['active_combat_elapsed_ms']}, got {combat.get('elapsed_ms')}")
        side_sizes = sorted(len(v) for v in combat.get("sides", {}).values() if isinstance(v, list))
        if side_sizes != sorted(int(x) for x in contract["active_combat_side_sizes"]):
            errors.append(f"combat side sizes mismatch: expected {contract['active_combat_side_sizes']}, got {side_sizes}")
        combatants = combat.get("combatants", {}) if isinstance(combat.get("combatants"), dict) else {}
        if len(combatants) != int(contract["active_combatant_count"]):
            errors.append(f"combatant count mismatch: expected {contract['active_combatant_count']}, got {len(combatants)}")

        refs = set(str(ref) for ref in combatants)
        people = _load_people(root, refs)
        if set(people) != refs:
            missing = sorted(refs - set(people))
            errors.append(f"active combatants missing exact person rows: {missing[:8]}")
        for person_ref, person in people.items():
            health = person.get("health", {}) if isinstance(person.get("health"), dict) else {}
            if int(person.get("fatigue_milli", 0) or 0) != 0:
                errors.append(f"fresh baseline fatigue nonzero: {person_ref}")
            if health.get("injuries") not in (None, []):
                errors.append(f"fresh baseline injuries present: {person_ref}")
            if int(health.get("blood_lost_ml", 0) or 0) != 0:
                errors.append(f"fresh baseline blood loss nonzero: {person_ref}")
            if int(health.get("shock", 0) or 0) != 0:
                errors.append(f"fresh baseline shock nonzero: {person_ref}")
            if int(health.get("toxicity_milli", 0) or 0) != 0:
                errors.append(f"fresh baseline toxicity nonzero: {person_ref}")
            if person.get("poison_burdens") not in (None, {}):
                errors.append(f"fresh baseline poison burden present: {person_ref}")
            if person.get("pending_poison_burdens") not in (None, {}):
                errors.append(f"fresh baseline pending poison burden present: {person_ref}")

        player_ref = str(contract["player_id"])
        player = people.get(player_ref)
        if not isinstance(player, dict):
            errors.append("player is not one of the exact active combatants")
        elif int(player.get("qi", -1)) != int(contract["player_qi"]):
            errors.append(f"player Qi mismatch: expected {contract['player_qi']}, got {player.get('qi')}")

        ledger = json.loads((root / "state/martial-world/equipment-ledger.json").read_text(encoding="utf-8"))
        loadout = effective_person_loadout(ledger, player_ref)
        needles = int(loadout.get("items", {}).get("weapon_needle", 0) or 0)
        if needles != int(contract["player_needles"]):
            errors.append(f"player needle count mismatch: expected {contract['player_needles']}, got {needles}")
        jian_condition = int(loadout.get("condition_milli", {}).get("weapon_jian", 0) or 0)
        if jian_condition != int(contract["player_jian_condition_milli"]):
            errors.append(f"player jian condition mismatch: expected {contract['player_jian_condition_milli']}, got {jian_condition}")

        attempts = json.loads((root / "state/martial-world/interaction-attempts.json").read_text(encoding="utf-8"))
        attempt_count = int(attempts.get("total_recorded", len(attempts.get("attempts", []))) or 0)
        if attempt_count != int(contract["interaction_attempt_count"]):
            errors.append(f"interaction attempt count mismatch: expected {contract['interaction_attempt_count']}, got {attempt_count}")
        history = json.loads((root / "state/martial-world/scene-history-head.json").read_text(encoding="utf-8"))
        history_count = int(history.get("total_recorded", 0) or 0)
        if history_count != int(contract["scene_history_count"]):
            errors.append(f"scene history count mismatch: expected {contract['scene_history_count']}, got {history_count}")
    except Exception as exc:
        errors.append(f"fresh combat baseline verification failed: {type(exc).__name__}: {exc}")

    return errors


def main() -> int:
    errors = verify_release_state(ROOT)
    if errors:
        print("RELEASE CAMPAIGN STATE FAILED")
        for error in errors:
            print(" -", error)
        return 1
    contract = json.loads((ROOT / CONTRACT_REL).read_text(encoding="utf-8"))
    print(
        "RELEASE CAMPAIGN STATE OK:",
        contract["release_baseline_id"],
        f"revision={contract['revision']}",
        f"state_sha256={contract['state_tree_sha256']}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
