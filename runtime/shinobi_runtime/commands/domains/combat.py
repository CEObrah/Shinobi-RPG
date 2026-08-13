"""Exact and aggregate combat command domain."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes, _stable_id
from shinobi_runtime.combat.models import (
    BattleKernel, CapabilityProfile, CombatContract, CombatIntent, CombatObjective, CombatTiming,
    Engagement, InformationState, Participant, PersonnelState, PositionState, ResourcePool, SideTerrain, TerrainState,
)
from shinobi_runtime.combat.resolver import required_draw_count, resolve_combat
from shinobi_runtime.reducers import (
    PopulationPool, PopulationTransfer, apply_personnel_effect, apply_transfer, neutral_proportional_selection,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, SchedulerHost, recurring_event
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.sim.rng import CounterRNG
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


from shinobi_runtime.commands.paths import (
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    COMBAT_ZOOM_REGISTRY_PATH as _COMBAT_ZOOM_REGISTRY_PATH,
    CUSTODY_REGISTRY_PATH as _CUSTODY_REGISTRY_PATH,
    FORMATION_TENDENCY_PROFILES_PATH as _FORMATION_TENDENCY_PROFILES_PATH,
    JINCHURIKI_REGISTRY_PATH as _JINCHURIKI_REGISTRY_PATH,
    SUMMON_REGISTRY_PATH as _SUMMON_REGISTRY_PATH,
)


class CombatCommandsMixin:
    def _formation_tendencies(self, formation: Mapping[str, Any]) -> Mapping[str, int]:
        """Return current formation tendencies without duplicating inherited state.

        Inline tendencies are a causal override.  Otherwise component tendency
        profiles are personnel-weighted from cold mechanics data.
        """
        inline = formation.get("tendencies")
        if isinstance(inline, Mapping) and inline:
            clean: Dict[str, int] = {}
            for key, value in inline.items():
                if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
                    raise CommandRejectedError("formation_tendencies_invalid")
                clean[key] = max(0, min(200, value))
            return clean
        try:
            registry = self.repository.read_json(_FORMATION_TENDENCY_PROFILES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("formation_tendency_profiles_invalid") from exc
        profiles = registry.get("profiles") if isinstance(registry, Mapping) else None
        components = formation.get("components")
        if not isinstance(profiles, Mapping) or not isinstance(components, list):
            raise CommandRejectedError("formation_tendency_profiles_invalid")
        totals: Dict[str, int] = {}
        weight = 0
        for component in components:
            if not isinstance(component, Mapping):
                raise CommandRejectedError("formation_tendency_profiles_invalid")
            count = component.get("count")
            profile_ref = component.get("tendency_profile_ref")
            profile = profiles.get(profile_ref) if isinstance(profile_ref, str) else None
            if isinstance(count, bool) or not isinstance(count, int) or count < 0 or not isinstance(profile, Mapping):
                raise CommandRejectedError("formation_tendency_profiles_invalid")
            for key, value in profile.items():
                if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
                    raise CommandRejectedError("formation_tendency_profiles_invalid")
                totals[key] = totals.get(key, 0) + value * count
            weight += count
        if weight <= 0:
            raise CommandRejectedError("formation_tendency_profiles_invalid")
        return {key: max(0, min(200, (value + weight // 2) // weight)) for key, value in totals.items()}

    def _combat_side_team_context(
        self, parsed_specs: Mapping[str, Mapping[str, Any]]
    ) -> Mapping[str, Tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]]:
        """Resolve one exact-team context per combat side without name-specific code.

        A team is selected only when at least two members of the same side are
        present.  This avoids giving a lone multi-affiliated actor doctrine from
        an arbitrary team while allowing a real team fighting together to use
        its practiced coordination.
        """
        by_side: Dict[str, set[str]] = {}
        for actor_ref, spec in parsed_specs.items():
            side_ref = spec.get("side_ref")
            if isinstance(side_ref, str):
                by_side.setdefault(side_ref, set()).add(actor_ref)
        candidates: list[Tuple[str, Mapping[str, Any]]] = []
        for team_ref in self._active_exact_team_refs():
            try:
                _path, team = self._exact_team(team_ref)
            except CommandRejectedError:
                continue
            if team.get("status") != "active":
                continue
            candidates.append((team_ref, team))
        result: Dict[str, Tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]] = {}
        for side_ref, actors in by_side.items():
            ranked = []
            for team_ref, team in candidates:
                members = team.get("member_refs")
                if not isinstance(members, list):
                    continue
                overlap = len(actors.intersection(value for value in members if isinstance(value, str)))
                if overlap >= 2:
                    ranked.append((overlap, team_ref, team))
            if not ranked:
                continue
            _overlap, _team_ref, team = max(ranked, key=lambda row: (row[0], row[1]))
            doctrine = None
            doctrine_ref = team.get("doctrine_ref")
            if isinstance(doctrine_ref, str) and doctrine_ref:
                try:
                    _p, _d, view = self._resolve_covered_owner_view(
                        doctrine_ref, cache=_OwnerResolutionCache()
                    )
                    if isinstance(view, Mapping) and view.get("schema") == "team-doctrine":
                        doctrine = view
                except CommandRejectedError:
                    doctrine = None
            result[side_ref] = (team, doctrine)
        return result


    @staticmethod
    def _combat_axis(record: Mapping[str, Any], mapping: Sequence[Tuple[str, str]]) -> int:
        values = []
        for root, key in mapping:
            container = record.get(root)
            value = container.get(key) if isinstance(container, Mapping) else None
            if isinstance(value, bool) or not isinstance(value, int):
                value = 0
            values.append(max(0, min(200, value)))
        return max(0, min(200, sum(values) // max(1, len(values))))


    @classmethod
    def _combat_capability(cls, record: Mapping[str, Any]) -> CapabilityProfile:
        return CapabilityProfile(
            offense=cls._combat_axis(record, (("martial_skills", "sword"), ("martial_skills", "unarmed"), ("chakra_dimensions", "output"))),
            defense=cls._combat_axis(record, (("attributes", "toughness"), ("attributes", "agility"), ("martial_skills", "movement"), ("chakra_dimensions", "control"))),
            control=cls._combat_axis(record, (("chakra_dimensions", "control"), ("operational_skills", "tactics"), ("martial_skills", "grappling"))),
            mobility=cls._combat_axis(record, (("attributes", "agility"), ("attributes", "coordination"), ("martial_skills", "movement"))),
            perception=cls._combat_axis(record, (("attributes", "awareness"), ("chakra_dimensions", "sensing"), ("operational_skills", "investigation"))),
            stealth=cls._combat_axis(record, (("martial_skills", "stealth"), ("chakra_dimensions", "suppression"), ("operational_skills", "infiltration"))),
            capture=cls._combat_axis(record, (("martial_skills", "grappling"), ("chakra_dimensions", "control"), ("operational_skills", "traps"))),
            escape=cls._combat_axis(record, (("martial_skills", "movement"), ("attributes", "agility"), ("operational_skills", "survival"))),
            protection=cls._combat_axis(record, (("attributes", "toughness"), ("attributes", "endurance"))),
        )


    @staticmethod
    def _combat_readiness(record: Mapping[str, Any]) -> int:
        condition = record.get("condition")
        readiness = condition.get("readiness") if isinstance(condition, Mapping) else None
        return {"ready": 100, "fatigued": 80, "injured": 65, "incapacitated": 0, "captured": 20, "dead": 0}.get(readiness, 90)


    @staticmethod
    def _combat_action_for_objective(kind: str) -> str:
        """Map a side objective to the intent vocabulary used by combat.

        ``eliminate`` is the only objective without an identically named intent;
        it is expressed as an attack with lethal force.  Keeping this mapping in
        the runtime means callers never need a setting-specific NPC action table.
        """
        return "attack" if kind == "eliminate" else kind


    @staticmethod
    def _formation_autonomous_objective_kind(formation: Mapping[str, Any]) -> str:
        """Choose autonomous combat intent from typed saved state only."""
        readiness = formation.get("readiness", 100)
        morale = formation.get("morale", 100)
        if (
            isinstance(readiness, int) and not isinstance(readiness, bool) and readiness <= 30
        ) or (
            isinstance(morale, int) and not isinstance(morale, bool) and morale <= 25
        ):
            return "disengage"
        objective = formation.get("operational_objective")
        kind = objective.get("kind") if isinstance(objective, Mapping) else None
        if kind not in ("capture", "escape", "extract", "eliminate", "hold", "secure", "delay", "disengage"):
            return "delay"
        return str(kind)


    @classmethod
    def _exact_autonomous_objective_kind(cls, record: Mapping[str, Any]) -> str:
        """Choose exact-NPC combat intent from typed saved order state only."""
        resources = record.get("resources")
        health = resources.get("health") if isinstance(resources, Mapping) else None
        if isinstance(health, Mapping):
            current = health.get("current")
            capacity = health.get("capacity")
            if (
                isinstance(current, int) and not isinstance(current, bool)
                and isinstance(capacity, int) and not isinstance(capacity, bool)
                and capacity > 0 and current * 100 <= capacity * 30
            ):
                return "disengage"
        if cls._combat_readiness(record) <= 30:
            return "disengage"
        goal_state = record.get("goal_state")
        kind = goal_state.get("current_order_kind") if isinstance(goal_state, Mapping) else None
        if kind not in ("capture", "escape", "extract", "eliminate", "hold", "secure", "delay", "disengage"):
            return "delay"
        return str(kind)


    @staticmethod
    def _derived_combat_targets(
        *,
        side_ref: str,
        participant_sides: Mapping[str, str],
    ) -> Tuple[str, ...]:
        return tuple(
            ref for ref, other_side in sorted(participant_sides.items())
            if other_side != side_ref
        )


    @staticmethod
    def _apply_exact_combat_effect(
        record: Dict[str, Any],
        *,
        effect: Any,
        combat_id: str,
        current_time: CampaignTime,
    ) -> None:
        try:
            apply_personnel_effect(
                record,
                effect=effect,
                event_marker=f"{combat_id}@{current_time}",
            )
        except ValueError as exc:
            raise CommandRejectedError("combat_actor_health_invalid") from exc


    @staticmethod
    def _operation_owner_path(combat_id: str) -> str:
        component = re.sub(r"[^a-z0-9._-]", "_", combat_id)
        if not component or len(component) > 128:
            raise CommandRejectedError("combat_id_invalid")
        return f"state/operation/{component}.json"


    def _active_mission_context(
        self,
        mission_ref: object,
        *,
        actor_id: str,
        current_time: CampaignTime,
    ) -> Optional[str]:
        if mission_ref is None:
            return None
        mission_id = _stable_id(mission_ref, "combat_mission_ref_invalid", prefix="mission.")
        _path, owner = self._read_mission(mission_id, actor_id=actor_id, current_time=current_time)
        if owner.mission.state != "active":
            raise CommandRejectedError("combat_mission_not_active")
        return mission_id


    @staticmethod
    def _aggregate_capability(record: Mapping[str, Any]) -> Tuple[CapabilityProfile, CapabilityProfile, int, int, int, int]:
        stats = record.get("stats")
        if not isinstance(stats, Mapping):
            raise CommandRejectedError("aggregate_capability_invalid")
        attributes = stats.get("attributes") if isinstance(stats.get("attributes"), Mapping) else {}
        chakra = stats.get("chakra") if isinstance(stats.get("chakra"), Mapping) else {}
        skills = stats.get("skills") if isinstance(stats.get("skills"), Mapping) else {}
        def v(container: Mapping[str, Any], key: str) -> int:
            raw = container.get(key, 0)
            return max(0, min(200, raw if isinstance(raw, int) and not isinstance(raw, bool) else 0))
        combat = v(attributes, "combat")
        awareness = v(attributes, "awareness")
        endurance = v(attributes, "endurance")
        control = v(chakra, "control")
        output = v(chakra, "output")
        combat_skill = v(skills, "combat")
        movement = v(skills, "movement")
        tactics = v(skills, "tactics")
        teamwork = v(skills, "team_coordination")
        mean = CapabilityProfile(
            offense=(combat + combat_skill + output) // 3,
            defense=(endurance + movement + control) // 3,
            control=(tactics + control + teamwork) // 3,
            mobility=movement,
            perception=awareness,
            stealth=(movement + tactics + awareness) // 3,
            capture=(combat_skill + control + tactics) // 3,
            escape=(movement + awareness + tactics) // 3,
            protection=(endurance + teamwork + control) // 3,
        )
        aptitude = record.get("aptitude_distribution")
        sd = aptitude.get("sd", 16) if isinstance(aptitude, Mapping) else 16
        spread_value = max(5, min(25, (sd if isinstance(sd, int) else 16) // 2))
        spread = CapabilityProfile(**{key: spread_value for key in mean.to_record()})
        morale = v(stats, "morale")
        discipline = v(stats, "discipline")
        experience = v(stats, "experience")
        readiness = max(1, min(200, (discipline + experience + endurance) // 3))
        cohesion = max(1, min(200, (teamwork + discipline) // 2))
        initiative = max(1, min(200, (awareness + tactics + movement) // 3))
        return mean, spread, readiness, max(1, morale), cohesion, initiative


    def _formation_aggregate_capability(
        self,
        *,
        formation: Mapping[str, Any],
        force: Mapping[str, Any],
    ) -> Tuple[CapabilityProfile, CapabilityProfile, int, int, int, int, Tuple[str, ...], str]:
        """Derive an aggregate combat kernel from the saved formation composition.

        Troop pools are capability/source classifications, not a conserved second
        personnel partition.  Components choose the best matching capability
        source while component and command-cadre counts provide the weights.
        """
        pools = force.get("troop_pools")
        components = formation.get("components")
        if not isinstance(pools, list) or not isinstance(components, list) or not components:
            raise CommandRejectedError("aggregate_capability_invalid")
        valid_pools = [
            row for row in pools
            if isinstance(row, Mapping) and isinstance(row.get("capability_ref"), str)
        ]
        if not valid_pools:
            raise CommandRejectedError("aggregate_capability_invalid")

        role_preferences = {
            "assault": ("field_ready", "garrison_security"),
            "ranged_control": ("field_ready", "garrison_security"),
            "recon": ("intelligence_support", "field_ready"),
            "support": ("logistics_support", "medical_support", "field_ready"),
            "medical": ("medical_support", "field_ready"),
            "logistics": ("logistics_support", "field_ready"),
            "command": ("field_ready", "garrison_security"),
        }

        def choose_pool(component: Mapping[str, Any]) -> Mapping[str, Any]:
            troop_type = component.get("troop_type")
            if isinstance(troop_type, str):
                exact = [row for row in valid_pools if row.get("troop_type") == troop_type]
                if exact:
                    return exact[0]
                lowered = troop_type.lower()
                if "medic" in lowered:
                    preferred = ("medical_support", "field_ready")
                elif "logistic" in lowered or "supply" in lowered:
                    preferred = ("logistics_support", "field_ready")
                elif "sensor" in lowered or "scout" in lowered or "intel" in lowered or "infiltrat" in lowered:
                    preferred = ("intelligence_support", "field_ready")
                else:
                    preferred = ()
                for role in preferred:
                    hit = next((row for row in valid_pools if row.get("role") == role), None)
                    if hit is not None:
                        return hit
            role = component.get("role")
            for wanted in role_preferences.get(str(role), (str(role), "field_ready")):
                hit = next((row for row in valid_pools if row.get("role") == wanted), None)
                if hit is not None:
                    return hit
            hit = next((row for row in valid_pools if row.get("role") == "field_ready"), None)
            return hit if hit is not None else valid_pools[0]

        axis_totals = {key: 0 for key in CapabilityProfile(0,0,0,0,0,0,0,0,0).to_record()}
        spread_totals = dict(axis_totals)
        readiness_total = morale_total = cohesion_total = initiative_total = 0
        total_weight = 0
        source_refs = []
        source_digests = []
        capability_cache: Dict[str, Tuple[CapabilityProfile, CapabilityProfile, int, int, int, int, str]] = {}

        def add_source(pool: Mapping[str, Any], count: int) -> None:
            nonlocal readiness_total, morale_total, cohesion_total, initiative_total, total_weight
            if count <= 0:
                return
            cap_path = pool.get("capability_ref")
            if not isinstance(cap_path, str):
                raise CommandRejectedError("aggregate_capability_invalid")
            cached = capability_cache.get(cap_path)
            if cached is None:
                try:
                    cap_record = self.repository.read_json(cap_path)
                    digest = self.repository.digest(cap_path)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("aggregate_capability_invalid") from exc
                if not isinstance(cap_record, Mapping) or not isinstance(digest, str):
                    raise CommandRejectedError("aggregate_capability_invalid")
                mean, spread, readiness, morale, cohesion, initiative = self._aggregate_capability(cap_record)
                cached = (mean, spread, readiness, morale, cohesion, initiative, digest)
                capability_cache[cap_path] = cached
            mean, spread, readiness, morale, cohesion, initiative, digest = cached
            for key, value in mean.to_record().items():
                axis_totals[key] += value * count
            for key, value in spread.to_record().items():
                spread_totals[key] += value * count
            readiness_total += readiness * count
            morale_total += morale * count
            cohesion_total += cohesion * count
            initiative_total += initiative * count
            total_weight += count
            source_refs.append(cap_path)
            source_digests.append(digest)

        for component in components:
            if not isinstance(component, Mapping):
                raise CommandRejectedError("aggregate_capability_invalid")
            count = component.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise CommandRejectedError("aggregate_capability_invalid")
            add_source(choose_pool(component), count)

        command = formation.get("command_personnel")
        command_count = command.get("count", 0) if isinstance(command, Mapping) else 0
        if isinstance(command_count, bool) or not isinstance(command_count, int) or command_count < 0:
            raise CommandRejectedError("aggregate_capability_invalid")
        if command_count:
            command_pool = next((row for row in valid_pools if row.get("role") == "field_ready"), valid_pools[0])
            add_source(command_pool, command_count)

        formation_total = formation.get("personnel_total")
        if not isinstance(formation_total, int) or isinstance(formation_total, bool) or total_weight != formation_total or total_weight <= 0:
            raise CommandRejectedError("aggregate_capability_headcount_mismatch")
        mean = CapabilityProfile(**{key: max(0, min(200, (value + total_weight // 2) // total_weight)) for key, value in axis_totals.items()})
        spread = CapabilityProfile(**{key: max(1, min(50, (value + total_weight // 2) // total_weight)) for key, value in spread_totals.items()})
        digest_payload = "|".join(sorted(source_digests)) + "|" + json.dumps(formation.get("components"), sort_keys=True, separators=(",", ":"))
        source_digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        return (
            mean, spread,
            max(1, readiness_total // total_weight),
            max(1, morale_total // total_weight),
            max(1, cohesion_total // total_weight),
            max(1, initiative_total // total_weight),
            tuple(sorted(set(source_refs))),
            source_digest,
        )


    def _reconcile_rostered_person_death(
        self,
        registry: Dict[str, Any],
        *,
        person_ref: str,
        at: CampaignTime,
        command: CommandEnvelope,
        force_writes: Dict[str, Dict[str, Any]],
        team_writes: Dict[str, Dict[str, Any]],
        formation_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[str]:
        """Move one named person's physical headcount out of its living pool.

        Aggregate demography never kills rostered identities.  When an exact
        health/combat result does, this bridge keeps the census, service force,
        and persistent representation synchronized in the same transaction.
        """
        pools = registry.get("pools") if isinstance(registry, dict) else None
        transfers = registry.get("transfers") if isinstance(registry, dict) else None
        if not isinstance(pools, dict) or not isinstance(transfers, list):
            raise CommandRejectedError("population_registry_invalid")
        source_id = None
        source_record = None
        for pool_id, record in pools.items():
            if not isinstance(record, dict):
                continue
            rep = record.get("representation")
            refs = rep.get("rostered_person_refs") if isinstance(rep, Mapping) else None
            if isinstance(refs, list) and person_ref in refs:
                if source_id is not None:
                    raise CommandRejectedError("population_person_multiple_pool_membership")
                source_id, source_record = pool_id, record
        if source_id is None or not isinstance(source_record, dict):
            return None

        source_pool = self._pool_reducer_view(source_id, source_record)
        if source_pool.total <= 0:
            raise CommandRejectedError("population_pool_invalid")
        selected = neutral_proportional_selection(source_pool, 1)
        source_rep = source_record.get("representation")
        if not isinstance(source_rep, dict):
            raise CommandRejectedError("population_representation_invalid")
        refs = source_rep.get("rostered_person_refs")
        if not isinstance(refs, list) or person_ref not in refs:
            raise CommandRejectedError("population_representation_invalid")

        destination_id = None
        if source_record.get("category") == "shinobi_service":
            candidate = source_id.rsplit(".", 1)[0] + ".deceased_service"
            if isinstance(pools.get(candidate), dict):
                destination_id = candidate

        transfer_id = f"death.{command.digest[:14]}.{re.sub(r'[^a-z0-9]+', '_', person_ref.lower()).strip('_')}"
        if destination_id is not None:
            destination_record = pools[destination_id]
            destination_pool = self._pool_reducer_view(destination_id, destination_record)
            transfer = PopulationTransfer(
                transfer_id=transfer_id,
                source_pool_id=source_id,
                destination_pool_id=destination_id,
                count=1,
                selected_dimensions=selected,
                selection_mode="explicit_selection",
            )
            source_after, destination_after = apply_transfer(source_pool, destination_pool, transfer)
            destination_rep = destination_record.get("representation")
            if not isinstance(destination_rep, dict):
                raise CommandRejectedError("population_representation_invalid")
            source_rep["rostered_person_refs"] = sorted(x for x in refs if x != person_ref)
            source_rep["rostered_count"] = int(source_rep.get("rostered_count", 0)) - 1
            destination_refs = destination_rep.get("rostered_person_refs")
            if not isinstance(destination_refs, list) or person_ref in destination_refs:
                raise CommandRejectedError("population_representation_invalid")
            destination_rep["rostered_person_refs"] = sorted([*destination_refs, person_ref])
            destination_rep["rostered_count"] = int(destination_rep.get("rostered_count", 0)) + 1
            self._persist_population_pool_record(source_record, source_after, at=at)
            self._persist_population_pool_record(destination_record, destination_after, at=at)
            destination_added = 1
        else:
            remaining = {
                dimension: {category: before - selected[dimension].get(category, 0) for category, before in categories.items()}
                for dimension, categories in source_pool.dimensions.items()
            }
            source_after = PopulationPool(source_id, source_pool.total - 1, remaining)
            source_rep["rostered_person_refs"] = sorted(x for x in refs if x != person_ref)
            source_rep["rostered_count"] = int(source_rep.get("rostered_count", 0)) - 1
            self._persist_population_pool_record(source_record, source_after, at=at)
            destination_added = 0

        transfers.append({
            "id": transfer_id, "at": str(at), "source_pool_id": source_id,
            "destination_ref": destination_id or "death.outside_living_population",
            "requested_count": 1, "accepted": 1, "rejected": 0,
            "authority_ref": person_ref, "authority_basis": "exact_person_death",
            "policy_ref": None, "method": "exact_person_death_reconciliation",
            "accepted_profile": {
                "numeric_distributions": {},
                "category_counts": {str(source_record.get("category", "population")): 1},
                "dimension_counts": {name: dict(values) for name, values in selected.items()},
                "tags": ["exact_person_death", person_ref],
            },
            "materialized_person_ids": [person_ref], "source_removed": 1,
            "destination_added": destination_added,
            "selection_note": "Exact persistent death reconciled against the person's existing physical population membership; aggregate demography did not choose the named casualty.",
        })
        self._trim_population_transfer_history(transfers)

        linked_force_ref = source_record.get("linked_force_ref")
        if isinstance(linked_force_ref, str):
            try:
                force_path, _digest, force_view = self._resolve_covered_owner_view(
                    linked_force_ref, cache=_OwnerResolutionCache()
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("population_linked_force_invalid") from exc
            force = force_writes.get(force_path)
            if force is None:
                force = copy.deepcopy(dict(force_view))
                force_writes[force_path] = force
            if force.get("schema") != "force" or force.get("population_pool_id") != source_id:
                raise CommandRejectedError("population_linked_force_invalid")
            availability = force.get("availability")
            if not isinstance(availability, dict):
                raise CommandRejectedError("population_linked_force_invalid")
            embedded = self._detach_embedded_person_from_formation(
                person_ref=person_ref,
                expected_force_ref=linked_force_ref,
                team_writes=team_writes,
                formation_writes=formation_writes,
            )
            if embedded is not None:
                deployed = availability.get("deployed")
                if isinstance(deployed, bool) or not isinstance(deployed, int) or deployed <= 0:
                    raise CommandRejectedError("population_linked_force_no_personnel_slot")
                availability["deployed"] = deployed - 1
            else:
                for key in ("ready_24h", "mobilizable_7d", "mobilizable_30d", "training_or_instruction", "essential_fixed_duty", "medical_or_recovery", "captured_or_missing"):
                    value = availability.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        availability[key] = value - 1
                        break
                else:
                    raise CommandRejectedError("population_linked_force_no_personnel_slot")
            force["total"] = int(force.get("total", 0)) - 1
            if sum(availability.values()) != force["total"]:
                raise CommandRejectedError("population_linked_force_conservation_failed")
        return transfer_id


    def _reconcile_rostered_person_injury(
        self,
        registry: Mapping[str, Any],
        *,
        person_ref: str,
        force_writes: Dict[str, Dict[str, Any]],
        team_writes: Dict[str, Dict[str, Any]],
        formation_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        """Move one rostered shinobi service slot into medical recovery.

        Exact people remain members of the physical service census while hurt;
        only their force availability changes.  The prior readiness class is
        returned so the person's recovery host can restore that same slot when
        the health reducer reports them ready again.
        """
        pools = registry.get("pools") if isinstance(registry, Mapping) else None
        if not isinstance(pools, Mapping):
            raise CommandRejectedError("population_registry_invalid")
        source_record: Optional[Mapping[str, Any]] = None
        for record in pools.values():
            if not isinstance(record, Mapping) or record.get("category") != "shinobi_service":
                continue
            rep = record.get("representation")
            refs = rep.get("rostered_person_refs") if isinstance(rep, Mapping) else None
            if isinstance(refs, list) and person_ref in refs:
                if source_record is not None:
                    raise CommandRejectedError("population_person_multiple_pool_membership")
                source_record = record
        if source_record is None:
            return None
        force_ref = source_record.get("linked_force_ref")
        if not isinstance(force_ref, str) or not force_ref:
            return None
        try:
            force_path, _digest, force_view = self._resolve_covered_owner_view(
                force_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("population_linked_force_invalid") from exc
        force = force_writes.get(force_path)
        if force is None:
            force = copy.deepcopy(dict(force_view))
            force_writes[force_path] = force
        availability = force.get("availability")
        if not isinstance(availability, dict):
            raise CommandRejectedError("population_linked_force_invalid")

        # An exact team identity overlay may sit inside an aggregate formation.
        # Significant injury evacuates that exact body from the formation and
        # moves one force slot from deployed to medical recovery.  Unembedded
        # people are drawn from their nearest non-deployed readiness class.
        embedded = self._detach_embedded_person_from_formation(
            person_ref=person_ref,
            expected_force_ref=force_ref,
            team_writes=team_writes,
            formation_writes=formation_writes,
        )
        source_class: Optional[str] = "deployed" if embedded is not None else None
        try:
            assignments = self.repository.read_json("state/org/assignments.json")
        except (FileNotFoundError, ValueError):
            assignments = {"records": []}
        records = assignments.get("records") if isinstance(assignments, Mapping) else None
        if source_class is None and isinstance(records, list):
            for assignment in records:
                if (
                    isinstance(assignment, Mapping)
                    and assignment.get("status", "active") == "active"
                    and assignment.get("source_owner") == force_ref
                    and isinstance(assignment.get("raw_allocations"), list)
                    and person_ref in assignment.get("raw_allocations", [])
                ):
                    candidate = assignment.get("source_availability_class")
                    if isinstance(candidate, str) and candidate != "deployed":
                        source_class = candidate
                        break
        if source_class is None:
            for candidate in (
                "ready_24h",
                "mobilizable_7d",
                "mobilizable_30d",
                "training_or_instruction",
                "essential_fixed_duty",
            ):
                value = availability.get(candidate)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    source_class = candidate
                    break
        if source_class is None:
            return None
        current = availability.get(source_class)
        medical = availability.get("medical_or_recovery")
        if (
            isinstance(current, bool) or not isinstance(current, int) or current <= 0
            or isinstance(medical, bool) or not isinstance(medical, int) or medical < 0
        ):
            raise CommandRejectedError("population_linked_force_no_personnel_slot")
        availability[source_class] = current - 1
        availability["medical_or_recovery"] = medical + 1
        if sum(value for value in availability.values() if isinstance(value, int) and not isinstance(value, bool)) != force.get("total"):
            raise CommandRejectedError("population_linked_force_conservation_failed")
        result = {
            "force_ref": force_ref,
            "force_path": force_path,
            "return_availability_class": source_class,
        }
        if embedded is not None:
            result.update({
                "return_formation_ref": str(embedded["formation_ref"]),
                "return_formation_path": str(embedded["formation_path"]),
                "return_team_refs": list(embedded["team_refs"]),
            })
        return result


    def _reconcile_rostered_person_capture(
        self,
        registry: Mapping[str, Any],
        *,
        person_ref: str,
        force_writes: Dict[str, Dict[str, Any]],
        team_writes: Dict[str, Dict[str, Any]],
        formation_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        """Move one exact service member into captured/missing accounting."""

        pools = registry.get("pools") if isinstance(registry, Mapping) else None
        if not isinstance(pools, Mapping):
            raise CommandRejectedError("population_registry_invalid")
        source_record: Optional[Mapping[str, Any]] = None
        for record in pools.values():
            if not isinstance(record, Mapping) or record.get("category") != "shinobi_service":
                continue
            rep = record.get("representation")
            refs = rep.get("rostered_person_refs") if isinstance(rep, Mapping) else None
            if isinstance(refs, list) and person_ref in refs:
                if source_record is not None:
                    raise CommandRejectedError("population_person_multiple_pool_membership")
                source_record = record
        if source_record is None:
            return None
        force_ref = source_record.get("linked_force_ref")
        if not isinstance(force_ref, str) or not force_ref:
            return None
        try:
            force_path, _digest, force_view = self._resolve_covered_owner_view(
                force_ref, cache=_OwnerResolutionCache()
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("population_linked_force_invalid") from exc
        force = force_writes.get(force_path)
        if force is None:
            force = copy.deepcopy(dict(force_view))
            force_writes[force_path] = force
        availability = force.get("availability")
        if not isinstance(availability, dict):
            raise CommandRejectedError("population_linked_force_invalid")
        embedded = self._detach_embedded_person_from_formation(
            person_ref=person_ref,
            expected_force_ref=force_ref,
            team_writes=team_writes,
            formation_writes=formation_writes,
        )
        source_class: Optional[str] = "deployed" if embedded is not None else None
        if source_class is None:
            try:
                assignments = self.repository.read_json("state/org/assignments.json")
            except (FileNotFoundError, ValueError):
                assignments = {"records": []}
            records = assignments.get("records") if isinstance(assignments, Mapping) else None
            if isinstance(records, list):
                for assignment in records:
                    if (
                        isinstance(assignment, Mapping)
                        and assignment.get("status", "active") == "active"
                        and assignment.get("source_owner") == force_ref
                        and isinstance(assignment.get("raw_allocations"), list)
                        and person_ref in assignment.get("raw_allocations", [])
                    ):
                        candidate = assignment.get("source_availability_class")
                        if isinstance(candidate, str) and candidate != "captured_or_missing":
                            source_class = candidate
                            break
        if source_class is None:
            for candidate in (
                "ready_24h", "mobilizable_7d", "mobilizable_30d",
                "training_or_instruction", "essential_fixed_duty", "medical_or_recovery",
            ):
                value = availability.get(candidate)
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    source_class = candidate
                    break
        if source_class is None:
            return None
        current = availability.get(source_class)
        captured = availability.get("captured_or_missing")
        if (
            isinstance(current, bool) or not isinstance(current, int) or current <= 0
            or isinstance(captured, bool) or not isinstance(captured, int) or captured < 0
        ):
            raise CommandRejectedError("population_linked_force_no_personnel_slot")
        availability[source_class] = current - 1
        availability["captured_or_missing"] = captured + 1
        if sum(value for value in availability.values() if isinstance(value, int) and not isinstance(value, bool)) != force.get("total"):
            raise CommandRejectedError("population_linked_force_conservation_failed")
        return {
            "force_ref": force_ref,
            "force_path": force_path,
            "source_availability_class": source_class,
            "formation_ref": None if embedded is None else embedded.get("formation_ref"),
        }


    @staticmethod
    def _transfer_population_representation(source_record: Dict[str, Any], destination_record: Dict[str, Any], count: int) -> None:
        """Move only anonymous representation with an aggregate physical transfer.

        Persistent identities remain in their source pool until an explicit person-level
        membership/assignment transaction moves them, preventing aggregate operations
        from silently relocating a named person.
        """
        source_rep = source_record.get("representation")
        destination_rep = destination_record.get("representation")
        if not isinstance(source_rep, dict) or not isinstance(destination_rep, dict):
            raise CommandRejectedError("population_representation_invalid")
        source_anonymous = source_rep.get("anonymous_count")
        destination_anonymous = destination_rep.get("anonymous_count")
        if (
            isinstance(source_anonymous, bool) or not isinstance(source_anonymous, int) or source_anonymous < count
            or isinstance(destination_anonymous, bool) or not isinstance(destination_anonymous, int) or destination_anonymous < 0
        ):
            raise CommandRejectedError("population_anonymous_source_insufficient")
        source_rep["anonymous_count"] = source_anonymous - count
        destination_rep["anonymous_count"] = destination_anonymous + count


    @staticmethod
    def _persist_population_pool_record(record: Dict[str, Any], pool: PopulationPool, *, at: CampaignTime) -> None:
        record["count"] = pool.total
        profile = record.get("profile")
        if not isinstance(profile, dict):
            raise CommandRejectedError("population_pool_invalid")
        profile["dimension_counts"] = {name: dict(values) for name, values in pool.dimensions.items()}
        profile["category_counts"] = {str(record.get("category", "population")): pool.total}
        numeric = profile.get("numeric_distributions")
        if isinstance(numeric, dict):
            for distribution in numeric.values():
                if isinstance(distribution, dict):
                    distribution["count"] = pool.total
        representation = record.get("representation")
        if not isinstance(representation, Mapping):
            raise CommandRejectedError("population_representation_invalid")
        rostered = representation.get("rostered_count")
        anonymous = representation.get("anonymous_count")
        refs = representation.get("rostered_person_refs")
        if (
            isinstance(rostered, bool) or not isinstance(rostered, int) or rostered < 0
            or isinstance(anonymous, bool) or not isinstance(anonymous, int) or anonymous < 0
            or not isinstance(refs, list) or len(refs) != rostered
            or anonymous + rostered != pool.total
        ):
            raise CommandRejectedError("population_representation_invalid")
        record["last_changed_at"] = str(at)
        record["status"] = "exhausted" if pool.total == 0 else "active"


    def _aggregate_combat_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        payload = command.payload
        spec = COMMAND_SPECS[command.command_type]
        required_fields = set(spec.required_fields)
        allowed_optional = set(spec.optional_fields)
        if not required_fields.issubset(set(payload)) or set(payload) - required_fields - allowed_optional:
            raise CommandRejectedError("combat_resolution_invalid")
        combat_id = _stable_id(payload.get("combat_id"), "combat_id_invalid", prefix="combat.")
        scale = payload.get("scale")
        if scale not in ("formation", "battle"):
            raise CommandRejectedError("combat_scale_invalid")
        mission_context = self._active_mission_context(
            payload.get("mission_ref"), actor_id=command.actor_id, current_time=current_time
        )
        specs = payload.get("participants")
        objective_specs = payload.get("objectives")
        if not isinstance(specs, Sequence) or isinstance(specs, (str, bytes, bytearray)) or not 2 <= len(specs) <= 24:
            raise CommandRejectedError("combat_participants_invalid")
        if not isinstance(objective_specs, Sequence) or isinstance(objective_specs, (str, bytes, bytearray)) or not 1 <= len(objective_specs) <= 24:
            raise CommandRejectedError("combat_objectives_invalid")
        operation_path = self._operation_owner_path(combat_id)
        if payload.get("parent_combat_ref") is not None:
            raise CommandRejectedError("aggregate_combat_cannot_have_parent")
        if self.repository.read_optional_bytes(operation_path) is not None:
            raise CommandRejectedError("combat_operation_already_exists")
        try:
            zoom_registry = copy.deepcopy(self.repository.read_json(_COMBAT_ZOOM_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("combat_zoom_registry_invalid") from exc
        pending_by_actor = zoom_registry.get("pending_by_actor") if isinstance(zoom_registry, Mapping) else None
        if not isinstance(pending_by_actor, dict):
            raise CommandRejectedError("combat_zoom_registry_invalid")
        scene = self._scene_base(current_time)
        scene_location = scene.get("location_id")
        if not isinstance(scene_location, str) or not scene_location:
            raise CommandRejectedError("combat_scene_location_invalid")
        requested_location = payload.get("location_ref")
        if requested_location is not None:
            requested_location = _stable_id(requested_location, "combat_location_invalid")
            if command.mode == "gameplay" and requested_location != scene_location:
                raise CommandRejectedError("combat_location_not_player_scene")
            location_id = requested_location
        else:
            location_id = scene_location

        owner_cache = _OwnerResolutionCache()
        parsed: Dict[str, Dict[str, Any]] = {}
        force_records: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        formation_records: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        formation_registry_records: Dict[str, Dict[str, Any]] = {}
        allocation_by_force_availability: Dict[Tuple[str, str], int] = {}
        allocation_by_assignment: Dict[str, int] = {}
        side_refs = set()
        aggregate_actor_refs = set()
        expected_fields = {
            "participant_ref", "committed_count", "side_ref", "action", "target_refs",
            "objective_ref", "lethal", "command_authority_ref", "named_actor_refs",
        }
        for sequence, spec in enumerate(specs):
            if not isinstance(spec, Mapping) or set(spec) != expected_fields:
                raise CommandRejectedError("combat_participants_invalid")
            participant_ref = _stable_id(spec.get("participant_ref"), "combat_participant_ref_invalid", prefix="formation:")
            if participant_ref in parsed:
                raise CommandRejectedError("combat_participants_invalid")
            formation_id = participant_ref.split(":", 1)[1]
            formation_path, force_ref, formation_view = self._formation_by_id(formation_id)
            committed = spec.get("committed_count")
            if isinstance(committed, bool) or not isinstance(committed, int) or committed <= 0 or committed > 1_000_000:
                raise CommandRejectedError("combat_committed_count_invalid")
            source_class = "deployed"
            side_ref = _stable_id(spec.get("side_ref"), "combat_side_ref_invalid", prefix="side:")
            action = spec.get("action")
            if action not in ("attack", "capture", "escape", "extract", "disengage", "hold", "secure", "delay"):
                raise CommandRejectedError("combat_intent_invalid")
            targets = spec.get("target_refs")
            if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes, bytearray)) or any(not isinstance(x, str) for x in targets) or len(targets) != len(set(targets)):
                raise CommandRejectedError("combat_intent_invalid")
            objective_ref = spec.get("objective_ref")
            if objective_ref is not None:
                objective_ref = _stable_id(objective_ref, "combat_objective_ref_invalid", prefix="objective:")
            if not isinstance(spec.get("lethal"), bool):
                raise CommandRejectedError("combat_intent_invalid")
            command_authority_ref = _stable_id(spec.get("command_authority_ref"), "combat_command_authority_invalid")
            operational_attachment_ref = formation_id
            named = spec.get("named_actor_refs")
            if not isinstance(named, Sequence) or isinstance(named, (str, bytes, bytearray)) or any(not isinstance(x, str) for x in named) or len(named) != len(set(named)) or len(named) > 16:
                raise CommandRejectedError("combat_named_actor_refs_invalid")
            if len(named) > committed:
                # Named actors are exact identities inside the committed
                # aggregate headcount. They are never bonus personnel added on
                # top of a formation slice.
                raise CommandRejectedError("combat_named_actor_count_exceeds_commitment")
            if named and len(named) == committed:
                # A fully named engagement belongs in exact combat, not the
                # aggregate casualty engine.
                raise CommandRejectedError("combat_fully_named_slice_requires_exact_resolution")
            if any(ref in pending_by_actor for ref in named):
                raise CommandRejectedError("combat_named_actor_already_pending_zoom")
            try:
                self._resolve_covered_owner(command_authority_ref, cache=owner_cache)
            except CommandRejectedError as exc:
                raise CommandRejectedError("combat_command_authority_unresolved") from exc
            if force_ref not in force_records:
                try:
                    fpath, _, fview = self._resolve_covered_owner_view(force_ref, cache=owner_cache)
                except CommandRejectedError as exc:
                    raise CommandRejectedError("combat_force_unresolved") from exc
                if not isinstance(fview, Mapping) or fview.get("schema") != "force" or fview.get("id") != force_ref:
                    raise CommandRejectedError("combat_force_unresolved")
                force_records[force_ref] = (fpath, copy.deepcopy(dict(fview)))
            fpath, force = force_records[force_ref]
            if formation_path not in formation_registry_records:
                try:
                    loaded_registry = self.repository.read_json(formation_path)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("formation_registry_unresolved") from exc
                if not isinstance(loaded_registry, dict) or not isinstance(loaded_registry.get("formations"), list):
                    raise CommandRejectedError("formation_registry_invalid")
                formation_registry_records[formation_path] = copy.deepcopy(loaded_registry)
            formation = next(
                (row for row in formation_registry_records[formation_path]["formations"] if isinstance(row, dict) and row.get("id") == formation_id),
                None,
            )
            if not isinstance(formation, dict) or formation.get("force_ref") != force_ref:
                raise CommandRejectedError("combat_formation_unresolved")
            formation_total = formation.get("personnel_total")
            formation_location = formation.get("location_ref")
            if (
                isinstance(formation_total, bool)
                or not isinstance(formation_total, int)
                or committed > formation_total
            ):
                raise CommandRejectedError("combat_formation_commitment_exceeds_strength")
            if isinstance(formation_location, str) and formation_location and formation_location != location_id:
                raise CommandRejectedError("combat_formation_not_at_scene")

            # Exact named actors may be woken inside an aggregate formation, but
            # must remain exact subsets of this formation and be physically at
            # the same battle location.
            named_actor_team_refs: Dict[str, Tuple[str, ...]] = {}
            named_actor_capabilities: Dict[str, Dict[str, int]] = {}
            for ref in named:
                try:
                    _person_path, _person_digest, person_view = self._resolve_covered_owner_view(
                        ref, cache=owner_cache
                    )
                except CommandRejectedError as exc:
                    raise CommandRejectedError("combat_named_actor_unresolved") from exc
                actor_location = person_view.get("current_location_id") if isinstance(person_view, Mapping) else None
                if actor_location != location_id:
                    raise CommandRejectedError("combat_named_actor_not_at_scene")
                named_actor_capabilities[ref] = self._combat_capability(person_view).to_record()
                assigned_formation_ref, assigned_team_refs = self._exact_team_assignment_for_person(ref)
                membership_assignment_refs: set[str] = set()
                membership_embedded_here = False
                for team_ref in self._active_exact_team_refs():
                    try:
                        _team_path, team_view = self._exact_team(team_ref)
                    except CommandRejectedError:
                        continue
                    members = team_view.get("member_refs")
                    if team_view.get("status") != "active" or not isinstance(members, list) or ref not in members:
                        continue
                    team_assignment = team_view.get("current_assignment_ref")
                    if isinstance(team_assignment, str) and team_assignment:
                        membership_assignment_refs.add(team_assignment)
                        embedded = team_view.get("embedded_member_refs", [])
                        if team_assignment == formation_id and isinstance(embedded, list) and ref in embedded:
                            membership_embedded_here = True
                if membership_assignment_refs and formation_id not in membership_assignment_refs:
                    raise CommandRejectedError("combat_named_actor_team_assignment_conflict")
                if formation_id in membership_assignment_refs and not membership_embedded_here:
                    raise CommandRejectedError("combat_named_actor_not_embedded")
                if assigned_formation_ref is not None and formation_id != assigned_formation_ref:
                    raise CommandRejectedError("combat_named_actor_team_assignment_conflict")
                named_actor_team_refs[ref] = assigned_team_refs
            formation_records[participant_ref] = (formation_path, formation)

            authority = self._domain_authority(cache=owner_cache)
            authority_decision = authority.force_grant(
                grantor_ref=command_authority_ref,
                force_record=force,
            )
            if not authority_decision.allowed:
                authority_decision = authority.force_command(
                    commander_ref=command_authority_ref,
                    force_ref=force_ref,
                    operational_attachment_ref=formation_id,
                    named_actor_refs=tuple(named),
                    committed_count=committed,
                    effective_at=str(current_time),
                )
            if not authority_decision.allowed:
                raise CommandRejectedError("combat_force_command_not_authorized")
            availability = force.get("availability")
            if not isinstance(availability, Mapping) or not isinstance(availability.get("deployed"), int):
                raise CommandRejectedError("combat_availability_class_invalid")
            allocation_by_force_availability[(force_ref, source_class)] = allocation_by_force_availability.get((force_ref, source_class), 0) + committed
            if authority_decision.basis == "force_assignment" and isinstance(authority_decision.authority_ref, str):
                allocation_by_assignment[authority_decision.authority_ref] = allocation_by_assignment.get(authority_decision.authority_ref, 0) + committed
            parsed[participant_ref] = {
                "sequence": sequence, "participant_ref": participant_ref, "force_ref": force_ref,
                "committed_count": committed,
                "source_availability_class": source_class, "side_ref": side_ref,
                "action": action, "target_refs": tuple(targets), "objective_ref": objective_ref,
                "lethal": spec.get("lethal"), "command_authority_ref": command_authority_ref,
                "operational_attachment_ref": operational_attachment_ref, "named_actor_refs": tuple(named),
                "named_actor_team_refs": named_actor_team_refs,
                "named_actor_capabilities": named_actor_capabilities,
                "authority_basis": authority_decision.basis,
                "force_path": fpath,
                "formation_path": formation_path,
            }
            aggregate_actor_refs.add(participant_ref)
            side_refs.add(side_ref)
        if len(side_refs) < 2:
            raise CommandRejectedError("combat_sides_invalid")
        named_sides = {
            item["side_ref"]
            for item in parsed.values()
            if item["named_actor_refs"]
        }
        if named_sides and len(named_sides) < 2:
            # Exact identities are removed from anonymous aggregate casualty
            # allocation only when an opposed exact sub-engagement can actually
            # be resolved.  Reserving named actors on one side only would leave
            # the parent combat permanently awaiting an exact opponent that the
            # state does not identify.
            raise CommandRejectedError("combat_named_zoom_requires_opposed_exact_actors")
        participant_sides = {ref: item["side_ref"] for ref, item in parsed.items()}
        controlled_refs = {
            ref for ref, item in parsed.items()
            if item["command_authority_ref"] == command.actor_id
        }
        controlled_sides = {parsed[ref]["side_ref"] for ref in controlled_refs}
        for item in parsed.values():
            # Caller intent is authoritative only for formations the authenticated
            # actor actually commands.  Enemy/allied independent formations are
            # normalized from their saved formation state below.
            if (
                item["participant_ref"] in controlled_refs
                and any(target not in aggregate_actor_refs or target == item["participant_ref"] for target in item["target_refs"])
            ):
                raise CommandRejectedError("combat_intent_invalid")
        for (force_ref, source_class), amount in allocation_by_force_availability.items():
            force = force_records[force_ref][1]
            if amount > force["availability"][source_class]:
                raise CommandRejectedError("combat_force_availability_insufficient")
        if allocation_by_assignment:
            try:
                assignment_registry = self.repository.read_json("state/org/assignments.json")
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("force_assignment_registry_invalid") from exc
            assignment_records = assignment_registry.get("records") if isinstance(assignment_registry, Mapping) else None
            if not isinstance(assignment_records, list):
                raise CommandRejectedError("force_assignment_registry_invalid")
            by_id = {row.get("id"): row for row in assignment_records if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
            for assignment_id, amount in allocation_by_assignment.items():
                assignment = by_id.get(assignment_id)
                allocated = assignment.get("allocated_count") if isinstance(assignment, Mapping) else None
                if isinstance(allocated, bool) or not isinstance(allocated, int) or amount > allocated:
                    raise CommandRejectedError("combat_force_assignment_overcommitted")
        objective_ids = set()
        objectives = []
        objective_kind_by_ref: Dict[str, str] = {}
        objective_ref_by_side: Dict[str, str] = {}
        for spec in objective_specs:
            if not isinstance(spec, Mapping) or set(spec) != {"objective_ref", "side_ref", "kind", "target_refs", "zone_ref", "deadline_tick"}:
                raise CommandRejectedError("combat_objectives_invalid")
            objective_ref = _stable_id(spec.get("objective_ref"), "combat_objective_ref_invalid", prefix="objective:")
            side_ref = _stable_id(spec.get("side_ref"), "combat_side_ref_invalid", prefix="side:")
            # An external caller may describe only a side they actually control.
            # Objective rows submitted for an independent/enemy side are ignored
            # and deterministically replaced below.
            if side_ref not in controlled_sides:
                continue
            targets = spec.get("target_refs")
            zone_ref = spec.get("zone_ref")
            deadline_tick = spec.get("deadline_tick")
            if objective_ref in objective_ids or side_ref not in side_refs or not isinstance(targets, Sequence) or isinstance(targets, (str, bytes, bytearray)) or any(t not in aggregate_actor_refs for t in targets):
                raise CommandRejectedError("combat_objectives_invalid")
            if zone_ref is not None:
                zone_ref = _stable_id(zone_ref, "combat_zone_ref_invalid")
            if deadline_tick is not None and (isinstance(deadline_tick, bool) or not isinstance(deadline_tick, int) or deadline_tick < 0):
                raise CommandRejectedError("combat_objectives_invalid")
            try:
                objective = CombatObjective(objective_ref=objective_ref, side_ref=side_ref, kind=spec.get("kind"), required_progress=1, target_refs=tuple(targets), primary=True, deadline_tick=deadline_tick, zone_ref=zone_ref)
                objectives.append(objective)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("combat_objectives_invalid") from exc
            objective_ids.add(objective_ref)
            objective_kind_by_ref[objective_ref] = objective.kind
            objective_ref_by_side.setdefault(side_ref, objective_ref)

        # Every side outside the authenticated actor's command derives its own
        # tactical objective from persisted formation state.  This prevents the
        # player-facing payload from making an enemy retreat, surrender, or use
        # lethal force simply by supplying a convenient objective row.
        for side_ref in sorted(side_refs - controlled_sides):
            side_items = [item for item in parsed.values() if item["side_ref"] == side_ref]
            if not side_items:
                continue
            lead_item = min(side_items, key=lambda item: item["sequence"])
            _formation_path, lead_formation = formation_records[lead_item["participant_ref"]]
            kind = self._formation_autonomous_objective_kind(lead_formation)
            objective_ref = f"objective:auto:{command.digest[:12]}:{re.sub(r'[^a-z0-9._-]', '_', side_ref.lower())}"
            targets = self._derived_combat_targets(side_ref=side_ref, participant_sides=participant_sides)
            try:
                objective = CombatObjective(
                    objective_ref=objective_ref,
                    side_ref=side_ref,
                    kind=kind,
                    required_progress=1,
                    target_refs=targets,
                    primary=True,
                    deadline_tick=8 if kind == "delay" else None,
                    zone_ref=location_id if kind in ("hold", "secure") else None,
                )
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("combat_objectives_invalid") from exc
            objectives.append(objective)
            objective_ids.add(objective_ref)
            objective_kind_by_ref[objective_ref] = kind
            objective_ref_by_side[side_ref] = objective_ref

        for item in parsed.values():
            if item["participant_ref"] not in controlled_refs:
                objective_ref = objective_ref_by_side.get(item["side_ref"])
                if objective_ref is None:
                    # An independent allied commander on a player-controlled side
                    # follows the side objective but chooses their own tactical
                    # intent from it.
                    objective_ref = next(
                        (ref for ref, objective in ((obj.objective_ref, obj) for obj in objectives) if objective.side_ref == item["side_ref"]),
                        None,
                    )
                if objective_ref is None:
                    raise CommandRejectedError("combat_objectives_invalid")
                objective_kind = objective_kind_by_ref[objective_ref]
                item["objective_ref"] = objective_ref
                item["action"] = self._combat_action_for_objective(objective_kind)
                item["target_refs"] = self._derived_combat_targets(
                    side_ref=item["side_ref"], participant_sides=participant_sides
                )
                item["lethal"] = objective_kind == "eliminate"
            elif item["objective_ref"] is not None and item["objective_ref"] not in objective_ids:
                raise CommandRejectedError("combat_intent_invalid")

        participants = []
        engagements = []
        for ref, item in parsed.items():
            formation_path, formation = formation_records[ref]
            force = force_records[item["force_ref"]][1]
            mean, spread, readiness, morale, cohesion, initiative, capability_source_refs, digest = self._formation_aggregate_capability(
                formation=formation, force=force
            )
            item["capability_source_refs"] = capability_source_refs
            formation_readiness = formation.get("readiness")
            formation_morale = formation.get("morale")
            formation_cohesion = formation.get("cohesion")
            if isinstance(formation_readiness, int) and not isinstance(formation_readiness, bool):
                readiness = max(1, min(200, formation_readiness))
            if isinstance(formation_morale, int) and not isinstance(formation_morale, bool):
                morale = max(1, min(200, formation_morale))
            if isinstance(formation_cohesion, int) and not isinstance(formation_cohesion, bool):
                cohesion = max(1, min(200, formation_cohesion))
            tendencies = self._formation_tendencies(formation)
            tendency_initiative = tendencies.get("initiative")
            if isinstance(tendency_initiative, int) and not isinstance(tendency_initiative, bool):
                initiative = max(1, min(200, (initiative + tendency_initiative) // 2))
            supply_state, fortification_milli = self._formation_front_effects(
                formation_ref=str(formation.get("id")),
                force_side_ref=(force.get("owner_ref") if isinstance(force.get("owner_ref"), str) and force.get("owner_ref") else item["force_ref"]),
                location_ref=(formation.get("location_ref") if isinstance(formation.get("location_ref"), str) else None),
            )
            supply_multiplier = {"supported": 1000, "strained": 950, "critical": 850, "cut_off": 700}[supply_state]
            readiness = max(1, min(200, readiness * supply_multiplier // 1000))
            mean_record = {
                key: max(0, min(200, value * supply_multiplier // 1000))
                for key, value in mean.to_record().items()
            }
            if fortification_milli > 0:
                defense_bonus = min(30, fortification_milli * 30 // 1000)
                mean_record["defense"] = min(200, mean_record["defense"] + defense_bonus)
                mean_record["protection"] = min(200, mean_record["protection"] + defense_bonus)
            mean = CapabilityProfile(**mean_record)
            item["supply_state"] = supply_state
            item["fortification_milli"] = fortification_milli
            # Formation condition affects effective coordinated output, not the
            # soldiers' underlying physical stats.  This is deliberately a
            # bounded coordination multiplier rather than a commander-stat buff.
            coordination = max(50, min(125, (readiness + morale + cohesion) // 3))
            mean = CapabilityProfile(
                **{
                    key: max(0, min(200, (value * coordination + 50) // 100))
                    for key, value in mean.to_record().items()
                }
            )
            named_capability_records = item.get("named_actor_capabilities", {})
            if isinstance(named_capability_records, Mapping) and named_capability_records:
                base_record = mean.to_record()
                actor_records = [
                    row for row in named_capability_records.values() if isinstance(row, Mapping)
                ]
                if len(actor_records) != len(item["named_actor_refs"]):
                    raise CommandRejectedError("combat_named_actor_capability_invalid")
                influence_milli = min(
                    200,
                    max(20, len(actor_records) * 5000 // max(1, item["committed_count"])),
                )
                adjusted = {}
                for key, base_value in base_record.items():
                    values = [row.get(key) for row in actor_records]
                    if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
                        raise CommandRejectedError("combat_named_actor_capability_invalid")
                    exact_average = sum(values) // len(values)
                    adjusted[key] = max(
                        0,
                        min(200, base_value + ((exact_average - base_value) * influence_milli) // 1000),
                    )
                mean = CapabilityProfile(**adjusted)
            formation_digest = hashlib.sha256(
                json.dumps(
                    {
                        "formation": formation,
                        "named_actor_capabilities": named_capability_records,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
                + digest.encode("ascii")
            ).hexdigest()
            participant = Participant(
                participant_ref=ref,
                authoritative_owner_ref=formation_path,
                side_ref=item["side_ref"], sequence=item["sequence"], representation="aggregate",
                capability=mean,
                personnel=PersonnelState(
                    total=item["committed_count"] - len(item["named_actor_refs"]),
                    active=item["committed_count"] - len(item["named_actor_refs"]),
                ),
                position=PositionState(zone_ref=location_id),
                information=InformationState(observed_refs=tuple(sorted(aggregate_actor_refs - {ref}))),
                intent=CombatIntent(action=item["action"], objective_ref=item["objective_ref"], target_refs=item["target_refs"], commitment_milli=1000, lethal_force_milli=1000 if item["lethal"] else 0),
                initiative=initiative, readiness=readiness, morale=morale, cohesion=cohesion,
                effective_range_bands=(0, 1, 2),
                kernel=BattleKernel(source_ref=f"{formation_path}#{formation.get('id')}", source_sha256=formation_digest, mean=mean, spread=spread),
                named_actor_refs=item["named_actor_refs"],
            )
            participants.append(participant)
            for index, target in enumerate(item["target_refs"]):
                engagements.append(Engagement(engagement_ref=f"engagement:{command.digest[:12]}:{item['sequence']}:{index}", actor_ref=ref, target_ref=target, range_band=1, line_of_sight=True, frontage_milli=1000))
        try:
            contract = CombatContract(
                combat_ref=combat_id,
                transaction_ref=("tx.autonomous." if command.mode == "autonomous" else "tx.gameplay.") + command.digest,
                scale=scale,
                participants=tuple(participants), objectives=tuple(objectives), engagements=tuple(engagements),
                terrain=TerrainState(terrain_ref=f"terrain:{location_id}", side_modifiers=tuple(SideTerrain(side_ref=s) for s in sorted(side_refs))),
                timing=CombatTiming(current_tick=0, exchange_seconds=60, max_ticks=2), rng_stream=f"combat:{combat_id}",
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("combat_contract_invalid") from exc
        world_seed = meta.get("world_seed")
        if not isinstance(world_seed, str) or not world_seed:
            raise CommandRejectedError("campaign_rng_seed_invalid")
        rng = CounterRNG(world_seed=world_seed, transaction_id=contract.transaction_ref, stream=contract.rng_stream)
        for _ in range(required_draw_count(contract)):
            rng.draw_u64()
        effect_plan = resolve_combat(contract, rng.receipts)
        effects = {effect.participant_ref: effect for effect in effect_plan.participant_effects}

        # Persistent formations represent the force's deployed partition.  A
        # battle may commit only personnel already represented by those saved
        # formations; mobilization/formation creation is a separate semantic
        # lifecycle action.  This keeps command authority, force availability
        # and formation strength from drifting apart.
        killed_by_force: Dict[str, int] = {}
        operation_participants = []
        for ref, item in parsed.items():
            if item["source_availability_class"] != "deployed":
                raise CommandRejectedError("combat_formation_not_deployed")
            effect = effects[ref]
            after = effect.after_personnel
            force = force_records[item["force_ref"]][1]
            availability = force["availability"]
            committed = item["committed_count"]
            aggregate_resolved_count = committed - len(item["named_actor_refs"])
            if availability.get("deployed", 0) < committed:
                raise CommandRejectedError("combat_force_reconciliation_invalid")

            # Named exact actors are reserved for a linked zoom scene and remain
            # in the deployed partition here. Only the anonymous remainder is
            # exposed to aggregate casualty allocation.
            availability["deployed"] -= aggregate_resolved_count
            operational_survivors = after.active + after.escaped
            availability["deployed"] += operational_survivors
            availability["medical_or_recovery"] += after.wounded + after.incapacitated
            availability["captured_or_missing"] += after.captured

            formation_path, formation = formation_records[ref]
            prior_formation_total = formation.get("personnel_total")
            if isinstance(prior_formation_total, bool) or not isinstance(prior_formation_total, int):
                raise CommandRejectedError("formation_strength_invalid")
            roster_losses = after.killed + after.wounded + after.incapacitated + after.captured
            new_formation_total = prior_formation_total - roster_losses
            if new_formation_total < 0:
                raise CommandRejectedError("formation_strength_invalid")
            self._resize_formation_strength(formation, new_formation_total)
            if aggregate_resolved_count > 0 and roster_losses > 0 and new_formation_total > 0:
                loss_milli = min(1000, roster_losses * 1000 // aggregate_resolved_count)
                readiness_loss = max(1, loss_milli // 60)
                cohesion_loss = max(1, loss_milli // 50)
                morale_loss = max(1, loss_milli // 45)
                formation["readiness"] = max(1, int(formation.get("readiness", 50)) - readiness_loss)
                formation["cohesion"] = max(1, int(formation.get("cohesion", 50)) - cohesion_loss)
                formation["morale"] = max(1, int(formation.get("morale", 50)) - morale_loss)
                formation["role"] = "recovering"
                formation["activity_summary"] = "post-combat recovery and reconstitution"

            if after.killed:
                force["total"] -= after.killed
                killed_by_force[item["force_ref"]] = killed_by_force.get(item["force_ref"], 0) + after.killed
            if force["total"] < 0 or any(not isinstance(v, int) or v < 0 for v in availability.values()) or sum(availability.values()) != force["total"]:
                raise CommandRejectedError("combat_force_reconciliation_invalid")
            operation_participants.append({
                "participant_ref": ref,
                "force_ref": item["force_ref"],
                "population_pool_id": force.get("population_pool_id"),
                "capability_source_refs": list(item.get("capability_source_refs", ())),
                "supply_state": item.get("supply_state", "supported"),
                "fortification_milli": item.get("fortification_milli", 0),
                "side_ref": item["side_ref"],
                "command_authority_ref": item["command_authority_ref"],
                "command_authority_basis": item["authority_basis"],
                "operational_attachment_ref": item["operational_attachment_ref"],
                "source_availability_class": item["source_availability_class"],
                "committed_count": committed,
                "aggregate_resolved_count": aggregate_resolved_count,
                "location_id": location_id,
                "formation_ref": formation.get("id"),
                "doctrine_ref": formation.get("doctrine_ref"),
                "training_ref": formation.get("training_ref"),
                "readiness": formation.get("readiness"),
                "morale": formation.get("morale"),
                "cohesion": formation.get("cohesion"),
                "personnel": after.to_record(),
                "formation_personnel_after": new_formation_total,
                "named_actor_refs": list(item["named_actor_refs"]),
                "named_actor_team_refs": {
                    actor_ref: list(team_refs)
                    for actor_ref, team_refs in item["named_actor_team_refs"].items()
                },
                "named_actor_accounting": "exact_subset_of_committed_count_not_additional_personnel",
            })

        try:
            population_registry = copy.deepcopy(self.repository.read_json(_POPULATION_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("population_registry_invalid") from exc
        pools = population_registry.get("pools") if isinstance(population_registry, dict) else None
        transfers = population_registry.get("transfers") if isinstance(population_registry, dict) else None
        if not isinstance(pools, dict) or not isinstance(transfers, list):
            raise CommandRejectedError("population_registry_invalid")
        population_before_pairs = {}
        casualty_transfer_ids = []
        for force_ref, killed in sorted(killed_by_force.items()):
            if killed <= 0:
                continue
            force = force_records[force_ref][1]
            source_id = force.get("population_pool_id")
            if not isinstance(source_id, str) or not source_id.endswith(".shinobi_service"):
                raise CommandRejectedError("combat_force_population_source_missing")
            destination_id = source_id[:-len("shinobi_service")] + "deceased_service"
            source_record = pools.get(source_id)
            destination_record = pools.get(destination_id)
            if not isinstance(source_record, dict) or not isinstance(destination_record, dict):
                raise CommandRejectedError("combat_force_population_source_missing")
            source_pool = self._pool_reducer_view(source_id, source_record)
            destination_pool = self._pool_reducer_view(destination_id, destination_record)
            if source_pool.total != force["total"] + killed:
                raise CommandRejectedError("combat_force_population_drift")
            selected = neutral_proportional_selection(source_pool, killed)
            transfer_id = f"casualty.{command.digest[:16]}.{force_ref}"
            transfer = PopulationTransfer(transfer_id=transfer_id, source_pool_id=source_id, destination_pool_id=destination_id, count=killed, selected_dimensions=selected, selection_mode="neutral_proportional")
            source_after, destination_after = apply_transfer(source_pool, destination_pool, transfer)
            self._transfer_population_representation(source_record, destination_record, killed)
            population_before_pairs[(source_id, destination_id)] = source_pool.total + destination_pool.total
            self._persist_population_pool_record(source_record, source_after, at=current_time)
            self._persist_population_pool_record(destination_record, destination_after, at=current_time)
            transfers.append({
                "id": transfer_id, "at": str(current_time), "source_pool_id": source_id, "destination_ref": destination_id,
                "requested_count": killed, "accepted": killed, "rejected": 0,
                "authority_ref": combat_id, "authority_basis": "combat_casualty_conservation", "policy_ref": None,
                "method": "combat_casualty_conserved_transfer",
                "accepted_profile": {"numeric_distributions": {}, "category_counts": {"deceased_service": killed}, "dimension_counts": {name: dict(values) for name, values in selected.items()}, "tags": ["combat_casualty", combat_id]},
                "materialized_person_ids": [], "source_removed": killed, "destination_added": killed,
                "selection_note": "Aggregate battlefield deaths transferred from living shinobi service to deceased-service conservation pool; no named casualty silently selected.",
            })
            self._trim_population_transfer_history(transfers)
            casualty_transfer_ids.append(transfer_id)

        effect_record = effect_plan.to_record()
        effect_hash = hashlib.sha256(json.dumps(effect_record, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        pending_named_actor_refs = sorted({
            actor_ref
            for item in parsed.values()
            for actor_ref in item["named_actor_refs"]
        })
        world_events = self._world_events()
        force_paths = tuple(sorted({path for path, _ in force_records.values()}))
        formation_paths = tuple(sorted(formation_registry_records))
        consequence_refs = []
        for ref, effect in effects.items():
            a = effect.after_personnel
            consequence_refs.extend([f"combat:{ref}:killed:{a.killed}", f"combat:{ref}:wounded:{a.wounded}", f"combat:{ref}:captured:{a.captured}"])
        aggregate_event_kind = "aggregate_combat_zoom_pending" if pending_named_actor_refs else "aggregate_combat_resolved"
        event_id = self._append_semantic_event(
            world_events, command=command, kind=aggregate_event_kind, at=current_time,
            host_refs=(combat_id,), actor_refs=(command.actor_id,), place_refs=(location_id,),
            causal_refs=(() if mission_context is None else (mission_context,)),
            affected_owner_refs=force_paths + formation_paths + (operation_path, _POPULATION_REGISTRY_PATH),
            material_consequence_refs=tuple(consequence_refs), classification="restricted", audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.combat.resolve_combat",
        )
        for actor_ref in pending_named_actor_refs:
            pending_by_actor[actor_ref] = combat_id
        operation = {
            "schema": "combat-operation", "operation_id": combat_id, "opened_at": str(current_time), "location_id": location_id,
            "scale": scale, "status": ("awaiting_named_zoom" if pending_named_actor_refs else "resolved"),
            "authority_model": "source force owns manpower; command authority and operational attachment are separate; operation owns temporary committed-slice combat condition only",
            "participants": sorted(operation_participants, key=lambda x: x["participant_ref"]),
            "outcome": {
                "resolution_mode": effect_plan.resolution_mode, "status": effect_plan.status,
                "victorious_side_refs": list(effect_plan.victorious_side_refs),
                "wake_triggers": [x.to_record() for x in effect_plan.wake_triggers],
                "semantic_event_id": event_id, "effect_plan_sha256": effect_hash,
                "pending_named_actor_refs": pending_named_actor_refs,
                "resolved_zoom_refs": [],
            },
        }
        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            operation_path: _json_bytes(operation),
            _COMBAT_ZOOM_REGISTRY_PATH: _json_bytes(zoom_registry),
            **self._world_event_writes(world_events),
        }
        for path, force in force_records.values():
            writes[path] = _json_bytes(force)
        for path, registry in formation_registry_records.items():
            writes[path] = _json_bytes(registry)
        if killed_by_force:
            writes[_POPULATION_REGISTRY_PATH] = _json_bytes(population_registry)
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        represented_personnel = sum(x["committed_count"] for x in operation_participants)

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("aggregate combat write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_operation = overlay.read_json(operation_path)
            if sum(item["committed_count"] for item in staged_operation["participants"]) != represented_personnel:
                raise ValueError("aggregate combat participant conservation failed")
            staged_zoom = overlay.read_json(_COMBAT_ZOOM_REGISTRY_PATH)
            staged_pending = staged_zoom.get("pending_by_actor", {})
            for actor_ref in pending_named_actor_refs:
                if staged_pending.get(actor_ref) != combat_id:
                    raise ValueError("aggregate combat named zoom reservation missing")
            for force_ref, (path, _force) in force_records.items():
                staged_force = overlay.read_json(path)
                if sum(staged_force["availability"].values()) != staged_force["total"]:
                    raise ValueError("force availability partition no longer conserves total")
                formation_path = self._formation_registry_path(force_ref)
                if formation_path in formation_registry_records:
                    staged_registry = overlay.read_json(formation_path)
                    represented = sum(
                        row.get("personnel_total", 0) for row in staged_registry.get("formations", [])
                        if isinstance(row, Mapping) and isinstance(row.get("personnel_total"), int)
                    )
                    deployed = staged_force["availability"]["deployed"]
                    if represented > deployed:
                        raise ValueError("formation representation exceeds deployed force headcount")
            if killed_by_force:
                staged_pop = overlay.read_json(_POPULATION_REGISTRY_PATH)
                for (source_id, destination_id), before_total in population_before_pairs.items():
                    after_total = staged_pop["pools"][source_id]["count"] + staged_pop["pools"][destination_id]["count"]
                    if after_total != before_total:
                        raise ValueError("combat casualty population transfer violated conservation")
                staged_ids = {item.get("id") for item in staged_pop.get("transfers", []) if isinstance(item, Mapping)}
                if any(tid not in staged_ids for tid in casualty_transfer_ids):
                    raise ValueError("combat casualty transfer receipt missing")

        return _BuiltPlan(
            code="combat_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "combat_id": combat_id, "scale": scale,
                "represented_personnel": represented_personnel, "operational_participants": len(operation_participants),
                "canonical_write_count": len(expected_paths), "resolution_mode": effect_plan.resolution_mode,
                "status": effect_plan.status, "victorious_side_refs": list(effect_plan.victorious_side_refs),
                "wake_triggers": [x.to_record() for x in effect_plan.wake_triggers],
                "participant_effects": [x.to_record() for x in effect_plan.participant_effects],
                "casualty_transfer_ids": casualty_transfer_ids, "effect_plan_sha256": effect_hash,
                "semantic_event_id": event_id, "operation_owner_ref": operation_path,
                "pending_custody_refs": [],
            }, validator=validate,
        )


    def _combat_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        payload = command.payload
        required_fields = {"combat_id", "scale", "participants", "objectives"}
        allowed_optional = {"mission_ref", "location_ref", "parent_combat_ref"}
        if not required_fields.issubset(set(payload)) or set(payload) - required_fields - allowed_optional:
            raise CommandRejectedError("combat_resolution_invalid")
        combat_id = _stable_id(payload.get("combat_id"), "combat_id_invalid", prefix="combat.")
        scale = payload.get("scale")
        if scale in ("formation", "battle"):
            return self._aggregate_combat_resolution(command, meta, current_time)
        if payload.get("location_ref") is not None:
            raise CommandRejectedError("combat_exact_location_is_scene_owned")
        parent_combat_ref = payload.get("parent_combat_ref")
        if parent_combat_ref is not None:
            parent_combat_ref = _stable_id(parent_combat_ref, "combat_parent_ref_invalid", prefix="combat.")
        if scale not in ("duel", "skirmish"):
            raise CommandRejectedError("combat_scale_invalid")
        mission_context = self._active_mission_context(
            payload.get("mission_ref"), actor_id=command.actor_id, current_time=current_time
        )
        participant_specs = payload.get("participants")
        objective_specs = payload.get("objectives")
        if not isinstance(participant_specs, Sequence) or isinstance(participant_specs, (str, bytes, bytearray)) or not 2 <= len(participant_specs) <= 16:
            raise CommandRejectedError("combat_participants_invalid")
        if not isinstance(objective_specs, Sequence) or isinstance(objective_specs, (str, bytes, bytearray)) or not 1 <= len(objective_specs) <= 16:
            raise CommandRejectedError("combat_objectives_invalid")

        scene = self._scene_base(current_time)
        encounter_location = scene.get("location_id")
        if not isinstance(encounter_location, str) or not encounter_location:
            raise CommandRejectedError("combat_scene_location_invalid")
        exact_records: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        parsed_specs: Dict[str, Dict[str, Any]] = {}
        side_refs = set()
        for sequence, spec in enumerate(participant_specs):
            if not isinstance(spec, Mapping) or set(spec) != {"actor_ref", "side_ref", "action", "target_refs", "objective_ref", "lethal"}:
                raise CommandRejectedError("combat_participants_invalid")
            actor_ref = _stable_id(spec.get("actor_ref"), "combat_actor_ref_invalid")
            if actor_ref in parsed_specs:
                raise CommandRejectedError("combat_participants_invalid")
            side_ref = _stable_id(spec.get("side_ref"), "combat_side_ref_invalid", prefix="side:")
            action = spec.get("action")
            if action not in ("attack", "capture", "escape", "extract", "disengage", "hold", "secure", "delay"):
                raise CommandRejectedError("combat_intent_invalid")
            target_refs = spec.get("target_refs")
            if not isinstance(target_refs, Sequence) or isinstance(target_refs, (str, bytes, bytearray)) or any(not isinstance(v, str) for v in target_refs) or len(target_refs) != len(set(target_refs)):
                raise CommandRejectedError("combat_intent_invalid")
            objective_ref = spec.get("objective_ref")
            if objective_ref is not None:
                objective_ref = _stable_id(objective_ref, "combat_objective_ref_invalid", prefix="objective:")
            if not isinstance(spec.get("lethal"), bool):
                raise CommandRejectedError("combat_intent_invalid")
            path, record = self._resolve_actor_for_write(actor_ref)
            if record.get("life_status") not in ("active", "alive"):
                raise CommandRejectedError("combat_actor_not_active")
            if record.get("current_location_id") != encounter_location:
                raise CommandRejectedError("combat_participant_not_co_located")
            exact_records[actor_ref] = (path, record)
            parsed_specs[actor_ref] = {**spec, "sequence": sequence, "side_ref": side_ref, "objective_ref": objective_ref}
            side_refs.add(side_ref)
        if len(side_refs) < 2:
            raise CommandRejectedError("combat_sides_invalid")
        actor_refs = set(parsed_specs)
        try:
            zoom_registry = copy.deepcopy(self.repository.read_json(_COMBAT_ZOOM_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("combat_zoom_registry_invalid") from exc
        pending_by_actor = zoom_registry.get("pending_by_actor") if isinstance(zoom_registry, Mapping) else None
        if not isinstance(pending_by_actor, dict):
            raise CommandRejectedError("combat_zoom_registry_invalid")
        pending_parent_refs = {pending_by_actor.get(ref) for ref in actor_refs if ref in pending_by_actor}
        pending_parent_refs.discard(None)
        parent_operation_path: Optional[str] = None
        parent_operation: Optional[Dict[str, Any]] = None
        if pending_parent_refs:
            if len(pending_parent_refs) != 1 or parent_combat_ref not in pending_parent_refs:
                raise CommandRejectedError("combat_pending_zoom_parent_required")
            if any(pending_by_actor.get(ref) != parent_combat_ref for ref in actor_refs):
                raise CommandRejectedError("combat_zoom_participants_not_reserved_together")
            parent_operation_path = self._operation_owner_path(parent_combat_ref)
            try:
                parent_operation = copy.deepcopy(self.repository.read_json(parent_operation_path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("combat_parent_operation_invalid") from exc
            outcome = parent_operation.get("outcome") if isinstance(parent_operation, Mapping) else None
            pending_refs = outcome.get("pending_named_actor_refs") if isinstance(outcome, Mapping) else None
            if (
                parent_operation.get("status") != "awaiting_named_zoom"
                or parent_operation.get("location_id") != encounter_location
                or not isinstance(pending_refs, list)
                or any(ref not in pending_refs for ref in actor_refs)
            ):
                raise CommandRejectedError("combat_parent_operation_invalid")
        elif parent_combat_ref is not None:
            raise CommandRejectedError("combat_parent_has_no_pending_named_actors")
        participant_sides = {ref: spec["side_ref"] for ref, spec in parsed_specs.items()}
        controlled_refs = {command.actor_id} if command.actor_id in parsed_specs else set()
        controlled_sides = {parsed_specs[ref]["side_ref"] for ref in controlled_refs}
        for actor_ref, spec in parsed_specs.items():
            if (
                actor_ref in controlled_refs
                and any(target not in actor_refs or target == actor_ref for target in spec["target_refs"])
            ):
                raise CommandRejectedError("combat_intent_invalid")

        objectives = []
        objective_ids = set()
        objective_kind_by_ref: Dict[str, str] = {}
        objective_ref_by_side: Dict[str, str] = {}
        for spec in objective_specs:
            if not isinstance(spec, Mapping) or set(spec) != {"objective_ref", "side_ref", "kind", "target_refs", "zone_ref", "deadline_tick"}:
                raise CommandRejectedError("combat_objectives_invalid")
            objective_ref = _stable_id(spec.get("objective_ref"), "combat_objective_ref_invalid", prefix="objective:")
            side_ref = _stable_id(spec.get("side_ref"), "combat_side_ref_invalid", prefix="side:")
            if side_ref not in controlled_sides:
                continue
            kind = spec.get("kind")
            targets = spec.get("target_refs")
            zone_ref = spec.get("zone_ref")
            deadline_tick = spec.get("deadline_tick")
            if objective_ref in objective_ids or side_ref not in side_refs or not isinstance(targets, Sequence) or isinstance(targets, (str, bytes, bytearray)) or any(t not in actor_refs for t in targets):
                raise CommandRejectedError("combat_objectives_invalid")
            if kind in ("hold", "secure") and not isinstance(zone_ref, str):
                raise CommandRejectedError("combat_objectives_invalid")
            if zone_ref is not None:
                zone_ref = _stable_id(zone_ref, "combat_zone_ref_invalid")
            if deadline_tick is not None and (isinstance(deadline_tick, bool) or not isinstance(deadline_tick, int) or deadline_tick < 0):
                raise CommandRejectedError("combat_objectives_invalid")
            try:
                objective = CombatObjective(
                    objective_ref=objective_ref, side_ref=side_ref, kind=kind,
                    required_progress=1, current_progress=0, target_refs=tuple(targets),
                    primary=True, deadline_tick=deadline_tick, zone_ref=zone_ref,
                )
                objectives.append(objective)
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("combat_objectives_invalid") from exc
            objective_ids.add(objective_ref)
            objective_kind_by_ref[objective_ref] = objective.kind
            objective_ref_by_side.setdefault(side_ref, objective_ref)

        # Independent NPC sides author their own objective from persistent
        # character state.  The player may observe or oppose that choice, but
        # cannot submit it on the NPC's behalf.
        for side_ref in sorted(side_refs - controlled_sides):
            side_actor_refs = sorted(ref for ref, other_side in participant_sides.items() if other_side == side_ref)
            if not side_actor_refs:
                continue
            lead_ref = side_actor_refs[0]
            _lead_path, lead_record = exact_records[lead_ref]
            kind = self._exact_autonomous_objective_kind(lead_record)
            objective_ref = f"objective:auto:{command.digest[:12]}:{re.sub(r'[^a-z0-9._-]', '_', side_ref.lower())}"
            targets = self._derived_combat_targets(side_ref=side_ref, participant_sides=participant_sides)
            try:
                objective = CombatObjective(
                    objective_ref=objective_ref,
                    side_ref=side_ref,
                    kind=kind,
                    required_progress=1,
                    current_progress=0,
                    target_refs=targets,
                    primary=True,
                    deadline_tick=1 if kind == "delay" else None,
                    zone_ref=encounter_location if kind in ("hold", "secure") else None,
                )
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("combat_objectives_invalid") from exc
            objectives.append(objective)
            objective_ids.add(objective_ref)
            objective_kind_by_ref[objective_ref] = kind
            objective_ref_by_side[side_ref] = objective_ref

        for actor_ref, spec in parsed_specs.items():
            if actor_ref != command.actor_id:
                objective_ref = objective_ref_by_side.get(spec["side_ref"])
                if objective_ref is None:
                    objective_ref = next(
                        (objective.objective_ref for objective in objectives if objective.side_ref == spec["side_ref"]),
                        None,
                    )
                if objective_ref is None:
                    raise CommandRejectedError("combat_objectives_invalid")
                objective_kind = objective_kind_by_ref[objective_ref]
                spec["objective_ref"] = objective_ref
                spec["action"] = self._combat_action_for_objective(objective_kind)
                spec["target_refs"] = list(
                    self._derived_combat_targets(side_ref=spec["side_ref"], participant_sides=participant_sides)
                )
                spec["lethal"] = objective_kind == "eliminate"
            elif spec["objective_ref"] is not None and spec["objective_ref"] not in objective_ids:
                raise CommandRejectedError("combat_intent_invalid")

        zone_ref = encounter_location
        side_team_context = self._combat_side_team_context(parsed_specs)
        special_context = self._special_combat_context()
        participants = []
        engagements = []
        for actor_ref, spec in parsed_specs.items():
            path, record = exact_records[actor_ref]
            resource_pools = []
            chakra = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
            if isinstance(chakra, Mapping) and isinstance(chakra.get("capacity"), int) and isinstance(chakra.get("current"), int):
                resource_pools.append(ResourcePool("chakra", chakra["capacity"], chakra["current"]))
            target_refs = tuple(spec["target_refs"])
            intent = CombatIntent(
                action=spec["action"], objective_ref=spec["objective_ref"], target_refs=target_refs,
                commitment_milli=1000, lethal_force_milli=1000 if spec["lethal"] else 0,
                destination_zone_ref=zone_ref if spec["action"] in ("escape", "extract", "disengage") else None,
            )
            attributes = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
            martial = record.get("martial_skills") if isinstance(record.get("martial_skills"), Mapping) else {}
            operational = record.get("operational_skills") if isinstance(record.get("operational_skills"), Mapping) else {}
            base_capability = self._combat_capability(record)
            capability, special_initiative, special_technique_refs, special_equipment_refs = self._special_exact_combat_capability(
                record, base_capability, special_context
            )
            initiative = max(0, min(200, (int(attributes.get("agility", 0)) + int(attributes.get("awareness", 0)) + int(operational.get("tactics", 0))) // 3 + special_initiative))
            cohesion = 100
            team_context = side_team_context.get(spec["side_ref"])
            if team_context is not None:
                _team, doctrine = team_context
                familiarity = doctrine.get("familiarity") if isinstance(doctrine, Mapping) else None
                practiced = familiarity.get(actor_ref, 0) if isinstance(familiarity, Mapping) else 0
                if isinstance(practiced, int) and not isinstance(practiced, bool):
                    practiced = max(0, min(100, practiced))
                    cohesion = max(75, min(100, 80 + practiced // 5))
                    # Doctrine improves timing/coordination only.  It does not
                    # increase the actor's physical capability profile.
                    initiative = max(0, min(200, initiative + (practiced - 50) // 10))
            participants.append(Participant(
                participant_ref=actor_ref, authoritative_owner_ref=path, side_ref=spec["side_ref"], sequence=spec["sequence"],
                representation="exact", capability=capability, personnel=PersonnelState(total=1, active=1),
                position=PositionState(zone_ref=zone_ref), information=InformationState(observed_refs=tuple(sorted(actor_refs - {actor_ref}))),
                intent=intent, initiative=initiative, readiness=self._combat_readiness(record), morale=100, cohesion=cohesion,
                resources=tuple(resource_pools), effective_range_bands=(0, 1), named_actor_refs=(actor_ref,),
                unusual_technique_refs=special_technique_refs, unusual_equipment_refs=special_equipment_refs,
                detailed_injury_refs=tuple(str(x) for x in record.get("condition", {}).get("injuries", []) if isinstance(x, str)),
            ))
            for target_index, target_ref in enumerate(target_refs):
                engagements.append(Engagement(
                    engagement_ref=f"engagement:{command.digest[:12]}:{spec['sequence']}:{target_index}",
                    actor_ref=actor_ref, target_ref=target_ref, range_band=1, line_of_sight=True, frontage_milli=1000, timing_delay_ms=0,
                ))
        try:
            contract = CombatContract(
                combat_ref=combat_id,
                transaction_ref=("tx.autonomous." if command.mode == "autonomous" else "tx.gameplay.") + command.digest,
                scale=scale,
                participants=tuple(participants), objectives=tuple(objectives), engagements=tuple(engagements),
                terrain=TerrainState(terrain_ref=f"terrain:{zone_ref}", side_modifiers=tuple(SideTerrain(side_ref=s) for s in sorted(side_refs))),
                timing=CombatTiming(current_tick=0, exchange_seconds=6, max_ticks=2),
                rng_stream=f"combat:{combat_id}",
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("combat_contract_invalid") from exc
        world_seed = meta.get("world_seed")
        if not isinstance(world_seed, str) or not world_seed:
            raise CommandRejectedError("campaign_rng_seed_invalid")
        rng = CounterRNG(world_seed=world_seed, transaction_id=contract.transaction_ref, stream=contract.rng_stream)
        for _ in range(required_draw_count(contract)):
            rng.draw_u64()
        effect_plan = resolve_combat(contract, rng.receipts)
        scheduler = self._load_scheduler(current_time=current_time, scene=scene)
        recovery_host_refs: list[str] = []
        service_recovery_refs: list[Mapping[str, Any]] = []
        try:
            exact_population = copy.deepcopy(self.repository.read_json(_POPULATION_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("population_registry_invalid") from exc
        exact_force_writes: Dict[str, Dict[str, Any]] = {}
        exact_team_writes: Dict[str, Dict[str, Any]] = {}
        exact_formation_writes: Dict[str, Dict[str, Any]] = {}

        writes: Dict[str, bytes] = {}
        affected_paths = []
        consequence_refs = []
        dead_exact_refs: list[str] = []
        captured_exact_refs = {
            effect.participant_ref for effect in effect_plan.participant_effects
            if effect.after_personnel.captured
        }
        custody_registry: Optional[Dict[str, Any]] = None
        custody_records: Optional[Dict[str, Any]] = None
        if captured_exact_refs:
            try:
                custody_registry = copy.deepcopy(self.repository.read_json(_CUSTODY_REGISTRY_PATH))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("custody_registry_invalid") from exc
            raw_custody_records = custody_registry.get("records") if isinstance(custody_registry, dict) else None
            if not isinstance(raw_custody_records, dict):
                raise CommandRejectedError("custody_registry_invalid")
            custody_records = raw_custody_records
        pending_custody_refs: list[str] = []
        for effect in effect_plan.participant_effects:
            actor_ref = effect.participant_ref
            path, record = exact_records[actor_ref]
            self._apply_exact_combat_effect(record, effect=effect, combat_id=combat_id, current_time=current_time)
            self._settle_special_exact_combat_state(
                actor_ref=actor_ref,
                record=record,
                duration_seconds=contract.timing.exchange_seconds * contract.timing.max_ticks,
                context=special_context,
            )
            writes[path] = _json_bytes(record)
            affected_paths.append(path)
            after = effect.after_personnel
            if after.killed:
                consequence_refs.append(f"death:{actor_ref}")
                dead_exact_refs.append(actor_ref)
            elif after.incapacitated:
                consequence_refs.append(f"incapacitated:{actor_ref}")
            elif after.wounded:
                consequence_refs.append(f"wounded:{actor_ref}")
            if after.wounded or after.incapacitated:
                host_id = "host.recovery." + actor_ref
                due = current_time.add_seconds(24 * 60 * 60)
                if host_id not in scheduler.hosts:
                    service_recovery = self._reconcile_rostered_person_injury(
                        exact_population,
                        person_ref=actor_ref,
                        force_writes=exact_force_writes,
                        team_writes=exact_team_writes,
                        formation_writes=exact_formation_writes,
                    )
                    host_metadata: Dict[str, Any] = {"person_ref": actor_ref}
                    if service_recovery is not None:
                        host_metadata.update(service_recovery)
                        service_recovery_refs.append(service_recovery)
                    scheduler.add_host(SchedulerHost(
                        state=HostState(
                            host_id=host_id, kind="person_recovery", resolved_through=current_time,
                            safe_through=due.add_seconds(-1), handler_ref="causal.scheduler",
                            rng_namespace="recovery:" + actor_ref, next_due=due,
                        ),
                        authority_kind="person_recovery", owner_ref=path, metadata=host_metadata,
                    ))
                    scheduler.upsert_event(recurring_event(
                        kind="person.recovery.periodic_review", identity=actor_ref, host_id=host_id, due_at=due,
                        recurrence={"kind": "fixed_interval", "interval_seconds": 86400, "accrual_mode": "boundary_only"},
                        payload={"actor_ref": actor_ref, "owner_ref": path}, priority=25,
                        visibility="restricted", requires_player=False,
                    ))
                recovery_host_refs.append(host_id)
            elif after.captured:
                consequence_refs.append(f"captured:{actor_ref}")
                capture_info = self._reconcile_rostered_person_capture(
                    exact_population,
                    person_ref=actor_ref,
                    force_writes=exact_force_writes,
                    team_writes=exact_team_writes,
                    formation_writes=exact_formation_writes,
                )
                # Exact capture creates physical custody immediately, but facility
                # placement remains a separate choice.  Resolve the captor only
                # from an opposing exact actor who actually issued a capture
                # intent against this subject; never invent a custodian.
                capture_candidates = sorted(
                    (int(spec.get("sequence", 0)), ref)
                    for ref, spec in parsed_specs.items()
                    if ref != actor_ref
                    and spec.get("side_ref") != parsed_specs[actor_ref].get("side_ref")
                    and spec.get("action") == "capture"
                    and actor_ref in spec.get("target_refs", ())
                )
                if not capture_candidates or custody_records is None:
                    raise CommandRejectedError("combat_capture_custodian_unresolved")
                captor_ref = capture_candidates[0][1]
                custody_ref = "custody.capture." + hashlib.sha256(
                    f"{combat_id}|{actor_ref}".encode("utf-8")
                ).hexdigest()[:20]
                if custody_ref in custody_records:
                    raise CommandRejectedError("combat_capture_custody_conflict")
                custody_records[custody_ref] = {
                    "id": custody_ref,
                    "subject_kind": "exact",
                    "subject_ref": actor_ref,
                    "force_ref": None if capture_info is None else capture_info.get("force_ref"),
                    "count": 1,
                    "custodian_ref": captor_ref,
                    "place_ref": encounter_location,
                    "status": "captured_pending_placement",
                    "captured_at": str(current_time),
                    "detained_at": None,
                    "source_combat_ref": combat_id,
                    "updated_at": str(current_time),
                    "summary": f"{actor_ref} is in {captor_ref}'s custody after {combat_id}; secure detention placement is unresolved.",
                    "visibility": "restricted",
                }
                pending_custody_refs.append(custody_ref)
                consequence_refs.append(f"custody_pending:{custody_ref}")
            elif after.escaped:
                consequence_refs.append(f"escaped:{actor_ref}")

        if captured_exact_refs:
            # Capture can change the force availability partition even when no
            # death occurs, so persist the population registry whenever the
            # capture reconciliation touched a service member.
            writes[_POPULATION_REGISTRY_PATH] = _json_bytes(exact_population)
            affected_paths.append(_POPULATION_REGISTRY_PATH)
            if custody_registry is not None:
                writes[_CUSTODY_REGISTRY_PATH] = _json_bytes(custody_registry)
                affected_paths.append(_CUSTODY_REGISTRY_PATH)

        exact_death_transfer_ids: list[str] = []
        if dead_exact_refs:
            for actor_ref in dead_exact_refs:
                transfer_id = self._reconcile_rostered_person_death(
                    exact_population, person_ref=actor_ref, at=current_time,
                    command=command, force_writes=exact_force_writes,
                    team_writes=exact_team_writes,
                    formation_writes=exact_formation_writes,
                )
                if transfer_id is not None:
                    exact_death_transfer_ids.append(transfer_id)
            if exact_death_transfer_ids:
                writes[_POPULATION_REGISTRY_PATH] = _json_bytes(exact_population)
                affected_paths.append(_POPULATION_REGISTRY_PATH)
        for force_path, force_record in exact_force_writes.items():
            writes[force_path] = _json_bytes(force_record)
            affected_paths.append(force_path)
        for team_path, team_record in exact_team_writes.items():
            writes[team_path] = _json_bytes(team_record)
            affected_paths.append(team_path)
        for formation_path, formation_record in exact_formation_writes.items():
            writes[formation_path] = _json_bytes(formation_record)
            affected_paths.append(formation_path)

        if parent_operation is not None and parent_operation_path is not None and parent_combat_ref is not None:
            parent_outcome = parent_operation.get("outcome")
            pending_refs = parent_outcome.get("pending_named_actor_refs")
            resolved_refs = parent_outcome.get("resolved_zoom_refs")
            if not isinstance(pending_refs, list) or not isinstance(resolved_refs, list):
                raise CommandRejectedError("combat_parent_operation_invalid")
            actor_set = set(actor_refs)
            parent_outcome["pending_named_actor_refs"] = [ref for ref in pending_refs if ref not in actor_set]
            if combat_id not in resolved_refs:
                resolved_refs.append(combat_id)
            if not parent_outcome["pending_named_actor_refs"]:
                parent_operation["status"] = "resolved"
            for actor_ref in actor_refs:
                pending_by_actor.pop(actor_ref, None)
            writes[parent_operation_path] = _json_bytes(parent_operation)
            affected_paths.append(parent_operation_path)
            writes[_COMBAT_ZOOM_REGISTRY_PATH] = _json_bytes(zoom_registry)
            affected_paths.append(_COMBAT_ZOOM_REGISTRY_PATH)

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind="combat_resolved", at=current_time,
            host_refs=(combat_id,), actor_refs=tuple(sorted(actor_refs)), place_refs=(zone_ref,),
            causal_refs=tuple(ref for ref in (mission_context, parent_combat_ref) if isinstance(ref, str)),
            affected_owner_refs=tuple(sorted(affected_paths)), material_consequence_refs=tuple(consequence_refs) or (f"combat:{combat_id}:exchange",),
            classification="restricted", audience_refs=(command.actor_id,), reducer_ref="shinobi_runtime.combat.resolve_combat",
        )
        parent_completion_event_id = None
        if (
            parent_operation is not None
            and parent_operation_path is not None
            and parent_combat_ref is not None
            and parent_operation.get("status") == "resolved"
        ):
            parent_location = parent_operation.get("location_id")
            completion_places = tuple(sorted({
                ref for ref in (zone_ref, parent_location) if isinstance(ref, str) and ref
            }))
            parent_completion_event_id = self._append_semantic_event(
                world_events, command=command, kind="aggregate_combat_resolved", at=current_time,
                host_refs=(parent_combat_ref,), actor_refs=tuple(sorted(actor_refs)), place_refs=completion_places,
                causal_refs=(combat_id,), affected_owner_refs=(parent_operation_path,),
                material_consequence_refs=(f"combat:{parent_combat_ref}:named_zoom_complete",),
                classification="restricted", audience_refs=(command.actor_id,),
                reducer_ref="shinobi_runtime.combat.named_zoom_reconciliation",
            )
            parent_operation["outcome"]["semantic_event_id"] = parent_completion_event_id
            writes[parent_operation_path] = _json_bytes(parent_operation)
        writes.update(self._world_event_writes(world_events))
        writes[_JINCHURIKI_REGISTRY_PATH] = _json_bytes(special_context["jinchuriki"])
        writes[_SUMMON_REGISTRY_PATH] = _json_bytes(special_context["summons"])
        writes[self.scheduler_path] = _json_bytes(scheduler.to_record())
        writes[self.meta_path] = _json_bytes(self._meta_after(meta, command, world_time=current_time))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        plan_hash = hashlib.sha256(json.dumps(effect_plan.to_record(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("combat write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged_events = overlay.read_json(_WORLD_EVENT_REGISTRY_PATH).get("events", [])
            if not any(isinstance(item, Mapping) and item.get("id") == event_id for item in staged_events):
                raise ValueError("combat semantic event missing")
            staged_scheduler = CausalSchedulerRegistry.from_record(overlay.read_json(self.scheduler_path))
            for host_id in recovery_host_refs:
                if host_id not in staged_scheduler.hosts:
                    raise ValueError("combat injury recovery host missing")
            for effect in effect_plan.participant_effects:
                staged = overlay.read_json(effect.authoritative_owner_ref)
                if staged.get("owner_id") != effect.participant_ref:
                    raise ValueError("combat participant owner identity changed")
                after = effect.after_personnel
                if after.killed and staged.get("life_status") != "dead":
                    raise ValueError("combat death did not persist")
            if exact_death_transfer_ids:
                staged_population = overlay.read_json(_POPULATION_REGISTRY_PATH)
                ids = {row.get("id") for row in staged_population.get("transfers", []) if isinstance(row, Mapping)}
                if any(transfer_id not in ids for transfer_id in exact_death_transfer_ids):
                    raise ValueError("exact combat death population reconciliation missing")
            if pending_custody_refs:
                staged_custody = overlay.read_json(_CUSTODY_REGISTRY_PATH)
                staged_records = staged_custody.get("records") if isinstance(staged_custody, Mapping) else None
                if not isinstance(staged_records, Mapping):
                    raise ValueError("exact combat custody registry missing")
                for custody_ref in pending_custody_refs:
                    row = staged_records.get(custody_ref)
                    if (
                        not isinstance(row, Mapping)
                        or row.get("status") != "captured_pending_placement"
                        or row.get("source_combat_ref") != combat_id
                        or row.get("count") != 1
                    ):
                        raise ValueError("exact combat pending custody missing")
            if parent_operation_path is not None and parent_combat_ref is not None:
                staged_parent = overlay.read_json(parent_operation_path)
                staged_pending = staged_parent.get("outcome", {}).get("pending_named_actor_refs", [])
                if any(ref in staged_pending for ref in actor_refs):
                    raise ValueError("exact combat zoom did not reconcile parent reservation")
                staged_zoom = overlay.read_json(_COMBAT_ZOOM_REGISTRY_PATH).get("pending_by_actor", {})
                if any(ref in staged_zoom for ref in actor_refs):
                    raise ValueError("exact combat zoom reservation not released")

        return _BuiltPlan(
            code="combat_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "combat_id": combat_id, "scale": scale,
                "resolution_mode": effect_plan.resolution_mode, "status": effect_plan.status,
                "victorious_side_refs": list(effect_plan.victorious_side_refs),
                "exchange_effects": [x.to_record() for x in effect_plan.exchange_effects],
                "participant_effects": [x.to_record() for x in effect_plan.participant_effects],
                "objective_effects": [x.to_record() for x in effect_plan.objective_effects],
                "rng_receipts": effect_plan.to_record()["rng_receipts"],
                "effect_plan_sha256": plan_hash, "semantic_event_id": event_id,
                "parent_completion_event_id": parent_completion_event_id,
                "recovery_host_refs": sorted(set(recovery_host_refs)),
                "service_recovery_refs": [dict(item) for item in service_recovery_refs],
                "exact_death_transfer_ids": exact_death_transfer_ids,
                "pending_custody_refs": pending_custody_refs,
                "parent_combat_ref": parent_combat_ref,
            }, validator=validate,
        )
