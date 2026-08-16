"""Causal House Tang external recruitment outreach.

Outreach is distinct from intake. Starting the campaign sends the registered
letters and opens the registered tryout process, but moves no population. Each
eligible foreign civilian/support pool receives a dated review commitment. Only
after that review date may the existing conserved intake reducer accept a batch
from that pool.
"""
from __future__ import annotations

import copy
from functools import wraps
from typing import Any, Mapping, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _exact_payload, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import COMMITMENT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.sim.scheduler import SchedulerHost, one_shot_event
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_RULE = "game/rules/recruitment/sword-manor-outreach.json"
_POPULATION = "state/population/registry.json"
_POLICY_REF = "recruitment.sword_manor_outreach"
_REVIEW_DAYS = 14
_INSTALLED = False


def _outreach_rule(repository: Any) -> Mapping[str, Any]:
    try:
        rule = repository.read_json(_RULE)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("institution_recruitment_outreach_policy_invalid") from exc
    if (
        not isinstance(rule, Mapping)
        or rule.get("schema") != "sword-manor-external-recruitment-policy"
        or rule.get("version") != 1
        or rule.get("policy_ref") != _POLICY_REF
    ):
        raise CommandRejectedError("institution_recruitment_outreach_policy_invalid")
    owners = rule.get("eligible_source_owner_refs")
    categories = rule.get("eligible_source_categories")
    modes = rule.get("outreach_modes")
    if (
        not isinstance(owners, list)
        or not owners
        or any(not isinstance(value, str) or not value for value in owners)
        or not isinstance(categories, list)
        or not categories
        or any(not isinstance(value, str) or not value for value in categories)
        or not isinstance(modes, list)
        or not modes
        or any(not isinstance(value, str) or not value for value in modes)
    ):
        raise CommandRejectedError("institution_recruitment_outreach_policy_invalid")
    return rule


