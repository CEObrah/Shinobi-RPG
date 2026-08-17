"""Production career-dossier rules for exceptional exact-person progression.

Routine practice still owns progression through the ordinary ceiling.  Above it,
raw capability points require target-specific consolidation plus several distinct
persisted field experiences.  Mission rank is evidence quality, not a direct
point award.  The dossier is stored alongside each exact person's development
bank so evidence remains one-use and cooldowns remain persistent.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import (
    _BuiltPlan,
    _OwnerResolutionCache,
    _campaign_datetime,
    _exact_payload,
    _json_bytes,
    _stable_id,
)
from shinobi_runtime.commands.development_breakthrough import (
    DevelopmentBreakthroughMixin,
    _GENERIC_BREAKTHROUGH_CEILING,
    _ROUTINE_CEILING,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import (
    DEVELOPMENT_BANK_PATH as _DEVELOPMENT_BANK_PATH,
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


class DevelopmentBreakthroughDossierMixin(DevelopmentBreakthroughMixin):
    """Replace single-event breakthrough awards with persisted career dossiers."""

    @staticmethod
    def _dossier_requirements(current_value: int) -> Mapping[str, int]:
        if current_value < 170:
            return {
                "evidence_count": 2,
                "distinct_contexts": 2,
                "consolidation_units": 6,
                "cooldown_days": 7,
            }
        if current_value < 180:
            return {
                "evidence_count": 3,
                "distinct_contexts": 2,
                "consolidation_units": 12,
                "cooldown_days": 14,
            }
        return {
            "evidence_count": 4,
            "distinct_contexts": 3,
            "consolidation_units": 18,
            "cooldown_days": 30,
        }

    @staticmethod
    def _event_context_signature(event: Mapping[str, Any], subject_ref: str) -> str:
        causal = event.get("causal_refs")
        if isinstance(causal, Sequence) and not isinstance(causal, (str, bytes, bytearray)):
            mission_refs = sorted(
                value
                for value in causal
                if isinstance(value, str) and value.startswith("mission.")
            )
            if mission_refs:
                return "mission:" + mission_refs[0]
        hosts = event.get("host_refs")
        if isinstance(hosts, Sequence) and not isinstance(hosts, (str, bytes, bytearray)):
            contextual = sorted(
                value
                for value in hosts
                if isinstance(value, str) and value and value != subject_ref
            )
            if contextual:
                return "host:" + contextual[0]
        kind = event.get("kind")
        return "kind:" + (kind if isinstance(kind, str) and kind else "unknown")

    def _dossier_evidence(
        self,
        *,
        selected_event_ref: str,
        subject_ref: str,
        target: str,
        current_value: int,
        consumed_refs: Sequence[str],
    ) -> tuple[list[str], list[Mapping[str, Any]], set[str]]:
        consumed = set(consumed_refs)
        if selected_event_ref in consumed:
            raise CommandRejectedError("breakthrough_evidence_already_consumed")
        requirements = self._dossier_requirements(current_value)
        needed = requirements["evidence_count"]
        selected_event = self._breakthrough_world_event(selected_event_ref)
        selected_basis = self._breakthrough_evidence_basis(
            event=selected_event,
            subject_ref=subject_ref,
            target=target,
            current_value=current_value,
        )
        candidates: list[tuple[str, Mapping[str, Any], Mapping[str, Any], str]] = [
            (
                selected_event_ref,
                selected_event,
                selected_basis,
                self._event_context_signature(selected_event, subject_ref),
            )
        ]
        seen = {selected_event_ref}
        for event in self._breakthrough_event_sources():
            event_ref = event.get("id")
            if (
                not isinstance(event_ref, str)
                or event_ref in seen
                or event_ref in consumed
            ):
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
            seen.add(event_ref)
            candidates.append(
                (
                    event_ref,
                    event,
                    basis,
                    self._event_context_signature(event, subject_ref),
                )
            )
            if len(candidates) >= 64:
                break

        # Build the smallest dossier that satisfies both depth and novelty.
        chosen = [candidates[0]]
        contexts = {candidates[0][3]}
        while len(chosen) < needed:
            unused = [row for row in candidates if row not in chosen]
            if not unused:
                break
            novel = next((row for row in unused if row[3] not in contexts), None)
            row = novel or unused[0]
            chosen.append(row)
            contexts.add(row[3])
        if len(chosen) < needed:
            raise CommandRejectedError("breakthrough_dossier_experience_depth_insufficient")
        if len(contexts) < requirements["distinct_contexts"]:
            raise CommandRejectedError("breakthrough_dossier_novelty_insufficient")
        return (
            [row[0] for row in chosen],
            [dict(row[2]) for row in chosen],
            contexts,
        )

    def _require_dossier_consolidation(
        self,
        *,
        entry: Dict[str, Any],
        target: str,
        current_value: int,
        at: CampaignTime,
    ) -> tuple[Decimal, Dict[str, Any]]:
        requirements = self._dossier_requirements(current_value)
        credits = entry.get("credits")
        if not isinstance(credits, dict):
            raise CommandRejectedError("development_bank_invalid")
        try:
            available = Decimal(str(credits.get(target, 0)))
        except Exception as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        required = Decimal(requirements["consolidation_units"])
        if not available.is_finite() or available < required:
            raise CommandRejectedError("breakthrough_dossier_consolidation_insufficient")

        dossiers = entry.setdefault("breakthrough_dossiers", {})
        if not isinstance(dossiers, dict):
            raise CommandRejectedError("development_bank_invalid")
        dossier = dossiers.setdefault(target, {})
        if not isinstance(dossier, dict):
            raise CommandRejectedError("development_bank_invalid")
        last_raw = dossier.get("last_breakthrough_at")
        if last_raw is not None:
            try:
                last = CampaignTime.parse(last_raw)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("development_bank_invalid") from exc
            elapsed = (_campaign_datetime(at) - _campaign_datetime(last)).total_seconds()
            if elapsed < requirements["cooldown_days"] * 86400:
                raise CommandRejectedError("breakthrough_dossier_cooldown_active")
        return required, dossier

    @staticmethod
    def _record_dossier_resolution(
        *,
        entry: Dict[str, Any],
        dossier: Dict[str, Any],
        target: str,
        at: CampaignTime,
        current_value: int,
        evidence_refs: Sequence[str],
        context_signatures: Sequence[str],
        consolidation_units: Decimal,
    ) -> None:
        credits = entry["credits"]
        credits[target] = float(Decimal(str(credits.get(target, 0))) - consolidation_units)
        dossier["last_breakthrough_at"] = str(at)
        dossier["last_starting_value"] = current_value
        dossier["last_ending_value"] = current_value + 1
        dossier["last_evidence_refs"] = list(evidence_refs)
        dossier["last_context_signatures"] = sorted(set(context_signatures))
        dossier["last_consolidation_units"] = float(consolidation_units)
        count = dossier.get("resolved_breakthroughs", 0)
        dossier["resolved_breakthroughs"] = count + 1 if isinstance(count, int) and not isinstance(count, bool) else 1

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
        # Deliberately bypass the retired single-event exceptional award while
        # retaining the underlying autonomous team-training settlement.
        result = super(DevelopmentBreakthroughMixin, self)._apply_autonomous_team_training(
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
        banks = record_writes.get(_DEVELOPMENT_BANK_PATH)
        entries = banks.get("entries") if isinstance(banks, Mapping) else None
        if not isinstance(outcomes, Mapping) or not isinstance(entries, dict):
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
                try:
                    consolidation, dossier = self._require_dossier_consolidation(
                        entry=entry,
                        target=target,
                        current_value=starting_value,
                        at=at,
                    )
                    candidate = self._find_unused_breakthrough_event(
                        subject_ref=subject_ref,
                        target=target,
                        current_value=starting_value,
                        consumed_refs=consumed,
                    )
                    if candidate is None:
                        continue
                    selected_ref, _basis = candidate
                    evidence_refs, bases, contexts = self._dossier_evidence(
                        selected_event_ref=selected_ref,
                        subject_ref=subject_ref,
                        target=target,
                        current_value=starting_value,
                        consumed_refs=consumed,
                    )
                except CommandRejectedError:
                    continue

                container, leaf, current_value = self._training_target(subject, target)
                if current_value != starting_value:
                    continue
                container[leaf] = current_value + 1
                for ref in evidence_refs:
                    if ref not in consumed:
                        consumed.append(ref)
                self._record_dossier_resolution(
                    entry=entry,
                    dossier=dossier,
                    target=target,
                    at=at,
                    current_value=current_value,
                    evidence_refs=evidence_refs,
                    context_signatures=contexts,
                    consolidation_units=consolidation,
                )
                row["ending_value"] = current_value + 1
                row["breakthrough_point"] = 1
                row["breakthrough_evidence_event_refs"] = list(evidence_refs)
                record_writes[subject_path] = subject
                event_id = self._append_internal_event(
                    world_events,
                    command=command,
                    identity=f"{subject_ref}:{at}:{target}:dossier-breakthrough",
                    kind="autonomous_development_breakthrough_resolved",
                    at=at,
                    host_refs=(subject_ref,),
                    actor_refs=(subject_ref,),
                    causal_refs=tuple(evidence_refs),
                    affected_owner_refs=(subject_path, _DEVELOPMENT_BANK_PATH),
                    material_consequence_refs=(
                        f"breakthrough:{subject_ref}:{target}:{current_value}->{current_value + 1}",
                    ),
                    classification="restricted",
                    audience_refs=(),
                    source_refs=tuple(evidence_refs),
                    reducer_ref="shinobi_runtime.commands.development_breakthrough_dossier.autonomous",
                )
                breakthroughs.append({
                    "subject_ref": subject_ref,
                    "target": target,
                    "starting_value": current_value,
                    "ending_value": current_value + 1,
                    "evidence_event_refs": list(evidence_refs),
                    "evidence_bases": bases,
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
        selected_event_ref = _stable_id(
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
                "breakthrough_dossiers": {},
            }
            entries[subject_ref] = entry
        if not isinstance(entry, dict) or not isinstance(entry.get("credits"), dict):
            raise CommandRejectedError("development_bank_invalid")
        consumed = entry.setdefault("breakthrough_event_refs", [])
        if not isinstance(consumed, list) or any(not isinstance(value, str) for value in consumed):
            raise CommandRejectedError("development_bank_invalid")

        consolidation, dossier = self._require_dossier_consolidation(
            entry=entry,
            target=target,
            current_value=current_value,
            at=current_time,
        )
        evidence_refs, bases, contexts = self._dossier_evidence(
            selected_event_ref=selected_event_ref,
            subject_ref=subject_ref,
            target=target,
            current_value=current_value,
            consumed_refs=consumed,
        )

        container[leaf] = current_value + 1
        for ref in evidence_refs:
            if ref not in consumed:
                consumed.append(ref)
        self._record_dossier_resolution(
            entry=entry,
            dossier=dossier,
            target=target,
            at=current_time,
            current_value=current_value,
            evidence_refs=evidence_refs,
            context_signatures=contexts,
            consolidation_units=consolidation,
        )

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="development_breakthrough_resolved",
            at=current_time,
            host_refs=(subject_ref,),
            actor_refs=(subject_ref,),
            causal_refs=tuple(evidence_refs),
            affected_owner_refs=(actor_path, _DEVELOPMENT_BANK_PATH),
            material_consequence_refs=(
                f"breakthrough:{subject_ref}:{target}:{current_value}->{current_value + 1}",
            ),
            classification="restricted",
            audience_refs=(subject_ref,),
            reducer_ref="shinobi_runtime.commands.development_breakthrough_dossier",
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
                raise ValueError("breakthrough dossier write set changed after planning")
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
            staged_entry = staged_bank["entries"][subject_ref]
            staged_refs = staged_entry.get("breakthrough_event_refs", [])
            if any(ref not in staged_refs for ref in evidence_refs):
                raise ValueError("breakthrough dossier evidence consumption missing")
            staged_dossier = staged_entry.get("breakthrough_dossiers", {}).get(target)
            if not isinstance(staged_dossier, Mapping) or staged_dossier.get("last_breakthrough_at") != str(current_time):
                raise ValueError("breakthrough dossier record missing")
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
                "evidence_event_refs": list(evidence_refs),
                "evidence_bases": bases,
                "distinct_contexts": sorted(contexts),
                "consolidation_units_consumed": str(consolidation),
                "requirements": dict(self._dossier_requirements(current_value)),
                "semantic_event_id": event_id,
                "summary": summary,
                "world_time": str(current_time),
            },
            validator=validate,
        )


__all__ = ["DevelopmentBreakthroughDossierMixin"]
