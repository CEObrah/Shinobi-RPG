"""Production stability guards shared across semantic command domains.

These guards intentionally sit above the generic reducers. They normalize
legacy/current exact-person read shapes and fail closed when a newly produced
terminal world event lacks causal ownership. They do not create a second
writable authority.
"""

from __future__ import annotations

from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError


_TERMINAL_EVENT_STATUSES = frozenset(("resolved", "failed", "cancelled", "superseded"))
_COMPACT_UNAVAILABLE = frozenset(
    ("dead", "deceased", "captured", "incapacitated", "critical", "unconscious")
)


def _nonempty_refs(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and bool(item) for item in value
    )


def _event_failure_code(prefix: str, event: Mapping[str, Any]) -> str:
    kind = event.get("kind")
    if not isinstance(kind, str) or not kind:
        return prefix
    clean = "".join(character if character.isalnum() else "_" for character in kind)[:80]
    return f"{prefix}__{clean}"


def _validate_new_terminal_event(event: Mapping[str, Any]) -> None:
    """Enforce the causal invariants required by committed semantic history."""

    if event.get("status") not in _TERMINAL_EVENT_STATUSES:
        return
    if not _nonempty_refs(event.get("affected_owner_refs")):
        raise CommandRejectedError(_event_failure_code("world_event_missing_affected_owner", event))
    if not _nonempty_refs(event.get("material_consequence_refs")):
        raise CommandRejectedError(_event_failure_code("world_event_missing_material_consequence", event))
    causal_context = []
    for field in ("host_refs", "actor_refs", "place_refs"):
        value = event.get(field)
        if isinstance(value, list):
            causal_context.extend(item for item in value if isinstance(item, str) and item)
    if not causal_context:
        raise CommandRejectedError(_event_failure_code("world_event_missing_causal_context", event))


def _event_by_id(registry: Mapping[str, Any], event_id: str) -> Mapping[str, Any]:
    events = registry.get("events")
    if not isinstance(events, list):
        raise CommandRejectedError("world_event_registry_invalid")
    matches = [
        event for event in events
        if isinstance(event, Mapping) and event.get("id") == event_id
    ]
    if len(matches) != 1:
        raise CommandRejectedError("world_event_registry_invalid")
    return matches[0]


