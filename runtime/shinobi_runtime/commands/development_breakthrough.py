"""Evidence-backed exceptional capability advancement.

Routine training owns ordinary development through the configured ceiling.
This mixin owns the separate exact-person breakthrough transaction that may
advance an already-elite capability by exactly one point when a persisted
mission/combat event proves sufficiently consequential experience. It also
lets autonomous exact-team reviews consolidate at most one qualifying unused
field event per non-player member per review.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.mission_owner import MissionOwner, mission_owner_path
from shinobi_runtime.commands.paths import (
    DEVELOPMENT_BANK_PATH as _DEVELOPMENT_BANK_PATH,
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_ROUTINE_CEILING = 160
_GENERIC_BREAKTHROUGH_CEILING = 185
_RANK_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4}
_DANGEROUS_COMBAT_PREFIXES = (
    "killed:",
    "wounded:",
    "incapacitated:",
    "captured:",
    "custody_pending:",
)


class DevelopmentBreakthroughMixin:
    """Resolve exact-person high-tier points from immutable causal evidence."""

    def _breakthrough_event_sources(self) -> Iterable[Mapping[str, Any]]:
        try:
            registry = self.repository.read_json(_WORLD_EVENT_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("breakthrough_evidence_registry_invalid") from exc
        if not isinstance(registry, Mapping):
            raise CommandRejectedError("breakthrough_evidence_registry_invalid")
        events = registry.get("events")
        if not isinstance(events, list):
            raise CommandRejectedError("breakthrough_evidence_registry_invalid")
        for row in reversed(events):
            if isinstance(row, Mapping):
                yield row
        archive_refs = registry.get("archive_refs")
        if not isinstance(archive_refs, list):
            raise CommandRejectedError("breakthrough_evidence_registry_invalid")
        for archive_ref in reversed(archive_refs):
            if not isinstance(archive_ref, str) or not archive_ref:
                continue
            try:
                archived = self.repository.read_json(archive_ref)
            except (FileNotFoundError, ValueError):
                continue
            archived_events = archived.get("events") if isinstance(archived, Mapping) else archived
            if not isinstance(archived_events, list):
                continue
            for row in reversed(archived_events):
                if isinstance(row, Mapping):
                    yield row

    def _breakthrough_world_event(self, event_ref: str) -> Mapping[str, Any]:
        for row in self._breakthrough_event_sources():
            if row.get("id") == event_ref:
                return row
        raise CommandRejectedError("breakthrough_evidence_not_found")

    def _breakthrough_mission_rank(self, event: Mapping[str, Any]) -> Optional[str]:
        causal_refs = event.get("causal_refs")
        if not isinstance(causal_refs, Sequence) or isinstance(causal_refs, (str, bytes, bytearray)):
            return None
        best: Optional[str] = None
        for ref in causal_refs:
            if not isinstance(ref, str) or not ref.startswith("mission."):
                continue
            try:
                owner = MissionOwner.from_record(self.repository.read_json(mission_owner_path(ref)))
            except (FileNotFoundError, TypeError, ValueError):
                continue
            rank = owner.mission_rank
            if rank in _RANK_ORDER and (best is None or _RANK_ORDER[rank] > _RANK_ORDER[best]):
                best = rank
        return best

    @staticmethod
    def _breakthrough_required_rank(current_value: int) -> str:
        if current_value < 170:
            return "B"
        if current_value < 180:
            return "A"
        return "S"

    @staticmethod
    def _breakthrough_target_family(target: str) -> str:
        if target.startswith("operational_skills."):
            return "operational"
        if (
            target.startswith("martial_skills.")
            or target.startswith("attributes.")
            or target.startswith("chakra_dimensions.")
            or target.startswith("domain_proficiencies.")
            or target.startswith("repertoire.method_mastery.")
        ):
            return "field"
        raise CommandRejectedError("breakthrough_target_invalid")

    def _breakthrough_evidence_basis(
        self,
        *,
        event: Mapping[str, Any],
        subject_ref: str,
        target: str,
        current_value: int,
    ) -> Mapping[str, Any]:
        if event.get("status") != "resolved":
            raise CommandRejectedError("breakthrough_evidence_unresolved")
        actors = event.get("actor_refs")
        if not isinstance(actors, list) or subject_ref not in actors:
            raise CommandRejectedError("breakthrough_evidence_actor_mismatch")
        kind = event.get("kind")
        if kind not in ("combat_resolved", "mission_settled"):
            raise CommandRejectedError("breakthrough_evidence_kind_invalid")

        family = self._breakthrough_target_family(target)
        if family == "field" and kind != "combat_resolved":
            raise CommandRejectedError("breakthrough_evidence_target_mismatch")

        required_rank = self._breakthrough_required_rank(current_value)
        mission_rank = self._breakthrough_mission_rank(event)
        if mission_rank is not None and _RANK_ORDER[mission_rank] >= _RANK_ORDER[required_rank]:
            return {
                "event_kind": kind,
                "mission_rank": mission_rank,
                "required_rank": required_rank,
                "basis": "mission_grade_experience",
            }

        # The lower exceptional band can also be entered through genuinely
        # consequential unranked exact combat. Sparring or consequence-free
        # exchanges do not qualify, and higher legendary bands always require
        # ranked mission pressure.
        if kind == "combat_resolved" and current_value < 170:
            consequences = event.get("material_consequence_refs")
            if isinstance(consequences, list) and any(
                isinstance(value, str) and value.startswith(_DANGEROUS_COMBAT_PREFIXES)
                for value in consequences
            ):
                return {
                    "event_kind": kind,
                    "mission_rank": None,
                    "required_rank": required_rank,
                    "basis": "dangerous_exact_combat",
                }
        raise CommandRejectedError("breakthrough_evidence_insufficient")

    def _find_unused_breakthrough_event(
        self,
        *,
        subject_ref: str,
        target: str,
        current_value: int,
        consumed_refs: Sequence[str],
    ) -> Optional[tuple[str, Mapping[str, Any]]]:
        consumed = set(consumed_refs)
        for event in self._breakthrough_event_sources():
            event_ref = event.get("id")
            if not isinstance(event_ref, str) or event_ref in consumed:
                continue
            try:
                basis = self._breakthrough_evidence_basis(
                    event=event,
                    subject_ref=subject_ref,
                    target=target,
                    current_value=current_value,
                )
            except CommandRejectedError:
                continue
            return event_ref, basis
        return None

    def _apply_autonomous_team_training(
        self,
        *,
        team: Dict[str, Any],
        owner_ref: str,
        at: CampaignTime,
        compacted: int,
        command: CommandEnvelope,
        scheduler: Any,
        policy_book: Any,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        result = super()._apply_autonomous_team_training(
            team=team,
            owner_ref=owner_ref,
            at=at,
            compacted=compacted,
            command=command,
            scheduler=scheduler,
            policy_book=policy_book,
            world_events=world_events,
            record_writes=record_writes,
        )
        outcomes = result.get("outcomes") if isinstance(result, Mapping) else None
        if not isinstance(outcomes, Mapping) or not outcomes:
            return result
        banks = record_writes.get(_DEVELOPMENT_BANK_PATH)
        entries = banks.get("entries") if isinstance(banks, Mapping) else None
        if not isinstance(entries, dict):
            return result

        breakthroughs: list[Mapping[str, Any]] = []
        for subject_ref in sorted(outcomes):
            rows = outcomes.get(subject_ref)
            entry = entries.get(subject_ref)
            if not isinstance(rows, list) or not isinstance(entry, dict):
                continue
            consumed = entry.setdefault("breakthrough_event_refs", [])
            if not isinstance(consumed, list) or any(not isinstance(value, str) for value in consumed):
                raise CommandRejectedError("development_bank_invalid")
            try:
                subject_path, _digest, _view = self._resolve_covered_owner_view(
                    subject_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError:
                continue
            subject = record_writes.get(subject_path)
            if not isinstance(subject, dict):
                continue

            # At most one exceptional point per member per review, even if a
            # long compacted interval contains many qualifying historical events.
            for row in rows:
                if not isinstance(row, dict):
                    continue
                target = row.get("target")
                starting_value = row.get("starting_value")
                ending_value = row.get("ending_value")
                if (
                    not isinstance(target, str)
                    or isinstance(starting_value, bool)
                    or not isinstance(starting_value, int)
                    or isinstance(ending_value, bool)
                    or not isinstance(ending_value, int)
                    or starting_value < _ROUTINE_CEILING
                    or starting_value >= _GENERIC_BREAKTHROUGH_CEILING
                    or ending_value != starting_value
                ):
                    continue
                candidate = self._find_unused_breakthrough_event(
                    subject_ref=subject_ref,
                    target=target,
                    current_value=starting_value,
                    consumed_refs=consumed,
                )
                if candidate is None:
                    continue
                evidence_event_ref, basis = candidate
                container, leaf, current_value = self._training_target(subject, target)
                if current_value != starting_value:
                    continue
                container[leaf] = current_value + 1
                consumed.append(evidence_event_ref)
                row["ending_value"] = current_value + 1
                row["breakthrough_point"] = 1
                row["breakthrough_evidence_event_ref"] = evidence_event_ref
                record_writes[subject_path] = subject
                event_id = self._append_internal_event(
                    world_events,
                    command=command,
                    identity=f"{subject_ref}:{at}:{target}:breakthrough",
                    kind="autonomous_development_breakthrough_resolved",
                    at=at,
                    host_refs=(subject_ref,),
                    actor_refs=(subject_ref,),
                    causal_refs=(evidence_event_ref,),
                    affected_owner_refs=(subject_path, _DEVELOPMENT_BANK_PATH),
                    material_consequence_refs=(
                        f"breakthrough:{subject_ref}:{target}:{current_value}->{current_value + 1}",
                    ),
                    classification="restricted",
                    audience_refs=(),
                    source_refs=(evidence_event_ref,),
                    reducer_ref="shinobi_runtime.commands.development_breakthrough.autonomous",
                )
                breakthroughs.append({
                    "subject_ref": subject_ref,
                    "target": target,
                    "starting_value": current_value,
                    "ending_value": current_value + 1,
                    "evidence_event_ref": evidence_event_ref,
                    "evidence_basis": dict(basis),
                    "event_id": event_id,
                })
                break
        if not breakthroughs:
            return result
        updated = dict(result)
        updated["breakthroughs"] = breakthroughs
        return updated

    def _breakthrough_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("subject_ref", "target", "evidence_event_ref", "summary"),
            command.command_type,
        )
        subject_ref = _stable_id(command.payload.get("subject_ref"), "breakthrough_subject_invalid")
        if subject_ref != command.actor_id:
            raise CommandRejectedError("breakthrough_actor_not_authorized")
        target = command.payload.get("target")
        if not isinstance(target, str) or not target:
            raise CommandRejectedError("breakthrough_target_invalid")
        evidence_event_ref = _stable_id(
            command.payload.get("evidence_event_ref"),
            "breakthrough_evidence_invalid",
            prefix="event.",
        )
        summary = command.payload.get("summary")
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("breakthrough_summary_invalid")

        actor_path, actor = self._resolve_actor_for_write(subject_ref)
        container, leaf, current_value = self._training_target(actor, target)
        if current_value < _ROUTINE_CEILING:
            raise CommandRejectedError("breakthrough_not_required_below_routine_ceiling")
        if current_value >= _GENERIC_BREAKTHROUGH_CEILING:
            raise CommandRejectedError("breakthrough_requires_exceptional_subsystem")

        event = self._breakthrough_world_event(evidence_event_ref)
        basis = self._breakthrough_evidence_basis(
            event=event,
            subject_ref=subject_ref,
            target=target,
            current_value=current_value,
        )

        try:
            banks = copy.deepcopy(self.repository.read_json(_DEVELOPMENT_BANK_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        entries = banks.get("entries") if isinstance(banks, dict) else None
        if not isinstance(entries, dict):
            raise CommandRejectedError("development_bank_invalid")
        entry = entries.get(subject_ref)
        if entry is None:
            entry = {
                "owner_type": "character",
                "resolved_through": str(current_time),
                "credits": {},
                "breakthrough_event_refs": [],
            }
            entries[subject_ref] = entry
        if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
            raise CommandRejectedError("development_bank_invalid")
        consumed = entry.setdefault("breakthrough_event_refs", [])
        if not isinstance(consumed, list) or any(not isinstance(value, str) for value in consumed):
            raise CommandRejectedError("development_bank_invalid")
        if evidence_event_ref in consumed:
            raise CommandRejectedError("breakthrough_evidence_already_consumed")

        container[leaf] = current_value + 1
        consumed.append(evidence_event_ref)

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="development_breakthrough_resolved",
            at=current_time,
            host_refs=(subject_ref,),
            actor_refs=(subject_ref,),
            causal_refs=(evidence_event_ref,),
            affected_owner_refs=(actor_path, _DEVELOPMENT_BANK_PATH),
            material_consequence_refs=(
                f"breakthrough:{subject_ref}:{target}:{current_value}->{current_value + 1}",
            ),
            classification="restricted",
            audience_refs=(subject_ref,),
            reducer_ref="shinobi_runtime.commands.development_breakthrough",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            actor_path: _json_bytes(actor),
            _DEVELOPMENT_BANK_PATH: _json_bytes(banks),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("breakthrough write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            staged_actor = overlay.read_json(actor_path)
            _container, staged_leaf, staged_value = self._training_target(staged_actor, target)
            if staged_leaf != leaf or staged_value != current_value + 1:
                raise ValueError("breakthrough capability after-image invalid")
            staged_bank = overlay.read_json(_DEVELOPMENT_BANK_PATH)
            staged_refs = staged_bank["entries"][subject_ref].get("breakthrough_event_refs", [])
            if evidence_event_ref not in staged_refs:
                raise ValueError("breakthrough evidence consumption missing")
            staged_events = overlay.read_json(_WORLD_EVENT_REGISTRY_PATH).get("events", [])
            if not any(isinstance(row, Mapping) and row.get("id") == event_id for row in staged_events):
                raise ValueError("breakthrough semantic event missing")

        return _BuiltPlan(
            code="breakthrough_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "subject_ref": subject_ref,
                "target": target,
                "starting_value": current_value,
                "ending_value": current_value + 1,
                "evidence_event_ref": evidence_event_ref,
                "evidence_basis": dict(basis),
                "semantic_event_id": event_id,
                "summary": summary,
                "world_time": str(current_time),
            },
            validator=validate,
        )
