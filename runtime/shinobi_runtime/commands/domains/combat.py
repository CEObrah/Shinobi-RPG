"""Exact and aggregate combat command domain."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes, _stable_id
from shinobi_runtime.combat.models import (
    BattleKernel, CapabilityProfile, CombatContract, CombatIntent, CombatObjective, CombatTiming,
    Engagement, InformationState, Participant, PersonnelState, PositionState, ResourceCost, ResourcePool, SideTerrain, TerrainState,
)
from shinobi_runtime.combat.resolver import required_draw_count, resolve_combat
from shinobi_runtime.combat.capabilities import project_component, record_field_experience, select_method, weighted_state
from shinobi_runtime.people.profiles import record_rostered_field_evidence
from shinobi_runtime.people.repertoire import field_usable_method_refs, technique_prerequisites_met
from shinobi_runtime.reducers import (
    PopulationPool, PopulationTransfer, apply_personnel_effect, apply_transfer, neutral_proportional_selection,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.sim.scheduler import CausalSchedulerRegistry, SchedulerHost, recurring_event
from shinobi_runtime.sim.hosts import HostState
from shinobi_runtime.sim.rng import CounterRNG
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest
from shinobi_runtime.membership_routes import team_refs_for_assignment, team_refs_for_member


from shinobi_runtime.commands.paths import (
    WORLD_EVENT_REGISTRY_PATH as _WORLD_EVENT_REGISTRY_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    COMBAT_ZOOM_REGISTRY_PATH as _COMBAT_ZOOM_REGISTRY_PATH,
    CUSTODY_REGISTRY_PATH as _CUSTODY_REGISTRY_PATH,
    FORMATION_TENDENCY_PROFILES_PATH as _FORMATION_TENDENCY_PROFILES_PATH,
    FORMATION_RESOLUTION_MECHANICS_PATH as _FORMATION_RESOLUTION_MECHANICS_PATH,
    COMMANDER_PROFILES_PATH as _COMMANDER_PROFILES_PATH,
    DOCTRINE_REGISTRY_PATH as _DOCTRINE_REGISTRY_PATH,
    ROUTES_PATH as _ROUTES_PATH,
    JINCHURIKI_REGISTRY_PATH as _JINCHURIKI_REGISTRY_PATH,
    SUMMON_REGISTRY_PATH as _SUMMON_REGISTRY_PATH,
    INVENTORY_REGISTRY_PATH as _INVENTORY_REGISTRY_PATH,
    ITEM_INDEX_PATH as _ITEM_INDEX_PATH,
    TECHNIQUE_MANIFEST_PATH as _TECHNIQUE_MANIFEST_PATH,
    OCULAR_REGISTRY_PATH as _OCULAR_REGISTRY_PATH,
    DOJUTSU_MECHANICS_PATH as _DOJUTSU_MECHANICS_PATH,
)

_HOUSE_ROSTER_PATH = "state/person-core/house-tang.json"


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

    def _formation_command_channels(
        self, formation: Mapping[str, Any], mechanics: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Project commander quality into timing/control, never troop skill."""
        slot = formation.get("command_slot")
        profile_ref = slot.get("profile_ref") if isinstance(slot, Mapping) else None
        if not isinstance(profile_ref, str):
            return {"profile_ref": None, "control_milli": 1000, "initiative_milli": 1000}
        try:
            registry = self.repository.read_json(_COMMANDER_PROFILES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("commander_profiles_invalid") from exc
        profiles = registry.get("profiles") if isinstance(registry, Mapping) else None
        profile = profiles.get(profile_ref) if isinstance(profiles, Mapping) else None
        if not isinstance(profile, Mapping):
            raise CommandRejectedError("commander_profile_invalid")
        cfg = mechanics.get("command_projection") if isinstance(mechanics, Mapping) else None
        if not isinstance(cfg, Mapping):
            raise CommandRejectedError("formation_command_projection_invalid")
        base = cfg.get("baseline_milli", 850)
        step = cfg.get("score_multiplier_milli", 3)
        ceiling = cfg.get("max_channel_milli", 1200)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (base, step, ceiling)):
            raise CommandRejectedError("formation_command_projection_invalid")
        def score(*keys: str) -> int:
            values = [profile.get(key, 0) for key in keys]
            clean = [int(value) for value in values if isinstance(value, int) and not isinstance(value, bool)]
            return max(0, min(200, sum(clean) // max(1, len(clean))))
        control_score = score("leadership", "tactics", "team_coordination")
        initiative_score = score("tactics", "intelligence", "leadership")
        return {
            "profile_ref": profile_ref,
            "control_milli": max(700, min(ceiling, base + control_score * step)),
            "initiative_milli": max(700, min(ceiling, base + initiative_score * step)),
        }

    def _doctrine_descriptor(self, doctrine_ref: object) -> Tuple[Optional[str], Optional[str]]:
        """Resolve the stable doctrine inheritance chain to institution/role keys."""
        if not isinstance(doctrine_ref, str) or not doctrine_ref:
            return None, None
        try:
            registry = self.repository.read_json(_DOCTRINE_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("doctrine_registry_invalid") from exc
        index = registry.get("record_index") if isinstance(registry, Mapping) else None
        if not isinstance(index, Mapping):
            raise CommandRejectedError("doctrine_registry_invalid")
        current = doctrine_ref
        institution = None
        role = None
        visited: set[str] = set()
        for _ in range(12):
            if current in visited:
                raise CommandRejectedError("doctrine_inheritance_cycle")
            visited.add(current)
            path = index.get(current)
            if not isinstance(path, str):
                break
            try:
                row = self.repository.read_json(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("doctrine_record_invalid") from exc
            doctrine = row.get("doctrine") if isinstance(row, Mapping) else None
            if not isinstance(doctrine, Mapping):
                raise CommandRejectedError("doctrine_record_invalid")
            overlay = doctrine.get("institution_overlay_ref")
            role_ref = doctrine.get("role_profile_ref")
            if institution is None and isinstance(overlay, str) and "#" in overlay:
                institution = overlay.rsplit("#", 1)[1]
            if role is None and isinstance(role_ref, str) and "#" in role_ref:
                role = role_ref.rsplit("#", 1)[1]
            parent = doctrine.get("inherits")
            if not isinstance(parent, str) or not parent:
                break
            current = parent
        return institution, role

    def _location_kind(self, location_ref: object) -> str:
        if not isinstance(location_ref, str) or not location_ref:
            return "default"
        try:
            world = self.repository.read_json(_ROUTES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("world_route_registry_invalid") from exc
        payload = world.get("payload") if isinstance(world, Mapping) else None
        places = payload.get("places") if isinstance(payload, Mapping) else None
        if not isinstance(places, list):
            raise CommandRejectedError("world_route_registry_invalid")
        for place in places:
            if isinstance(place, Mapping) and place.get("id") == location_ref:
                kind = place.get("kind")
                return str(kind) if isinstance(kind, str) and kind else "default"
        return "default"

    def _formation_doctrine_channels(
        self,
        formation: Mapping[str, Any],
        *,
        action: str,
        location_ref: object,
        mechanics: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Keep persistent familiarity distinct from situational doctrine fit."""
        cfg = mechanics.get("doctrine_projection") if isinstance(mechanics, Mapping) else None
        if not isinstance(cfg, Mapping):
            raise CommandRejectedError("formation_doctrine_projection_invalid")
        familiarity = formation.get("doctrine_familiarity", 50)
        if isinstance(familiarity, bool) or not isinstance(familiarity, int):
            raise CommandRejectedError("formation_doctrine_familiarity_invalid")
        familiarity = max(0, min(200, familiarity))
        base = cfg.get("familiarity_baseline_milli", 850)
        step = cfg.get("familiarity_per_point_milli", 2)
        minimum = cfg.get("minimum_milli", 750)
        maximum = cfg.get("maximum_milli", 1150)
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (base, step, minimum, maximum)):
            raise CommandRejectedError("formation_doctrine_projection_invalid")
        familiarity_milli = max(minimum, min(maximum, base + familiarity * step))
        institution, role = self._doctrine_descriptor(formation.get("doctrine_ref"))
        place_kind = self._location_kind(location_ref)
        profiles = cfg.get("institution_profiles")
        fit_milli = 1000
        if institution is not None and isinstance(profiles, Mapping):
            profile = profiles.get(institution)
            if isinstance(profile, Mapping):
                actions = profile.get("preferred_actions", ())
                kinds = profile.get("preferred_place_kinds", ())
                bonus = profile.get("fit_bonus_milli", 0)
                penalty = profile.get("mismatch_penalty_milli", 0)
                if isinstance(bonus, int) and not isinstance(bonus, bool) and action in actions:
                    fit_milli += bonus
                if isinstance(kinds, Sequence) and not isinstance(kinds, (str, bytes, bytearray)) and kinds:
                    if place_kind in kinds and isinstance(bonus, int) and not isinstance(bonus, bool):
                        fit_milli += bonus
                    elif isinstance(penalty, int) and not isinstance(penalty, bool):
                        fit_milli -= penalty
        fit_milli = max(850, min(1150, fit_milli))
        combined = max(minimum, min(maximum, familiarity_milli * fit_milli // 1000))
        return {
            "doctrine_ref": formation.get("doctrine_ref"),
            "institution": institution,
            "role": role,
            "familiarity": familiarity,
            "familiarity_milli": familiarity_milli,
            "suitability_milli": fit_milli,
            "coordination_milli": combined,
            "place_kind": place_kind,
        }

    def _terrain_state_for_location(
        self, *, location_ref: object, side_refs: Sequence[str], mechanics: Mapping[str, Any]
    ) -> TerrainState:
        """Derive deterministic decision-relevant terrain from authoritative location kind."""
        kind = self._location_kind(location_ref)
        profiles = mechanics.get("terrain_profiles_by_place_kind") if isinstance(mechanics, Mapping) else None
        if not isinstance(profiles, Mapping):
            raise CommandRejectedError("formation_terrain_profiles_invalid")
        profile = profiles.get(kind, profiles.get("default"))
        if not isinstance(profile, Mapping):
            raise CommandRejectedError("formation_terrain_profiles_invalid")
        def field(name: str, default: int) -> int:
            value = profile.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise CommandRejectedError("formation_terrain_profile_invalid")
            return value
        modifiers = tuple(
            SideTerrain(
                side_ref=side_ref,
                cover_milli=field("cover_milli", 1000),
                mobility_milli=field("mobility_milli", 1000),
                visibility_milli=field("visibility_milli", 1000),
                hazard_milli=field("hazard_milli", 0),
            )
            for side_ref in sorted(set(side_refs))
        )
        return TerrainState(terrain_ref=f"terrain:{location_ref}:{kind}", side_modifiers=modifiers)

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
        candidate_refs: set[str] = set()
        try:
            for actor_ref in parsed_specs:
                candidate_refs.update(team_refs_for_member(self.repository, actor_ref))
        except ValueError as exc:
            raise CommandRejectedError("membership_routes_invalid") from exc
        candidates: list[Tuple[str, Mapping[str, Any]]] = []
        for team_ref in sorted(candidate_refs):
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
        # Generic attack is the actor's no-special-resource physical baseline.
        # Weapon skill and jutsu output enter only when the corresponding method
        # is actually selected and validated below.
        return CapabilityProfile(
            offense=cls._combat_axis(record, (("attributes", "strength"), ("attributes", "coordination"), ("martial_skills", "unarmed"))),
            defense=cls._combat_axis(record, (("attributes", "toughness"), ("attributes", "agility"), ("martial_skills", "movement"), ("chakra_dimensions", "control"))),
            control=cls._combat_axis(record, (("chakra_dimensions", "control"), ("operational_skills", "tactics"), ("martial_skills", "grappling"))),
            mobility=cls._combat_axis(record, (("attributes", "agility"), ("attributes", "coordination"), ("martial_skills", "movement"))),
            perception=cls._combat_axis(record, (("attributes", "awareness"), ("chakra_dimensions", "sensing"), ("operational_skills", "investigation"))),
            stealth=cls._combat_axis(record, (("martial_skills", "stealth"), ("chakra_dimensions", "suppression"), ("operational_skills", "infiltration"))),
            capture=cls._combat_axis(record, (("martial_skills", "grappling"), ("chakra_dimensions", "control"), ("operational_skills", "traps"))),
            escape=cls._combat_axis(record, (("martial_skills", "movement"), ("attributes", "agility"), ("operational_skills", "survival"))),
            protection=cls._combat_axis(record, (("attributes", "toughness"), ("attributes", "endurance"))),
        )


    def _combat_technique_record(self, technique_ref: str) -> Mapping[str, Any]:
        try:
            manifest = self.repository.read_json(_TECHNIQUE_MANIFEST_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("technique_manifest_invalid") from exc
        routes = manifest.get("techniques") if isinstance(manifest, Mapping) else None
        path = routes.get(technique_ref) if isinstance(routes, Mapping) else None
        if not isinstance(path, str):
            raise CommandRejectedError("combat_technique_unresolved")
        try:
            record = self.repository.read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("combat_technique_unresolved") from exc
        if not isinstance(record, Mapping) or record.get("method_id") != technique_ref:
            raise CommandRejectedError("combat_technique_invalid")
        return record

    def _combat_item_record(self, item_ref: str) -> Mapping[str, Any]:
        try:
            index = self.repository.read_json(_ITEM_INDEX_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("combat_item_index_invalid") from exc
        routes = index.get("items") if isinstance(index, Mapping) else None
        filename = routes.get(item_ref) if isinstance(routes, Mapping) else None
        if not isinstance(filename, str) or "/" in filename or ".." in filename:
            raise CommandRejectedError("combat_item_unresolved")
        try:
            table = self.repository.read_json(f"game/data/items/{filename}")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("combat_item_unresolved") from exc
        record = table.get(item_ref) if isinstance(table, Mapping) else None
        if not isinstance(record, Mapping):
            raise CommandRejectedError("combat_item_unresolved")
        return record

    def _technique_equipment_binding(
        self,
        *,
        technique: Mapping[str, Any],
        holder: Mapping[str, Any],
        requested_weapon_ref: Optional[str],
        record: Mapping[str, Any],
    ) -> Optional[str]:
        """Validate equipment prerequisites and return an implied held weapon when exact.

        Technique data may name a concrete item ID or a deliberately generic
        equipment class. Concrete items are hard custody requirements. Generic
        classes never materialize gear: they must be satisfied by the explicitly
        selected held weapon (or a saved direct-control exception where the
        technique definition explicitly allows it).
        """
        required = technique.get("required_equipment", ())
        if not isinstance(required, Sequence) or isinstance(required, (str, bytes, bytearray)):
            raise CommandRejectedError("combat_technique_equipment_invalid")
        if not required:
            return requested_weapon_ref
        bound = requested_weapon_ref
        for requirement in required:
            if not isinstance(requirement, str) or not requirement:
                raise CommandRejectedError("combat_technique_equipment_invalid")
            # Concrete inventory IDs are exact custody requirements.
            if requirement.startswith(("weapon_", "item_", "armor_")):
                quantity = holder.get(requirement, 0)
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                    raise CommandRejectedError("combat_technique_required_equipment_missing")
                try:
                    item = self._combat_item_record(requirement)
                except CommandRejectedError:
                    item = None
                if isinstance(item, Mapping) and item.get("type") == "weapon":
                    if bound is not None and bound != requirement:
                        raise CommandRejectedError("combat_technique_required_equipment_mismatch")
                    bound = requirement
                continue
            if requirement == "matching_named_or_specialized_equipment":
                physical = technique.get("physical_profile")
                named_item = physical.get("named_item_id") if isinstance(physical, Mapping) else None
                if isinstance(named_item, str):
                    quantity = holder.get(named_item, 0)
                    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                        raise CommandRejectedError("combat_technique_required_equipment_missing")
                    if bound is not None and bound != named_item:
                        raise CommandRejectedError("combat_technique_required_equipment_mismatch")
                    bound = named_item
                    continue
                if bound is None:
                    raise CommandRejectedError("combat_technique_required_equipment_missing")
                quantity = holder.get(bound, 0)
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                    raise CommandRejectedError("combat_technique_required_equipment_missing")
                continue
            if requirement == "chakra_conductive_weapon_or_sufficient_direct_control":
                if bound is not None:
                    quantity = holder.get(bound, 0)
                    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                        raise CommandRejectedError("combat_technique_required_equipment_missing")
                    continue
                control = record.get("chakra_dimensions", {}).get("control") if isinstance(record.get("chakra_dimensions"), Mapping) else None
                if isinstance(control, bool) or not isinstance(control, int) or control < 100:
                    raise CommandRejectedError("combat_technique_required_equipment_missing")
                continue
            # Unknown generic equipment classes are not guessed by the runtime.
            raise CommandRejectedError("combat_technique_required_equipment_unresolved")
        return bound

    @staticmethod
    def _combat_range_bands(maximum_range_m: object) -> Tuple[int, ...]:
        if isinstance(maximum_range_m, bool) or not isinstance(maximum_range_m, (int, float)) or maximum_range_m < 0:
            return (0,)
        bands = [0]
        if maximum_range_m > 3:
            bands.append(1)
        if maximum_range_m > 15:
            bands.append(2)
        if maximum_range_m > 40:
            bands.append(3)
        return tuple(bands)

    @staticmethod
    def _technique_domain_score(record: Mapping[str, Any], technique: Mapping[str, Any]) -> int:
        domains = record.get("domain_proficiencies")
        if not isinstance(domains, Mapping):
            return 0
        domain = technique.get("domain")
        if isinstance(domain, str):
            direct = domains.get(domain)
            if isinstance(direct, int) and not isinstance(direct, bool):
                return max(0, min(200, direct))
            # Compound release labels are allowed to use the strongest exact
            # domain component already present on the person, never a free stat.
            parts = [part for part in re.split(r"[^a-z0-9]+", domain.lower()) if part]
            values = [domains.get(part) for part in parts]
            clean = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
            if clean:
                return max(0, min(200, max(clean)))
        return 0

    @classmethod
    def _technique_method(cls, technique: Mapping[str, Any], weapon: Optional[Mapping[str, Any]]) -> str:
        if weapon is not None:
            skill = weapon.get("skill")
            if isinstance(skill, str) and skill:
                return skill
        domain = str(technique.get("domain", "")).lower()
        function = str(technique.get("function", "")).lower()
        delivery = str(technique.get("delivery", "")).lower()
        combined = " ".join((domain, function, delivery))
        if "genjutsu" in combined or "illusion" in combined:
            return "genjutsu"
        if "medical" in combined or "surgery" in combined or "healing" in combined:
            return "medical"
        if "seal" in combined or "sealing" in combined:
            return "sealing"
        if "sens" in combined or "perception" in combined or "recon" in combined:
            return "sensory"
        if domain in ("physical_or_operational", "taijutsu") and int(technique.get("base_chakra_load", 0) or 0) == 0:
            return "unarmed"
        return "ninjutsu"

    @classmethod
    def _project_exact_method_capability(
        cls,
        record: Mapping[str, Any],
        *,
        base: CapabilityProfile,
        technique: Optional[Mapping[str, Any]] = None,
        mastery: Optional[int] = None,
        weapon: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[CapabilityProfile, str]:
        attrs = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
        martial = record.get("martial_skills") if isinstance(record.get("martial_skills"), Mapping) else {}
        chakra = record.get("chakra_dimensions") if isinstance(record.get("chakra_dimensions"), Mapping) else {}
        operational = record.get("operational_skills") if isinstance(record.get("operational_skills"), Mapping) else {}
        def score(container: Mapping[str, Any], key: str) -> int:
            value = container.get(key, 0)
            return max(0, min(200, value if isinstance(value, int) and not isinstance(value, bool) else 0))
        method = "unarmed"
        if technique is not None:
            method = cls._technique_method(technique, weapon)
        elif weapon is not None and isinstance(weapon.get("skill"), str):
            method = str(weapon["skill"])
        method_skill = score(martial, method)
        if mastery is not None:
            mastery = max(0, min(200, mastery))
            method_skill = (method_skill + mastery) // 2 if method in martial else mastery
        strength = score(attrs, "strength")
        coordination = score(attrs, "coordination")
        agility = score(attrs, "agility")
        awareness = score(attrs, "awareness")
        control = score(chakra, "control")
        output = score(chakra, "output")
        stability = score(chakra, "casting_stability")
        tactics = score(operational, "tactics")
        item_impact = 0
        item_penetration = 0
        item_precision = 0
        item_guard = 0
        if weapon is not None:
            for key, target in (("impact", "impact"), ("penetration", "penetration"), ("precision", "precision"), ("guard", "guard")):
                value = weapon.get(key, 0)
                if isinstance(value, int) and not isinstance(value, bool):
                    if target == "impact": item_impact = max(0, min(20, value))
                    elif target == "penetration": item_penetration = max(0, min(20, value))
                    elif target == "precision": item_precision = max(0, min(20, value))
                    else: item_guard = max(0, min(20, value))
        physical_methods = {"sword", "unarmed", "thrown_tools", "bow", "polearm", "staff", "heavy_weapon"}
        if method in physical_methods:
            offense = (30 * strength + 25 * coordination + 35 * method_skill + 10 * awareness) // 100
            offense = min(200, offense + item_impact * 2 + item_penetration)
            control_axis = min(200, (tactics + coordination + method_skill) // 3 + item_precision)
            defense = min(200, base.defense + item_guard * 2)
        elif method == "genjutsu":
            offense = (35 * method_skill + 25 * control + 20 * stability + 20 * awareness) // 100
            control_axis = (method_skill + control + stability + tactics) // 4
            defense = base.defense
        elif method in ("sensory", "medical", "sealing"):
            offense = (20 * method_skill + 20 * output + 30 * control + 30 * tactics) // 100
            control_axis = (method_skill + control + stability + tactics) // 4
            defense = base.defense
        else:
            offense = (35 * method_skill + 30 * output + 20 * control + 15 * coordination) // 100
            control_axis = (method_skill + control + stability + tactics) // 4
            defense = base.defense
        return CapabilityProfile(
            offense=max(0, min(200, offense)),
            defense=defense,
            control=max(0, min(200, control_axis)),
            mobility=max(base.mobility, agility),
            perception=base.perception,
            stealth=base.stealth,
            capture=max(base.capture, (method_skill + control_axis + coordination) // 3),
            escape=base.escape,
            protection=base.protection,
        ), method

    @staticmethod
    def _technique_chakra_cost(record: Mapping[str, Any], technique: Mapping[str, Any]) -> int:
        chakra = record.get("chakra_dimensions") if isinstance(record.get("chakra_dimensions"), Mapping) else {}
        efficiency = chakra.get("efficiency", 0)
        control = chakra.get("control", 0)
        demand = technique.get("control_demand", 0)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (efficiency, control, demand)):
            raise CommandRejectedError("combat_technique_resource_invalid")
        factor_milli = 1000 + (100 - efficiency) * 4 + max(0, demand - control) * 5 // 2
        factor_milli = max(550, min(1500, factor_milli))
        base = technique.get("base_chakra_load", 0)
        sustained = technique.get("sustained_chakra_per_6s", 0)
        if isinstance(base, bool) or not isinstance(base, int) or base < 0 or isinstance(sustained, bool) or not isinstance(sustained, int) or sustained < 0:
            raise CommandRejectedError("combat_technique_resource_invalid")
        raw = base + sustained
        return 0 if raw == 0 else max(1, (raw * factor_milli + 999) // 1000)

    def _choose_autonomous_exact_method(
        self,
        *,
        actor_ref: str,
        record: Mapping[str, Any],
        action: str,
        range_band: int,
        inventory_holders: Mapping[str, Any],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Choose one lawful saved method for a non-player exact combatant."""
        if action not in ("attack", "capture"):
            return None, None
        repertoire = record.get("repertoire")
        mastery_map = repertoire.get("method_mastery") if isinstance(repertoire, Mapping) else None
        if isinstance(mastery_map, Mapping):
            try:
                usable = field_usable_method_refs(record)
            except ValueError:
                usable = frozenset()
            ranked = sorted(
                ((int(mastery_map.get(ref, 0)), ref) for ref in usable if isinstance(mastery_map.get(ref), int) and not isinstance(mastery_map.get(ref), bool)),
                reverse=True,
            )
            chakra_resource = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
            chakra_current = chakra_resource.get("current", 0) if isinstance(chakra_resource, Mapping) else 0
            for mastery, technique_ref in ranked:
                try:
                    technique = self._combat_technique_record(technique_ref)
                    if not technique_prerequisites_met(record, technique):
                        continue
                    bands = self._combat_range_bands(technique.get("maximum_range_m", 0))
                    if range_band not in bands:
                        continue
                    function = str(technique.get("function", "")).lower()
                    if action == "attack" and not any(token in function for token in ("attack", "strike", "project", "physical", "damage", "control", "restraint")):
                        continue
                    holder = inventory_holders.get(actor_ref)
                    holder = holder if isinstance(holder, Mapping) else {}
                    try:
                        bound_weapon = self._technique_equipment_binding(
                            technique=technique, holder=holder, requested_weapon_ref=None, record=record
                        )
                    except CommandRejectedError:
                        continue
                    cost = self._technique_chakra_cost(record, technique)
                    if isinstance(chakra_current, int) and not isinstance(chakra_current, bool) and chakra_current >= cost:
                        return technique_ref, bound_weapon
                except (CommandRejectedError, ValueError):
                    continue
        holder = inventory_holders.get(actor_ref)
        if isinstance(holder, Mapping):
            candidates = []
            martial = record.get("martial_skills") if isinstance(record.get("martial_skills"), Mapping) else {}
            for item_ref, quantity in holder.items():
                if not isinstance(item_ref, str) or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or not item_ref.startswith("weapon_"):
                    continue
                try:
                    item = self._combat_item_record(item_ref)
                except CommandRejectedError:
                    continue
                bands = self._combat_range_bands(item.get("maximum_range_m", 0))
                if range_band not in bands:
                    continue
                skill = item.get("skill")
                skill_value = martial.get(skill, 0) if isinstance(skill, str) else 0
                if isinstance(skill_value, bool) or not isinstance(skill_value, int):
                    skill_value = 0
                candidates.append((skill_value, int(item.get("impact", 0) or 0), item_ref))
            if candidates:
                candidates.sort(reverse=True)
                return None, candidates[0][2]
        return None, None


    def _prepare_exact_elite_overlay(
        self,
        *,
        actor_ref: str,
        path: str,
        record: Dict[str, Any],
        side_ref: str,
        sequence: int,
        action: str,
        target_refs: Tuple[str, ...],
        objective_ref: Optional[str],
        lethal: bool,
        range_band: int,
        location_id: str,
        observed_refs: Tuple[str, ...],
        inventory_holders: Dict[str, Dict[str, Any]],
        special_context: Mapping[str, Any],
        team_refs: Tuple[str, ...],
        autonomous_method: bool,
        duration_seconds: int,
    ) -> Tuple[Participant, Dict[str, Any], bool]:
        """Build one conserved exact overlay for a mass battle.

        The body remains exactly one person. ``effect_capacity`` describes how
        many anonymous targets the selected lawful method can mechanically
        affect in this exchange; it never changes personnel accounting.
        """
        if record.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("combat_named_actor_not_active")
        if record.get("current_location_id") != location_id:
            raise CommandRejectedError("combat_named_actor_not_at_scene")
        technique_ref: Optional[str] = None
        weapon_ref: Optional[str] = None
        if autonomous_method:
            technique_ref, weapon_ref = self._choose_autonomous_exact_method(
                actor_ref=actor_ref, record=record, action=action, range_band=range_band,
                inventory_holders=inventory_holders,
            )
        technique: Optional[Mapping[str, Any]] = None
        weapon: Optional[Mapping[str, Any]] = None
        mastery: Optional[int] = None
        resource_costs: list[ResourceCost] = []
        effective_range_bands: Tuple[int, ...] = (0, 1)
        effect_capacity = 1
        inventory_changed = False
        holder = inventory_holders.get(actor_ref)
        if not isinstance(holder, dict):
            holder = {}
            inventory_holders[actor_ref] = holder
        if technique_ref is not None:
            technique = self._combat_technique_record(technique_ref)
            try:
                usable = field_usable_method_refs(record)
            except ValueError as exc:
                raise CommandRejectedError("combat_technique_repertoire_invalid") from exc
            if technique_ref not in usable or not technique_prerequisites_met(record, technique):
                raise CommandRejectedError("combat_technique_not_field_usable")
            mastery_map = record.get("repertoire", {}).get("method_mastery") if isinstance(record.get("repertoire"), Mapping) else None
            mastery_value = mastery_map.get(technique_ref) if isinstance(mastery_map, Mapping) else None
            if isinstance(mastery_value, bool) or not isinstance(mastery_value, int):
                raise CommandRejectedError("combat_technique_repertoire_invalid")
            mastery = mastery_value
            weapon_ref = self._technique_equipment_binding(
                technique=technique, holder=holder, requested_weapon_ref=weapon_ref, record=record
            )
            effective_range_bands = self._combat_range_bands(technique.get("maximum_range_m", 0))
            if range_band not in effective_range_bands:
                raise CommandRejectedError("combat_technique_out_of_range")
            chakra_cost = self._technique_chakra_cost(record, technique)
            if chakra_cost:
                chakra_resource = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
                current_chakra = chakra_resource.get("current") if isinstance(chakra_resource, Mapping) else None
                if isinstance(current_chakra, bool) or not isinstance(current_chakra, int) or current_chakra < chakra_cost:
                    raise CommandRejectedError("combat_technique_resource_insufficient")
                resource_costs.append(ResourceCost("chakra", chakra_cost))
            target_limit = technique.get("target_limit", 1)
            if isinstance(target_limit, int) and not isinstance(target_limit, bool):
                effect_capacity = max(1, min(256, target_limit))
        if weapon_ref is not None:
            weapon = self._combat_item_record(weapon_ref)
            if weapon.get("type") != "weapon":
                raise CommandRejectedError("combat_weapon_invalid")
            quantity = holder.get(weapon_ref, 0)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                raise CommandRejectedError("combat_weapon_not_held")
            weapon_bands = self._combat_range_bands(weapon.get("maximum_range_m", 0))
            if technique is None:
                effective_range_bands = weapon_bands
            if range_band not in weapon_bands and technique is None:
                raise CommandRejectedError("combat_weapon_out_of_range")
            projectile_speed = weapon.get("projectile_speed_mps", 0)
            reach = weapon.get("reach_m", 0)
            thrown_use = (
                isinstance(projectile_speed, (int, float)) and not isinstance(projectile_speed, bool) and projectile_speed > 0
                and (range_band > 0 or not isinstance(reach, (int, float)) or reach <= 0)
            )
            if thrown_use:
                holder[weapon_ref] = quantity - 1
                inventory_changed = True
            if weapon.get("skill") == "bow":
                arrows = holder.get("item_arrow", 0)
                if isinstance(arrows, bool) or not isinstance(arrows, int) or arrows <= 0:
                    raise CommandRejectedError("combat_ammunition_insufficient")
                holder["item_arrow"] = arrows - 1
                inventory_changed = True

        base = self._combat_capability(record)
        projected, method = self._project_exact_method_capability(
            record, base=base, technique=technique, mastery=mastery, weapon=weapon
        )
        ocular_capability, ocular_initiative, ocular_passive_cost, ocular_meta = self._ocular_combat_projection(
            actor_ref=actor_ref, record=record, capability=projected, method=method, technique=technique,
            requested_eye_refs=None, autonomous=autonomous_method, duration_seconds=duration_seconds,
            technique_chakra_cost=sum(cost.amount for cost in resource_costs if cost.resource_ref == "chakra"),
        )
        if ocular_passive_cost:
            existing_chakra_cost = sum(cost.amount for cost in resource_costs if cost.resource_ref == "chakra")
            resource_costs = [cost for cost in resource_costs if cost.resource_ref != "chakra"]
            combined_chakra_cost = existing_chakra_cost + ocular_passive_cost
            chakra_resource = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
            current_chakra = chakra_resource.get("current") if isinstance(chakra_resource, Mapping) else None
            if isinstance(current_chakra, bool) or not isinstance(current_chakra, int) or current_chakra < combined_chakra_cost:
                raise CommandRejectedError("combat_technique_resource_insufficient")
            resource_costs.append(ResourceCost("chakra", combined_chakra_cost))
        capability, special_initiative, special_techniques, special_equipment = self._special_exact_combat_capability(
            record, ocular_capability, special_context
        )
        attrs = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
        operational = record.get("operational_skills") if isinstance(record.get("operational_skills"), Mapping) else {}
        initiative = max(0, min(200, (int(attrs.get("agility", 0)) + int(attrs.get("awareness", 0)) + int(operational.get("tactics", 0))) // 3 + special_initiative + ocular_initiative))
        cohesion = 85
        for team_ref in team_refs:
            try:
                _team_path, team = self._exact_team(team_ref)
            except CommandRejectedError:
                continue
            doctrine_ref = team.get("doctrine_ref")
            if not isinstance(doctrine_ref, str):
                continue
            try:
                _dp, _dd, doctrine = self._resolve_covered_owner_view(doctrine_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                continue
            familiarity = doctrine.get("familiarity") if isinstance(doctrine, Mapping) else None
            practiced = familiarity.get(actor_ref, 0) if isinstance(familiarity, Mapping) else 0
            if isinstance(practiced, int) and not isinstance(practiced, bool):
                cohesion = max(cohesion, max(75, min(100, 80 + max(0, min(100, practiced)) // 5)))

        resource_pools: list[ResourcePool] = []
        chakra = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
        if isinstance(chakra, Mapping) and isinstance(chakra.get("capacity"), int) and isinstance(chakra.get("current"), int):
            resource_pools.append(ResourcePool("chakra", chakra["capacity"], chakra["current"]))
        intent = CombatIntent(
            action=action, objective_ref=objective_ref, target_refs=target_refs,
            commitment_milli=1000, lethal_force_milli=1000 if lethal else 0,
            resource_costs=tuple(resource_costs),
        )
        participant = Participant(
            participant_ref=actor_ref, authoritative_owner_ref=path, side_ref=side_ref, sequence=sequence,
            representation="exact", capability=capability, personnel=PersonnelState(total=1, active=1),
            position=PositionState(zone_ref=location_id), information=InformationState(observed_refs=observed_refs),
            intent=intent, initiative=initiative, readiness=self._combat_readiness(record), morale=100, cohesion=cohesion,
            resources=tuple(resource_pools), effective_range_bands=effective_range_bands, named_actor_refs=(actor_ref,),
            unusual_technique_refs=tuple(dict.fromkeys(((technique_ref,) if technique_ref else ()) + tuple(special_techniques))),
            unusual_equipment_refs=tuple(dict.fromkeys(((weapon_ref,) if weapon_ref else ()) + tuple(special_equipment))),
            detailed_injury_refs=tuple(str(x) for x in record.get("condition", {}).get("injuries", []) if isinstance(x, str)),
            effect_capacity=effect_capacity,
        )
        return participant, {
            "actor_ref": actor_ref, "parent_side_ref": side_ref, "method": method,
            "technique_ref": technique_ref, "weapon_ref": weapon_ref,
            "chakra_cost": sum(cost.amount for cost in resource_costs),
            "effect_capacity": effect_capacity, "team_refs": list(team_refs),
            "player_action_delegated": autonomous_method,
            "dojutsu": ocular_meta,
        }, inventory_changed


    @staticmethod
    def _persist_exact_combat_resources(record: Dict[str, Any], effect: Any) -> None:
        """Persist resolver-owned resource pools back into the exact actor.

        Combat resolution has always carried authoritative ``after_resources``
        in the participant effect.  Exact-character persistence must copy those
        values back into the character owner or chakra-consuming techniques can
        resolve successfully without actually spending chakra.
        """
        resources = record.get("resources")
        if not isinstance(resources, dict):
            raise CommandRejectedError("combat_actor_resources_invalid")
        for pool in effect.after_resources:
            resource_ref = pool.resource_ref
            row = resources.get(resource_ref)
            if not isinstance(row, dict):
                raise CommandRejectedError("combat_actor_resources_invalid")
            capacity = row.get("capacity")
            current = row.get("current")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (capacity, current)):
                raise CommandRejectedError("combat_actor_resources_invalid")
            if capacity != pool.capacity or not 0 <= pool.current <= capacity:
                raise CommandRejectedError("combat_actor_resources_invalid")
            row["current"] = pool.current

    def _ocular_owner_eyes(self, actor_ref: str) -> Tuple[Mapping[str, Any], ...]:
        """Return exact ocular records physically owned by one actor."""
        try:
            registry = self.repository.read_json(_OCULAR_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("ocular_registry_invalid") from exc
        owner_index = registry.get("owner_index") if isinstance(registry, Mapping) else None
        path = owner_index.get(actor_ref) if isinstance(owner_index, Mapping) else None
        if path is None:
            return ()
        if not isinstance(path, str):
            raise CommandRejectedError("ocular_registry_invalid")
        try:
            shard = self.repository.read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("ocular_owner_shard_invalid") from exc
        eyes = shard.get("eyes") if isinstance(shard, Mapping) else None
        if not isinstance(eyes, list):
            raise CommandRejectedError("ocular_owner_shard_invalid")
        result = []
        for eye in eyes:
            if not isinstance(eye, Mapping):
                raise CommandRejectedError("ocular_owner_shard_invalid")
            if eye.get("current_owner_id") == actor_ref:
                result.append(eye)
        return tuple(result)

    @staticmethod
    def _functional_socket_eye(actor_ref: str, eye: Mapping[str, Any]) -> bool:
        side = eye.get("side")
        if side not in ("left", "right"):
            return False
        return (
            eye.get("current_owner_id") == actor_ref
            and eye.get("current_location") == f"{actor_ref}:{side}_eye_socket"
            and eye.get("preservation_state") == "living"
            and eye.get("condition") == "functional"
            and isinstance(eye.get("integration"), int)
            and not isinstance(eye.get("integration"), bool)
            and isinstance(eye.get("vision_integrity"), int)
            and not isinstance(eye.get("vision_integrity"), bool)
        )

    @staticmethod
    def _sharingan_hardware_features(eye: Mapping[str, Any], mechanics: Mapping[str, Any]) -> set[str]:
        hardware = str(eye.get("hardware_stage", "")).lower()
        rows = mechanics.get("sharingan_hardware") if isinstance(mechanics, Mapping) else None
        if not isinstance(rows, Mapping):
            raise CommandRejectedError("dojutsu_mechanics_invalid")
        if "mangekyo" in hardware or "3_tomoe" in hardware:
            key = "3_tomoe"
        elif "2_tomoe" in hardware:
            key = "2_tomoe"
        elif "1_tomoe" in hardware:
            key = "1_tomoe"
        else:
            return set()
        features = rows.get(key)
        if not isinstance(features, list) or any(not isinstance(row, str) for row in features):
            raise CommandRejectedError("dojutsu_mechanics_invalid")
        normalized = set(features)
        if "basic_motion_perception" in normalized:
            normalized.add("motion_perception")
        if "early_predictive_tracking" in normalized:
            normalized.add("predictive_tracking")
        return normalized

    @staticmethod
    def _effective_ocular_mastery(feature_mastery: int, integration: int, ocular_control: int) -> int:
        """Integer form of feature_mastery*sqrt(integration*control)/200."""
        import math
        feature = max(0, min(200, feature_mastery))
        integration = max(0, min(200, integration))
        ocular_control = max(0, min(200, ocular_control))
        root_milli = math.isqrt(integration * ocular_control * 1_000_000)
        return max(0, min(200, (feature * root_milli + 100_000) // 200_000))

    def _resolve_active_combat_eyes(
        self,
        *,
        actor_ref: str,
        record: Mapping[str, Any],
        requested_eye_refs: Optional[Sequence[str]],
        technique: Optional[Mapping[str, Any]],
        autonomous: bool,
    ) -> Tuple[Mapping[str, Any], ...]:
        """Select lawful combat-active eyes without inventing activation state.

        Transplanted Sharingan in a living eye socket remain mechanically active
        because the registered bloodline rule says normal deactivation is not
        available without a special mechanism.  Native dōjutsu require an
        explicit player activation, a selected dōjutsu technique, or autonomous
        NPC combat policy.  Latent/not-field-usable eyes are never activated.
        """
        all_eyes = [eye for eye in self._ocular_owner_eyes(actor_ref) if self._functional_socket_eye(actor_ref, eye)]
        by_id = {str(eye.get("eye_id")): eye for eye in all_eyes if isinstance(eye.get("eye_id"), str)}
        if requested_eye_refs is not None:
            if isinstance(requested_eye_refs, (str, bytes, bytearray)):
                raise CommandRejectedError("combat_active_eye_refs_invalid")
            refs = list(requested_eye_refs)
            if not 1 <= len(refs) <= 8 or any(not isinstance(ref, str) or ref not in by_id for ref in refs) or len(set(refs)) != len(refs):
                raise CommandRejectedError("combat_active_eye_refs_invalid")
            selected = [by_id[ref] for ref in refs]
            for eye in selected:
                activation = str(eye.get("activation_state", "")).lower()
                if "latent_not_field_usable" in activation or activation in {"unimplanted", "unavailable", "consumed", "blind"}:
                    raise CommandRejectedError("combat_active_eye_not_field_usable")
        else:
            selected = []

        technique_domain = str(technique.get("domain", "")).lower() if isinstance(technique, Mapping) else ""
        technique_ref = str(technique.get("method_id", "")).lower() if isinstance(technique, Mapping) else ""
        technique_is_dojutsu = "dojutsu" in technique_domain or any(token in technique_ref for token in ("sharingan", "byakugan", "kamui", "tsukuyomi", "amaterasu", "izanagi"))

        for eye in all_eyes:
            activation = str(eye.get("activation_state", "")).lower()
            if "latent_not_field_usable" in activation or activation in {"unimplanted", "unavailable", "consumed", "blind"}:
                continue
            eye_type = str(eye.get("dojutsu_type", "")).lower()
            transplanted_sharingan = "sharingan" in eye_type and eye.get("native_to_current_owner") is False
            already_active = any(token in activation for token in ("standard_3_tomoe", "active", "activated"))
            if transplanted_sharingan or already_active or autonomous or technique_is_dojutsu:
                if eye not in selected:
                    selected.append(eye)

        # Eye-specific abilities bind to the eye that actually owns the ability.
        if technique_ref:
            matching = [eye for eye in all_eyes if technique_ref in set(eye.get("unique_ability_refs", ())) or technique_ref == str(eye.get("eye_id", ""))]
            if matching:
                selected = matching
            elif technique_ref == "sharingan_left_eye":
                selected = [eye for eye in all_eyes if eye.get("side") == "left" and "sharingan" in str(eye.get("dojutsu_type", "")).lower()]

        return tuple(sorted(selected, key=lambda eye: (str(eye.get("side")), str(eye.get("eye_id")))))

    @staticmethod
    def _ocular_active_cost_penalty(
        *,
        technique: Optional[Mapping[str, Any]],
        active_eyes: Sequence[Mapping[str, Any]],
        ocular_control: int,
        registered_cost: int,
    ) -> Tuple[int, Optional[str]]:
        """Apply the registered transplant inefficiency formula when one eye is causal.

        The runtime does not guess how a generic multi-eye activation divides its
        registered cost.  It applies transplant inefficiency only when the
        technique is unambiguously bound to one exact eye, or when a future
        technique explicitly declares an ocular-pair requirement.
        """
        if not isinstance(technique, Mapping) or registered_cost <= 0:
            return registered_cost, None
        technique_ref = technique.get("method_id")
        physical = technique.get("physical_profile")
        relevant: list[Mapping[str, Any]] = []
        if isinstance(technique_ref, str):
            relevant = [eye for eye in active_eyes if technique_ref in set(eye.get("unique_ability_refs", ()))]
            if technique_ref == "sharingan_left_eye":
                relevant = [eye for eye in active_eyes if eye.get("side") == "left" and "sharingan" in str(eye.get("dojutsu_type", "")).lower()]
            elif technique_ref == "sharingan" and len([eye for eye in active_eyes if "sharingan" in str(eye.get("dojutsu_type", "")).lower()]) == 1:
                relevant = [eye for eye in active_eyes if "sharingan" in str(eye.get("dojutsu_type", "")).lower()]
            elif technique_ref == "byakugan" and len([eye for eye in active_eyes if "byakugan" in str(eye.get("dojutsu_type", "")).lower()]) == 1:
                relevant = [eye for eye in active_eyes if "byakugan" in str(eye.get("dojutsu_type", "")).lower()]
        pair_required = isinstance(physical, Mapping) and physical.get("requires_ocular_pair") is True
        if pair_required:
            left = next((eye for eye in active_eyes if eye.get("side") == "left"), None)
            right = next((eye for eye in active_eyes if eye.get("side") == "right"), None)
            if left is None or right is None:
                raise CommandRejectedError("combat_dojutsu_pair_required")
            li = max(0, min(200, int(left.get("integration", 0))))
            ri = max(0, min(200, int(right.get("integration", 0))))
            if li + ri <= 0:
                raise CommandRejectedError("combat_dojutsu_pair_integration_invalid")
            pair_integration = (2 * li * ri + (li + ri) // 2) // (li + ri)
            if left.get("native_to_current_owner") is False or right.get("native_to_current_owner") is False:
                relevant_integration = pair_integration
                relevant_ref = f"{left.get('eye_id')}+{right.get('eye_id')}"
            else:
                return registered_cost, None
        elif len(relevant) == 1 and relevant[0].get("native_to_current_owner") is False:
            value = relevant[0].get("integration")
            if isinstance(value, bool) or not isinstance(value, int):
                raise CommandRejectedError("ocular_eye_state_invalid")
            relevant_integration = max(0, min(200, value))
            relevant_ref = str(relevant[0].get("eye_id"))
        else:
            return registered_cost, None
        control = max(0, min(200, ocular_control))
        # 1 + (200-integration)/400 + (200-control)/400.
        factor_numer = 400 + (200 - relevant_integration) + (200 - control)
        adjusted = max(1, (registered_cost * factor_numer + 399) // 400)
        return adjusted, relevant_ref

    def _ocular_passive_chakra_cost(
        self,
        *,
        record: Dict[str, Any],
        active_eyes: Sequence[Mapping[str, Any]],
        duration_seconds: int,
    ) -> Tuple[int, int]:
        """Accrue registered non-native Sharingan passive drain per exact eye.

        Fixed-point milli-chakra preserves the rule that mastery can approach
        zero continuously and reaches exactly zero at Integration 200 + Ocular
        Control 200, without rounding every low drain event up to one chakra.
        """
        state = record.get("dojutsu_state")
        if not isinstance(state, dict):
            return 0, 0
        control = state.get("ocular_control", 0)
        if isinstance(control, bool) or not isinstance(control, int):
            raise CommandRejectedError("dojutsu_state_invalid")
        try:
            sharingan = self._combat_technique_record("sharingan")
        except CommandRejectedError:
            sharingan = {}
        base_per_6s = sharingan.get("sustained_chakra_per_6s", 0) if isinstance(sharingan, Mapping) else 0
        if isinstance(base_per_6s, bool) or not isinstance(base_per_6s, int) or base_per_6s < 0:
            raise CommandRejectedError("dojutsu_mechanics_invalid")
        ticks = max(1, (duration_seconds + 5) // 6)
        added_milli = 0
        for eye in active_eyes:
            eye_type = str(eye.get("dojutsu_type", "")).lower()
            if "sharingan" not in eye_type or eye.get("native_to_current_owner") is not False:
                continue
            integration = eye.get("integration")
            if isinstance(integration, bool) or not isinstance(integration, int):
                raise CommandRejectedError("ocular_eye_state_invalid")
            delta = max(0, 400 - max(0, min(200, integration)) - max(0, min(200, control)))
            # base * ticks * (delta/400)^2 in milli-chakra.
            added_milli += (base_per_6s * ticks * delta * delta * 1000 + 80_000) // 160_000
        accumulator = state.get("passive_drain_milli_accumulator", 0)
        if isinstance(accumulator, bool) or not isinstance(accumulator, int) or accumulator < 0:
            raise CommandRejectedError("dojutsu_state_invalid")
        total = accumulator + added_milli
        spend, remainder = divmod(total, 1000)
        state["passive_drain_milli_accumulator"] = remainder
        return spend, added_milli

    def _ocular_combat_projection(
        self,
        *,
        actor_ref: str,
        record: Dict[str, Any],
        capability: CapabilityProfile,
        method: str,
        technique: Optional[Mapping[str, Any]],
        requested_eye_refs: Optional[Sequence[str]],
        autonomous: bool,
        duration_seconds: int,
        technique_chakra_cost: int = 0,
    ) -> Tuple[CapabilityProfile, int, int, Mapping[str, Any]]:
        """Project exact per-eye dōjutsu state into combat axes.

        Every active eye is resolved separately and its lawful visual channel is
        accumulated into the combat projection.  Two functional eyes therefore
        cannot collapse to the same projection as one otherwise-identical eye:
        each eye contributes from its own hardware, integration and vision
        integrity.  This is deliberately not an arbitrary flat "two-eye bonus".
        Pair integration is a different mechanic and is used only when an
        ability explicitly requires the two eyes to function as a pair.
        """
        state = record.get("dojutsu_state")
        if not isinstance(state, Mapping):
            return capability, 0, 0, {"active_eye_refs": [], "eye_contributions": []}
        control = state.get("ocular_control", 0)
        feature_mastery = state.get("feature_mastery")
        if isinstance(control, bool) or not isinstance(control, int) or not isinstance(feature_mastery, Mapping):
            raise CommandRejectedError("dojutsu_state_invalid")
        try:
            mechanics = self.repository.read_json(_DOJUTSU_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("dojutsu_mechanics_invalid") from exc
        active_eyes = self._resolve_active_combat_eyes(
            actor_ref=actor_ref, record=record, requested_eye_refs=requested_eye_refs,
            technique=technique, autonomous=autonomous,
        )
        values = capability.to_record()
        initiative_bonus = 0
        contributions: list[Dict[str, Any]] = []
        for eye in active_eyes:
            eye_ref = str(eye.get("eye_id"))
            integration = eye.get("integration")
            vision = eye.get("vision_integrity")
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (integration, vision)):
                raise CommandRejectedError("ocular_eye_state_invalid")
            integration = max(0, min(200, integration))
            vision = max(0, min(200, vision))
            eye_type = str(eye.get("dojutsu_type", "")).lower()
            row: Dict[str, Any] = {
                "eye_ref": eye_ref,
                "side": eye.get("side"),
                "dojutsu_type": eye.get("dojutsu_type"),
                "hardware_stage": eye.get("hardware_stage"),
                "integration": integration,
                "vision_integrity": vision,
                "native_to_current_owner": eye.get("native_to_current_owner"),
            }
            if "sharingan" in eye_type:
                hardware_features = self._sharingan_hardware_features(eye, mechanics)
                motion_bonus = 0
                predictive_bonus = 0
                if "motion_perception" in hardware_features:
                    mastery = feature_mastery.get("motion_perception", 0)
                    if isinstance(mastery, bool) or not isinstance(mastery, int):
                        raise CommandRejectedError("dojutsu_state_invalid")
                    effective = self._effective_ocular_mastery(mastery, integration, control)
                    motion_bonus = (effective * vision + 1000) // 2000
                    values["perception"] = min(200, values["perception"] + motion_bonus)
                if "predictive_tracking" in hardware_features:
                    mastery = feature_mastery.get("predictive_tracking", 0)
                    if isinstance(mastery, bool) or not isinstance(mastery, int):
                        raise CommandRejectedError("dojutsu_state_invalid")
                    effective = self._effective_ocular_mastery(mastery, integration, control)
                    predictive_bonus = min(30, (effective * vision * 15 + 10_000) // 20_000)
                    values["defense"] = min(200, values["defense"] + predictive_bonus)
                    initiative_bonus += predictive_bonus
                row.update({"motion_perception_bonus": motion_bonus, "predictive_tracking_bonus": predictive_bonus})
            elif "byakugan" in eye_type:
                perception_bonus = 0
                tenketsu_bonus = 0
                mastery = feature_mastery.get("chakra_network_resolution", 0)
                if isinstance(mastery, bool) or not isinstance(mastery, int):
                    raise CommandRejectedError("dojutsu_state_invalid")
                effective = self._effective_ocular_mastery(mastery, integration, control)
                perception_bonus = (effective * 15 + 50) // 100
                values["perception"] = min(200, values["perception"] + perception_bonus)
                if method == "unarmed":
                    mastery = feature_mastery.get("tenketsu_identification", 0)
                    if isinstance(mastery, bool) or not isinstance(mastery, int):
                        raise CommandRejectedError("dojutsu_state_invalid")
                    effective = self._effective_ocular_mastery(mastery, integration, control)
                    tenketsu_bonus = (effective * 15 + 50) // 100
                    values["control"] = min(200, values["control"] + tenketsu_bonus)
                    values["capture"] = min(200, values["capture"] + tenketsu_bonus)
                row.update({"chakra_resolution_bonus": perception_bonus, "tenketsu_precision_bonus": tenketsu_bonus})
            contributions.append(row)

        pair_integration = None
        left = next((eye for eye in active_eyes if eye.get("side") == "left"), None)
        right = next((eye for eye in active_eyes if eye.get("side") == "right"), None)
        if left is not None and right is not None:
            li = max(0, min(200, int(left.get("integration", 0))))
            ri = max(0, min(200, int(right.get("integration", 0))))
            if li + ri > 0:
                pair_integration = (2 * li * ri + (li + ri) // 2) // (li + ri)
        physical = technique.get("physical_profile") if isinstance(technique, Mapping) else None
        if isinstance(physical, Mapping) and physical.get("requires_ocular_pair") is True:
            if left is None or right is None or pair_integration is None:
                raise CommandRejectedError("combat_dojutsu_pair_required")

        adjusted_active_cost, active_cost_eye_ref = self._ocular_active_cost_penalty(
            technique=technique, active_eyes=active_eyes, ocular_control=control,
            registered_cost=max(0, technique_chakra_cost),
        )
        active_cost_delta = max(0, adjusted_active_cost - max(0, technique_chakra_cost))
        passive_cost, passive_milli = self._ocular_passive_chakra_cost(
            record=record, active_eyes=active_eyes, duration_seconds=duration_seconds,
        )
        return CapabilityProfile(**values), max(0, min(80, initiative_bonus)), passive_cost + active_cost_delta, {
            "active_eye_refs": [str(eye.get("eye_id")) for eye in active_eyes],
            "eye_contributions": contributions,
            "pair_integration": pair_integration,
            "passive_drain_milli_accrued": passive_milli,
            "passive_chakra_cost": passive_cost,
            "registered_active_chakra_cost": max(0, technique_chakra_cost),
            "adjusted_active_chakra_cost": adjusted_active_cost,
            "active_transplant_cost_eye_ref": active_cost_eye_ref,
        }


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


    def _embedded_exact_members_for_formation(
        self, formation_id: str
    ) -> Tuple[Tuple[str, ...], Dict[str, Tuple[str, ...]]]:
        """Discover exact bodies embedded in a formation from team authority.

        The formation stores aggregate headcount, while exact-team state owns
        identity overlays.  Callers therefore never decide whether an elite
        member is present for accounting purposes.  Multiple social teams may
        reference the same person, but all operational assignments must agree
        on this formation.
        """
        by_actor: Dict[str, set[str]] = {}
        try:
            assigned_team_refs = team_refs_for_assignment(self.repository, formation_id)
        except ValueError as exc:
            raise CommandRejectedError("membership_routes_invalid") from exc
        for team_ref in assigned_team_refs:
            try:
                _team_path, team = self._exact_team(team_ref)
            except CommandRejectedError:
                continue
            if team.get("status") != "active" or team.get("current_assignment_ref") != formation_id:
                continue
            embedded = team.get("embedded_member_refs", [])
            if not isinstance(embedded, list) or any(not isinstance(ref, str) for ref in embedded):
                raise CommandRejectedError("team_invalid")
            for actor_ref in embedded:
                by_actor.setdefault(actor_ref, set()).add(team_ref)
        actors = tuple(sorted(by_actor))
        return actors, {actor_ref: tuple(sorted(refs)) for actor_ref, refs in by_actor.items()}


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


    def _prepare_aggregate_combat_supply(
        self,
        *,
        formation: Mapping[str, Any],
        action: str,
        range_band: int,
        aggregate_count: int,
        stock: MutableMapping[str, Any],
        mechanics: Mapping[str, Any],
        exchanges: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Project and consume aggregate expendables for the methods actually used.

        This keeps mass combat scalable without granting anonymous soldiers
        infinite arrows, kunai, shuriken, tags, wire, sealing media, or medical
        kits.  Persistent component skill remains unchanged; shortage only
        limits how much equipment-dependent method proficiency can be expressed
        in this combat.
        """
        adjusted = copy.deepcopy(dict(formation))
        cfg = mechanics.get("aggregate_consumables") if isinstance(mechanics, Mapping) else None
        if not isinstance(cfg, Mapping):
            raise CommandRejectedError("aggregate_consumable_mechanics_invalid")
        action_rates = cfg.get("action_usage_milli")
        method_usage = cfg.get("method_usage_per_person_per_exchange_milli")
        sustainment = cfg.get("sustainment")
        if not isinstance(action_rates, Mapping) or not isinstance(method_usage, Mapping) or not isinstance(sustainment, Mapping):
            raise CommandRejectedError("aggregate_consumable_mechanics_invalid")
        action_milli = action_rates.get(action, 500)
        if isinstance(action_milli, bool) or not isinstance(action_milli, int) or action_milli < 0:
            raise CommandRejectedError("aggregate_consumable_mechanics_invalid")
        if aggregate_count <= 0:
            return adjusted, {
                "aggregate_count": 0,
                "selected_method_counts": {},
                "demand": {},
                "consumed": {},
                "method_supply_milli": {},
                "sustainment_supply_milli": 1000,
                "sustainment_state": "supported",
            }
        components = adjusted.get("components")
        if not isinstance(components, list):
            raise CommandRejectedError("aggregate_component_capability_missing")
        component_rows = []
        component_total = 0
        for component in components:
            if not isinstance(component, Mapping):
                raise CommandRejectedError("aggregate_component_capability_missing")
            count = component.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise CommandRejectedError("aggregate_component_capability_missing")
            if count > 0:
                component_rows.append(component)
                component_total += count
        if component_total <= 0:
            raise CommandRejectedError("aggregate_component_capability_missing")

        # Partition the anonymous committed slice proportionally across saved
        # components while preserving exactly aggregate_count bodies.
        allocations: Dict[str, int] = {}
        remainders = []
        allocated = 0
        for index, component in enumerate(component_rows):
            count = int(component["count"])
            numerator = count * aggregate_count
            share = numerator // component_total
            remainder = numerator % component_total
            key = str(component.get("id") or f"component:{index}")
            allocations[key] = share
            allocated += share
            remainders.append((remainder, -index, key))
        for _remainder, _neg_index, key in sorted(remainders, reverse=True)[: max(0, aggregate_count - allocated)]:
            allocations[key] += 1

        selected_by_component: Dict[str, str] = {}
        method_counts: Dict[str, int] = {}
        method_item_demand: Dict[str, Dict[str, int]] = {}
        exchanges = max(1, int(exchanges))
        for index, component in enumerate(component_rows):
            key = str(component.get("id") or f"component:{index}")
            represented = allocations.get(key, 0)
            if represented <= 0:
                continue
            state = component.get("capability_state")
            if not isinstance(state, Mapping):
                raise CommandRejectedError("aggregate_component_capability_missing")
            try:
                method, _score = select_method(
                    state, role=str(component.get("role") or "assault"), range_band=range_band, mechanics=mechanics
                )
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("aggregate_component_capability_invalid") from exc
            selected_by_component[key] = method
            method_counts[method] = method_counts.get(method, 0) + represented
            usage = method_usage.get(method)
            if not isinstance(usage, Mapping):
                continue
            demands = method_item_demand.setdefault(method, {})
            for item_key, rate_milli in usage.items():
                if not isinstance(item_key, str) or isinstance(rate_milli, bool) or not isinstance(rate_milli, int) or rate_milli < 0:
                    raise CommandRejectedError("aggregate_consumable_mechanics_invalid")
                if rate_milli <= 0:
                    continue
                numerator = represented * exchanges * rate_milli * action_milli
                quantity = (numerator + 999_999) // 1_000_000
                if quantity > 0:
                    demands[item_key] = demands.get(item_key, 0) + quantity

        total_demand: Dict[str, int] = {}
        method_supply_milli: Dict[str, int] = {}
        for method, demands in method_item_demand.items():
            ratio = 1000
            for item_key, demand in demands.items():
                available = stock.get(item_key)
                if isinstance(available, bool) or not isinstance(available, int) or available < 0:
                    raise CommandRejectedError("aggregate_combat_supply_stock_invalid")
                total_demand[item_key] = total_demand.get(item_key, 0) + demand
                if demand > 0:
                    ratio = min(ratio, min(1000, available * 1000 // demand))
            method_supply_milli[method] = ratio

        # Sustained fighting also consumes conserved food and water from the
        # same force stock.  This is intentionally aggregate and bounded: we do
        # not create one ration record per soldier, but we also do not let an
        # army fight indefinitely on a route-status label while its real depot
        # is empty.
        ration_rate = sustainment.get("rations_person_days_per_battle_day_milli")
        water_rate = sustainment.get("water_liters_per_person_day_milli")
        exchange_seconds = sustainment.get("exchange_seconds")
        coverage_bands = sustainment.get("coverage_bands_milli")
        if (
            isinstance(ration_rate, bool) or not isinstance(ration_rate, int) or ration_rate < 0
            or isinstance(water_rate, bool) or not isinstance(water_rate, int) or water_rate < 0
            or isinstance(exchange_seconds, bool) or not isinstance(exchange_seconds, int) or exchange_seconds <= 0
            or not isinstance(coverage_bands, Mapping)
        ):
            raise CommandRejectedError("aggregate_consumable_mechanics_invalid")
        battle_seconds = exchanges * exchange_seconds
        person_days_milli = (
            aggregate_count * battle_seconds * 1000 + (24 * 60 * 60) - 1
        ) // (24 * 60 * 60)
        sustainment_demand = {
            "rations_days": (person_days_milli * ration_rate + 999_999) // 1_000_000,
            "water_liters": (person_days_milli * water_rate + 999_999) // 1_000_000,
        }
        sustainment_ratio = 1000
        for item_key, demand in sustainment_demand.items():
            if demand <= 0:
                continue
            available = stock.get(item_key)
            if isinstance(available, bool) or not isinstance(available, int) or available < 0:
                raise CommandRejectedError("aggregate_combat_supply_stock_invalid")
            sustainment_ratio = min(sustainment_ratio, min(1000, available * 1000 // demand))
            total_demand[item_key] = total_demand.get(item_key, 0) + demand
        band_values = {}
        for name in ("supported", "strained", "critical", "cut_off"):
            raw = coverage_bands.get(name)
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > 1000:
                raise CommandRejectedError("aggregate_consumable_mechanics_invalid")
            band_values[name] = raw
        if sustainment_ratio >= band_values["supported"]:
            sustainment_state = "supported"
        elif sustainment_ratio >= band_values["strained"]:
            sustainment_state = "strained"
        elif sustainment_ratio >= band_values["critical"]:
            sustainment_state = "critical"
        else:
            sustainment_state = "cut_off"

        # Shortage is an expression constraint, not a permanent skill loss.
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                continue
            key = str(component.get("id") or f"component:{index}")
            method = selected_by_component.get(key)
            if method is None:
                continue
            ratio = method_supply_milli.get(method, 1000)
            state = component.get("capability_state")
            if not isinstance(state, dict):
                continue
            if ratio <= 0:
                methods = state.get("equipment_methods")
                if isinstance(methods, list) and method in methods:
                    state["equipment_methods"] = [value for value in methods if value != method]
            elif ratio < 1000:
                readiness = state.get("equipment_readiness_milli", 1000)
                if isinstance(readiness, bool) or not isinstance(readiness, int):
                    raise CommandRejectedError("aggregate_component_capability_invalid")
                state["equipment_readiness_milli"] = max(0, min(1000, readiness * ratio // 1000))

        consumed: Dict[str, int] = {}
        for item_key, demand in sorted(total_demand.items()):
            available = stock.get(item_key)
            if isinstance(available, bool) or not isinstance(available, int) or available < 0:
                raise CommandRejectedError("aggregate_combat_supply_stock_invalid")
            quantity = min(available, demand)
            if quantity > 0:
                stock[item_key] = available - quantity
                consumed[item_key] = quantity
        return adjusted, {
            "aggregate_count": aggregate_count,
            "selected_method_counts": dict(sorted(method_counts.items())),
            "demand": dict(sorted(total_demand.items())),
            "consumed": dict(sorted(consumed.items())),
            "method_supply_milli": dict(sorted(method_supply_milli.items())),
            "sustainment_supply_milli": sustainment_ratio,
            "sustainment_state": sustainment_state,
        }


    def _formation_aggregate_capability(
        self,
        *,
        formation: Mapping[str, Any],
        force: Mapping[str, Any],
        action: str = "attack",
        range_band: int = 1,
    ) -> Tuple[CapabilityProfile, CapabilityProfile, int, int, int, int, Tuple[str, ...], str]:
        """Project the *current formation-local* component distributions.

        Manpower capability records are intake/reference baselines.  Once people
        are deployed, each component owns persistent fundamentals, method
        proficiency, veterancy and development evidence.  Method proficiency is
        selected by role/range and never added as a universal bonus.
        """
        components = formation.get("components")
        if not isinstance(components, list) or not components:
            raise CommandRejectedError("aggregate_capability_invalid")
        try:
            mechanics = self.repository.read_json(_FORMATION_RESOLUTION_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("aggregate_capability_invalid") from exc
        frontage = mechanics.get("frontage_weight_milli") if isinstance(mechanics, Mapping) else None
        if not isinstance(frontage, Mapping):
            raise CommandRejectedError("aggregate_capability_invalid")

        axis_totals = {key: 0 for key in CapabilityProfile(0,0,0,0,0,0,0,0,0).to_record()}
        spread_totals = dict(axis_totals)
        initiative_total = 0
        projection_weight = 0
        actual_component_count = 0
        source_refs: list[str] = []
        method_audit: list[tuple[str, str, int]] = []
        teamwork_total = 0
        endurance_total = 0
        experience_total = 0

        for component in components:
            if not isinstance(component, Mapping):
                raise CommandRejectedError("aggregate_capability_invalid")
            count = component.get("count")
            state = component.get("capability_state")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0 or not isinstance(state, Mapping):
                raise CommandRejectedError("aggregate_component_capability_missing")
            actual_component_count += count
            if count <= 0:
                continue
            role = str(component.get("role") or "assault")
            try:
                profile, spread, initiative, method = project_component(
                    state, role=role, action=action, range_band=range_band, mechanics=mechanics
                )
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("aggregate_component_capability_invalid") from exc
            weight_milli = frontage.get(role, 600)
            if isinstance(weight_milli, bool) or not isinstance(weight_milli, int) or weight_milli <= 0:
                raise CommandRejectedError("aggregate_component_frontage_invalid")
            weight = count * weight_milli
            for key, value in profile.to_record().items():
                axis_totals[key] += value * weight
            for key, value in spread.to_record().items():
                spread_totals[key] += value * weight
            initiative_total += initiative * weight
            projection_weight += weight
            fundamentals = state.get("fundamentals")
            if isinstance(fundamentals, Mapping):
                teamwork_total += int(fundamentals.get("team_coordination", 0)) * count
                endurance_total += int(fundamentals.get("endurance", 0)) * count
            experience_total += int(state.get("experience", 0)) * count
            source_ref = state.get("source_capability_ref")
            if isinstance(source_ref, str):
                source_refs.append(source_ref)
            method_audit.append((str(component.get("id")), method, count))

        command = formation.get("command_personnel")
        command_count = command.get("count", 0) if isinstance(command, Mapping) else 0
        if isinstance(command_count, bool) or not isinstance(command_count, int) or command_count < 0:
            raise CommandRejectedError("aggregate_capability_invalid")
        formation_total = formation.get("personnel_total")
        if (
            not isinstance(formation_total, int) or isinstance(formation_total, bool)
            or actual_component_count + command_count != formation_total
            or formation_total <= 0 or projection_weight <= 0
        ):
            raise CommandRejectedError("aggregate_capability_headcount_mismatch")

        mean = CapabilityProfile(**{
            key: max(0, min(200, (value + projection_weight // 2) // projection_weight))
            for key, value in axis_totals.items()
        })
        spread = CapabilityProfile(**{
            key: max(1, min(50, (value + projection_weight // 2) // projection_weight))
            for key, value in spread_totals.items()
        })
        component_count = max(1, actual_component_count)
        average_teamwork = max(1, min(200, teamwork_total // component_count))
        average_endurance = max(1, min(200, endurance_total // component_count))
        average_experience = max(0, min(200, experience_total // component_count))
        readiness = max(1, min(200, (average_endurance + average_experience + average_teamwork) // 3))
        cohesion = max(1, min(200, (average_teamwork * 2 + average_experience) // 3))
        initiative = max(1, min(200, initiative_total // projection_weight))
        morale = max(1, min(200, int(formation.get("morale", 75)) if isinstance(formation.get("morale"), int) else 75))
        digest_payload = json.dumps(
            {"components": components, "action": action, "range_band": range_band, "methods": method_audit},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        source_digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
        return mean, spread, readiness, morale, cohesion, initiative, tuple(sorted(set(source_refs))), source_digest


    @staticmethod
    def _embedded_generic_capability(embedded: Mapping[str, Any]) -> Dict[str, Any]:
        components = embedded.get("capability_components_before")
        if not isinstance(components, list):
            raise CommandRejectedError("formation_capability_state_invalid")
        rows = []
        for component in components:
            if not isinstance(component, Mapping):
                continue
            count = component.get("count")
            state = component.get("capability_state")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0 and isinstance(state, Mapping):
                rows.append((state, count))
        if not rows:
            raise CommandRejectedError("formation_capability_state_invalid")
        return weighted_state(rows)


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
                source_class = "deployed"
            else:
                source_class = None
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
                            and assignment.get("source_owner") == linked_force_ref
                            and isinstance(assignment.get("raw_allocations"), list)
                            and person_ref in assignment.get("raw_allocations", [])
                        ):
                            candidate = assignment.get("source_availability_class")
                            if isinstance(candidate, str):
                                source_class = candidate
                                break
                if source_class is None:
                    source_class = next((
                        key for key in (
                            "ready_24h", "mobilizable_7d", "mobilizable_30d",
                            "training_or_instruction", "essential_fixed_duty",
                            "medical_or_recovery", "captured_or_missing",
                        )
                        if isinstance(availability.get(key), int)
                        and not isinstance(availability.get(key), bool)
                        and availability.get(key, 0) > 0
                    ), None)
            if not isinstance(source_class, str):
                raise CommandRejectedError("population_linked_force_no_personnel_slot")
            current_slot = availability.get(source_class)
            if isinstance(current_slot, bool) or not isinstance(current_slot, int) or current_slot <= 0:
                raise CommandRejectedError("population_linked_force_no_personnel_slot")
            if source_class != "deployed":
                self._reserve_draw(force, source_class, 1)
            availability[source_class] = current_slot - 1
            force["total"] = int(force.get("total", 0)) - 1
            self._validate_reserve_counts(force)
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
        if source_class == "deployed":
            if embedded is None:
                raise CommandRejectedError("population_linked_force_no_personnel_slot")
            service_capability = self._embedded_generic_capability(embedded)
        else:
            service_capability = self._reserve_draw(force, source_class, 1)
        availability[source_class] = current - 1
        availability["medical_or_recovery"] = medical + 1
        self._reserve_add(force, "medical_or_recovery", service_capability, 1)
        self._validate_reserve_counts(force)
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
        if source_class == "deployed":
            if embedded is None:
                raise CommandRejectedError("population_linked_force_no_personnel_slot")
            service_capability = self._embedded_generic_capability(embedded)
        else:
            service_capability = self._reserve_draw(force, source_class, 1)
        availability[source_class] = current - 1
        availability["captured_or_missing"] = captured + 1
        self._reserve_add(force, "captured_or_missing", service_capability, 1)
        self._validate_reserve_counts(force)
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
        range_band = payload.get("range_band", 1)
        if isinstance(range_band, bool) or not isinstance(range_band, int) or not 0 <= range_band <= 3:
            raise CommandRejectedError("combat_range_band_invalid")
        line_of_sight = payload.get("line_of_sight", True)
        if not isinstance(line_of_sight, bool):
            raise CommandRejectedError("combat_line_of_sight_invalid")

        owner_cache = _OwnerResolutionCache()
        parsed: Dict[str, Dict[str, Any]] = {}
        force_records: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        formation_records: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        formation_registry_records: Dict[str, Dict[str, Any]] = {}
        allocation_by_force_availability: Dict[Tuple[str, str], int] = {}
        allocation_by_assignment: Dict[str, int] = {}
        side_refs = set()
        aggregate_actor_refs = set()
        required_participant_fields = {
            "participant_ref", "committed_count", "side_ref", "action", "target_refs",
            "objective_ref", "lethal", "command_authority_ref",
        }
        allowed_participant_fields = required_participant_fields | {"named_actor_refs"}
        for sequence, spec in enumerate(specs):
            if (
                not isinstance(spec, Mapping)
                or not required_participant_fields.issubset(set(spec))
                or set(spec) - allowed_participant_fields
            ):
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
            declared_named = spec.get("named_actor_refs", ())
            if (
                not isinstance(declared_named, Sequence)
                or isinstance(declared_named, (str, bytes, bytearray))
                or any(not isinstance(x, str) for x in declared_named)
                or len(declared_named) != len(set(declared_named))
                or len(declared_named) > 32
            ):
                raise CommandRejectedError("combat_named_actor_refs_invalid")
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

            authoritative_named, authoritative_team_refs = self._embedded_exact_members_for_formation(formation_id)
            if any(ref not in authoritative_named for ref in declared_named):
                raise CommandRejectedError("combat_named_actor_not_embedded")
            named = authoritative_named
            if len(named) > 32:
                raise CommandRejectedError("combat_named_actor_refs_invalid")
            if len(named) > committed:
                raise CommandRejectedError("combat_named_actor_count_exceeds_commitment")
            if named and len(named) == committed:
                raise CommandRejectedError("combat_fully_named_slice_requires_exact_resolution")
            if any(ref in pending_by_actor for ref in named):
                raise CommandRejectedError("combat_named_actor_already_pending_zoom")

            # Exact named actors may be woken inside an aggregate formation, but
            # must remain exact subsets of this formation and be physically at
            # the same battle location.
            named_actor_team_refs: Dict[str, Tuple[str, ...]] = dict(authoritative_team_refs)
            named_actor_capabilities: Dict[str, Dict[str, int]] = {}
            named_actor_records: Dict[str, Dict[str, Any]] = {}
            for ref in named:
                try:
                    person_path, person_view = self._resolve_actor_for_write(ref)
                except CommandRejectedError as exc:
                    raise CommandRejectedError("combat_named_actor_unresolved") from exc
                if not isinstance(person_view, Mapping) or person_view.get("schema") != "shinobi_character":
                    raise CommandRejectedError("combat_named_actor_requires_exact_materialization")
                actor_location = person_view.get("current_location_id")
                if actor_location != location_id:
                    raise CommandRejectedError("combat_named_actor_not_at_scene")
                assigned_formation_ref, assigned_team_refs = self._exact_team_assignment_for_person(ref)
                if assigned_formation_ref != formation_id:
                    raise CommandRejectedError("combat_named_actor_team_assignment_conflict")
                if tuple(sorted(assigned_team_refs)) != tuple(sorted(named_actor_team_refs.get(ref, ()))):
                    raise CommandRejectedError("combat_named_actor_team_assignment_conflict")
                named_actor_capabilities[ref] = self._combat_capability(person_view).to_record()
                named_actor_records[ref] = {"path": person_path, "record": copy.deepcopy(dict(person_view))}
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
                "named_actor_records": named_actor_records,
                "authority_basis": authority_decision.basis,
                "force_path": fpath,
                "formation_path": formation_path,
            }
            aggregate_actor_refs.add(participant_ref)
            side_refs.add(side_ref)
        if len(side_refs) < 2:
            raise CommandRejectedError("combat_sides_invalid")
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

        elite_exact_records: Dict[str, Dict[str, Any]] = {}
        elite_parent_by_actor: Dict[str, str] = {}
        for parent_ref, item in parsed.items():
            for actor_ref, actor_row in item.get("named_actor_records", {}).items():
                if actor_ref in elite_exact_records:
                    raise CommandRejectedError("combat_named_actor_multiple_formations")
                elite_exact_records[actor_ref] = actor_row
                elite_parent_by_actor[actor_ref] = parent_ref
        elite_actor_refs = tuple(sorted(elite_exact_records))
        all_combat_refs = tuple(sorted(set(aggregate_actor_refs).union(elite_actor_refs)))
        try:
            combat_inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_registry_invalid") from exc
        inventory_holders = combat_inventory.get("holders") if isinstance(combat_inventory, dict) else None
        if not isinstance(inventory_holders, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        special_context = self._special_combat_context()
        inventory_changed = False
        elite_overlay_meta: Dict[str, Dict[str, Any]] = {}

        aggregate_stock_cache: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        aggregate_stock_changed: set[str] = set()
        for force_ref in sorted(force_records):
            stock_ref = "stock." + force_ref.replace(".", "_")
            try:
                stock_path, stock, stock_owner = self._stock_record(stock_ref)
            except CommandRejectedError as exc:
                if force_ref.startswith("force.civil.") and str(exc) == "inventory_stock_unresolved":
                    continue
                raise
            if stock_owner != force_ref or stock.get("schema") != "shinobi-stock":
                raise CommandRejectedError("aggregate_combat_supply_stock_invalid")
            aggregate_stock_cache[force_ref] = (stock_path, stock)

        # Prepare aggregate supply in stable formation-ID order so the same
        # authoritative state cannot produce different ammunition allocation
        # merely because a caller reorders participant rows.  The queue is
        # still bounded to the formations in this combat transaction.
        aggregate_supply_projection: Dict[str, Tuple[Mapping[str, Any], Optional[Dict[str, Any]]]] = {}
        mechanics_record = self.repository.read_json(_FORMATION_RESOLUTION_MECHANICS_PATH)
        for ref in sorted(parsed):
            item = parsed[ref]
            _formation_path, formation = formation_records[ref]
            combat_formation: Mapping[str, Any] = formation
            aggregate_supply = None
            stock_entry = aggregate_stock_cache.get(item["force_ref"])
            aggregate_count_for_supply = item["committed_count"] - len(item["named_actor_refs"])
            if stock_entry is not None and aggregate_count_for_supply > 0:
                stock_path, stock = stock_entry
                combat_formation, aggregate_supply = self._prepare_aggregate_combat_supply(
                    formation=formation, action=item["action"], range_band=range_band,
                    aggregate_count=aggregate_count_for_supply, stock=stock, mechanics=mechanics_record,
                    exchanges=2,
                )
                if aggregate_supply.get("consumed"):
                    stock["last_reconciled_at"] = str(current_time)
                    aggregate_stock_changed.add(stock_path)
            item["supply_stock_consumption"] = aggregate_supply
            aggregate_supply_projection[ref] = (combat_formation, aggregate_supply)

        participants = []
        engagements = []
        for ref, item in parsed.items():
            formation_path, formation = formation_records[ref]
            force = force_records[item["force_ref"]][1]
            combat_formation, aggregate_supply = aggregate_supply_projection[ref]
            mean, spread, readiness, morale, cohesion, initiative, capability_source_refs, digest = self._formation_aggregate_capability(
                formation=combat_formation, force=force, action=item["action"], range_band=range_band
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
            if isinstance(aggregate_supply, Mapping):
                stock_state = aggregate_supply.get("sustainment_state")
                order = {"supported": 0, "strained": 1, "critical": 2, "cut_off": 3}
                if stock_state in order and order[str(stock_state)] > order[supply_state]:
                    supply_state = str(stock_state)
            supply_channels = {
                "supported": {"readiness": 1000, "offense": 1000, "control": 1000, "mobility": 1000, "protection": 1000},
                "strained": {"readiness": 970, "offense": 980, "control": 985, "mobility": 960, "protection": 990},
                "critical": {"readiness": 900, "offense": 920, "control": 940, "mobility": 860, "protection": 930},
                "cut_off": {"readiness": 780, "offense": 840, "control": 870, "mobility": 760, "protection": 860},
            }[supply_state]
            readiness = max(1, min(200, readiness * supply_channels["readiness"] // 1000))
            mean_record = mean.to_record()
            for axis in ("offense", "control", "mobility", "protection"):
                mean_record[axis] = max(0, min(200, mean_record[axis] * supply_channels[axis] // 1000))
            command_channels = self._formation_command_channels(formation, mechanics=self.repository.read_json(_FORMATION_RESOLUTION_MECHANICS_PATH))
            doctrine_channels = self._formation_doctrine_channels(
                formation, action=item["action"], location_ref=location_id,
                mechanics=self.repository.read_json(_FORMATION_RESOLUTION_MECHANICS_PATH),
            )
            mean_record["control"] = max(0, min(200, mean_record["control"] * command_channels["control_milli"] // 1000))
            mean_record["control"] = max(0, min(200, mean_record["control"] * doctrine_channels["coordination_milli"] // 1000))
            initiative = max(1, min(200, initiative * command_channels["initiative_milli"] // 1000))
            initiative = max(1, min(200, initiative * doctrine_channels["coordination_milli"] // 1000))
            if fortification_milli > 0:
                defense_bonus = min(30, fortification_milli * 30 // 1000)
                mean_record["defense"] = min(200, mean_record["defense"] + defense_bonus)
                mean_record["protection"] = min(200, mean_record["protection"] + defense_bonus)
            mean = CapabilityProfile(**mean_record)
            item["supply_state"] = supply_state
            item["supply_channels_milli"] = supply_channels
            item["fortification_milli"] = fortification_milli
            item["command_channels"] = dict(command_channels)
            item["doctrine_channels"] = dict(doctrine_channels)
            # Readiness, morale and cohesion remain first-class participant state
            # consumed by the resolver.  They are deliberately not multiplied
            # back into underlying troop capability a second time.  Likewise,
            # exact/named people are never averaged into the anonymous kernel.
            named_capability_records = item.get("named_actor_capabilities", {})
            formation_digest = hashlib.sha256(
                json.dumps(
                    {
                        "formation": formation,
                        "action": item["action"],
                        "range_band": range_band,
                        "supply_channels_milli": item["supply_channels_milli"],
                        "command_channels": item["command_channels"],
                        "doctrine_channels": item["doctrine_channels"],
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
                information=InformationState(observed_refs=tuple(x for x in all_combat_refs if x != ref)),
                intent=CombatIntent(
                    action=item["action"], objective_ref=item["objective_ref"],
                    target_refs=tuple(dict.fromkeys((*item["target_refs"], *(actor for actor in elite_actor_refs if parsed[elite_parent_by_actor[actor]]["side_ref"] != item["side_ref"])))),
                    commitment_milli=1000, lethal_force_milli=1000 if item["lethal"] else 0,
                ),
                initiative=initiative, readiness=readiness, morale=morale, cohesion=cohesion,
                effective_range_bands=(range_band,),
                kernel=BattleKernel(source_ref=f"{formation_path}#{formation.get('id')}", source_sha256=formation_digest, mean=mean, spread=spread),
                named_actor_refs=item["named_actor_refs"],
            )
            participants.append(participant)
            for index, target in enumerate(item["target_refs"]):
                engagements.append(Engagement(engagement_ref=f"engagement:{command.digest[:12]}:{item['sequence']}:{index}", actor_ref=ref, target_ref=target, range_band=range_band, line_of_sight=line_of_sight, frontage_milli=1000))

        # Named people are separate exact participants. Their bodies were
        # already subtracted from the anonymous formation participant above.
        # NPC elites may select one lawful saved method under their standing
        # formation order; the authenticated player is never auto-puppeted.
        for elite_index, actor_ref in enumerate(elite_actor_refs):
            parent_ref = elite_parent_by_actor[actor_ref]
            parent = parsed[parent_ref]
            actor_row = elite_exact_records[actor_ref]
            actor_path = actor_row["path"]
            actor_record = actor_row["record"]
            autonomous_method = command.mode == "autonomous" or actor_ref != command.actor_id
            elite_action = parent["action"] if autonomous_method else "hold"
            elite_targets = tuple(parent["target_refs"]) if elite_action in ("attack", "capture") else ()
            participant, overlay_meta, changed = self._prepare_exact_elite_overlay(
                actor_ref=actor_ref, path=actor_path, record=actor_record, side_ref=parent["side_ref"],
                sequence=1000 + elite_index, action=elite_action, target_refs=elite_targets,
                objective_ref=parent["objective_ref"], lethal=bool(parent["lethal"] and autonomous_method),
                range_band=range_band, location_id=location_id,
                observed_refs=tuple(x for x in all_combat_refs if x != actor_ref),
                inventory_holders=inventory_holders, special_context=special_context,
                team_refs=tuple(parent["named_actor_team_refs"].get(actor_ref, ())),
                autonomous_method=autonomous_method, duration_seconds=120,
            )
            participants.append(participant)
            elite_overlay_meta[actor_ref] = {**overlay_meta, "parent_formation_participant_ref": parent_ref}
            inventory_changed = inventory_changed or changed
            if elite_action in ("attack", "capture") and elite_targets:
                target = elite_targets[0]
                engagements.append(Engagement(
                    engagement_ref=f"engagement:{command.digest[:12]}:elite:{elite_index}:out",
                    actor_ref=actor_ref, target_ref=target, range_band=range_band,
                    line_of_sight=line_of_sight, frontage_milli=1000,
                ))
            # One opposing aggregate element gets a bounded counter-engagement
            # against this exact body. This creates personal risk without making
            # the whole enemy formation collapse into an exact duel.
            counter = next((
                (other_ref, other) for other_ref, other in sorted(parsed.items(), key=lambda row: row[1]["sequence"])
                if other["side_ref"] != parent["side_ref"] and other["action"] in ("attack", "capture")
            ), None)
            if counter is not None:
                counter_ref, _counter_item = counter
                engagements.append(Engagement(
                    engagement_ref=f"engagement:{command.digest[:12]}:elite:{elite_index}:counter",
                    actor_ref=counter_ref, target_ref=actor_ref, range_band=range_band,
                    line_of_sight=line_of_sight, frontage_milli=350,
                ))
        try:
            contract = CombatContract(
                combat_ref=combat_id,
                transaction_ref=("tx.autonomous." if command.mode == "autonomous" else "tx.gameplay.") + command.digest,
                scale=scale,
                participants=tuple(participants), objectives=tuple(objectives), engagements=tuple(engagements),
                terrain=self._terrain_state_for_location(
                    location_ref=location_id, side_refs=tuple(sorted(side_refs)),
                    mechanics=self.repository.read_json(_FORMATION_RESOLUTION_MECHANICS_PATH),
                ),
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
            formation_path, formation = formation_records[ref]
            formation_capability_before = self._formation_capability_composition(formation)
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
            medical_count = after.wounded + after.incapacitated
            availability["medical_or_recovery"] += medical_count
            availability["captured_or_missing"] += after.captured
            if medical_count:
                self._reserve_add(force, "medical_or_recovery", formation_capability_before, medical_count)
            if after.captured:
                self._reserve_add(force, "captured_or_missing", formation_capability_before, after.captured)

            prior_formation_total = formation.get("personnel_total")
            if isinstance(prior_formation_total, bool) or not isinstance(prior_formation_total, int):
                raise CommandRejectedError("formation_strength_invalid")
            roster_losses = after.killed + after.wounded + after.incapacitated + after.captured
            new_formation_total = prior_formation_total - roster_losses
            if new_formation_total < 0:
                raise CommandRejectedError("formation_strength_invalid")
            self._resize_formation_strength(formation, new_formation_total)
            if aggregate_resolved_count > 0 and new_formation_total > 0:
                for component in formation.get("components", ()):
                    if not isinstance(component, dict):
                        continue
                    state = component.get("capability_state")
                    count = component.get("count")
                    if isinstance(state, Mapping) and isinstance(count, int) and not isinstance(count, bool) and count > 0:
                        component["capability_state"] = record_field_experience(
                            state, event_ref=combat_id, exchanges=1
                        )
            if aggregate_resolved_count > 0 and roster_losses > 0 and new_formation_total > 0:
                loss_milli = min(1000, roster_losses * 1000 // aggregate_resolved_count)
                equipment_cfg = self.repository.read_json(_FORMATION_RESOLUTION_MECHANICS_PATH).get("equipment_projection", {})
                decay_divisor = equipment_cfg.get("battle_loss_decay_divisor", 5) if isinstance(equipment_cfg, Mapping) else 5
                floor_milli = equipment_cfg.get("minimum_readiness_milli", 250) if isinstance(equipment_cfg, Mapping) else 250
                if isinstance(decay_divisor, bool) or not isinstance(decay_divisor, int) or decay_divisor <= 0:
                    decay_divisor = 5
                if isinstance(floor_milli, bool) or not isinstance(floor_milli, int):
                    floor_milli = 250
                for component in formation.get("components", ()):
                    state = component.get("capability_state") if isinstance(component, Mapping) else None
                    if isinstance(state, dict):
                        current_equipment = state.get("equipment_readiness_milli", 1000)
                        if isinstance(current_equipment, int) and not isinstance(current_equipment, bool):
                            state["equipment_readiness_milli"] = max(floor_milli, current_equipment - max(1, loss_milli // decay_divisor))
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
            self._validate_reserve_counts(force)
            if force["total"] < 0 or any(not isinstance(v, int) or v < 0 for v in availability.values()) or sum(availability.values()) != force["total"]:
                raise CommandRejectedError("combat_force_reconciliation_invalid")
            operation_participants.append({
                "participant_ref": ref,
                "force_ref": item["force_ref"],
                "population_pool_id": force.get("population_pool_id"),
                "capability_source_refs": list(item.get("capability_source_refs", ())),
                "supply_state": item.get("supply_state", "supported"),
                "supply_channels_milli": dict(item.get("supply_channels_milli", {})),
                "supply_stock_consumption": item.get("supply_stock_consumption"),
                "fortification_milli": item.get("fortification_milli", 0),
                "command_channels": dict(item.get("command_channels", {})),
                "doctrine_channels": dict(item.get("doctrine_channels", {})),
                "range_band": range_band,
                "line_of_sight": line_of_sight,
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

        # Settle exact elite overlays against their own owners and the same
        # force/population conservation graph. Anonymous casualties above never
        # choose or absorb these named outcomes.
        elite_person_writes: Dict[str, Dict[str, Any]] = {}
        elite_team_writes: Dict[str, Dict[str, Any]] = {}
        elite_force_writes: Dict[str, Dict[str, Any]] = {path: force for path, force in force_records.values()}
        elite_formation_writes: Dict[str, Dict[str, Any]] = formation_registry_records
        elite_recovery_host_refs: list[str] = []
        elite_death_transfer_ids: list[str] = []
        scheduler: Optional[CausalSchedulerRegistry] = None
        for actor_ref in elite_actor_refs:
            effect = effects.get(actor_ref)
            actor_row = elite_exact_records.get(actor_ref)
            if effect is None or not isinstance(actor_row, Mapping):
                raise CommandRejectedError("combat_elite_overlay_effect_missing")
            path = actor_row["path"]
            record = actor_row["record"]
            self._apply_exact_combat_effect(record, effect=effect, combat_id=combat_id, current_time=current_time)
            self._persist_exact_combat_resources(record, effect)
            self._settle_special_exact_combat_state(
                actor_ref=actor_ref, record=record,
                duration_seconds=contract.timing.exchange_seconds * contract.timing.max_ticks,
                context=special_context,
            )
            elite_person_writes[path] = record
            after = effect.after_personnel
            if after.killed:
                transfer_id = self._reconcile_rostered_person_death(
                    population_registry, person_ref=actor_ref, at=current_time, command=command,
                    force_writes=elite_force_writes, team_writes=elite_team_writes,
                    formation_writes=elite_formation_writes,
                )
                if transfer_id is not None:
                    elite_death_transfer_ids.append(transfer_id)
            elif after.wounded or after.incapacitated:
                service_recovery = self._reconcile_rostered_person_injury(
                    population_registry, person_ref=actor_ref, force_writes=elite_force_writes,
                    team_writes=elite_team_writes, formation_writes=elite_formation_writes,
                )
                scheduler = scheduler or self._load_scheduler(current_time=current_time, scene=scene)
                host_id = "host.recovery." + actor_ref
                due = current_time.add_seconds(24 * 60 * 60)
                if host_id not in scheduler.hosts:
                    metadata: Dict[str, Any] = {"person_ref": actor_ref}
                    if service_recovery is not None:
                        metadata.update(service_recovery)
                    scheduler.add_host(SchedulerHost(
                        state=HostState(
                            host_id=host_id, kind="person_recovery", resolved_through=current_time,
                            safe_through=due.add_seconds(-1), handler_ref="causal.scheduler",
                            rng_namespace="recovery:" + actor_ref, next_due=due,
                        ),
                        authority_kind="person_recovery", owner_ref=path, metadata=metadata,
                    ))
                    scheduler.upsert_event(recurring_event(
                        kind="person.recovery.periodic_review", identity=actor_ref, host_id=host_id, due_at=due,
                        recurrence={"kind": "fixed_interval", "interval_seconds": 86400, "accrual_mode": "boundary_only"},
                        payload={"actor_ref": actor_ref, "owner_ref": path}, priority=25,
                        visibility="restricted", requires_player=False,
                    ))
                elite_recovery_host_refs.append(host_id)
            elite_overlay_meta[actor_ref]["personnel"] = after.to_record()
            elite_overlay_meta[actor_ref]["resources_after"] = [row.to_record() for row in effect.after_resources]

        # Re-read final formation strengths from the mutable registries after any
        # exact casualty detached its identity overlay/body.
        for row in operation_participants:
            formation_ref = row.get("formation_ref")
            formation_path = parsed[row["participant_ref"]]["formation_path"]
            registry = formation_registry_records[formation_path]
            final_formation = next((f for f in registry.get("formations", []) if isinstance(f, Mapping) and f.get("id") == formation_ref), None)
            if isinstance(final_formation, Mapping):
                row["formation_personnel_after"] = final_formation.get("personnel_total")

        effect_record = effect_plan.to_record()
        effect_hash = hashlib.sha256(json.dumps(effect_record, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        resolved_elite_actor_refs = sorted(elite_actor_refs)
        # Exact elite overlays were resolved inside this same combat contract;
        # they are not deferred into a second transaction that could double-apply
        # casualties or resources.
        pending_named_actor_refs: list[str] = []
        world_events = self._world_events()
        force_paths = tuple(sorted({path for path, _ in force_records.values()}))
        formation_paths = tuple(sorted(formation_registry_records))
        consequence_refs = []
        for ref, effect in effects.items():
            a = effect.after_personnel
            consequence_refs.extend([f"combat:{ref}:killed:{a.killed}", f"combat:{ref}:wounded:{a.wounded}", f"combat:{ref}:captured:{a.captured}"])
        for item in parsed.values():
            usage = item.get("supply_stock_consumption")
            consumed = usage.get("consumed") if isinstance(usage, Mapping) else None
            if isinstance(consumed, Mapping):
                for item_key, quantity in sorted(consumed.items()):
                    consequence_refs.append(f"combat_supply:{item['force_ref']}:{item_key}:{quantity}")
        aggregate_event_kind = "aggregate_combat_resolved"
        event_id = self._append_semantic_event(
            world_events, command=command, kind=aggregate_event_kind, at=current_time,
            host_refs=(combat_id,), actor_refs=(command.actor_id,), place_refs=(location_id,),
            causal_refs=(() if mission_context is None else (mission_context,)),
            affected_owner_refs=tuple(dict.fromkeys(
                force_paths + formation_paths + tuple(elite_person_writes) + tuple(elite_team_writes)
                + (operation_path, _POPULATION_REGISTRY_PATH)
                + ((self.scheduler_path,) if scheduler is not None else ())
                + ((_INVENTORY_REGISTRY_PATH,) if inventory_changed else ())
                + tuple(sorted(aggregate_stock_changed))
            )),
            material_consequence_refs=tuple(consequence_refs), classification="restricted", audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.combat.resolve_combat",
        )
        for actor_ref in pending_named_actor_refs:
            pending_by_actor[actor_ref] = combat_id
        operation = {
            "schema": "combat-operation", "operation_id": combat_id, "opened_at": str(current_time), "location_id": location_id,
            "scale": scale, "status": "resolved",
            "authority_model": "source force owns manpower; command authority and operational attachment are separate; operation owns temporary committed-slice combat condition only",
            "participants": sorted(operation_participants, key=lambda x: x["participant_ref"]),
            "outcome": {
                "resolution_mode": effect_plan.resolution_mode, "status": effect_plan.status,
                "victorious_side_refs": list(effect_plan.victorious_side_refs),
                "wake_triggers": [x.to_record() for x in effect_plan.wake_triggers],
                "semantic_event_id": event_id, "effect_plan_sha256": effect_hash,
                "pending_named_actor_refs": pending_named_actor_refs,
                "resolved_elite_actor_refs": resolved_elite_actor_refs,
                "elite_overlay_model": "exact_discrete_participants_not_averaged_into_formation_kernel",
                "elite_overlay_results": [elite_overlay_meta[ref] for ref in resolved_elite_actor_refs],
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
        for path, record in elite_person_writes.items():
            writes[path] = _json_bytes(record)
        for path, team in elite_team_writes.items():
            writes[path] = _json_bytes(team)
        if killed_by_force or elite_death_transfer_ids:
            writes[_POPULATION_REGISTRY_PATH] = _json_bytes(population_registry)
        if inventory_changed:
            writes[_INVENTORY_REGISTRY_PATH] = _json_bytes(combat_inventory)
        for _force_ref, (stock_path, stock) in aggregate_stock_cache.items():
            if stock_path in aggregate_stock_changed:
                writes[stock_path] = _json_bytes(stock)
        if scheduler is not None:
            writes.update(self._scheduler_write_images(scheduler))
        writes[_JINCHURIKI_REGISTRY_PATH] = _json_bytes(special_context["jinchuriki"])
        writes[_SUMMON_REGISTRY_PATH] = _json_bytes(special_context["summons"])
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
            for stock_path in aggregate_stock_changed:
                staged_stock = overlay.read_json(stock_path)
                for item_key, value in staged_stock.items():
                    if isinstance(value, int) and not isinstance(value, bool) and value < 0:
                        raise ValueError(f"aggregate combat stock went negative: {item_key}")
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
                "resolved_elite_actor_refs": resolved_elite_actor_refs,
                "elite_overlay_results": [elite_overlay_meta[ref] for ref in resolved_elite_actor_refs],
                "elite_death_transfer_ids": elite_death_transfer_ids,
                "elite_recovery_host_refs": sorted(set(elite_recovery_host_refs)),
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
        allowed_optional = {"mission_ref", "location_ref", "parent_combat_ref", "range_band", "line_of_sight"}
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
        range_band = payload.get("range_band", 1)
        if isinstance(range_band, bool) or not isinstance(range_band, int) or not 0 <= range_band <= 3:
            raise CommandRejectedError("combat_range_band_invalid")
        line_of_sight = payload.get("line_of_sight", True)
        if not isinstance(line_of_sight, bool):
            raise CommandRejectedError("combat_line_of_sight_invalid")
        exact_records: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        parsed_specs: Dict[str, Dict[str, Any]] = {}
        side_refs = set()
        exact_required_fields = {"actor_ref", "side_ref", "action", "target_refs", "objective_ref", "lethal"}
        exact_optional_fields = {"technique_ref", "weapon_ref", "consumable_ref", "consumable_quantity", "active_eye_refs"}
        for sequence, spec in enumerate(participant_specs):
            if (
                not isinstance(spec, Mapping)
                or not exact_required_fields.issubset(set(spec))
                or set(spec) - exact_required_fields - exact_optional_fields
            ):
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
            for method_key in ("technique_ref", "weapon_ref", "consumable_ref"):
                value = spec.get(method_key)
                if value is not None and (not isinstance(value, str) or not value or len(value) > 160):
                    raise CommandRejectedError("combat_method_ref_invalid")
            quantity = spec.get("consumable_quantity", 1)
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 8:
                raise CommandRejectedError("combat_consumable_quantity_invalid")
            active_eye_refs = spec.get("active_eye_refs")
            if active_eye_refs is not None:
                if (
                    not isinstance(active_eye_refs, Sequence)
                    or isinstance(active_eye_refs, (str, bytes, bytearray))
                    or not 1 <= len(active_eye_refs) <= 8
                    or any(not isinstance(ref, str) or not ref or len(ref) > 160 for ref in active_eye_refs)
                    or len(set(active_eye_refs)) != len(active_eye_refs)
                ):
                    raise CommandRejectedError("combat_active_eye_refs_invalid")
            path, record = self._resolve_actor_for_write(actor_ref)
            if record.get("life_status") not in ("active", "alive"):
                raise CommandRejectedError("combat_actor_not_active")
            if record.get("current_location_id") != encounter_location:
                raise CommandRejectedError("combat_participant_not_co_located")
            exact_records[actor_ref] = (path, record)
            parsed_specs[actor_ref] = {**spec, "sequence": sequence, "side_ref": side_ref, "objective_ref": objective_ref, "consumable_quantity": quantity}
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
        try:
            combat_inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_registry_invalid") from exc
        inventory_holders = combat_inventory.get("holders") if isinstance(combat_inventory, Mapping) else None
        if not isinstance(inventory_holders, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        inventory_changed = False

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
                # The gameplay caller cannot select an independent NPC's
                # technique or equipment.  Runtime policy chooses from that
                # person's own repertoire/custody below.
                if command.mode == "gameplay":
                    spec["technique_ref"] = None
                    spec["weapon_ref"] = None
                    spec["consumable_ref"] = None
                    spec["consumable_quantity"] = 1
                    spec["active_eye_refs"] = None
            elif spec["objective_ref"] is not None and spec["objective_ref"] not in objective_ids:
                raise CommandRejectedError("combat_intent_invalid")

        zone_ref = encounter_location
        side_team_context = self._combat_side_team_context(parsed_specs)
        special_context = self._special_combat_context()
        participants = []
        engagements = []
        methods_used: Dict[str, Dict[str, Any]] = {}
        for actor_ref, spec in parsed_specs.items():
            path, record = exact_records[actor_ref]
            technique_ref = spec.get("technique_ref")
            weapon_ref = spec.get("weapon_ref")
            if actor_ref != command.actor_id or command.mode == "autonomous":
                auto_technique, auto_weapon = self._choose_autonomous_exact_method(
                    actor_ref=actor_ref,
                    record=record,
                    action=str(spec["action"]),
                    range_band=range_band,
                    inventory_holders=inventory_holders,
                )
                technique_ref = auto_technique
                weapon_ref = auto_weapon
            technique = None
            mastery = None
            weapon = None
            resource_costs = []
            effective_range_bands: Tuple[int, ...] = (0, 1)
            if technique_ref is not None:
                technique = self._combat_technique_record(str(technique_ref))
                try:
                    usable = field_usable_method_refs(record)
                except ValueError as exc:
                    raise CommandRejectedError("combat_technique_repertoire_invalid") from exc
                if technique_ref not in usable or not technique_prerequisites_met(record, technique):
                    raise CommandRejectedError("combat_technique_not_field_usable")
                mastery_map = record.get("repertoire", {}).get("method_mastery") if isinstance(record.get("repertoire"), Mapping) else None
                mastery_value = mastery_map.get(technique_ref) if isinstance(mastery_map, Mapping) else None
                if isinstance(mastery_value, bool) or not isinstance(mastery_value, int):
                    raise CommandRejectedError("combat_technique_repertoire_invalid")
                mastery = mastery_value
                holder = inventory_holders.get(actor_ref)
                holder = holder if isinstance(holder, Mapping) else {}
                weapon_ref = self._technique_equipment_binding(
                    technique=technique,
                    holder=holder,
                    requested_weapon_ref=str(weapon_ref) if weapon_ref is not None else None,
                    record=record,
                )
                technique_bands = self._combat_range_bands(technique.get("maximum_range_m", 0))
                if range_band not in technique_bands:
                    raise CommandRejectedError("combat_technique_out_of_range")
                effective_range_bands = technique_bands
                chakra_cost = self._technique_chakra_cost(record, technique)
                if chakra_cost:
                    chakra_resource = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
                    current_chakra = chakra_resource.get("current") if isinstance(chakra_resource, Mapping) else None
                    if isinstance(current_chakra, bool) or not isinstance(current_chakra, int) or current_chakra < chakra_cost:
                        raise CommandRejectedError("combat_technique_resource_insufficient")
                    resource_costs.append(ResourceCost("chakra", chakra_cost))
            if weapon_ref is not None:
                weapon = self._combat_item_record(str(weapon_ref))
                if weapon.get("type") != "weapon":
                    raise CommandRejectedError("combat_weapon_invalid")
                holder = inventory_holders.get(actor_ref)
                quantity_held = holder.get(weapon_ref, 0) if isinstance(holder, Mapping) else 0
                if isinstance(quantity_held, bool) or not isinstance(quantity_held, int) or quantity_held <= 0:
                    raise CommandRejectedError("combat_weapon_not_held")
                weapon_bands = self._combat_range_bands(weapon.get("maximum_range_m", 0))
                if range_band not in weapon_bands:
                    raise CommandRejectedError("combat_weapon_out_of_range")
                if technique is None:
                    effective_range_bands = weapon_bands
                projectile_speed = weapon.get("projectile_speed_mps", 0)
                reach = weapon.get("reach_m", 0)
                thrown_use = (
                    isinstance(projectile_speed, (int, float)) and not isinstance(projectile_speed, bool) and projectile_speed > 0
                    and (range_band > 0 or not isinstance(reach, (int, float)) or reach <= 0)
                )
                if thrown_use:
                    spend = spec.get("consumable_quantity", 1)
                    if quantity_held < spend:
                        raise CommandRejectedError("combat_ammunition_insufficient")
                    inventory_holders[actor_ref][weapon_ref] = quantity_held - spend
                    inventory_changed = True
                if weapon.get("skill") == "bow":
                    arrows = inventory_holders.get(actor_ref, {}).get("item_arrow", 0)
                    if isinstance(arrows, bool) or not isinstance(arrows, int) or arrows <= 0:
                        raise CommandRejectedError("combat_ammunition_insufficient")
                    inventory_holders[actor_ref]["item_arrow"] = arrows - 1
                    inventory_changed = True
            consumable_ref = spec.get("consumable_ref")
            if consumable_ref is not None:
                consumable = self._combat_item_record(str(consumable_ref))
                if consumable.get("consumable") is not True:
                    raise CommandRejectedError("combat_consumable_invalid")
                holder = inventory_holders.get(actor_ref)
                quantity_held = holder.get(consumable_ref, 0) if isinstance(holder, Mapping) else 0
                spend = spec.get("consumable_quantity", 1)
                if isinstance(quantity_held, bool) or not isinstance(quantity_held, int) or quantity_held < spend:
                    raise CommandRejectedError("combat_consumable_insufficient")
                inventory_holders[actor_ref][consumable_ref] = quantity_held - spend
                inventory_changed = True
            resource_pools = []
            chakra = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
            if isinstance(chakra, Mapping) and isinstance(chakra.get("capacity"), int) and isinstance(chakra.get("current"), int):
                resource_pools.append(ResourcePool("chakra", chakra["capacity"], chakra["current"]))
            target_refs = tuple(spec["target_refs"])
            intent = CombatIntent(
                action=spec["action"], objective_ref=spec["objective_ref"], target_refs=target_refs,
                commitment_milli=1000, lethal_force_milli=1000 if spec["lethal"] else 0,
                resource_costs=tuple(resource_costs),
                destination_zone_ref=zone_ref if spec["action"] in ("escape", "extract", "disengage") else None,
            )
            attributes = record.get("attributes") if isinstance(record.get("attributes"), Mapping) else {}
            operational = record.get("operational_skills") if isinstance(record.get("operational_skills"), Mapping) else {}
            base_capability = self._combat_capability(record)
            method_capability, method = self._project_exact_method_capability(
                record, base=base_capability, technique=technique, mastery=mastery, weapon=weapon
            )
            ocular_capability, ocular_initiative, ocular_passive_cost, ocular_meta = self._ocular_combat_projection(
                actor_ref=actor_ref, record=record, capability=method_capability, method=method, technique=technique,
                requested_eye_refs=spec.get("active_eye_refs") if actor_ref == command.actor_id and command.mode == "gameplay" else None,
                autonomous=(actor_ref != command.actor_id or command.mode == "autonomous"),
                duration_seconds=12,
                technique_chakra_cost=sum(cost.amount for cost in resource_costs if cost.resource_ref == "chakra"),
            )
            if ocular_passive_cost:
                existing_chakra_cost = sum(cost.amount for cost in resource_costs if cost.resource_ref == "chakra")
                resource_costs = [cost for cost in resource_costs if cost.resource_ref != "chakra"]
                combined_chakra_cost = existing_chakra_cost + ocular_passive_cost
                chakra_resource = record.get("resources", {}).get("chakra") if isinstance(record.get("resources"), Mapping) else None
                current_chakra = chakra_resource.get("current") if isinstance(chakra_resource, Mapping) else None
                if isinstance(current_chakra, bool) or not isinstance(current_chakra, int) or current_chakra < combined_chakra_cost:
                    raise CommandRejectedError("combat_technique_resource_insufficient")
                resource_costs.append(ResourceCost("chakra", combined_chakra_cost))
            # Rebuild the intent after ocular passive drain has been folded into
            # the same conserved chakra resource cost.
            intent = CombatIntent(
                action=spec["action"], objective_ref=spec["objective_ref"], target_refs=target_refs,
                commitment_milli=1000, lethal_force_milli=1000 if spec["lethal"] else 0,
                resource_costs=tuple(resource_costs),
                destination_zone_ref=zone_ref if spec["action"] in ("escape", "extract", "disengage") else None,
            )
            capability, special_initiative, special_technique_refs, special_equipment_refs = self._special_exact_combat_capability(
                record, ocular_capability, special_context
            )
            initiative = max(0, min(200, (int(attributes.get("agility", 0)) + int(attributes.get("awareness", 0)) + int(operational.get("tactics", 0))) // 3 + special_initiative + ocular_initiative))
            cohesion = 100
            team_context = side_team_context.get(spec["side_ref"])
            if team_context is not None:
                _team, doctrine = team_context
                familiarity = doctrine.get("familiarity") if isinstance(doctrine, Mapping) else None
                practiced = familiarity.get(actor_ref, 0) if isinstance(familiarity, Mapping) else 0
                if isinstance(practiced, int) and not isinstance(practiced, bool):
                    practiced = max(0, min(100, practiced))
                    cohesion = max(75, min(100, 80 + practiced // 5))
                    initiative = max(0, min(200, initiative + (practiced - 50) // 10))
            unusual_techniques = tuple(dict.fromkeys(((str(technique_ref),) if technique_ref else ()) + tuple(special_technique_refs)))
            unusual_equipment = tuple(dict.fromkeys(((str(weapon_ref),) if weapon_ref else ()) + ((str(consumable_ref),) if consumable_ref else ()) + tuple(special_equipment_refs)))
            methods_used[actor_ref] = {
                "method": method,
                "technique_ref": technique_ref,
                "weapon_ref": weapon_ref,
                "consumable_ref": consumable_ref,
                "consumable_quantity": spec.get("consumable_quantity", 1) if (consumable_ref or weapon_ref) else 0,
                "chakra_cost": sum(cost.amount for cost in resource_costs),
                "effective_range_bands": list(effective_range_bands),
                "dojutsu": ocular_meta,
            }
            participants.append(Participant(
                participant_ref=actor_ref, authoritative_owner_ref=path, side_ref=spec["side_ref"], sequence=spec["sequence"],
                representation="exact", capability=capability, personnel=PersonnelState(total=1, active=1),
                position=PositionState(zone_ref=zone_ref), information=InformationState(observed_refs=tuple(sorted(actor_refs - {actor_ref}))),
                intent=intent, initiative=initiative, readiness=self._combat_readiness(record), morale=100, cohesion=cohesion,
                resources=tuple(resource_pools), effective_range_bands=effective_range_bands, named_actor_refs=(actor_ref,),
                unusual_technique_refs=unusual_techniques, unusual_equipment_refs=unusual_equipment,
                detailed_injury_refs=tuple(str(x) for x in record.get("condition", {}).get("injuries", []) if isinstance(x, str)),
            ))
            for target_index, target_ref in enumerate(target_refs):
                engagements.append(Engagement(
                    engagement_ref=f"engagement:{command.digest[:12]}:{spec['sequence']}:{target_index}",
                    actor_ref=actor_ref, target_ref=target_ref, range_band=range_band, line_of_sight=line_of_sight, frontage_milli=1000, timing_delay_ms=0,
                ))
        try:
            contract = CombatContract(
                combat_ref=combat_id,
                transaction_ref=("tx.autonomous." if command.mode == "autonomous" else "tx.gameplay.") + command.digest,
                scale=scale,
                participants=tuple(participants), objectives=tuple(objectives), engagements=tuple(engagements),
                terrain=self._terrain_state_for_location(
                    location_ref=zone_ref, side_refs=tuple(sorted(side_refs)),
                    mechanics=self.repository.read_json(_FORMATION_RESOLUTION_MECHANICS_PATH),
                ),
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
            self._persist_exact_combat_resources(record, effect)
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

        # House Tang individual-lite profiles keep field evidence even after an
        # exact component is materialized.  The exact sheet owns the fight; the
        # persistent lightweight profile owns later institutional consolidation.
        try:
            house_roster = copy.deepcopy(self.repository.read_json(_HOUSE_ROSTER_PATH))
        except (FileNotFoundError, ValueError):
            house_roster = None
        if isinstance(house_roster, dict):
            profiles = house_roster.get("profiles")
            changed_roster = False
            if isinstance(profiles, MutableMapping):
                for actor_ref in actor_refs:
                    entry = profiles.get(actor_ref)
                    if not isinstance(entry, MutableMapping):
                        continue
                    method = methods_used.get(actor_ref, {}).get("method")
                    try:
                        record_rostered_field_evidence(
                            entry, evidence_ref=combat_id, kind="combat",
                            exchanges=contract.timing.max_ticks,
                            method_ref=(str(method) if isinstance(method, str) else None),
                        )
                    except ValueError as exc:
                        raise CommandRejectedError("house_roster_field_evidence_invalid") from exc
                    changed_roster = True
            if changed_roster:
                writes[_HOUSE_ROSTER_PATH] = _json_bytes(house_roster)
                affected_paths.append(_HOUSE_ROSTER_PATH)

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
        if inventory_changed:
            writes[_INVENTORY_REGISTRY_PATH] = _json_bytes(combat_inventory)
            affected_paths.append(_INVENTORY_REGISTRY_PATH)
        writes[_JINCHURIKI_REGISTRY_PATH] = _json_bytes(special_context["jinchuriki"])
        writes[_SUMMON_REGISTRY_PATH] = _json_bytes(special_context["summons"])
        writes.update(self._scheduler_write_images(scheduler))
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
            staged_scheduler = self._scheduler_from_reader(overlay)
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
                "range_band": range_band, "line_of_sight": line_of_sight,
                "methods_used": methods_used,
            }, validator=validate,
        )