class RuntimeStabilityMixin:
    """Compatibility and producer guards for the production campaign planner."""

    @staticmethod
    def _health_recovery_factor(record: Mapping[str, Any]) -> tuple[str, str]:
        """Project healthy legacy and current condition shapes identically."""

        if record.get("schema") == "person":
            health = record.get("health")
            if not isinstance(health, Mapping):
                return "0.85", "0.90"
            status = str(health.get("status", "")).lower()
            fatigue = health.get("fatigue")
            if (
                status in {"healthy", "ready", "fit", "active"}
                and not isinstance(fatigue, bool)
                and isinstance(fatigue, (int, float))
                and fatigue == 0
            ):
                return "1", "1"
            if status in {"injured", "wounded", "limited", "critical", "incapacitated"}:
                return "0.65", "0.75"
            return "0.85", "0.90"

        condition = record.get("condition")
        if not isinstance(condition, Mapping):
            return "1", "1"

        readiness = condition.get("readiness")
        injuries = condition.get("injuries")
        if readiness == "ready" and (injuries is None or injuries == []):
            return "1", "1"
        if readiness in ("injured", "limited"):
            return "0.65", "0.75"

        legacy_health = condition.get("health")
        legacy_fatigue = condition.get("fatigue")
        if (
            legacy_health == "healthy"
            and not isinstance(legacy_fatigue, bool)
            and isinstance(legacy_fatigue, (int, float))
            and legacy_fatigue == 0
            and (injuries is None or injuries == [])
        ):
            return "1", "1"
        if legacy_health in ("injured", "wounded", "critical"):
            return "0.65", "0.75"
        return "0.85", "0.90"

    def _living_member_profile(
        self,
        person_ref: str,
        *,
        record_writes: Mapping[str, Mapping[str, Any]],
    ):
        """Honor compact-person health when evaluating autonomous availability."""

        profile = super()._living_member_profile(
            person_ref, record_writes=record_writes
        )
        if profile is None:
            return None
        try:
            path, _digest = self._resolve_covered_owner(person_ref)
        except CommandRejectedError:
            return profile
        record = record_writes.get(path)
        if record is None:
            try:
                record = self.repository.read_json(path)
            except (FileNotFoundError, ValueError):
                return profile
        if not isinstance(record, Mapping) or record.get("schema") != "person":
            return profile
        health = record.get("health")
        status = str(health.get("status", "")).lower() if isinstance(health, Mapping) else ""
        if status in _COMPACT_UNAVAILABLE:
            return type(profile)(person_ref, False, status, profile.scores)
        stats = record.get("stats")
        resources = stats.get("resources") if isinstance(stats, Mapping) else None
        pool = resources.get("health") if isinstance(resources, Mapping) else None
        if isinstance(pool, Mapping):
            current, capacity = pool.get("current"), pool.get("capacity")
            if (
                not isinstance(current, bool)
                and isinstance(current, int)
                and not isinstance(capacity, bool)
                and isinstance(capacity, int)
                and capacity > 0
                and current * 2 < capacity
            ):
                return type(profile)(
                    person_ref, False, "health_below_operational_threshold", profile.scores
                )
        return profile

    def _apply_institution_autonomy_review(self, *args: Any, **kwargs: Any):
        """Carry scheduler-provided enclosing world authority into nested events."""

        owner_path = kwargs.pop("institution_owner_ref", None)
        if owner_path is not None and (
            not isinstance(owner_path, str) or not owner_path.startswith("state/")
        ):
            raise CommandRejectedError("institution_world_owner_invalid")
        missing = object()
        previous = getattr(self, "_stability_institution_owner_path", missing)
        if owner_path is not None:
            self._stability_institution_owner_path = owner_path
        try:
            return super()._apply_institution_autonomy_review(*args, **kwargs)
        finally:
            if owner_path is not None:
                if previous is missing:
                    delattr(self, "_stability_institution_owner_path")
                else:
                    self._stability_institution_owner_path = previous

    def _derive_event_owner_refs(self, kwargs: Mapping[str, Any]) -> tuple[str, ...]:
        """Resolve missing causal-host identities to their registered owners."""

        paths: set[str] = set()
        host_refs = kwargs.get("host_refs", ())
        if isinstance(host_refs, (list, tuple, set)):
            for host_ref in host_refs:
                if not isinstance(host_ref, str) or not host_ref:
                    continue
                try:
                    path, _digest = self._resolve_covered_owner(host_ref)
                except CommandRejectedError:
                    continue
                if isinstance(path, str) and path:
                    paths.add(path)
        return tuple(sorted(paths))

    def _append_internal_event(self, registry: dict[str, Any], *args: Any, **kwargs: Any) -> str:
        affected = tuple(
            item for item in kwargs.get("affected_owner_refs", ())
            if isinstance(item, str) and item
        )
        kind = kwargs.get("kind")
        if kind == "institution_autonomy_reviewed":
            owner_path = getattr(self, "_stability_institution_owner_path", None)
            if isinstance(owner_path, str) and owner_path:
                affected = tuple(sorted(set(affected + (owner_path,))))
                kwargs["affected_owner_refs"] = affected
        if not affected:
            if kind == "canon_pressure_reviewed":
                affected = (self.pressures_path, self.scheduler_path)
            else:
                affected = self._derive_event_owner_refs(kwargs)
            if affected:
                kwargs["affected_owner_refs"] = affected
        event_id = super()._append_internal_event(registry, *args, **kwargs)
        _validate_new_terminal_event(_event_by_id(registry, event_id))
        return event_id

    def _append_semantic_event(self, registry: dict[str, Any], *args: Any, **kwargs: Any) -> str:
        event_id = super()._append_semantic_event(registry, *args, **kwargs)
        _validate_new_terminal_event(_event_by_id(registry, event_id))
        return event_id


__all__ = ["RuntimeStabilityMixin"]
