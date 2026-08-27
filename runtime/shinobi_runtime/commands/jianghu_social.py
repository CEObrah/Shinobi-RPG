"""Player-facing sparse Jianghu social commitments and investigation."""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.martial_world.escort_living_world import principal_ransom_value_cash
from shinobi_runtime.martial_world.faction_state import read_faction
from shinobi_runtime.martial_world.public_observation import disclosure_credibility_milli
from shinobi_runtime.martial_world.regional_economy import current_cargo_market_value_cash
from shinobi_runtime.martial_world.relationships import apply_relationship_event
from shinobi_runtime.martial_world.property import validate_property_evidence
from shinobi_runtime.martial_world.social_causality import (
    add_personal_obligation, add_vow, record_belief, release_vow,
    resolve_personal_obligation, revise_belief,
)
from shinobi_runtime.sim.events import CampaignTime

_SOCIAL = "state/martial-world/social.json"
_ROUTE_OPS = "state/martial-world/route-operations.json"
_CONTRACTS = "state/martial-world/contracts/index.json"
_EQUIPMENT = "state/martial-world/equipment-ledger.json"


def _scene_contains(scene: Mapping[str, Any], person_ref: str) -> bool:
    refs = set()
    for key in ("present_person_ids", "visible_person_ids"):
        rows = scene.get(key, []) if isinstance(scene, Mapping) else []
        if isinstance(rows, list): refs.update(str(x) for x in rows if isinstance(x, str))
    return person_ref in refs


