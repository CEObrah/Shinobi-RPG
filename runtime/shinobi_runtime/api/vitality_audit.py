"""Read-only diagnostics for player-facing causal throughput."""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import OocAuditResult

_TERMINAL = frozenset(("completed", "resolved", "failed", "cancelled", "abandoned", "superseded"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def summarize_playability_vitality(repository: Any) -> Mapping[str, int]:
    try:
        meta = _mapping(repository.read_json("state/meta.json"))
        pressure_registry = _mapping(repository.read_json("state/canon/pressures.json"))
        front_policy = _mapping(repository.read_json("game/rules/autonomy/world-fronts.json"))
        information = _mapping(repository.read_json("state/reg/information-deliveries.json"))
        scene = _mapping(repository.read_json("state/scene.json"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return {"available": 0, "active_fronts": 0, "bootstrap_capable_fronts": 0, "claims": 0, "deliveries": 0, "player_known_claims": 0, "scene_reports": 0}
    player_id = meta.get("player_id")
    pressures = _mapping(pressure_registry.get("pressures"))
    fronts = _mapping(front_policy.get("fronts"))
    active_ids = {
        pressure_id for pressure_id, raw in pressures.items()
        if isinstance(pressure_id, str) and isinstance(raw, Mapping)
        and isinstance(raw.get("status"), str) and raw.get("status") not in _TERMINAL
    }
    bootstrap_capable = 0
    for front_id in active_ids:
        config = fronts.get(front_id)
        if not isinstance(config, Mapping):
            continue
        roles = config.get("faction_roles")
        bootstrap = config.get("bootstrap_action_cycle")
        if isinstance(roles, Mapping) and any(role == "source" for role in roles.values()) and isinstance(bootstrap, list) and any(isinstance(action, str) and action for action in bootstrap):
            bootstrap_capable += 1
    claims = _mapping(information.get("claims"))
    deliveries = _list(information.get("deliveries"))
    knowledge = _mapping(information.get("knowledge"))
    known = knowledge.get(player_id) if isinstance(player_id, str) else []
    reports = _list(_mapping(scene.get("narrative")).get("available_reports"))
    return {
        "available": 1,
        "active_fronts": len(active_ids),
        "bootstrap_capable_fronts": bootstrap_capable,
        "claims": len(claims),
        "deliveries": len(deliveries),
        "player_known_claims": len(known) if isinstance(known, list) else 0,
        "scene_reports": len(reports),
    }


def install_playability_vitality_audit(audit_class: type) -> None:
    original = audit_class.__call__
    if getattr(original, "_playability_vitality_audit", False):
        return

    @wraps(original)
    def wrapped(self: Any, focus: Any, observations: Any) -> OocAuditResult:
        result = original(self, focus, observations)
        summary = summarize_playability_vitality(self.repository)
        diagnostics = list(result.diagnostics)
        suggestions = list(result.suggestions)
        if summary["available"]:
            line = (
                "playability_vitality:summary "
                f"active_fronts={summary['active_fronts']} bootstrap_capable={summary['bootstrap_capable_fronts']} "
                f"claims={summary['claims']} deliveries={summary['deliveries']} "
                f"player_known_claims={summary['player_known_claims']} scene_reports={summary['scene_reports']}"
            )
            if line not in diagnostics and len(diagnostics) < 64:
                diagnostics.append(line)
            if summary["active_fronts"] > 0 and summary["bootstrap_capable_fronts"] == 0 and len(suggestions) < 64:
                suggestions.append("review_active_world_front_bootstrap_routes_before_assuming_the_world_is_progressing")
            if summary["claims"] >= 8 and summary["deliveries"] == 0 and summary["player_known_claims"] == 0 and len(suggestions) < 64:
                suggestions.append("review_information_propagation_when_world_reports_accumulate_without_player_facing_delivery")
        return OocAuditResult(diagnostics=tuple(diagnostics[:64]), suggestions=tuple(dict.fromkeys(suggestions))[:64], write_plan=result.write_plan)

    wrapped._playability_vitality_audit = True
    audit_class.__call__ = wrapped


__all__ = ["install_playability_vitality_audit", "summarize_playability_vitality"]
