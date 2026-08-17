from __future__ import annotations

import copy
import hashlib
from functools import wraps
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.autonomy import AutonomousDecision
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache
from shinobi_runtime.commands.player_mission_offer_policy import PlayerMissionOfferPolicyMixin
from shinobi_runtime.commands.world_front_plan import progress_plan
from shinobi_runtime.commands.world_front_rules import front_phase, policy, pressure_registry
from shinobi_runtime.reducers import InformationClaim, deliver_claim
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.information import InformationStore

_INSTALLED = False
_PROJECTION_INSTALLED = False


def _profile_action_set(profile: Mapping[str, Any]) -> set[str]:
    raw = profile.get("action_cycle")
    if not isinstance(raw, list):
        return set()
    return {value for value in raw if isinstance(value, str) and value}


def _front_actions(config: Mapping[str, Any], *, key: str, profile: Mapping[str, Any], rules: Mapping[str, Any]) -> list[str]:
    raw = config.get(key)
    material = rules.get("material_action_kinds")
    if not isinstance(raw, list) or not isinstance(material, list):
        return []
    profile_allowed = _profile_action_set(profile)
    material_allowed = {value for value in material if isinstance(value, str)}
    return [value for value in raw if isinstance(value, str) and value in profile_allowed and value in material_allowed]


def _front_prerequisites_met(config: Mapping[str, Any], pressures: Mapping[str, Any]) -> bool:
    refs = config.get("prerequisite_front_refs")
    if refs is None:
        return True
    if not isinstance(refs, list):
        raise CommandRejectedError("world_front_policy_invalid")
    for ref in refs:
        prerequisite = pressures.get(ref) if isinstance(ref, str) else None
        evidence = prerequisite.get("evidence_refs") if isinstance(prerequisite, Mapping) else None
        if not isinstance(evidence, list) or not evidence:
            return False
    return True


def route_world_front_decision(decision: AutonomousDecision, *, at: Any, rules: Mapping[str, Any], registry: Mapping[str, Any], profile: Mapping[str, Any]) -> tuple[AutonomousDecision, str | None]:
    """Attach one lawful autonomous decision to one causal world front.

    A latent front may only be seeded by its configured source actor, and only
    when the faction was already going to take a material action allowed by its
    ordinary autonomy profile. Developing and operational fronts use their
    strategic action cycle. A crisis front may opt into a stronger configured
    crisis cycle, but that cycle is still intersected with the source faction's
    ordinary lawful action profile and the global material-action allowlist.
    Canon pressure therefore raises urgency without inventing a new capability
    or predetermining an outcome. A routine summary may be upgraded only to an
    action already present in the same faction profile.
    """
    payload = decision.payload if isinstance(decision.payload, Mapping) else {}
    faction_id = payload.get("faction_id")
    if not isinstance(faction_id, str):
        return decision, None
    fronts = rules.get("fronts")
    pressures = registry.get("pressures")
    if not isinstance(fronts, Mapping) or not isinstance(pressures, Mapping):
        return decision, None
    for front_id, config in sorted(fronts.items()):
        if not isinstance(front_id, str) or not isinstance(config, Mapping):
            continue
        roles = config.get("faction_roles")
        pressure = pressures.get(front_id)
        if not isinstance(roles, Mapping) or not isinstance(pressure, Mapping):
            continue
        role = roles.get(faction_id)
        if role not in ("source", "opposition"):
            continue
        if not _front_prerequisites_met(config, pressures):
            continue
        phase = front_phase(pressure, rules)
        if phase == "resolved":
            continue
        if phase == "latent":
            if role != "source":
                continue
            allowed = _front_actions(config, key="bootstrap_action_cycle", profile=profile, rules=rules)
        elif phase == "crisis":
            allowed = _front_actions(config, key="crisis_action_cycle", profile=profile, rules=rules)
            if not allowed:
                allowed = _front_actions(config, key="strategic_action_cycle", profile=profile, rules=rules)
        else:
            allowed = _front_actions(config, key="strategic_action_cycle", profile=profile, rules=rules)
        if not allowed:
            continue
        if decision.kind in allowed:
            kind = decision.kind
            reason = decision.reason
        elif decision.kind == "routine_summary":
            seed = f"{front_id}\x00{faction_id}\x00{at}\x00front-action"
            index = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
            kind = allowed[index % len(allowed)]
            reason = f"A saved faction review has a lawful material response inside the active pressure {front_id}."
        else:
            continue
        routed_payload = dict(payload)
        routed_payload["world_front_ref"] = front_id
        return AutonomousDecision(kind=kind, actor_ref=decision.actor_ref, reason=reason, payload=routed_payload, material=True), front_id
    return decision, None


