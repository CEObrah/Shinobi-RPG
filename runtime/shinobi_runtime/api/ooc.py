"""Bounded, read-only operational diagnostics for the private API.

The provider deliberately consumes only registered repository/runtime summaries.
It never executes validators, shell commands, models, or caller-supplied paths,
and it never turns an OOC observation into campaign truth.
"""

from __future__ import annotations

import heapq
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import OocAuditResult
from shinobi_runtime.sim import CampaignTime
from shinobi_runtime.sim.scheduler_store import SchedulerStore
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.tx.receipts import IdempotencyReceipt


_TERMINAL_FRONT_STATUSES = frozenset(
    ("completed", "resolved", "failed", "cancelled", "abandoned", "superseded")
)
_WAL_STATUSES = frozenset(("prepared", "applied", "committed", "rolled_back"))


def _mapping(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _sequence(value: Any) -> Optional[Sequence[Any]]:
    return value if isinstance(value, list) else None


def _campaign_time(value: Any) -> Optional[CampaignTime]:
    if not isinstance(value, str):
        return None
    try:
        return CampaignTime.parse(value)
    except (TypeError, ValueError):
        return None


def _bounded_files(directory: Path, limit: int) -> Tuple[Tuple[Path, ...], bool]:
    """Return the deterministic lexical prefix using at most ``limit + 1`` memory."""

    if not directory.is_dir():
        return (), False
    try:
        selected = heapq.nsmallest(
            limit + 1,
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix == ".json"
            ),
            key=lambda path: path.name,
        )
    except OSError:
        return (), False
    return tuple(selected[:limit]), len(selected) > limit


class _Report:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.diagnostics: List[str] = []
        self.suggestions: List[str] = []

    @staticmethod
    def _clean(value: str) -> str:
        return value.replace("\x00", "").replace("\r", " ").replace("\n", " ")[:2048]

    def diagnostic(self, value: str) -> None:
        cleaned = self._clean(value)
        if cleaned and cleaned not in self.diagnostics and len(self.diagnostics) < self.limit:
            self.diagnostics.append(cleaned)

    def suggestion(self, value: str) -> None:
        cleaned = self._clean(value)
        if cleaned and cleaned not in self.suggestions and len(self.suggestions) < self.limit:
            self.suggestions.append(cleaned)

    def result(self) -> OocAuditResult:
        return OocAuditResult(
            diagnostics=tuple(self.diagnostics),
            suggestions=tuple(self.suggestions),
            write_plan=None,
        )


