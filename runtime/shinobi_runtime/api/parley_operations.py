"""Player-safe combat-parley play-context enrichment.

Combat speech remains presentation-only. The exact combat owner proves which
combat side is being addressed; the interaction ledger preserves Wei's authored
questions; attributed scene history preserves reversible opposing-side lines.
No hidden opponent person identity is exposed by this projection.
"""
from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.models import validate_bounded_json
from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.travel_operations import TravelAwareCampaignOperations
from shinobi_runtime.martial_world.scene_sessions import (
    SPEECH_KINDS,
    interaction_ledger,
    recent_scene_history,
)
from shinobi_runtime.tx.errors import DirtyRepositoryError, LockUnavailableError


def _combat_question_open(row: Mapping[str, Any], *, combat_ref: str, player_id: str) -> bool:
    """Accept current combat questions plus the pre-thread legacy combat shape."""
    return (
        row.get("actor_ref") == player_id
        and row.get("target_ref") == combat_ref
        and row.get("target_kind") == "opposing_combat_side"
        and row.get("action") == "ask"
        and isinstance(row.get("player_statement"), str)
        and bool(row.get("player_statement"))
        and row.get("resolved_at") is None
        and row.get("response_ref") is None
        and row.get("thread_status") in {"open", "not_applicable"}
    )


def combat_parley_scene_projection(
    *,
    ledger: Mapping[str, Any],
    recent_history: list[Mapping[str, Any]],
    combat_ref: str,
    player_id: str,
) -> dict[str, Any]:
    """Return a bounded conversation handoff for the exact opposing combat side."""
    questions: list[dict[str, Any]] = []
    attempts = ledger.get("attempts", []) if isinstance(ledger, Mapping) else []
    if isinstance(attempts, list):
        for raw in reversed(attempts):
            if not isinstance(raw, Mapping) or not _combat_question_open(raw, combat_ref=combat_ref, player_id=player_id):
                continue
            row = {
                "attempt_ref": str(raw.get("attempt_ref") or ""),
                "at": str(raw.get("at") or ""),
                "player_statement": str(raw.get("player_statement") or ""),
            }
            for key in ("topic", "posture"):
                value = raw.get(key)
                if isinstance(value, str) and value:
                    row[key] = value
            scopes = raw.get("scopes")
            if isinstance(scopes, list) and scopes:
                row["scopes"] = [str(x) for x in scopes if isinstance(x, str)][:32]
            questions.append(row)
            if len(questions) >= 8:
                break

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

    return {
        "combat_ref": combat_ref,
        "target_ref": combat_ref,
        "target_kind": "opposing_combat_side",
        "open_questions": questions,
        "open_question_count": len(questions),
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
            "The GM may realize and persist ordinary reversible opposing-side dialogue. "
            "It may acknowledge, refuse, object, question, warn, speculate from player-safe evidence, "
            "or make a nonbinding proposal. It may not invent hidden identities, secret factual motives, "
            "binding terms, ceasefire, surrender, movement, injury, custody, or any other hard consequence."
        ),
        "identity_policy": "opposing_person_ids_remain_hidden",
    }


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
            )
            base["scene"] = scene
            try:
                validate_bounded_json(base, label="play context", allow_float=True)
            except ValueError as exc:
                raise OperationError(503, "play_context_out_of_bounds") from exc
            return base

        raise OperationError(503, "play_context_state_changed_during_parley_projection")


__all__ = ["ParleyAwareCampaignOperations", "combat_parley_scene_projection"]
