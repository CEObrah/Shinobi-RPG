"""One-shot repair for Wei campaign team-training supervision continuity.

The user clarified that both Black Hound and Team Fujin use Sword Manor under
Zhu/Linh supervision for routine training. Historical Hayama-led Black Hound
sessions were lawful under the then-saved configuration and remain untouched.
This repair changes only the current standing supervision policy and records the
correction through the normal transaction coordinator without advancing time.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _exact_payload,
    _json_bytes,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_REPAIR_ID = "repair.2026-08-15.team-training-supervision"
_PLAYER_REF = "pc_wei_tang"
_BLACK_HOUND_REF = "team.blackhound"
_BLACK_HOUND_PATH = "state/team/blackhound.json"
_BLACK_HOUND_DOCTRINE_PATH = "state/team/doctrine/black-hound.json"
_FUJIN_REF = "team.konoha.fujin"
_FUJIN_PATH = "state/team/fujin.json"
_FUJIN_DOCTRINE_PATH = "state/team/doctrine/team-konoha-fujin.json"
_PLAYER_PATH = "state/player.json"
_SWORD_MANOR = "place.sword_manor"
_SUPERVISOR_REFS = ("char.zhu", "char.linh")

_OLD_ORDER_1 = (
    "During Team Fujin's heavy-week training, Wei trains actively through compatible instructor work: "
    "live opposition, sparring, demonstrations and rotating technical drills. Shared hours may develop "
    "leadership, team coordination, tactics, sword, movement and Wind Release integration when the "
    "activity actually trains them; private practice that withdraws instructor attention is scheduled "
    "separately and is not double-counted as full instruction."
)
_OLD_ORDER_2 = (
    "Under Wei's House Tang field-command warrant, available House military elements use a repeating "
    "maximum-quality cycle of five six-active-hour training days, one four-active-hour taper, and one "
    "recovery/readiness day. Lawful missions, external assignments, injury, medical restrictions, and "
    "Zhu's retained strategic/dojo authority supersede the schedule; missed volume is not stacked later. "
    "Instructors rotate through active demonstrations, opposition, and practice so teaching does not "
    "freeze their own development. Unit training and doctrine references define the content."
)
_OLD_ORDER_3 = (
    "For Team Fujin coverage, Mei Arakawa remains the internal acting field leader while adult Jonin "
    "supervision stays separate. When Wei is unavailable for routine training, Team Fujin assembles at "
    "Sword Manor under the registered supervision of Zhu or Linh, subject to their availability and "
    "higher lawful duties. Black Hound is active under team.blackhound.doctrine with Hayama Shirakumo "
    "as deputy; Hayama leads training or a lawful five-person detachment when Wei is unavailable only if "
    "mission capability requirements remain covered. The five non-Wei members have Black Hound as their "
    "primary standing assignment unless the Hokage changes the order. If Wei is mission-critical he "
    "deploys and Team Fujin uses approved supervision coverage."
)

_NEW_ORDER_1 = (
    "During Team Fujin's heavy-week training at Sword Manor, Zhu or Linh provides the registered adult "
    "supervision and instruction. Wei trains as a participating shinobi through compatible live "
    "opposition, sparring, demonstrations and rotating technical drills when present; shared hours may "
    "develop leadership, team coordination, tactics, sword, movement and Wind Release integration when "
    "the activity actually trains them. Private practice that withdraws attention from the shared "
    "session is scheduled separately and is not double-counted as team instruction."
)
_NEW_ORDER_3 = (
    "For Team Fujin coverage, Mei Arakawa remains the internal acting field leader while adult Jonin "
    "supervision stays separate. Routine Team Fujin training assembles at Sword Manor under the registered "
    "supervision of Zhu or Linh, subject to their availability and higher lawful duties. Black Hound "
    "routine training likewise runs at Sword Manor under Zhu or Linh; Hayama Shirakumo remains deputy and "
    "may lead a lawful five-person detachment when Wei is unavailable only if mission capability "
    "requirements remain covered, but Hayama is not a standing training instructor. The five non-Wei "
    "members have Black Hound as their primary standing assignment unless the Hokage changes the order. "
    "If Wei is mission-critical he deploys and both teams use their approved supervision coverage."
)


def _training_block(record: Mapping[str, Any], code: str) -> Mapping[str, Any]:
    training = record.get("training") if isinstance(record, Mapping) else None
    if not isinstance(training, Mapping):
        raise CommandRejectedError(code)
    return training


def repair_training_supervision_records(
    black_hound: Mapping[str, Any],
    black_hound_doctrine: Mapping[str, Any],
    fujin: Mapping[str, Any],
    fujin_doctrine: Mapping[str, Any],
    player: Mapping[str, Any],
    *,
    current_time: CampaignTime,
) -> Dict[str, Dict[str, Any]]:
    """Return exact guarded after-images for the clarified standing policy."""

    bh = copy.deepcopy(dict(black_hound))
    bh_doctrine = copy.deepcopy(dict(black_hound_doctrine))
    fj = copy.deepcopy(dict(fujin))
    fj_doctrine = copy.deepcopy(dict(fujin_doctrine))
    pc = copy.deepcopy(dict(player))

    if bh.get("schema") != "exact-team" or bh.get("id") != _BLACK_HOUND_REF:
        raise CommandRejectedError("campaign_repair_black_hound_changed")
    if fj.get("schema") != "exact-team" or fj.get("id") != _FUJIN_REF:
        raise CommandRejectedError("campaign_repair_fujin_changed")
    if bh_doctrine.get("schema") != "team-doctrine" or bh_doctrine.get("id") != "team.blackhound.doctrine":
        raise CommandRejectedError("campaign_repair_black_hound_doctrine_changed")
    if fj_doctrine.get("schema") != "team-doctrine" or fj_doctrine.get("id") != "team.konoha.fujin.doctrine":
        raise CommandRejectedError("campaign_repair_fujin_doctrine_changed")
    if pc.get("owner_id") != _PLAYER_REF:
        raise CommandRejectedError("campaign_repair_player_changed")

    bh_training = _training_block(bh, "campaign_repair_black_hound_training_changed")
    fj_training = _training_block(fj, "campaign_repair_fujin_training_changed")
    bh_doctrine_training = _training_block(
        bh_doctrine, "campaign_repair_black_hound_doctrine_changed"
    )
    fj_doctrine_training = _training_block(
        fj_doctrine, "campaign_repair_fujin_doctrine_changed"
    )
    if bh_training.get("instructor_refs") != [_PLAYER_REF, "canon_hayama_shirakumo"]:
        raise CommandRejectedError("campaign_repair_black_hound_training_changed")
    if fj_training.get("instructor_refs") != [_PLAYER_REF, "char.zhu", "char.linh"]:
        raise CommandRejectedError("campaign_repair_fujin_training_changed")
    if bh_training.get("facility_refs") != [_SWORD_MANOR] or fj_training.get("facility_refs") != [_SWORD_MANOR]:
        raise CommandRejectedError("campaign_repair_team_facility_changed")
    if bh_doctrine_training.get("lead_instructors") != [_PLAYER_REF, "canon_hayama_shirakumo"]:
        raise CommandRejectedError("campaign_repair_black_hound_doctrine_changed")
    if fj_doctrine_training.get("lead_instructors") != [_PLAYER_REF, "char.zhu", "char.linh"]:
        raise CommandRejectedError("campaign_repair_fujin_doctrine_changed")

    goal_state = pc.get("goal_state")
    orders = goal_state.get("current_orders") if isinstance(goal_state, Mapping) else None
    if orders != [_OLD_ORDER_1, _OLD_ORDER_2, _OLD_ORDER_3]:
        raise CommandRejectedError("campaign_repair_player_orders_changed")

    bh["training"]["instructor_refs"] = list(_SUPERVISOR_REFS)
    fj["training"]["instructor_refs"] = list(_SUPERVISOR_REFS)
    bh_doctrine["training"]["lead_instructors"] = list(_SUPERVISOR_REFS)
    fj_doctrine["training"]["lead_instructors"] = list(_SUPERVISOR_REFS)
    bh_doctrine["effective_from"] = str(current_time)
    bh_doctrine["approved_by"] = _PLAYER_REF
    fj_doctrine["effective_from"] = str(current_time)
    fj_doctrine["approved_by"] = _PLAYER_REF
    pc["goal_state"]["current_orders"] = [_NEW_ORDER_1, _OLD_ORDER_2, _NEW_ORDER_3]

    return {
        _BLACK_HOUND_PATH: bh,
        _BLACK_HOUND_DOCTRINE_PATH: bh_doctrine,
        _FUJIN_PATH: fj,
        _FUJIN_DOCTRINE_PATH: fj_doctrine,
        _PLAYER_PATH: pc,
    }


def _campaign_repair_resolution(
    self,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("repair_id",), command.command_type)
    if command.payload.get("repair_id") != _REPAIR_ID:
        raise CommandRejectedError("campaign_repair_id_invalid")
    if command.actor_id != meta.get("player_id") or command.actor_id != _PLAYER_REF:
        raise CommandRejectedError("campaign_repair_actor_invalid")

    # Resolve the newly authoritative external supervisors through normal owner
    # routing before changing either team's standing training configuration.
    for supervisor_ref in _SUPERVISOR_REFS:
        try:
            _path, _digest, view = self._resolve_covered_owner_view(
                supervisor_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("campaign_repair_supervisor_unresolved") from exc
        if not isinstance(view, Mapping):
            raise CommandRejectedError("campaign_repair_supervisor_unresolved")

    try:
        before_records = {
            _BLACK_HOUND_PATH: self.repository.read_json(_BLACK_HOUND_PATH),
            _BLACK_HOUND_DOCTRINE_PATH: self.repository.read_json(_BLACK_HOUND_DOCTRINE_PATH),
            _FUJIN_PATH: self.repository.read_json(_FUJIN_PATH),
            _FUJIN_DOCTRINE_PATH: self.repository.read_json(_FUJIN_DOCTRINE_PATH),
            _PLAYER_PATH: self.repository.read_json(_PLAYER_PATH),
        }
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("campaign_repair_training_supervision_state_invalid") from exc

    repaired = repair_training_supervision_records(
        before_records[_BLACK_HOUND_PATH],
        before_records[_BLACK_HOUND_DOCTRINE_PATH],
        before_records[_FUJIN_PATH],
        before_records[_FUJIN_DOCTRINE_PATH],
        before_records[_PLAYER_PATH],
        current_time=current_time,
    )

    historical_guards = {
        _BLACK_HOUND_PATH: copy.deepcopy(
            before_records[_BLACK_HOUND_PATH]["training"].get("recent_sessions", [])
        ),
        _FUJIN_PATH: copy.deepcopy(
            before_records[_FUJIN_PATH]["training"].get("recent_sessions", [])
        ),
        _BLACK_HOUND_DOCTRINE_PATH: copy.deepcopy(
            before_records[_BLACK_HOUND_DOCTRINE_PATH].get("familiarity", {})
        ),
        _FUJIN_DOCTRINE_PATH: copy.deepcopy(
            before_records[_FUJIN_DOCTRINE_PATH].get("familiarity", {})
        ),
    }

    world_events = self._world_events()
    repair_event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_resolution",
        at=current_time,
        host_refs=(_BLACK_HOUND_REF, _FUJIN_REF),
        actor_refs=(),
        causal_refs=(),
        affected_owner_refs=tuple(sorted(repaired)),
        material_consequence_refs=(
            _REPAIR_ID,
            "team_training_supervision:team.blackhound:char.zhu,char.linh",
            "team_training_supervision:team.konoha.fujin:char.zhu,char.linh",
            "team_training_facility:place.sword_manor",
            "historical_training_preserved",
        ),
        classification="restricted",
        audience_refs=(_PLAYER_REF,),
        source_refs=("runtime.campaign_repair",),
        reducer_ref="shinobi_runtime.commands.campaign_repair_training_supervision",
    )

    writes = {
        self.meta_path: _json_bytes(
            self._meta_after(meta, command, world_time=current_time)
        ),
        **{path: _json_bytes(record) for path, record in repaired.items()},
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("campaign training supervision repair write set changed")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )

        staged_bh = overlay.read_json(_BLACK_HOUND_PATH)
        staged_fj = overlay.read_json(_FUJIN_PATH)
        staged_bh_doctrine = overlay.read_json(_BLACK_HOUND_DOCTRINE_PATH)
        staged_fj_doctrine = overlay.read_json(_FUJIN_DOCTRINE_PATH)
        staged_player = overlay.read_json(_PLAYER_PATH)
        if staged_bh.get("training", {}).get("instructor_refs") != list(_SUPERVISOR_REFS):
            raise ValueError("Black Hound supervision repair after-image invalid")
        if staged_fj.get("training", {}).get("instructor_refs") != list(_SUPERVISOR_REFS):
            raise ValueError("Fujin supervision repair after-image invalid")
        if staged_bh.get("training", {}).get("facility_refs") != [_SWORD_MANOR]:
            raise ValueError("Black Hound facility changed during repair")
        if staged_fj.get("training", {}).get("facility_refs") != [_SWORD_MANOR]:
            raise ValueError("Fujin facility changed during repair")
        if staged_bh_doctrine.get("training", {}).get("lead_instructors") != list(_SUPERVISOR_REFS):
            raise ValueError("Black Hound doctrine supervision repair invalid")
        if staged_fj_doctrine.get("training", {}).get("lead_instructors") != list(_SUPERVISOR_REFS):
            raise ValueError("Fujin doctrine supervision repair invalid")
        if staged_bh.get("training", {}).get("recent_sessions", []) != historical_guards[_BLACK_HOUND_PATH]:
            raise ValueError("Black Hound historical training changed during repair")
        if staged_fj.get("training", {}).get("recent_sessions", []) != historical_guards[_FUJIN_PATH]:
            raise ValueError("Fujin historical training changed during repair")
        if staged_bh_doctrine.get("familiarity", {}) != historical_guards[_BLACK_HOUND_DOCTRINE_PATH]:
            raise ValueError("Black Hound familiarity changed during repair")
        if staged_fj_doctrine.get("familiarity", {}) != historical_guards[_FUJIN_DOCTRINE_PATH]:
            raise ValueError("Fujin familiarity changed during repair")
        orders = staged_player.get("goal_state", {}).get("current_orders")
        if orders != [_NEW_ORDER_1, _OLD_ORDER_2, _NEW_ORDER_3]:
            raise ValueError("player training supervision orders were not repaired")
        events = overlay.read_json("state/reg/world-events.json").get("events", [])
        if not any(
            isinstance(row, Mapping) and row.get("id") == repair_event_id
            for row in events
        ):
            raise ValueError("campaign training supervision repair event missing")

    return _BuiltPlan(
        code="campaign_repair_resolution_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "repair_id": _REPAIR_ID,
            "world_time_unchanged": str(current_time),
            "black_hound_training_supervisors": list(_SUPERVISOR_REFS),
            "fujin_training_supervisors": list(_SUPERVISOR_REFS),
            "training_facility_ref": _SWORD_MANOR,
            "historical_training_preserved": True,
            "repair_event_id": repair_event_id,
        },
        validator=validate,
    )


def install_training_supervision_repair() -> None:
    """Temporarily expose the exact guarded repair through the final planner."""

    COMMAND_SPECS["campaign_repair_resolution"] = CommandSpec(
        required_fields=("repair_id",),
        optional_fields=(),
        summary="Apply the exact guarded team-training supervision continuity repair.",
        payload_hints={"repair_id": _REPAIR_ID},
        availability="explicit_ooc_campaign_repair_only",
    )
    from shinobi_runtime.commands import campaign_mission_assignment as planner_module

    planner = planner_module.CampaignCommandPlanner
    setattr(planner, "_campaign_repair_resolution", _campaign_repair_resolution)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)


__all__ = [
    "install_training_supervision_repair",
    "repair_training_supervision_records",
]