def _eligible_source_pools(repository: Any, rule: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        population = repository.read_json(_POPULATION)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("population_registry_invalid") from exc
    pools = population.get("pools") if isinstance(population, Mapping) else None
    if not isinstance(pools, Mapping):
        raise CommandRejectedError("population_registry_invalid")
    owners = set(rule["eligible_source_owner_refs"])
    categories = set(rule["eligible_source_categories"])
    selected = [
        pool_id
        for pool_id, pool in sorted(pools.items())
        if isinstance(pool_id, str)
        and isinstance(pool, Mapping)
        and pool.get("status") == "active"
        and pool.get("owner_ref") in owners
        and pool.get("category") in categories
    ]
    if not selected or len(selected) > 32:
        raise CommandRejectedError("institution_recruitment_outreach_sources_invalid")
    return tuple(selected)


def _outreach_commitments(
    repository: Any,
    *,
    institution_ref: str,
    policy_ref: str,
    source_pool_id: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    try:
        registry = repository.read_json(COMMITMENT_REGISTRY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("commitment_registry_invalid") from exc
    records = registry.get("records") if isinstance(registry, Mapping) else None
    if not isinstance(records, list):
        raise CommandRejectedError("commitment_registry_invalid")
    prefix = f"institution_recruitment_outreach:{policy_ref}"
    return tuple(
        row
        for row in records
        if isinstance(row, Mapping)
        and row.get("kind") == "promise"
        and row.get("host_ref") == institution_ref
        and (source_pool_id is None or row.get("target_ref") == source_pool_id)
        and isinstance(row.get("authority_basis"), str)
        and row.get("authority_basis").startswith(prefix)
        and row.get("status") in {"active", "overdue"}
    )


def _plan_outreach(
    self: Any,
    command: CommandEnvelope,
    meta: Mapping[str, Any],
    current_time: CampaignTime,
) -> _BuiltPlan:
    _exact_payload(command.payload, ("institution_ref", "policy_ref", "outreach_modes"), command.command_type)
    institution_ref = _stable_id(
        command.payload.get("institution_ref"),
        "institution_recruitment_outreach_institution_invalid",
        prefix="house.",
    )
    policy_ref = _stable_id(
        command.payload.get("policy_ref"),
        "institution_recruitment_outreach_policy_invalid",
        prefix="recruitment.",
    )
    if policy_ref != _POLICY_REF:
        raise CommandRejectedError("institution_recruitment_outreach_policy_invalid")
    rule = _outreach_rule(self.repository)
    raw_modes = command.payload.get("outreach_modes")
    if (
        not isinstance(raw_modes, Sequence)
        or isinstance(raw_modes, (str, bytes, bytearray))
        or not raw_modes
        or len(raw_modes) != len(set(raw_modes))
        or any(not isinstance(value, str) or not value for value in raw_modes)
    ):
        raise CommandRejectedError("institution_recruitment_outreach_modes_invalid")
    allowed_modes = set(rule["outreach_modes"])
    modes = tuple(sorted(raw_modes))
    if any(value not in allowed_modes for value in modes):
        raise CommandRejectedError("institution_recruitment_outreach_modes_invalid")

    authority_basis = self._require_growth_scope(
        command=command,
        institution_ref=institution_ref,
        scope_ref=f"recruitment:{policy_ref}",
    )
    source_pools = _eligible_source_pools(self.repository, rule)
    existing = _outreach_commitments(
        self.repository,
        institution_ref=institution_ref,
        policy_ref=policy_ref,
    )
    if existing:
        raise CommandRejectedError("institution_recruitment_outreach_already_active")

    try:
        commitments = copy.deepcopy(self.repository.read_json(COMMITMENT_REGISTRY_PATH))
    except (FileNotFoundError, ValueError) as exc:
        raise CommandRejectedError("commitment_registry_invalid") from exc
    records = commitments.get("records") if isinstance(commitments, dict) else None
    if not isinstance(records, list):
        raise CommandRejectedError("commitment_registry_invalid")

    scene = copy.deepcopy(self._scene_base(current_time))
    scheduler = self._load_scheduler(current_time=current_time, scene=scene)
    review_at = current_time.add_seconds(_REVIEW_DAYS * 24 * 60 * 60)
    commitment_ids: list[str] = []
    for index, source_pool_id in enumerate(source_pools):
        commitment_id = f"commitment.outreach.{command.digest[:16]}.{index:02d}"
        record = {
            "id": commitment_id,
            "kind": "promise",
            "subject_ref": command.actor_id,
            "target_ref": source_pool_id,
            "host_ref": institution_ref,
            "created_at": str(current_time),
            "due_at": str(review_at),
            "status": "active",
            "summary": (
                f"House Tang external recruitment outreach to {source_pool_id} through "
                f"{', '.join(modes)}; review voluntary responses after the travel/response window."
            ),
            "visibility": "public",
            "authority_basis": f"institution_recruitment_outreach:{policy_ref}:{authority_basis}",
        }
        records.append(record)
        commitment_ids.append(commitment_id)
        host_id = "host." + commitment_id
        scheduler.add_host(
            SchedulerHost(
                state=HostState(
                    host_id=host_id,
                    kind="commitment",
                    resolved_through=current_time,
                    safe_through=review_at.add_seconds(-1),
                    handler_ref="causal.scheduler",
                    rng_namespace=commitment_id,
                    next_due=review_at,
                ),
                authority_kind="commitment",
                owner_ref=COMMITMENT_REGISTRY_PATH,
                metadata={"commitment_id": commitment_id},
            )
        )
        scheduler.upsert_event(
            one_shot_event(
                kind="commitment.due",
                identity=commitment_id,
                source_host=host_id,
                target_host=host_id,
                due_at=review_at,
                payload={"commitment_id": commitment_id},
                priority=30,
                visibility="player_known",
                # The ordinary commitment reducer settles this boundary to an
                # overdue/review-ready record and removes its host. Event-seeking
                # may surface the resulting semantic event without leaving a
                # requires-player scheduler item parked beneath the intake action.
                requires_player=False,
            )
        )

    world_events = self._world_events()
    event_id = self._append_semantic_event(
        world_events,
        command=command,
        kind="institution_recruitment_outreach_started",
        at=current_time,
        host_refs=(institution_ref,),
        actor_refs=(command.actor_id,),
        affected_owner_refs=(COMMITMENT_REGISTRY_PATH, self.scheduler_path),
        material_consequence_refs=tuple(
            [*(f"outreach_mode:{mode}" for mode in modes), *(f"source_pool:{pool}" for pool in source_pools)]
        ),
        classification="public",
        audience_refs=(command.actor_id,),
        source_refs=(command.actor_id, institution_ref),
        reducer_ref="shinobi_runtime.commands.house_recruitment_outreach",
    )
    scene["scene_summary"] = (
        f"{institution_ref} begins external recruitment outreach through {', '.join(modes)}; "
        f"voluntary response review is due {review_at}."
    )
    scene["decision_required"] = None
    scene["time_passage_allowed"] = True

    writes = {
        self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
        self.scene_path: _json_bytes(scene),
        COMMITMENT_REGISTRY_PATH: _json_bytes(commitments),
        **self._scheduler_write_images(scheduler),
        **self._world_event_writes(world_events),
    }
    writes = self._prune_noop_writes(writes)
    expected_paths = tuple(sorted(writes))

    def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
        if overlay.changed_paths != expected_paths:
            raise ValueError("recruitment outreach write set changed after planning")
        self._assert_meta(
            overlay,
            manifest,
            meta_path=self.meta_path,
            command=command,
            world_time=current_time,
        )
        if overlay.read_json(COMMITMENT_REGISTRY_PATH) != commitments:
            raise ValueError("recruitment outreach commitments diverged from plan")
        self._scheduler_from_reader(overlay)

    return _BuiltPlan(
        code="institution_recruitment_outreach_resolution_ready",
        affected_refs=expected_paths,
        writes=writes,
        result={
            "command_type": command.command_type,
            "institution_ref": institution_ref,
            "policy_ref": policy_ref,
            "outreach_modes": list(modes),
            "source_pool_refs": list(source_pools),
            "review_at": str(review_at),
            "commitment_ids": commitment_ids,
            "semantic_event_id": event_id,
            "status": "outreach_active",
        },
        validator=validate,
    )


def install_house_recruitment_outreach() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.commands import campaign_environment as module

    COMMAND_SPECS.setdefault(
        "institution_recruitment_outreach_resolution",
        CommandSpec(
            ("institution_ref", "policy_ref", "outreach_modes"),
            (),
            "Begin one authorized House recruitment outreach campaign without moving population before the response window matures.",
            {
                "institution_ref": "house.<id>",
                "policy_ref": "recruitment.<id>",
                "outreach_modes": ["letters", "open_tryouts"],
            },
        ),
    )
    planner = module.CampaignCommandPlanner
    setattr(planner, "_institution_recruitment_outreach_resolution", _plan_outreach)
    planner.COMMAND_TYPES = frozenset(COMMAND_SPECS)
    _INSTALLED = True


__all__ = ["install_house_recruitment_outreach", "_outreach_commitments"]