class JianghuSocialCommandsMixin:
    def _jianghu_social_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime,
    ):
        action = str(command.payload.get("action") or "")
        social = copy.deepcopy(self.repository.read_json(_SOCIAL))
        at_iso = str(current_time).removeprefix("SE-")

        if action == "promise":
            other_ref = str(command.payload.get("other_ref") or "")
            kind_raw = str(command.payload.get("promise_kind") or "")
            strength = int(command.payload.get("strength", 0))
            kind = {
                "aid": "promise_aid",
                "protect": "promise_protect",
                "nonaggression": "promise_nonaggression",
            }.get(kind_raw)
            if not other_ref or kind is None or not 1 <= strength <= 100:
                raise CommandRejectedError("jianghu_personal_promise_invalid")
            try:
                self._person(other_ref)
            except (FileNotFoundError, KeyError, ValueError) as exc:
                raise CommandRejectedError("jianghu_personal_promise_target_unresolved") from exc
            scene = self.repository.read_json(self.scene_path)
            if command.mode == "gameplay" and not _scene_contains(scene, other_ref):
                raise CommandRejectedError("jianghu_personal_promise_requires_present_counterparty")
            result = add_personal_obligation(
                social, actor_ref=command.actor_id, counterparty_ref=other_ref,
                kind=kind, strength=strength, created_at=at_iso,
            )
            return self._simple_plan(
                command, meta, current_time, writes_records={_SOCIAL: result["state_after"]},
                code="jianghu_personal_promise_made",
                result={"command_type": command.command_type, "obligation_ref": result["obligation_ref"], "promise_kind": kind_raw, "strength": result["strength"]},
            )

        if action == "make_vow":
            vow_kind = str(command.payload.get("vow_kind") or "")
            strength = int(command.payload.get("strength", 0))
            subject_ref = str(command.payload.get("subject_ref") or "")
            faction_ref = str(command.payload.get("faction_ref") or "")
            if not 1 <= strength <= 100:
                raise CommandRejectedError("jianghu_vow_strength_invalid")
            if subject_ref:
                try: self._person(subject_ref)
                except (FileNotFoundError, KeyError, ValueError) as exc:
                    raise CommandRejectedError("jianghu_vow_subject_unresolved") from exc
            if faction_ref:
                try: read_faction(self.repository, faction_ref)
                except (FileNotFoundError, KeyError, ValueError) as exc:
                    raise CommandRejectedError("jianghu_vow_faction_unresolved") from exc
            try:
                result = add_vow(
                    social, person_ref=command.actor_id, kind=vow_kind, strength=strength,
                    declared_at=at_iso, subject_ref=subject_ref, faction_ref=faction_ref,
                )
            except (KeyError, ValueError) as exc:
                raise CommandRejectedError("jianghu_vow_invalid") from exc
            return self._simple_plan(
                command, meta, current_time, writes_records={_SOCIAL: result["state_after"]},
                code="jianghu_vow_declared",
                result={"command_type": command.command_type, "vow_ref": result["vow_ref"], "vow_kind": vow_kind},
            )

        if action == "release_vow":
            ref = str(command.payload.get("vow_ref") or "")
            result = release_vow(social, vow_ref_value=ref, person_ref=command.actor_id)
            if not result["released"]:
                raise CommandRejectedError("jianghu_vow_not_owned")
            return self._simple_plan(
                command, meta, current_time, writes_records={_SOCIAL: result["state_after"]},
                code="jianghu_vow_released",
                result={"command_type": command.command_type, "vow_ref": ref},
            )

        if action == "forgive_obligation":
            ref = str(command.payload.get("obligation_ref") or "")
            rows = social.get("obligations", {}) if isinstance(social.get("obligations"), Mapping) else {}
            row = rows.get(ref) if isinstance(rows, Mapping) else None
            if not isinstance(row, Mapping):
                raise CommandRejectedError("jianghu_obligation_not_player_relevant")
            kind = str(row.get("kind") or "")
            actor_ref = str(row.get("actor_ref") or "")
            counterparty_ref = str(row.get("counterparty_ref") or "")
            # Forgiveness belongs to the person holding the claim.  An avenger
            # may renounce their own vengeance; a debtor/promisor may not erase
            # the beneficiary's claim merely by choosing "forgive".
            allowed = (
                (kind == "vengeance" and command.actor_id == actor_ref)
                or (kind != "vengeance" and command.actor_id == counterparty_ref)
            )
            if not allowed:
                raise CommandRejectedError("jianghu_obligation_forgiveness_not_owned")
            result = resolve_personal_obligation(social, obligation_ref_value=ref)
            return self._simple_plan(
                command, meta, current_time, writes_records={_SOCIAL: result["state_after"]},
                code="jianghu_personal_obligation_forgiven",
                result={"command_type": command.command_type, "obligation_ref": ref},
            )

        if action == "renounce_obligation":
            ref = str(command.payload.get("obligation_ref") or "")
            rows = social.get("obligations", {}) if isinstance(social.get("obligations"), Mapping) else {}
            row = rows.get(ref) if isinstance(rows, Mapping) else None
            if not isinstance(row, Mapping) or str(row.get("actor_ref") or "") != command.actor_id or str(row.get("kind") or "") == "vengeance":
                raise CommandRejectedError("jianghu_obligation_renunciation_not_authorized")
            counterparty_ref = str(row.get("counterparty_ref") or "")
            scene = self.repository.read_json(self.scene_path)
            if command.mode == "gameplay" and not _scene_contains(scene, counterparty_ref):
                raise CommandRejectedError("jianghu_obligation_renunciation_requires_counterparty")
            result = resolve_personal_obligation(social, obligation_ref_value=ref)
            social_after = result["state_after"]
            if counterparty_ref:
                social_after = apply_relationship_event(
                    social_after, observer_ref=counterparty_ref, subject_ref=command.actor_id,
                    event_kind="oath_breach", observer_knows=True, severity_milli=1000,
                    protected_player_ref=str(meta.get("player_id") or "pc_wei_tang"),
                )["state_after"]
            return self._simple_plan(
                command, meta, current_time, writes_records={_SOCIAL: social_after},
                code="jianghu_personal_obligation_renounced",
                result={"command_type": command.command_type, "obligation_ref": ref},
            )

        if action == "hear_claim":
            # This command records words that were actually spoken in the scene;
            # it does not make the named source speak. The GM may only invoke it
            # after the claim is player-observable. Truth remains separate from
            # the claim and is not consulted at this stage.
            source_ref = str(command.payload.get("source_ref") or "")
            subject_ref = str(command.payload.get("subject_ref") or "")
            claim_kind = str(command.payload.get("claim_kind") or "")
            claimed_value = max(0, int(command.payload.get("claimed_value_cash", 0)))
            evidence_ref = str(command.payload.get("evidence_ref") or "")
            if not source_ref or source_ref == command.actor_id or claim_kind not in {
                "cargo_value", "principal_value", "property_crime_responsibility",
            }:
                raise CommandRejectedError("jianghu_heard_claim_invalid")
            try:
                _sp, _sr, _so, source = self._person(source_ref)
                _ap, _ar, _ao, actor = self._person(command.actor_id)
            except (FileNotFoundError, KeyError, ValueError) as exc:
                raise CommandRejectedError("jianghu_heard_claim_person_unresolved") from exc
            scene = self.repository.read_json(self.scene_path)
            if command.mode == "gameplay" and not _scene_contains(scene, source_ref):
                raise CommandRejectedError("jianghu_heard_claim_requires_present_source")
            if claim_kind in {"cargo_value", "principal_value"}:
                route_ops = self.repository.read_json(_ROUTE_OPS)
                movements = route_ops.get("movements", {}) if isinstance(route_ops, Mapping) else {}
                if not isinstance(movements, Mapping) or not isinstance(movements.get(subject_ref), Mapping):
                    raise CommandRejectedError("jianghu_heard_claim_subject_unresolved")
                claim_ref = f"claim:{subject_ref}:{claim_kind}:{claimed_value}"
            else:
                try:
                    self._person(subject_ref)
                except (FileNotFoundError, KeyError, ValueError) as exc:
                    raise CommandRejectedError("jianghu_heard_claim_subject_unresolved") from exc
                claim_ref = f"claim:{subject_ref}:{claim_kind}"
            at = datetime(current_time.year, current_time.month, current_time.day, current_time.hour, current_time.minute, current_time.second)
            confidence = disclosure_credibility_milli(
                actor, speaker_ref=source_ref, claimed_value_cash=claimed_value,
                at=at, disclosure_ref=f"{claim_ref}|{source_ref}",
            )
            recorded = record_belief(
                social, observer_ref=command.actor_id, claim_ref=claim_ref, subject_ref=subject_ref,
                claim_kind=claim_kind, confidence_milli=confidence,
                stance="supports" if confidence >= 300 else "uncertain", source_ref=source_ref,
                value_cash=(claimed_value if claim_kind in {"cargo_value", "principal_value"} else None),
                evidence_ref=evidence_ref,
            )
            return self._simple_plan(
                command, meta, current_time, writes_records={_SOCIAL: recorded["state_after"]},
                code="jianghu_claim_heard",
                result={"command_type": command.command_type, "belief_ref": recorded["belief_ref"], "confidence_milli": confidence},
            )

        if action == "investigate":
            ref = str(command.payload.get("belief_ref") or "")
            rows = social.get("beliefs", {}) if isinstance(social.get("beliefs"), Mapping) else {}
            row = rows.get(ref) if isinstance(rows, Mapping) else None
            if not isinstance(row, Mapping) or str(row.get("observer_ref") or "") != command.actor_id:
                raise CommandRejectedError("jianghu_belief_not_owned")
            claim_kind = str(row.get("claim_kind") or "")
            evidence_stance = "supports"; evidence_confidence = 0
            if claim_kind in {"cargo_value", "principal_value"}:
                movement_ref = str(row.get("subject_ref") or "")
                route_ops = self.repository.read_json(_ROUTE_OPS)
                movements = route_ops.get("movements", {}) if isinstance(route_ops, Mapping) else {}
                movement = movements.get(movement_ref) if isinstance(movements, Mapping) else None
                if not isinstance(movement, Mapping):
                    raise CommandRejectedError("jianghu_belief_subject_closed")
                participants = {str(x) for x in movement.get("participant_refs", []) if isinstance(x, str)}
                if command.actor_id not in participants:
                    raise CommandRejectedError("jianghu_investigation_requires_direct_access")
                contract_ref = str(movement.get("contract_ref") or "")
                index = self.repository.read_json(_CONTRACTS)
                active = index.get("active", {}) if isinstance(index, Mapping) else {}
                contract = active.get(contract_ref) if isinstance(active, Mapping) else None
                objective = contract.get("objective", {}) if isinstance(contract, Mapping) and isinstance(contract.get("objective"), Mapping) else {}
                actual = 0
                if claim_kind == "cargo_value":
                    actual = current_cargo_market_value_cash(movement, read_json=self.repository.read_json)
                else:
                    refs = [str(x) for x in objective.get("protected_person_refs", []) if isinstance(x, str)]
                    for person_ref in refs:
                        try:
                            _p, _r, _o, person = self._person(person_ref)
                        except (FileNotFoundError, KeyError, ValueError):
                            continue
                        actual = max(actual, principal_ransom_value_cash(person))
                claimed = max(0, int(row.get("value_cash", 0)))
                tolerance = max(500, actual // 5)
                evidence_stance = "confirmed" if actual > 0 and abs(claimed - actual) <= tolerance else "disproved"
                evidence_confidence = 900
            elif claim_kind == "property_crime_responsibility":
                subject_ref = str(row.get("subject_ref") or "")
                evidence_ref = str(row.get("evidence_ref") or "")
                if not subject_ref or not evidence_ref:
                    raise CommandRejectedError("jianghu_belief_has_no_registered_investigation_path")
                ledger = self.repository.read_json(_EQUIPMENT)
                validated = validate_property_evidence(ledger, evidence_ref, holder_ref=subject_ref)
                evidence_stance = "confirmed" if isinstance(validated, Mapping) else "refutes"
                evidence_confidence = 950 if isinstance(validated, Mapping) else 700
            else:
                raise CommandRejectedError("jianghu_belief_has_no_registered_investigation_path")
            revised = revise_belief(
                social, belief_ref_value=ref, observer_ref=command.actor_id,
                evidence_stance=evidence_stance, evidence_confidence_milli=evidence_confidence,
            )
            return self._simple_plan(
                command, meta, current_time, writes_records={_SOCIAL: revised["state_after"]},
                code="jianghu_belief_investigated",
                result={"command_type": command.command_type, "belief_ref": ref, "stance": revised["belief"]["stance"], "confidence_milli": revised["belief"]["confidence_milli"]},
            )

        raise CommandRejectedError("jianghu_social_action_invalid")


__all__ = ["JianghuSocialCommandsMixin"]
