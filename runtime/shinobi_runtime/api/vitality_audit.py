"""Read-only diagnostics for player-facing causal throughput."""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import OocAuditResult
from shinobi_runtime.information.store import InformationStore

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
        living_policy = _mapping(repository.read_json("game/rules/autonomy/living-world.json"))
        information_store = InformationStore(repository)
        information = _mapping(information_store.projection())
        scene = _mapping(repository.read_json("state/scene.json"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return {
            "available": 0,
            "active_fronts": 0,
            "bootstrap_capable_fronts": 0,
            "claims": 0,
            "deliveries": 0,
            "player_known_claims": 0,
            "scene_reports": 0,
            "player_offer_lanes": 0,
            "player_report_lanes": 0,
            "raw_decision_mismatch": 0,
        }

    player_id = meta.get("player_id")
    pressures = _mapping(pressure_registry.get("pressures"))
    fronts = _mapping(front_policy.get("fronts"))
    active_ids = {
        pressure_id
        for pressure_id, raw in pressures.items()
        if isinstance(pressure_id, str)
        and isinstance(raw, Mapping)
        and isinstance(raw.get("status"), str)
        and raw.get("status") not in _TERMINAL
    }
    bootstrap_capable = 0
    for front_id in active_ids:
        config = fronts.get(front_id)
        if not isinstance(config, Mapping):
            continue
        roles = config.get("faction_roles")
        bootstrap = config.get("bootstrap_action_cycle")
        prerequisites = config.get("prerequisite_front_refs", [])
        prerequisites_met = isinstance(prerequisites, list) and all(
            isinstance(ref, str)
            and isinstance(pressures.get(ref), Mapping)
            and isinstance(pressures[ref].get("evidence_refs"), list)
            and bool(pressures[ref]["evidence_refs"])
            for ref in prerequisites
        )
        if (
            prerequisites_met
            and isinstance(roles, Mapping)
            and any(role == "source" for role in roles.values())
            and isinstance(bootstrap, list)
            and any(isinstance(action, str) and action for action in bootstrap)
        ):
            bootstrap_capable += 1

    assignments = _mapping(living_policy.get("faction_assignments"))
    player_offer_lanes = 0
    player_report_lanes = 0
    for assignment in assignments.values():
        if not isinstance(assignment, Mapping):
            continue
        offer = assignment.get("player_offer")
        if isinstance(offer, Mapping) and offer.get("enabled") is True:
            player_offer_lanes += 1
        report = assignment.get("world_front_player_report")
        if isinstance(report, Mapping) and report.get("enabled") is True:
            player_report_lanes += 1

    claim_count = information.get("claim_count", 0)
    delivery_count = information.get("delivery_count", 0)
    holder_summary = information_store.holder_summary(player_id) if isinstance(player_id, str) else {"claim_count": 0}
    known_count = holder_summary.get("claim_count", 0)
    reports = _list(_mapping(scene.get("narrative")).get("available_reports"))
    decision = scene.get("decision_required")
    mismatch = int(
        scene.get("time_passage_allowed") is True
        and isinstance(decision, str)
        and bool(decision.strip())
    )
    return {
        "available": 1,
        "active_fronts": len(active_ids),
        "bootstrap_capable_fronts": bootstrap_capable,
        "claims": claim_count if isinstance(claim_count, int) else 0,
        "deliveries": delivery_count if isinstance(delivery_count, int) else 0,
        "player_known_claims": known_count if isinstance(known_count, int) and not isinstance(known_count, bool) else 0,
        "scene_reports": len(reports),
        "player_offer_lanes": player_offer_lanes,
        "player_report_lanes": player_report_lanes,
        "raw_decision_mismatch": mismatch,
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
                f"active_fronts={summary['active_fronts']} "
                f"bootstrap_capable={summary['bootstrap_capable_fronts']} "
                f"claims={summary['claims']} deliveries={summary['deliveries']} "
                f"player_known_claims={summary['player_known_claims']} "
                f"scene_reports={summary['scene_reports']} "
                f"player_offer_lanes={summary['player_offer_lanes']} "
                f"player_report_lanes={summary['player_report_lanes']} "
                f"raw_decision_mismatch={summary['raw_decision_mismatch']} "
                "boundary_semantics=internal_soft_hard"
            )
            if line not in diagnostics and len(diagnostics) < 64:
                diagnostics.append(line)
            if summary["active_fronts"] > 0 and summary["bootstrap_capable_fronts"] == 0 and len(suggestions) < 64:
                suggestions.append("review_active_world_front_bootstrap_routes_before_assuming_the_world_is_progressing")
            if summary["claims"] >= 8 and summary["deliveries"] == 0 and summary["player_known_claims"] == 0 and summary["player_report_lanes"] > 0 and len(suggestions) < 64:
                suggestions.append("review_information_propagation_when_world_reports_accumulate_without_player_facing_delivery")
            if summary["raw_decision_mismatch"] and len(suggestions) < 64:
                suggestions.append("open_time_scene_contains_stale_decision_marker_projection_must_not_treat_it_as_hard_stop")
        return OocAuditResult(
            diagnostics=tuple(diagnostics[:64]),
            suggestions=tuple(dict.fromkeys(suggestions))[:64],
            write_plan=result.write_plan,
        )

    wrapped._playability_vitality_audit = True
    audit_class.__call__ = wrapped


__all__ = ["install_playability_vitality_audit", "summarize_playability_vitality"]
