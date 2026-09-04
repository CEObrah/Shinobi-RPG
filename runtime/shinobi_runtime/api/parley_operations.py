"""Player-safe combat-parley play-context enrichment.

Combat speech remains presentation-only. The exact combat owner proves which
combat side is being addressed; the interaction ledger preserves Wei's authored
questions; attributed scene history preserves reversible opposing-side lines.
No hidden opponent person identity is exposed by this projection.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.encounter_causality import resolved_contact_causality
from shinobi_runtime.api.travel_operations import TravelAwareCampaignOperations
from shinobi_runtime.martial_world.scene_sessions import (
    SPEECH_KINDS,
    interaction_ledger,
    recent_scene_history,
)
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError


_RESPONSE_BEARING_ACTIONS = {"ask", "request", "petition", "offer", "present", "report", "speak"}


def _combat_thread_open(row: Mapping[str, Any], *, combat_ref: str, player_id: str) -> bool:
    """Accept current combat dialogue threads plus the pre-thread legacy question shape."""
    action = str(row.get("action") or "")
    explicit = row.get("expects_response")
    response_bearing = bool(explicit) if isinstance(explicit, bool) else action in _RESPONSE_BEARING_ACTIONS
    legacy_response_thread = response_bearing and row.get("thread_status") == "not_applicable"
    return (
        row.get("actor_ref") == player_id
        and row.get("target_ref") == combat_ref
        and row.get("target_kind") == "opposing_combat_side"
        and response_bearing
        and isinstance(row.get("player_statement"), str)
        and bool(row.get("player_statement"))
        and row.get("resolved_at") is None
        and row.get("response_ref") is None
        and (row.get("thread_status") == "open" or legacy_response_thread)
    )


def _authorized_dialogue_material(
    route_operations: Mapping[str, Any] | None,
    *,
    combat_ref: str,
    read_json: Callable[[str], Any] | None = None,
) -> dict[str, Any] | None:
    """Return private encounter causality plus bounded speech guidance.

    The GM may know the true local cause.  Disclosure is a separate decision: a
    hostile speaker may tell the truth, simplify it, lie, evade, bargain, or say
    nothing.  This gives the AI enough truth to direct a coherent human scene
    without making hidden runtime state automatic player knowledge.
    """
    if not isinstance(route_operations, Mapping):
        return None
    contacts = route_operations.get("contacts", {})
    if not isinstance(contacts, Mapping):
        return None
    contact = next((row for row in contacts.values() if isinstance(row, Mapping) and row.get("status") == "active" and row.get("combat_ref") == combat_ref), None)
    if not isinstance(contact, Mapping):
        return None
    causal, source = resolved_contact_causality(contact, route_operations, read_json=read_json)
    motive = str(causal.get("motive_kind") or "")
    intent = str(causal.get("attacker_intent") or "")
    truth = {
        "loot": "They may truthfully say that the convoy's cargo, goods, mounts, or valuables are what they want.",
        "ransom": "They may truthfully say that a protected or notable person is what they want, including demanding surrender for ransom, without naming undisclosed employers or intelligence.",
        "grievance": "They may truthfully say that this attack is about a grievance, hostility, or a score to settle, without inventing an undisclosed cause.",
        "recognized_notable_target": "They may truthfully say that the recognized notable person is the reason for the interception, without inventing who supplied that recognition or any secret employer.",
        "opportunistic_predation": "They may truthfully say that they chose the travelers as prey for robbery or extortion because they appeared worth taking from.",
    }.get(motive)
    if truth is None:
        if intent == "rob_cargo":
            truth = "They may truthfully demand the convoy's cargo or valuables."
        elif intent == "kidnap_principal":
            truth = "They may truthfully demand the protected person surrender for capture or ransom."
        elif intent == "revenge":
            truth = "They may truthfully frame the attack as a grievance or score being settled."
        else:
            truth = "They may refuse to explain, warn Wei back, or make a nonbinding demand supported by the visible confrontation."
    private_causal_context = {
        key: causal.get(key)
        for key in (
            "attacker_faction_ref", "attacker_intent", "motive_kind", "movement_ref",
            "route_ref", "target_ref", "principal_ref", "beneficiary_ref",
            "gm_private_decision_context",
        )
        if causal.get(key) not in (None, "", [], {})
    }
    return {
        "source": source,
        "privacy": "gm_private_scene_bounded_omniscient_truth_not_player_knowledge",
        "truthful_dialogue_material": truth,
        "gm_private_causal_context": private_causal_context,
        "other_lawful_modes": ["truthful_answer", "refuse_to_explain", "guarded_answer", "lie_or_misdirect_if_consistent_with_npc_goals", "warning", "nonbinding_demand_or_bargain"],
        "player_knowledge_rule": (
            "The GM may use the private causal context to understand why the attackers are here and to play them coherently. "
            "None of it is Wei's knowledge until perceived, inferred from evidence, or actually disclosed by an NPC."
        ),
        "truth_rule": (
            "The GM may let an NPC tell the truth, conceal it, simplify it, refuse, or misdirect when consistent with the scene. "
            "Persist only what was actually said as attributed speech; never promote that statement itself to objective truth."
        ),
    }


def combat_parley_scene_projection(
    *,
    ledger: Mapping[str, Any],
    recent_history: list[Mapping[str, Any]],
    combat_ref: str,
    player_id: str,
    route_operations: Mapping[str, Any] | None = None,
    read_json: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded conversation handoff for the exact opposing combat side."""
    threads: list[dict[str, Any]] = []
    open_thread_count = 0
    open_question_count = 0
    attempts = ledger.get("attempts", []) if isinstance(ledger, Mapping) else []
    if isinstance(attempts, list):
        for raw in reversed(attempts):
            if not isinstance(raw, Mapping) or not _combat_thread_open(raw, combat_ref=combat_ref, player_id=player_id):
                continue
            open_thread_count += 1
            if raw.get("action") == "ask":
                open_question_count += 1
            if len(threads) >= 8:
                continue
            row = {
                "attempt_ref": str(raw.get("attempt_ref") or ""),
                "at": str(raw.get("at") or ""),
                "action": str(raw.get("action") or ""),
                "player_statement": str(raw.get("player_statement") or ""),
            }
            for key in ("topic", "posture"):
                value = raw.get(key)
                if isinstance(value, str) and value:
                    row[key] = value
            scopes = raw.get("scopes")
            if isinstance(scopes, list) and scopes:
                row["scopes"] = [str(x) for x in scopes if isinstance(x, str)][:32]
            threads.append(row)

    opposing_speech = [
        dict(row)
        for row in recent_history
        if isinstance(row, Mapping)
        and row.get("session_ref") == combat_ref
        and row.get("speaker_ref") == combat_ref
        and row.get("truth_status") == "attributed_statement"
        and row.get("mechanical_consequence_authority") is False
    ]
    latest_opposing = opposing_speech[-1] if opposing_speech else None

    questions = [row for row in threads if row.get("action") == "ask"]
    dialogue_material = _authorized_dialogue_material(route_operations, combat_ref=combat_ref, read_json=read_json)

    result = {
        "combat_ref": combat_ref,
        "target_ref": combat_ref,
        "target_kind": "opposing_combat_side",
        "open_threads": threads,
        "open_thread_count": open_thread_count,
        "open_threads_truncated": open_thread_count > len(threads),
        "open_questions": questions,  # compatibility alias/subset
        "open_question_count": open_question_count,
        "open_questions_truncated": open_question_count > len(questions),
        "latest_opposing_speech": latest_opposing,
        "response_recording": {
            "command_type": "jianghu_scene_session_resolution",
            "action": "record_speech",
            "session_ref": combat_ref,
            "speaker_ref": combat_ref,
            "speaker_kind": "opposing_combat_side",
            "allowed_speech_kinds": sorted(SPEECH_KINDS),
            "mechanical_consequence_authority": False,
        },
        "response_policy": (
            "The GM may realize and persist ordinary reversible opposing-side dialogue using both Wei-facing evidence and explicitly marked GM-private causal truth. "
            "Private truth is authoring context, not automatic disclosure: attackers may answer honestly, partially, deceptively, evasively, or not at all according to their situation. "
            "Dialogue may not by itself establish binding terms, ceasefire, surrender, movement, injury, custody, or any other hard consequence."
        ),
        "identity_policy": "opposing_person_ids_remain_hidden",
        "identity_policy_rule": (
            "Exact opposing identities may exist in GM-private combat/director context; "
            "they remain undisclosed to Wei until identification is lawfully established."
        ),
    }
    if dialogue_material is not None:
        result["npc_response_envelope"] = dialogue_material
    return result


