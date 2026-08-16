"""Guarded one-shot repair for Wei Tang's missing immediate-family parentage."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import FAMILY_INDEX_PATH, KINSHIP_INDEX_PATH, WORLD_EVENT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_REPAIR_ID = "repair.tang_immediate_family_continuity.2026-08-16"
_PLAYER = "pc_wei_tang"
_BROTHER = "char.kai"
_PARENTS = ("char.zhu", "char.linh")
_CHILDREN = (_PLAYER, _BROTHER)
_PARENTAGE_IDS = {
    _PLAYER: "family.parentage.continuity.wei_tang",
    _BROTHER: "family.parentage.continuity.kai_tang",
}
_INSTALLED = False


def _parentage_record(child_ref: str) -> dict[str, Any]:
    parentage_id = _PARENTAGE_IDS[child_ref]
    return {
        "schema": "family-parentage",
        "parentage_id": parentage_id,
        "child_id": child_ref,
        "authority": True,
        "parent_links": [
            {"parent_id": parent_ref, "kind": "biological"}
            for parent_ref in _PARENTS
        ],
        "guardian_links": [],
        "provenance_note": (
            "Player-directed continuity repair on 2026-08-16: Zhu Tang and "
            "Linh Tang are the parents of Wei Tang and Kai Tang; Wei and Kai "
            "are brothers."
        ),
    }


def _repair(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("repair_id",), "campaign_family_continuity_repair")
    if command.payload["repair_id"] != _REPAIR_ID:
        raise CommandRejectedError("campaign_family_continuity_repair_id_invalid")
    if command.actor_id != meta.get("player_id") or command.actor_id != _PLAYER:
        raise CommandRejectedError("campaign_family_continuity_repair_actor_invalid")

    # The player owner is a campaign root rather than an ordinary covered-owner
    # route. Resolve it directly, while non-player family members still use the
    # normal exact-person authority resolver.
    try:
        player = self.repository.read_json("state/player.json")
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("campaign_family_continuity_repair_person_unresolved") from exc
    if (
        not isinstance(player, Mapping)
        or player.get("schema") != "shinobi_character"
        or player.get("owner_id") != _PLAYER
    ):
        raise CommandRejectedError("campaign_family_continuity_repair_person_unresolved")

    # This repair is provenance-backed by the player's explicit OOC continuity
    # assertion. It may fill only the known Tang immediate-family omission and
    # must fail closed if later state has already established competing truth.
    for person_ref in (*_PARENTS, _BROTHER):
        self._require_person_ref(person_ref, code="campaign_family_continuity_repair_person_unresolved")

    family, kinship = self._family_indexes()
    parentage_bucket = family.get("parentage")
    counts = family.get("counts")
    if not isinstance(parentage_bucket, dict) or not isinstance(counts, dict):
        raise CommandRejectedError("campaign_family_continuity_repair_family_invalid")

    # Existing parentage for either child is ambiguous and must not be silently
    # replaced. The repair only fills the confirmed omission.
    person_index = family.get("person_index")
    if not isinstance(person_index, dict):
        raise CommandRejectedError("campaign_family_continuity_repair_family_invalid")
    for child_ref in _CHILDREN:
        entry = person_index.get(child_ref)
        if isinstance(entry, Mapping) and entry.get("parentage"):
            raise CommandRejectedError("campaign_family_continuity_repair_competing_parentage")

    writes: dict[str, bytes] = {}
    created_refs: list[str] = []
    for child_ref in _CHILDREN:
        parentage_id = _PARENTAGE_IDS[child_ref]
        path = f"state/family/parentage/{parentage_id}.json"
        if parentage_id in parentage_bucket or self.repository.read_optional_bytes(path) is not None:
            raise CommandRejectedError("campaign_family_continuity_repair_parentage_conflict")
        record = _parentage_record(child_ref)
        parentage_bucket[parentage_id] = path
        self._append_unique(self._family_person_entry(family, child_ref)["parentage"], parentage_id)
        child_links = self._kinship_person_entry(kinship, child_ref)
        for parent_ref in _PARENTS:
            self._append_unique(self._family_person_entry(family, parent_ref)["parentage"], parentage_id)
            self._append_unique(child_links["parents"], parent_ref)
            self._append_unique(self._kinship_person_entry(kinship, parent_ref)["children"], child_ref)
        writes[path] = _json_bytes(record)
        created_refs.append(parentage_id)

    counts["parentage"] = len(parentage_bucket)

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="campaign_repair_applied",
        at=current_time,
        host_refs=("house.tang",),
        actor_refs=(command.actor_id,),
        affected_owner_refs=(
            FAMILY_INDEX_PATH,
            KINSHIP_INDEX_PATH,
            *(f"state/family/parentage/{ref}.json" for ref in created_refs),
        ),
        material_consequence_refs=(
            "family_parentage_restored:pc_wei_tang:char.zhu,char.linh",
            "family_parentage_restored:char.kai:char.zhu,char.linh",
            "family_sibling_inferred:pc_wei_tang:char.kai",
        ),
        classification="restricted",
        audience_refs=(command.actor_id,),
        source_refs=(command.actor_id, "char.zhu", "char.linh", "char.kai"),
        reducer_ref="shinobi_runtime.commands.campaign_family_continuity_repair",
    )

    writes[FAMILY_INDEX_PATH] = _json_bytes(family)
    writes[KINSHIP_INDEX_PATH] = _json_bytes(kinship)
    writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=current_time))
    writes.update(self._world_event_writes(world_events))
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))
    expected_family = copy.deepcopy(family)
    expected_kinship = copy.deepcopy(kinship)

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("campaign family continuity repair write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        if overlay.read_json(FAMILY_INDEX_PATH) != expected_family:
            raise ValueError("campaign family continuity repair family index mismatch")
        if overlay.read_json(KINSHIP_INDEX_PATH) != expected_kinship:
            raise ValueError("campaign family continuity repair kinship index mismatch")
        for child_ref in _CHILDREN:
            parentage_id = _PARENTAGE_IDS[child_ref]
            row = overlay.read_json(f"state/family/parentage/{parentage_id}.json")
            if row != _parentage_record(child_ref):
                raise ValueError("campaign family continuity repair parentage mismatch")
        staged_events = overlay.read_json(WORLD_EVENT_REGISTRY_PATH).get("events", [])
        if not any(isinstance(item, Mapping) and item.get("id") == event_id for item in staged_events):
            raise ValueError("campaign family continuity repair semantic event missing")

    return _BuiltPlan(
        code="campaign_family_continuity_repair_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "repair_id": _REPAIR_ID,
            "parent_refs": list(_PARENTS),
            "child_refs": list(_CHILDREN),
            "sibling_pair": [_PLAYER, _BROTHER],
            "status": "repaired",
            "world_time": str(current_time),
        },
        validator=validate,
    )


def install_campaign_family_continuity_repair() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        "campaign_family_continuity_repair",
        CommandSpec(
            ("repair_id",),
            (),
            "Apply the guarded one-shot Tang immediate-family continuity repair.",
            {"repair_id": _REPAIR_ID},
            availability="ooc_dev_guarded_repair_only",
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_campaign_family_continuity_repair", _repair)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = ["install_campaign_family_continuity_repair", "_parentage_record"]
