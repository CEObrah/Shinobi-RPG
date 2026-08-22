"""Bounded idempotent startup maintenance for the persistent Jianghu campaign.

Maintenance uses the same WAL, validators, Git commit, remote durability and
idempotency machinery as gameplay. It never edits campaign files directly and
never advances the gameplay world revision.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from shinobi_runtime.commands import CommandEnvelope
from shinobi_runtime.martial_world.escort_migration import plan_escort_policy_v3_migration
from shinobi_runtime.store import RegisteredSchemaValidator, RegisteredTemplateValidator, RepositoryStore
from shinobi_runtime.tx import TransactionCoordinator
from shinobi_runtime.tx.canonical import canonical_sha256

_MIGRATION_NAME = "escort_policy_v3"
_MIGRATION_SUBMITTED_AT = "2026-08-22T00:00:00Z"


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def run_startup_maintenance(
    repository: RepositoryStore,
    coordinator: TransactionCoordinator,
) -> Mapping[str, Any]:
    """Apply currently required state migrations through one maintenance tx."""
    plan = plan_escort_policy_v3_migration(repository.read_json)
    planned_writes = plan.get("writes", {})
    if not isinstance(planned_writes, Mapping) or not planned_writes:
        return {"status": "not_needed", "migration": _MIGRATION_NAME, "migrated_contracts": 0}

    write_paths = tuple(sorted(str(path) for path in planned_writes if isinstance(path, str)))
    before = {path: repository.digest(path) for path in write_paths}
    fingerprint = canonical_sha256(before)
    meta = repository.read_json("state/meta.json")
    campaign_id = str(meta.get("campaign_id") or "")
    player_id = str(meta.get("player_id") or "system")
    revision = int(meta.get("revision", 0))
    command = CommandEnvelope(
        campaign_id=campaign_id,
        request_id=f"maintenance.{_MIGRATION_NAME}.{fingerprint[:24]}",
        actor_id=player_id,
        command_type=f"maintenance_{_MIGRATION_NAME}",
        expected_revision=revision,
        submitted_at=_MIGRATION_SUBMITTED_AT,
        mode="maintenance",
        payload={"migration": _MIGRATION_NAME, "base_fingerprint": fingerprint},
    )
    transaction_id = "tx.maintenance." + command.digest
    writes = {
        path: _json_bytes(value)
        for path, value in planned_writes.items()
        if isinstance(path, str) and isinstance(value, Mapping)
    }
    if set(writes) != set(write_paths):
        raise ValueError("startup maintenance produced a non-object state owner")

    schema_validator = RegisteredSchemaValidator.optional(repository)
    template_validator = RegisteredTemplateValidator.optional(repository)

    def validate(overlay, manifest) -> None:
        if tuple(sorted(manifest.paths)) != write_paths:
            raise ValueError("startup maintenance write set changed after planning")
        if schema_validator is not None:
            schema_validator.validate_overlay(overlay, write_paths)
        if template_validator is not None:
            template_validator.validate_overlay(overlay, write_paths)

    result = {
        "migration": _MIGRATION_NAME,
        "migrated_contracts": len(plan.get("migrated_contract_refs", [])),
        "accepted_contracts_preserved": len(plan.get("accepted_contract_refs", [])),
        "refund_cash": int(plan.get("refund_cash", 0)),
        "topup_cash": int(plan.get("topup_cash", 0)),
        "cash_conserved": int(plan.get("market_cash_delta", 0)) + int(plan.get("escrow_delta", 0)) == 0,
    }
    execution = coordinator.execute(
        command,
        transaction_id=transaction_id,
        created_at=_MIGRATION_SUBMITTED_AT,
        writes=writes,
        result=result,
        validator=validate,
    )
    return {
        "status": execution.status,
        "migration": _MIGRATION_NAME,
        **result,
    }


__all__ = ["run_startup_maintenance"]
