"""Bounded idempotent startup maintenance for the persistent Jianghu campaign.

Maintenance uses the same WAL, validators, Git commit, remote durability and
idempotency machinery as gameplay. It never edits campaign files directly and
never advances the gameplay world revision. Each migration remains a pure state
planner; this module is the single durable write authority for startup repair.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.martial_world.escort_migration import plan_escort_policy_v3_migration
from shinobi_runtime.martial_world.retinue_migration import plan_permanent_team_cohort_v2_migration
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.tx import TransactionCoordinator
from shinobi_runtime.tx.canonical import canonical_sha256

_MAINTENANCE_SUBMITTED_AT = "2026-08-22T00:00:00Z"


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _execute_maintenance_plan(
    repository: RepositoryStore,
    coordinator: TransactionCoordinator,
    *,
    migration_name: str,
    planner: Callable[[Callable[[str], Mapping[str, Any]]], Mapping[str, Any]],
) -> Mapping[str, Any]:
    plan = planner(repository.read_json)
    planned_writes = plan.get("writes", {}) if isinstance(plan, Mapping) else {}
    if not isinstance(planned_writes, Mapping) or not planned_writes:
        return {
            "status": "not_needed",
            "migration": migration_name,
            **{
                str(key): value
                for key, value in plan.items()
                if key not in {"writes", "migration"}
            },
        }

    write_paths = tuple(sorted(str(path) for path in planned_writes if isinstance(path, str)))
    before = {path: repository.digest(path) for path in write_paths}
    fingerprint = canonical_sha256(before)
    meta = repository.read_json("state/meta.json")
    campaign_id = str(meta.get("campaign_id") or "")
    player_id = str(meta.get("player_id") or "system")
    revision = int(meta.get("revision", 0))
    command = CommandEnvelope(
        campaign_id=campaign_id,
        request_id=f"maintenance.{migration_name}.{fingerprint[:24]}",
        actor_id=player_id,
        command_type=f"maintenance_{migration_name}",
        expected_revision=revision,
        submitted_at=_MAINTENANCE_SUBMITTED_AT,
        mode="maintenance",
        payload={"migration": migration_name, "base_fingerprint": fingerprint},
    )
    transaction_id = "tx.maintenance." + command.digest
    writes = {
        path: _json_bytes(value)
        for path, value in planned_writes.items()
        if isinstance(path, str) and isinstance(value, Mapping)
    }
    if set(writes) != set(write_paths):
        raise ValueError(f"startup maintenance {migration_name} produced a non-object state owner")

    schema_validator = RegisteredSchemaValidator.optional(repository)
    template_validator = RegisteredTemplateValidator.optional(repository)

    def validate(overlay, manifest) -> None:
        if tuple(sorted(manifest.paths)) != write_paths:
            raise ValueError(f"startup maintenance {migration_name} write set changed after planning")
        if schema_validator is not None:
            schema_validator.validate_overlay(overlay, write_paths)
        if template_validator is not None:
            template_validator.validate_overlay(overlay, write_paths)

    result = {
        "migration": migration_name,
        **{
            str(key): value
            for key, value in plan.items()
            if key not in {"writes", "migration"}
        },
    }
    execution = coordinator.execute(
        command,
        transaction_id=transaction_id,
        created_at=_MAINTENANCE_SUBMITTED_AT,
        writes=writes,
        result=result,
        validator=validate,
    )
    return {
        "status": execution.status,
        **result,
    }


def run_startup_maintenance(
    repository: RepositoryStore,
    coordinator: TransactionCoordinator,
) -> Mapping[str, Any]:
    """Apply every currently required bounded migration in deterministic order."""
    migrations = [
        _execute_maintenance_plan(
            repository,
            coordinator,
            migration_name="escort_policy_v3",
            planner=plan_escort_policy_v3_migration,
        ),
        _execute_maintenance_plan(
            repository,
            coordinator,
            migration_name="permanent_travel_team_cohort_v2",
            planner=plan_permanent_team_cohort_v2_migration,
        ),
    ]
    committed = [row for row in migrations if str(row.get("status") or "") in {"committed", "duplicate"}]
    return {
        "status": "committed" if committed else "not_needed",
        "migrations": migrations,
    }


__all__ = ["run_startup_maintenance"]
