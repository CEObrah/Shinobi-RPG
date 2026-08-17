from __future__ import annotations
import copy
from typing import Any, Dict, Mapping
from shinobi_runtime.api.contracts import CommandRejectedError

PRESSURE_PATH = "state/canon/pressures.json"
POLICY_PATH = "game/rules/autonomy/world-fronts.json"
TERMINAL = frozenset(("completed","resolved","failed","cancelled","abandoned","superseded"))


def policy(repository: Any) -> Mapping[str, Any]:
    try: value = repository.read_json(POLICY_PATH)
    except (FileNotFoundError, ValueError) as exc: raise CommandRejectedError("world_front_policy_invalid") from exc
    if not isinstance(value, Mapping) or value.get("schema") != "world-front-policy" or value.get("version") != 1: raise CommandRejectedError("world_front_policy_invalid")
    if not isinstance(value.get("fronts"), Mapping) or not isinstance(value.get("phase_thresholds"), Mapping) or not isinstance(value.get("material_action_kinds"), list): raise CommandRejectedError("world_front_policy_invalid")
    return value


def pressure_registry(repository: Any) -> Dict[str, Any]:
    try: value = repository.read_json(PRESSURE_PATH)
    except (FileNotFoundError, ValueError) as exc: raise CommandRejectedError("canon_pressure_registry_invalid") from exc
    if not isinstance(value, dict) or value.get("schema") != "canon-pressure-registry" or not isinstance(value.get("pressures"), dict): raise CommandRejectedError("canon_pressure_registry_invalid")
    return copy.deepcopy(value)


def front_phase(pressure: Mapping[str, Any], rules: Mapping[str, Any]) -> str:
    if pressure.get("status") in TERMINAL: return "resolved"
    evidence = pressure.get("evidence_refs"); count = len(evidence) if isinstance(evidence, list) else 0
    limits = rules.get("phase_thresholds") if isinstance(rules.get("phase_thresholds"), Mapping) else {}
    developing, operational, crisis = int(limits.get("developing_evidence",1)), int(limits.get("operational_evidence",3)), int(limits.get("crisis_evidence",6))
    if count < developing: return "latent"
    if count < operational: return "developing"
    if count < crisis: return "operational"
    return "crisis"