def _front_report_config(assignment: Mapping[str, Any], front_ref: str) -> Mapping[str, Any] | None:
    config = assignment.get("world_front_player_report")
    if not isinstance(config, Mapping) or config.get("enabled") is not True:
        return None
    refs = config.get("front_refs")
    if not isinstance(refs, list) or front_ref not in refs:
        return None
    if config.get("recipient_scope") != "authenticated_player":
        return None
    channel = config.get("channel")
    confidence = config.get("channel_confidence_milli")
    classification = config.get("classification", "restricted")
    if not isinstance(channel, str) or not channel or isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 1000 or classification not in ("public", "restricted", "secret"):
        raise CommandRejectedError("world_front_player_report_policy_invalid")
    return config


def _claim_from_record(record: Mapping[str, Any]) -> InformationClaim:
    try:
        return InformationClaim(
            claim_id=str(record["claim_id"]),
            subject_ref=str(record["subject_ref"]),
            source_ref=str(record["source_ref"]),
            collected_at=CampaignTime.parse(record["collected_at"]),
            epistemic_kind=str(record["epistemic_kind"]),
            confidence_milli=int(record["confidence_milli"]),
            evidence_refs=tuple(value for value in record.get("evidence_refs", []) if isinstance(value, str)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CommandRejectedError("world_front_player_report_claim_invalid") from exc


def _deliver_player_front_report(owner: Any, *, result: Mapping[str, Any], decision: AutonomousDecision, front_ref: str, assignment: Mapping[str, Any], at: CampaignTime, command: Any, world_events: Dict[str, Any], record_writes: Dict[str, Dict[str, Any]]) -> list[Mapping[str, Any]]:
    if command.mode != "gameplay" or result.get("kind") != "information_report" or result.get("skipped") is not None:
        return []
    config = _front_report_config(assignment, front_ref)
    if config is None:
        return []
    claim_id = result.get("claim_id")
    if not isinstance(claim_id, str):
        return []
    information = InformationStore(owner.repository, record_writes)
    try:
        claim_record = information.claim(claim_id)
    except ValueError as exc:
        raise CommandRejectedError("information_registry_invalid") from exc
    if not isinstance(claim_record, Mapping):
        raise CommandRejectedError("world_front_player_report_claim_missing")
    sender_ref = decision.actor_ref
    try:
        sender_known = information.holder_knows(sender_ref, claim_id)
    except ValueError as exc:
        raise CommandRejectedError("information_registry_invalid") from exc
    if not sender_known:
        raise CommandRejectedError("world_front_player_report_sender_unknown")
    recipient_ref = command.actor_id
    try:
        owner._resolve_covered_owner(recipient_ref, cache=_OwnerResolutionCache())
    except CommandRejectedError as exc:
        raise CommandRejectedError("world_front_player_report_recipient_invalid") from exc
    claim = _claim_from_record(claim_record)
    digest = hashlib.sha256(f"{claim_id}\x00{sender_ref}\x00{recipient_ref}\x00{front_ref}".encode("utf-8")).hexdigest()[:24]
    delivery_id = f"delivery.world_front.{digest}"
    try:
        existing = information.delivery(delivery_id)
        if existing is None:
            delivery = deliver_claim(claim, delivery_id=delivery_id, sender_ref=sender_ref, recipient_ref=recipient_ref, channel=str(config["channel"]), delivered_at=at, channel_confidence_milli=int(config["channel_confidence_milli"]))
            delivery_record = dict(delivery.to_record())
            information.add_delivery(delivery_record)
        else:
            delivery_record = dict(existing)
        information.grant(recipient_ref, claim_id)
    except (TypeError, ValueError) as exc:
        raise CommandRejectedError("world_front_player_report_delivery_invalid") from exc
    source_event = result.get("event_id")
    causal_refs = [claim_id]
    if isinstance(source_event, str):
        causal_refs.append(source_event)
    event_id = owner._append_internal_event(
        world_events,
        command=command,
        identity=delivery_id,
        kind="world_front_information_delivered",
        at=at,
        host_refs=(front_ref, str(decision.payload.get("faction_id"))),
        actor_refs=(sender_ref, recipient_ref),
        causal_refs=tuple(causal_refs),
        affected_owner_refs=information.affected_paths,
        material_consequence_refs=(delivery_id,),
        classification=str(config.get("classification", "restricted")),
        audience_refs=(recipient_ref,),
        knowledge_refs=(claim_id,),
        source_refs=(sender_ref,),
        reducer_ref="shinobi_runtime.reducers.information.deliver_claim",
    )
    return [{**delivery_record, "world_front_ref": front_ref, "semantic_event_id": event_id}]


def _install_strategic_bias() -> None:
    original = PlayerMissionOfferPolicyMixin._apply_autonomous_decision
    if getattr(original, "_world_front_strategic_bias", False):
        return

    @wraps(original)
    def wrapped(self: Any, *, decision: Any, at: Any, command: Any, scheduler: Any, world_events: Dict[str, Any], record_writes: Dict[str, Dict[str, Any]], faction_record: Dict[str, Any]) -> Mapping[str, Any]:
        candidate = decision
        selected: str | None = None
        assignment: Mapping[str, Any] = {}
        payload = decision.payload if hasattr(decision, "payload") and isinstance(decision.payload, Mapping) else {}
        faction_id = payload.get("faction_id")
        if isinstance(faction_id, str):
            try:
                profile, assignment = self._autonomy_policy_book().faction_context(faction_id)
            except (TypeError, ValueError, CommandRejectedError):
                profile, assignment = {}, {}
            if profile:
                candidate, selected = route_world_front_decision(decision, at=at, rules=policy(self.repository), registry=pressure_registry(self.repository), profile=profile)
        result = original(self, decision=candidate, at=at, command=command, scheduler=scheduler, world_events=world_events, record_writes=record_writes, faction_record=faction_record)
        if selected is None or not isinstance(result, Mapping):
            return result
        result = dict(result)
        result["world_front_ref"] = selected
        deliveries = _deliver_player_front_report(self, result=result, decision=candidate, front_ref=selected, assignment=assignment, at=at, command=command, world_events=world_events, record_writes=record_writes)
        if deliveries:
            result["player_report_deliveries"] = deliveries
        return result

    wrapped._world_front_strategic_bias = True
    PlayerMissionOfferPolicyMixin._apply_autonomous_decision = wrapped


def _install_time_postprocessor() -> None:
    from shinobi_runtime.commands import campaign_runtime_planner as module
    original = module.CampaignCommandPlanner._advance_time
    if getattr(original, "_world_front_progression", False):
        return

    @wraps(original)
    def wrapped(self: Any, command: Any, meta: Mapping[str, Any], current_time: Any) -> _BuiltPlan:
        try:
            previous = self.repository.read_json(self.scene_path)
        except (FileNotFoundError, ValueError):
            previous = {}
        plan = progress_plan(self, original(self, command, meta, current_time), command)
        return module._refresh_time_advanced_plan(plan, self.scene_path, previous_scene=previous) if isinstance(previous, Mapping) else plan

    wrapped._world_front_progression = True
    module.CampaignCommandPlanner._advance_time = wrapped


def _player_visible_front_handoff(result: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """Project only concrete front information that has actually reached Wei.

    A world-front phase transition is internal causal bookkeeping, even when the
    evidence event that advanced it was public. The phase name itself is not a
    player observation and must never become vague IC pressure. Front information
    enters the player handoff only through an actual player-addressed delivery.
    """
    reports: list[str] = []
    actions = result.get("autonomous_actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            deliveries = action.get("player_report_deliveries")
            if isinstance(deliveries, list) and any(isinstance(row, Mapping) for row in deliveries):
                message = "A sourced operational report addressed to Wei is ready for review."
                if message not in reports:
                    reports.append(message)
    return [], reports[:6], []


def install_world_front_projection() -> None:
    global _PROJECTION_INSTALLED
    if _PROJECTION_INSTALLED:
        return
    from shinobi_runtime.commands import campaign_runtime_planner as module
    original = module._fresh_player_facing_time_handoff
    if getattr(original, "_world_front_projection", False):
        _PROJECTION_INSTALLED = True
        return

    @wraps(original)
    def wrapped(result: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        pressures, reports, approaching = original(result)
        extra_pressures, extra_reports, extra_approaching = _player_visible_front_handoff(result)
        for message in extra_pressures:
            if message not in pressures:
                pressures.append(message)
        for message in extra_reports:
            if message not in reports:
                reports.append(message)
        for message in extra_approaching:
            if message not in approaching:
                approaching.append(message)
        return pressures[:12], reports[:6], approaching[:8]

    wrapped._world_front_projection = True
    module._fresh_player_facing_time_handoff = wrapped
    _PROJECTION_INSTALLED = True


def install_world_front_progression() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_strategic_bias()
    install_world_front_projection()
    _install_time_postprocessor()
    _INSTALLED = True


__all__ = ["front_phase", "install_world_front_progression", "install_world_front_projection", "route_world_front_decision"]
