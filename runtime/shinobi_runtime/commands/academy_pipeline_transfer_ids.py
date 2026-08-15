"""Compatibility repairs for Academy autonomy review integrity.

The base Academy pipeline currently formats ``suffix`` while constructing intake
and graduation transfer IDs, but the refactored reducer no longer binds that
name. The same reducer also emits one aggregate ``institution_autonomy_reviewed``
world event whose material consequence list only considers bundled operation
work. A monthly Academy, service-training, or military review can therefore make
real state changes while the aggregate event is left with an empty material
consequence list and fails world-event validation.

The Academy graduation path also grows the service force's availability and
training troop pool without growing the matching reserve-capability cohort. A
later service-training or military review then detects an artificial capability
partition drift. This module repairs only that exact staged delta proven by the
same Academy result; unrelated force drift still fails closed.

One monthly ordering edge also lets civil-economy settlement load the population
registry into a dedicated ``population_write`` before the Academy review loads a
second copy into autonomous owner writes. The final write then has two competing
after-images for one authoritative owner. A context-local transaction bridge
shares the already-loaded population object with later Academy work so the same
owner is mutated exactly once regardless of due-event ordering.

Production campaign extensions install this wrapper before other
institution-autonomy wrappers. It keeps the missing transfer suffix context-local,
shares the monthly population owner, reconciles the Academy force capability
partition, then reconciles the aggregate review event against the reducer's own
returned sub-results. It never owns Academy population selection, graduation
rates, force totals, institutional policy, or capability development; those
remain in ``AutonomyCommandsMixin._apply_institution_autonomy_review``.

This module should be removed once the base reducer directly binds its Academy
transfer suffix, uses one population owner object across monthly settlement,
updates every conserved force representation during graduation, and
attributes/suppresses its aggregate institution review event correctly.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.domains import autonomy as autonomy_module
from shinobi_runtime.commands.domains.autonomy import AutonomyCommandsMixin
from shinobi_runtime.commands.domains.time import TimeCommandsMixin
from shinobi_runtime.commands.paths import POPULATION_REGISTRY_PATH

_INSTALLED = False
_SUFFIX: ContextVar[str | None] = ContextVar("academy_pipeline_transfer_suffix", default=None)
_SHARED_POPULATION: ContextVar[dict[str, Any] | None] = ContextVar(
    "monthly_shared_population_owner",
    default=None,
)


class _SuffixProxy:
    def _value(self) -> str:
        value = _SUFFIX.get()
        if value is None:
            raise RuntimeError("academy_pipeline_transfer_suffix_unbound")
        return value

    def __str__(self) -> str:
        return self._value()

    def __format__(self, format_spec: str) -> str:
        return format(self._value(), format_spec)


def academy_pipeline_transfer_suffix(institution_id: str, at: Any) -> str:
    """Return the stable transfer-id suffix for one institution review boundary."""
    if not isinstance(institution_id, str) or not institution_id:
        raise ValueError("institution_id must be a non-empty string")
    return hashlib.sha256(
        f"{institution_id}\x00{at}\x00academy-pipeline-transfer".encode("utf-8")
    ).hexdigest()[:20]


def _append_material_ref(refs: list[str], value: Any) -> None:
    if isinstance(value, str) and value and value not in refs:
        refs.append(value)


def _bind_shared_population_owner(record_writes: Any) -> None:
    """Ensure later Academy work reuses the population object already in flight."""
    shared = _SHARED_POPULATION.get()
    if shared is None:
        return
    if not isinstance(record_writes, dict):
        raise CommandRejectedError("population_registry_invalid")
    existing = record_writes.get(POPULATION_REGISTRY_PATH)
    if existing is None:
        record_writes[POPULATION_REGISTRY_PATH] = shared
        return
    if existing is not shared:
        # Two mutable objects for one authoritative owner inside one transaction
        # are unsafe even when their bytes happen to match at this instant.
        raise CommandRejectedError("population_owner_transaction_alias_conflict")


def _install_population_owner_bridge() -> None:
    """Share economy-loaded population state with later autonomy in one time plan."""
    advance = TimeCommandsMixin._advance_time
    if not getattr(advance, "_academy_population_owner_bridge", False):
        @wraps(advance)
        def advance_time(self: Any, *args: Any, **kwargs: Any):
            token = _SHARED_POPULATION.set(None)
            try:
                return advance(self, *args, **kwargs)
            finally:
                _SHARED_POPULATION.reset(token)

        advance_time._academy_population_owner_bridge = True  # type: ignore[attr-defined]
        TimeCommandsMixin._advance_time = advance_time

    civil = TimeCommandsMixin._settle_governed_civil_economies
    if not getattr(civil, "_academy_population_owner_bridge", False):
        @wraps(civil)
        def settle_civil(
            self: Any,
            governance: Any,
            population: Any,
            holders: Any,
            finance: Any,
            *args: Any,
            **kwargs: Any,
        ):
            if not isinstance(population, dict):
                raise CommandRejectedError("population_registry_invalid")
            _SHARED_POPULATION.set(population)
            return civil(
                self,
                governance,
                population,
                holders,
                finance,
                *args,
                **kwargs,
            )

        settle_civil._academy_population_owner_bridge = True  # type: ignore[attr-defined]
        TimeCommandsMixin._settle_governed_civil_economies = settle_civil


def _derived_material_refs(result: Mapping[str, Any]) -> list[str]:
    """Project concrete material consequences already proven by reducer output."""
    refs: list[str] = []

    pipeline = result.get("population_pipeline")
    if isinstance(pipeline, Mapping):
        _append_material_ref(refs, pipeline.get("intake_transfer_id"))
        _append_material_ref(refs, pipeline.get("graduation_transfer_id"))

    service = result.get("service_training")
    if isinstance(service, Mapping):
        completed = service.get("completed")
        if isinstance(completed, int) and not isinstance(completed, bool) and completed > 0:
            force_ref = service.get("force_ref")
            if isinstance(force_ref, str) and force_ref:
                _append_material_ref(refs, f"service_training:{force_ref}:{completed}")
            else:
                _append_material_ref(refs, service.get("event_ref"))

    military = result.get("military_lifecycle")
    if isinstance(military, Mapping):
        for key in ("formation_ref", "force_ref", "operation_ref"):
            _append_material_ref(refs, military.get(key))
        recovered = military.get("medical_recovered")
        force_ref = military.get("force_ref")
        if (
            isinstance(recovered, int)
            and not isinstance(recovered, bool)
            and recovered > 0
            and isinstance(force_ref, str)
            and force_ref
        ):
            _append_material_ref(refs, f"medical_recovery:{force_ref}:{recovered}")

    return refs


def repair_academy_force_reserve_capability(
    reducer: Any,
    result: Mapping[str, Any],
    record_writes: Any,
) -> None:
    """Reconcile only the reserve-capability delta caused by Academy graduates."""
    pipeline = result.get("population_pipeline")
    if not isinstance(pipeline, Mapping):
        return
    graduates = pipeline.get("graduates")
    force_ref = pipeline.get("force_ref")
    if (
        isinstance(graduates, bool)
        or not isinstance(graduates, int)
        or graduates < 0
        or not isinstance(force_ref, str)
        or not force_ref
    ):
        raise CommandRejectedError("institution_autonomy_force_invalid")
    if graduates == 0:
        return
    if not isinstance(record_writes, Mapping):
        raise CommandRejectedError("institution_autonomy_force_invalid")

    matches = [
        record
        for record in record_writes.values()
        if isinstance(record, dict)
        and record.get("schema") == "force"
        and record.get("id") == force_ref
    ]
    if len(matches) != 1:
        raise CommandRejectedError("institution_autonomy_force_invalid")
    force = matches[0]
    availability = force.get("availability")
    reserve = force.get("reserve_capability")
    row = reserve.get("training_or_instruction") if isinstance(reserve, Mapping) else None
    available = availability.get("training_or_instruction") if isinstance(availability, Mapping) else None
    row_count = row.get("count") if isinstance(row, Mapping) else None
    if (
        isinstance(available, bool)
        or not isinstance(available, int)
        or available < 0
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count < 0
    ):
        raise CommandRejectedError("force_reserve_capability_invalid")

    if row_count == available:
        return
    if row_count + graduates != available:
        raise CommandRejectedError("force_reserve_capability_drift")

    try:
        state = reducer._reserve_capability_state(row)
        reducer._reserve_add(force, "training_or_instruction", state, graduates)
    except CommandRejectedError:
        raise
    except (TypeError, ValueError, AttributeError) as exc:
        raise CommandRejectedError("force_reserve_capability_invalid") from exc
    if row.get("count") != available:
        raise CommandRejectedError("force_reserve_capability_drift")


def repair_institution_review_event(
    result: Mapping[str, Any],
    world_events: Any,
) -> Mapping[str, Any]:
    """Attribute or suppress the aggregate institution review event."""
    event_id = result.get("event_id")
    if not isinstance(event_id, str) or not event_id or not isinstance(world_events, dict):
        return result
    events = world_events.get("events")
    if not isinstance(events, list):
        return result

    event = next(
        (
            row
            for row in reversed(events)
            if isinstance(row, dict) and row.get("id") == event_id
        ),
        None,
    )
    if not isinstance(event, dict) or event.get("kind") != "institution_autonomy_reviewed":
        return result

    raw_refs = event.get("material_consequence_refs")
    refs = [
        ref
        for ref in raw_refs
        if isinstance(ref, str) and ref
    ] if isinstance(raw_refs, list) else []
    for ref in _derived_material_refs(result):
        _append_material_ref(refs, ref)

    if refs:
        event["material_consequence_refs"] = refs
        return result

    events.remove(event)
    repaired = dict(result)
    repaired["event_id"] = None
    return repaired


def install_academy_pipeline_transfer_ids() -> None:
    """Install Academy and monthly-owner compatibility repairs."""
    global _INSTALLED
    _install_population_owner_bridge()
    if _INSTALLED:
        return

    original = AutonomyCommandsMixin._apply_institution_autonomy_review
    if getattr(original, "_academy_pipeline_transfer_ids", False):
        _INSTALLED = True
        return

    autonomy_module.suffix = _SuffixProxy()

    @wraps(original)
    def wrapped(self: Any, **kwargs: Any):
        institution = kwargs.get("institution")
        at = kwargs.get("at")
        institution_id = institution.get("id") if isinstance(institution, dict) else None
        token: Token[str | None] | None = None
        if isinstance(institution_id, str) and institution_id:
            token = _SUFFIX.set(academy_pipeline_transfer_suffix(institution_id, at))
        try:
            _bind_shared_population_owner(kwargs.get("record_writes"))
            result = original(self, **kwargs)
            if not isinstance(result, Mapping):
                raise CommandRejectedError("institution_autonomy_review_result_invalid")
            repair_academy_force_reserve_capability(
                self,
                result,
                kwargs.get("record_writes"),
            )
            return repair_institution_review_event(result, kwargs.get("world_events"))
        finally:
            if token is not None:
                _SUFFIX.reset(token)

    wrapped._academy_pipeline_transfer_ids = True  # type: ignore[attr-defined]
    wrapped._institution_review_event_integrity = True  # type: ignore[attr-defined]
    wrapped._academy_force_reserve_capability = True  # type: ignore[attr-defined]
    wrapped._academy_population_owner_bridge = True  # type: ignore[attr-defined]
    AutonomyCommandsMixin._apply_institution_autonomy_review = wrapped
    _INSTALLED = True


__all__ = [
    "academy_pipeline_transfer_suffix",
    "install_academy_pipeline_transfer_ids",
    "repair_academy_force_reserve_capability",
    "repair_institution_review_event",
]