class RepositoryOocAudit:
    """Inspect small operational authorities without changing repository state.

    Caller text is represented only as a count/presence marker.  It is never
    parsed as a fact, path, command, or permission to write.
    """

    def __init__(
        self,
        repository: RepositoryStore,
        runtime_root: object,
        *,
        max_diagnostics: int = 48,
        max_scheduler_hosts: int = 512,
        max_canon_pressures: int = 256,
        max_runtime_records: int = 4096,
    ) -> None:
        for label, value in (
            ("max_diagnostics", max_diagnostics),
            ("max_scheduler_hosts", max_scheduler_hosts),
            ("max_canon_pressures", max_canon_pressures),
            ("max_runtime_records", max_runtime_records),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{label} must be a positive integer")
        if max_diagnostics > 64:
            raise ValueError("max_diagnostics may not exceed the API result limit")
        self.repository = repository
        self.runtime_root = Path(runtime_root).resolve()
        self.max_diagnostics = max_diagnostics
        self.max_scheduler_hosts = max_scheduler_hosts
        self.max_canon_pressures = max_canon_pressures
        self.max_runtime_records = max_runtime_records

    def _read_repository_json(
        self, report: _Report, path: str, label: str
    ) -> Optional[Mapping[str, Any]]:
        try:
            value = self.repository.read_json(path)
        except FileNotFoundError:
            report.diagnostic(f"{label}:missing")
            report.suggestion(f"restore_or_register_{label}_before_gameplay")
            return None
        except (OSError, TypeError, ValueError):
            report.diagnostic(f"{label}:invalid_or_unreadable")
            report.suggestion(f"repair_{label}_through_reviewed_maintenance")
            return None
        result = _mapping(value)
        if result is None:
            report.diagnostic(f"{label}:invalid_root_type")
            report.suggestion(f"repair_{label}_through_reviewed_maintenance")
        return result

    def _audit_meta(
        self, report: _Report
    ) -> Tuple[Optional[CampaignTime], Optional[int]]:
        meta = self._read_repository_json(report, "state/meta.json", "campaign_meta")
        if meta is None:
            return None, None
        revision = meta.get("revision")
        world_time = _campaign_time(meta.get("time"))
        revision_valid = (
            not isinstance(revision, bool)
            and isinstance(revision, int)
            and revision >= 0
        )
        if world_time is None or not revision_valid:
            report.diagnostic("campaign_meta:invalid_revision_or_world_time")
            report.suggestion("repair_campaign_meta_before_gameplay")
            return world_time, revision if revision_valid else None
        report.diagnostic(f"campaign_meta:ok revision={revision} world_time={world_time}")
        return world_time, revision

    def _audit_scheduler(
        self, report: _Report, world_time: Optional[CampaignTime]
    ) -> Optional[SchedulerStore]:
        try:
            store = SchedulerStore(self.repository)
            raw = store.root()
            scheduler_time = CampaignTime.parse(raw["world_time"])
        except (KeyError, TypeError, ValueError, FileNotFoundError) as exc:
            report.diagnostic("causal_scheduler:invalid_summary")
            report.suggestion("repair_causal_scheduler_before_time_advance")
            return None
        relation = "unknown"
        if world_time is not None:
            relation = (
                "current" if scheduler_time == world_time
                else "stale" if scheduler_time < world_time
                else "future"
            )
        host_count = int(raw.get("host_count", 0))
        event_count = int(raw.get("pending_event_count", 0))
        next_due = _campaign_time(raw.get("next_due"))
        overdue = bool(next_due is not None and next_due <= scheduler_time)
        over_budget = host_count > self.max_scheduler_hosts
        report.diagnostic(
            "causal_scheduler:summary "
            f"world_time={scheduler_time} relation={relation} "
            f"hosts={host_count} events={event_count} overdue={int(overdue)} "
            f"earliest_due={next_due if next_due is not None else 'none'} "
            f"diagnostic_budget_exceeded={str(over_budget).lower()}"
        )
        if relation != "current" or overdue:
            report.suggestion("reconcile_causal_scheduler_before_gameplay")
        if over_budget:
            report.suggestion("use_targeted_scheduler_host_audit")
        return store

    def _audit_pressures(
        self,
        report: _Report,
        world_time: Optional[CampaignTime],
        scheduler_store: Optional[SchedulerStore],
    ) -> None:
        registry = self._read_repository_json(
            report, "state/canon/pressures.json", "canon_pressure_registry"
        )
        if registry is None:
            return
        pressures = _mapping(registry.get("pressures"))
        if pressures is None:
            report.diagnostic("canon_pressure_registry:invalid_pressure_map")
            report.suggestion("repair_canon_pressure_registry_structure")
            return
        active = 0
        overdue = 0
        missing_boundaries = 0
        boundary_mismatches = 0
        unsourced = 0
        canon_forcing = 0
        malformed = 0
        for pressure_id, raw_pressure in pressures.items():
            pressure = _mapping(raw_pressure)
            if not isinstance(pressure_id, str) or pressure is None:
                malformed += 1
                continue
            source_refs = _sequence(pressure.get("source_refs"))
            if source_refs is None:
                malformed += 1
            elif not source_refs:
                unsourced += 1
            constraints = _mapping(pressure.get("constraints"))
            if constraints is not None and constraints.get("canon_forcing") is True:
                canon_forcing += 1
            status = pressure.get("status")
            is_active = isinstance(status, str) and status not in _TERMINAL_FRONT_STATUSES
            if not is_active:
                continue
            active += 1
            boundary = _mapping(pressure.get("next_boundary"))
            if boundary is None:
                missing_boundaries += 1
                continue
            due = _campaign_time(boundary.get("due_at"))
            if due is not None and world_time is not None and due <= world_time:
                overdue += 1
            host_ref = boundary.get("host_ref")
            host = (
                scheduler_store.load_host(host_ref)
                if scheduler_store is not None and isinstance(host_ref, str)
                else None
            )
            if host is None:
                boundary_mismatches += 1
                continue
            state = getattr(host, "state", None)
            metadata = getattr(host, "metadata", {})
            expected_due = None if state is None or state.next_due is None else str(state.next_due)
            if (
                expected_due != boundary.get("due_at")
                or metadata.get("pressure_id") != pressure_id
            ):
                boundary_mismatches += 1

        over_budget = len(pressures) > self.max_canon_pressures
        report.diagnostic(
            "canon_pressure_registry:summary "
            f"pressures={len(pressures)} active={active} overdue={overdue} "
            f"missing_boundaries={missing_boundaries} "
            f"boundary_mismatches={boundary_mismatches} unsourced={unsourced} "
            f"canon_forcing={canon_forcing} malformed={malformed} "
            f"budget_exceeded={str(over_budget).lower()}"
        )
        if overdue or missing_boundaries or boundary_mismatches:
            report.suggestion("reconcile_canon_pressures_with_registered_temporal_boundaries")
        if canon_forcing:
            report.suggestion("remove_canon_forcing_and_resolve_pressures_from_current_state")
        if unsourced:
            report.suggestion(
                "keep_unsourced_canon_pressure_details_unknown_until_approved_source_or_campaign_evidence_exists"
            )
        if malformed:
            report.suggestion("repair_canon_pressure_records_through_the_registered_contract")
        if over_budget:
            report.suggestion("shard_or_archive_terminal_pressures_before_raising_pressure_budget")

    def _audit_canon(self, report: _Report) -> None:
        manifest = self._read_repository_json(
            report, "game/data/canon/manifest.json", "canon_manifest"
        )
        if manifest is None:
            return
        anchor = _mapping(manifest.get("anchor"))
        source_catalog = _sequence(manifest.get("source_catalog"))
        event_index = _mapping(manifest.get("event_index"))
        source_policy = _mapping(manifest.get("source_policy")) or {}
        forbidden = _sequence(source_policy.get("forbidden_authorities")) or ()
        if anchor is None or source_catalog is None or event_index is None:
            report.diagnostic("canon_manifest:invalid_summary")
            report.suggestion("repair_canon_manifest_before_importing_history")
            return
        binding = anchor.get("binding_status")
        locators = _sequence(anchor.get("source_locators"))
        locator_count = len(locators) if locators is not None else -1
        guards_ok = "model_memory" in forbidden and manifest.get("campaign_authority") is False
        report.diagnostic(
            "canon_manifest:summary "
            f"binding={binding if isinstance(binding, str) else 'invalid'} "
            f"sources={len(source_catalog)} anchor_locators={locator_count} "
            f"events={len(event_index)} authority_guards={str(guards_ok).lower()}"
        )
        if binding == "unbound":
            report.suggestion(
                "bind_the_campaign_anchor_to_approved_primary_source_locators_before_seeding_canon_history"
            )
            if event_index:
                report.suggestion("remove_unverified_events_until_the_canon_anchor_is_bound")
        elif binding == "verified":
            if not source_catalog or not locators:
                report.suggestion("repair_verified_canon_anchor_source_provenance")
        else:
            report.suggestion("repair_canon_anchor_binding_status")
        if not guards_ok:
            report.suggestion("restore_canon_guards_that_forbid_model_memory_as_authority")

    @staticmethod
    def _read_json_file(path: Path) -> Optional[Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _audit_wal(self, report: _Report) -> None:
        directory = self.runtime_root / "wal"
        if not directory.is_dir():
            report.diagnostic("wal:unavailable")
            report.suggestion("mount_persistent_runtime_storage_for_wal_and_receipts")
            return
        files, truncated = _bounded_files(directory, self.max_runtime_records)
        statuses = {status: 0 for status in sorted(_WAL_STATUSES)}
        invalid = 0
        for path in files:
            record = _mapping(self._read_json_file(path))
            if record is None:
                invalid += 1
                continue
            status = record.get("status")
            if (
                record.get("schema") != "shinobi.wal"
                or record.get("version") != 1
                or status not in _WAL_STATUSES
                or not isinstance(record.get("transaction_id"), str)
                or not isinstance(record.get("manifest"), Mapping)
                or not isinstance(record.get("entries"), list)
            ):
                invalid += 1
                continue
            statuses[status] += 1
        pending = statuses["prepared"] + statuses["applied"]
        report.diagnostic(
            "wal:summary "
            f"scanned={len(files)} pending={pending} committed={statuses['committed']} "
            f"rolled_back={statuses['rolled_back']} invalid={invalid} "
            f"scan_budget_exceeded={str(truncated).lower()}"
        )
        if pending:
            report.suggestion("run_transaction_recovery_before_accepting_gameplay_writes")
        if invalid:
            report.suggestion("quarantine_and_investigate_invalid_wal_records_without_deleting_evidence")
        if truncated:
            report.suggestion("archive_terminal_wal_records_under_a_reviewed_retention_policy")

    def _audit_receipts(
        self, report: _Report, campaign_revision: Optional[int]
    ) -> None:
        directory = self.runtime_root / "receipts"
        if not directory.is_dir():
            report.diagnostic("receipts:unavailable")
            report.suggestion("mount_persistent_runtime_storage_for_wal_and_receipts")
            return
        files, truncated = _bounded_files(directory, self.max_runtime_records)
        invalid = 0
        future_revision = 0
        highest_revision: Optional[int] = None
        for path in files:
            record = _mapping(self._read_json_file(path))
            if record is None:
                invalid += 1
                continue
            try:
                receipt = IdempotencyReceipt.from_record(record)
            except (TypeError, ValueError):
                invalid += 1
                continue
            highest_revision = (
                receipt.committed_revision
                if highest_revision is None
                else max(highest_revision, receipt.committed_revision)
            )
            if (
                campaign_revision is not None
                and receipt.committed_revision > campaign_revision
            ):
                future_revision += 1
        report.diagnostic(
            "receipts:summary "
            f"scanned={len(files)} invalid={invalid} future_revision={future_revision} "
            f"highest_revision={highest_revision if highest_revision is not None else 'none'} "
            f"scan_budget_exceeded={str(truncated).lower()}"
        )
        if invalid or future_revision:
            report.suggestion("investigate_receipt_integrity_before_accepting_gameplay_writes")
        if truncated:
            report.suggestion("archive_receipts_only_under_a_reviewed_idempotency_retention_policy")

    def __call__(
        self, focus: Optional[str], observations: Tuple[str, ...]
    ) -> OocAuditResult:
        report = _Report(self.max_diagnostics)
        report.diagnostic(
            "caller_context:advisory_only "
            f"focus_provided={str(focus is not None).lower()} "
            f"observation_count={len(observations)}"
        )
        if focus is not None or observations:
            report.suggestion(
                "verify_caller_observations_against_authoritative_owners_before_any_maintenance"
            )
        meta_time, revision = self._audit_meta(report)
        scheduler_store = self._audit_scheduler(report, meta_time)
        self._audit_pressures(report, meta_time, scheduler_store)
        self._audit_canon(report)
        self._audit_wal(report)
        self._audit_receipts(report, revision)
        return report.result()


__all__ = ["RepositoryOocAudit"]