class ParleyAwareCampaignOperations(TravelAwareCampaignOperations):
    """Production projection with durable combat-side conversational handoff."""

    def play_context(self) -> Mapping[str, Any]:
        for _attempt in range(2):
            base = dict(super().play_context())
            campaign = base.get("campaign")
            scene = dict(base.get("scene", {})) if isinstance(base.get("scene"), Mapping) else {}
            if not isinstance(campaign, Mapping):
                return base
            combat_ref = scene.get("active_combat_ref")
            player_id = str(campaign.get("player_id") or "")
            if not isinstance(combat_ref, str) or not combat_ref or not player_id:
                scene.pop("combat_parley", None)
                base["scene"] = scene
                return base

            try:
                with self._locked():
                    self.coordinator.git.assert_pristine()
                    before = self._read_fingerprint()
                    meta = self.repository.read_json("state/meta.json")
                    if (
                        not isinstance(meta, Mapping)
                        or int(meta.get("revision", -1)) != int(campaign.get("revision", -2))
                        or str(meta.get("campaign_id") or "") != str(campaign.get("campaign_id") or "")
                        or before[1] != str(campaign.get("state_root") or "")
                    ):
                        continue
                    ledger = interaction_ledger(self.repository.read_json)
                    history = recent_scene_history(self.repository.read_json, 8)
                    try:
                        route_operations = self.repository.read_json("state/martial-world/route-operations.json")
                    except FileNotFoundError:
                        route_operations = None
                    self._require_read_only(before, "play_context_parley_projection_mutated_campaign")
            except OperationError:
                raise
            except (LockUnavailableError, DirtyRepositoryError) as exc:
                raise OperationError(503, "campaign_unavailable") from exc
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                return base

            scene["combat_parley"] = combat_parley_scene_projection(
                ledger=ledger,
                recent_history=history,
                combat_ref=combat_ref,
                player_id=player_id,
                route_operations=route_operations,
                read_json=self.repository.read_json,
            )
            base["scene"] = scene
            try:
                validate_bounded_json(base, label="play context", allow_float=True)
            except ValueError as exc:
                raise OperationError(503, "play_context_out_of_bounds") from exc
            return base

        raise OperationError(503, "play_context_state_changed_during_parley_projection")


__all__ = ["ParleyAwareCampaignOperations", "combat_parley_scene_projection"]
