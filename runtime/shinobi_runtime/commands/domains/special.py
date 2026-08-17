"""Bounded special-combat state: Eight Gates, jinchuriki, puppets, and summons.

These systems modify the existing exact-combat participant rather than creating
parallel combat engines.  Static technique/profile data remains descriptive
until one of these explicit state changes makes it causally active.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, Mapping, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.combat.models import CapabilityProfile
from shinobi_runtime.commands.core import _BuiltPlan, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.paths import (
    BIJU_MECHANICS_PATH as _BIJU_MECHANICS_PATH,
    JINCHURIKI_REGISTRY_PATH as _JINCHURIKI_REGISTRY_PATH,
    PUPPET_REGISTRY_PATH as _PUPPET_REGISTRY_PATH,
    SPECIAL_SYSTEMS_PATH as _SPECIAL_SYSTEMS_PATH,
    SUMMON_REGISTRY_PATH as _SUMMON_REGISTRY_PATH,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest
from shinobi_runtime.people import field_usable_method_refs


class SpecialCombatCommandsMixin:
    @staticmethod
    def _scaled(value: int, multiplier: object) -> int:
        if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
            raise CommandRejectedError("special_combat_mechanics_invalid")
        return max(0, min(200, int(round(value * float(multiplier)))))

    def _special_combat_context(self) -> Dict[str, Any]:
        try:
            systems = self.repository.read_json(_SPECIAL_SYSTEMS_PATH)
            biju = self.repository.read_json(_BIJU_MECHANICS_PATH)
            jinchuriki = copy.deepcopy(self.repository.read_json(_JINCHURIKI_REGISTRY_PATH))
            puppets = copy.deepcopy(self.repository.read_json(_PUPPET_REGISTRY_PATH))
            summons = copy.deepcopy(self.repository.read_json(_SUMMON_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("special_combat_registry_invalid") from exc
        if not all(isinstance(row, Mapping) for row in (systems, biju, jinchuriki, puppets, summons)):
            raise CommandRejectedError("special_combat_registry_invalid")
        j_rows = jinchuriki.get("records")
        p_rows = puppets.get("puppets")
        s_rows = summons.get("profiles")
        if not isinstance(j_rows, list) or not isinstance(p_rows, list) or not isinstance(s_rows, Mapping):
            raise CommandRejectedError("special_combat_registry_invalid")
        return {
            "systems": systems,
            "biju": biju,
            "jinchuriki": jinchuriki,
            "jinchuriki_by_host": {
                row.get("host_id"): row for row in j_rows
                if isinstance(row, Mapping) and isinstance(row.get("host_id"), str)
            },
            "puppets": puppets,
            "puppets_by_owner": {
                owner: [row for row in p_rows if isinstance(row, Mapping) and row.get("owner_id") == owner]
                for owner in sorted({row.get("owner_id") for row in p_rows if isinstance(row, Mapping) and isinstance(row.get("owner_id"), str)})
            },
            "summons": summons,
            "summons_by_owner": {
                owner: [(ref, row) for ref, row in s_rows.items() if isinstance(row, Mapping) and row.get("contract_owner") == owner]
                for owner in sorted({row.get("contract_owner") for row in s_rows.values() if isinstance(row, Mapping) and isinstance(row.get("contract_owner"), str)})
            },
        }

    def _special_exact_combat_capability(
        self,
        record: Mapping[str, Any],
        base: CapabilityProfile,
        context: Mapping[str, Any],
    ) -> Tuple[CapabilityProfile, int, Tuple[str, ...], Tuple[str, ...]]:
        """Apply only currently activated special state to one exact actor."""
        values = base.to_record()
        initiative_bonus = 0
        technique_refs: list[str] = []
        equipment_refs: list[str] = []
        actor_ref = record.get("owner_id")
        if not isinstance(actor_ref, str):
            raise CommandRejectedError("special_combat_actor_invalid")

        gates = record.get("eight_gates_state")
        if isinstance(gates, Mapping):
            gate = gates.get("current_gate", 0)
            if isinstance(gate, bool) or not isinstance(gate, int) or gate < 0 or gate > 8:
                raise CommandRejectedError("eight_gates_state_invalid")
            if gate:
                systems = context.get("systems")
                gate_map = systems.get("eight_gates", {}).get("gates") if isinstance(systems, Mapping) else None
                rule = gate_map.get(str(gate)) if isinstance(gate_map, Mapping) else None
                if not isinstance(rule, Mapping):
                    raise CommandRejectedError("special_combat_mechanics_invalid")
                power = rule.get("power_multiplier")
                speed = rule.get("speed_multiplier")
                for key in ("offense", "capture", "protection"):
                    values[key] = self._scaled(values[key], power)
                for key in ("defense", "mobility", "escape"):
                    values[key] = self._scaled(values[key], speed)
                initiative_bonus += max(0, int(round((float(speed) - 1.0) * 45)))
                technique_refs.append(f"special.eight_gate.{gate}")

        j_row = context.get("jinchuriki_by_host", {}).get(actor_ref)
        if isinstance(j_row, Mapping):
            state_name = j_row.get("current_state")
            if isinstance(state_name, str) and state_name != "base":
                biju = context.get("biju")
                states = biju.get("states") if isinstance(biju, Mapping) else None
                factors = biju.get("power_factors") if isinstance(biju, Mapping) else None
                state = states.get(state_name) if isinstance(states, Mapping) else None
                factor = factors.get(j_row.get("biju_id")) if isinstance(factors, Mapping) else None
                if not isinstance(state, Mapping) or isinstance(factor, bool) or not isinstance(factor, (int, float)):
                    raise CommandRejectedError("jinchuriki_state_invalid")
                output_mult = float(state.get("chakra_output_multiplier", 1.0)) * float(factor)
                strength_mult = float(state.get("strength_multiplier", 1.0)) * float(factor)
                speed_mult = float(state.get("speed_multiplier", 1.0))
                tough_mult = float(state.get("toughness_multiplier", 1.0))
                values["offense"] = self._scaled(values["offense"], max(output_mult, strength_mult))
                values["capture"] = self._scaled(values["capture"], strength_mult)
                values["defense"] = self._scaled(values["defense"], tough_mult)
                values["protection"] = self._scaled(values["protection"], tough_mult)
                values["mobility"] = self._scaled(values["mobility"], speed_mult)
                values["escape"] = self._scaled(values["escape"], speed_mult)
                penalty = state.get("control_penalty", 0)
                if isinstance(penalty, bool) or not isinstance(penalty, int):
                    raise CommandRejectedError("jinchuriki_state_invalid")
                values["control"] = max(0, min(200, values["control"] - penalty))
                initiative_bonus += max(0, int(round((speed_mult - 1.0) * 35)))
                technique_refs.append(f"biju.{j_row.get('biju_id')}.{state_name}")

        for puppet in context.get("puppets_by_owner", {}).get(actor_ref, ()):
            if not isinstance(puppet, Mapping) or puppet.get("deployed") is not True or puppet.get("available") is not True:
                continue
            count = puppet.get("count", 1)
            destroyed = puppet.get("current_destroyed", 0)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                count = 1
            if isinstance(destroyed, bool) or not isinstance(destroyed, int) or destroyed < 0:
                destroyed = 0
            surviving = max(0, count - destroyed)
            body = puppet.get("body_integrity", puppet.get("body_integrity_mean", 100))
            joint = puppet.get("joint_integrity", puppet.get("joint_integrity_mean", 100))
            damage = puppet.get("current_damage", 0)
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (body, joint, damage)):
                raise CommandRejectedError("puppet_state_invalid")
            quality = max(0, (body + joint) // 2 - max(0, damage))
            bonus = min(34, quality // 14 + min(18, int(math.isqrt(max(1, surviving))) * 2)) if surviving else 0
            role = str(puppet.get("role", "support"))
            if role in {"assault", "blade", "projectile", "elite_iron_sand", "mass_puppet_assault", "armor_shell_assault"}:
                values["offense"] = min(200, values["offense"] + bonus)
            elif role in {"capture", "wire", "control"}:
                values["capture"] = min(200, values["capture"] + bonus)
                values["control"] = min(200, values["control"] + bonus // 2)
            elif role == "shield":
                values["defense"] = min(200, values["defense"] + bonus)
                values["protection"] = min(200, values["protection"] + bonus)
            else:
                values["control"] = min(200, values["control"] + bonus // 2)
                values["offense"] = min(200, values["offense"] + bonus // 2)
            if puppet.get("poison") is True:
                values["offense"] = min(200, values["offense"] + 5)
                values["capture"] = min(200, values["capture"] + 5)
            puppet_ref = puppet.get("puppet_id")
            if isinstance(puppet_ref, str):
                equipment_refs.append(puppet_ref)

        for summon_ref, summon in context.get("summons_by_owner", {}).get(actor_ref, ()):
            if not isinstance(summon, Mapping) or summon.get("active") is not True or summon.get("available") is not True:
                continue
            combat = summon.get("combat", 0)
            count = summon.get("count", 1)
            if isinstance(combat, bool) or not isinstance(combat, int) or isinstance(count, bool) or not isinstance(count, int):
                raise CommandRejectedError("summon_state_invalid")
            base_bonus = min(28, max(0, combat) // 10 + min(8, int(math.isqrt(max(1, count))) * 2))
            values["offense"] = min(200, values["offense"] + base_bonus)
            for field, axis in (
                ("strength", "capture"), ("toughness", "protection"),
                ("tracking", "perception"), ("coordination", "control"),
                ("water_mobility", "mobility"), ("restraint", "capture"),
                ("medical", "protection"),
            ):
                raw = summon.get(field)
                if isinstance(raw, int) and not isinstance(raw, bool):
                    values[axis] = min(200, values[axis] + max(0, raw) // 18)
            initiative_bonus += max(0, summon.get("coordination", 0) // 20) if isinstance(summon.get("coordination", 0), int) else 0
            technique_refs.append(summon_ref)

        return CapabilityProfile(**values), max(0, min(80, initiative_bonus)), tuple(sorted(set(technique_refs))), tuple(sorted(set(equipment_refs)))

    def _settle_special_exact_combat_state(
        self,
        *,
        actor_ref: str,
        record: Dict[str, Any],
        duration_seconds: int,
        context: Dict[str, Any],
    ) -> None:
        ticks = max(1, (duration_seconds + 5) // 6)
        gates = record.get("eight_gates_state")
        if isinstance(gates, dict):
            gate = gates.get("current_gate", 0)
            if isinstance(gate, int) and not isinstance(gate, bool) and gate > 0:
                rule = context["systems"].get("eight_gates", {}).get("gates", {}).get(str(gate))
                strain_per = rule.get("strain_per_6s") if isinstance(rule, Mapping) else None
                resources = record.get("resources")
                strain = resources.get("strain") if isinstance(resources, dict) else None
                if isinstance(strain_per, int) and not isinstance(strain_per, bool) and isinstance(strain, dict):
                    current = strain.get("current")
                    safe = strain.get("safe_capacity")
                    if isinstance(current, int) and not isinstance(current, bool) and isinstance(safe, int) and not isinstance(safe, bool):
                        strain["current"] = max(0, current + strain_per * ticks)
                        condition = record.get("condition")
                        if strain["current"] > safe and isinstance(condition, dict) and condition.get("readiness") not in ("incapacitated", "dead"):
                            condition["readiness"] = "injured"
                        if gate == 8 and isinstance(condition, dict) and condition.get("readiness") != "dead":
                            condition["readiness"] = "incapacitated"

        j_row = context.get("jinchuriki_by_host", {}).get(actor_ref)
        if isinstance(j_row, dict):
            state_name = j_row.get("current_state")
            state = context["biju"].get("states", {}).get(state_name) if isinstance(state_name, str) else None
            if isinstance(state, Mapping) and state_name != "base":
                pressure = state.get("seal_pressure_per_6s", 0)
                if isinstance(pressure, int) and not isinstance(pressure, bool):
                    fraction = j_row.get("current_biju_chakra_fraction")
                    if isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
                        j_row["current_biju_chakra_fraction"] = max(0.0, round(float(fraction) - pressure * ticks * 0.002, 6))
                    control = j_row.get("host_control", 0)
                    cooperation = j_row.get("biju_cooperation", 0)
                    stability = j_row.get("seal_stability", 0)
                    if all(isinstance(v, int) and not isinstance(v, bool) for v in (control, cooperation, stability)):
                        raw_pressure = max(0, pressure * ticks - control // 20 - cooperation // 40)
                        j_row["seal_stability"] = max(0, stability - raw_pressure // 5)
                    if j_row.get("current_biju_chakra_fraction") == 0.0:
                        j_row["current_state"] = "base"

        # Summons are intentionally bounded one-combat support.  They do not
        # become permanent autonomous actors or scheduler hosts.
        profiles = context.get("summons", {}).get("profiles")
        if isinstance(profiles, dict):
            for _summon_ref, summon in context.get("summons_by_owner", {}).get(actor_ref, ()):
                if isinstance(summon, dict) and summon.get("active") is True:
                    summon["active"] = False

    @staticmethod
    def _puppet_channel_capacity(record: Mapping[str, Any]) -> int:
        try:
            known = set(field_usable_method_refs(record))
        except ValueError:
            known = set()
        if "human_puppets" in known:
            return 100
        if "ten_puppets_of_chikamatsu" in known:
            return 10
        if "puppet_thread_control" in known:
            chakra = record.get("chakra_dimensions")
            control = chakra.get("control") if isinstance(chakra, Mapping) else 0
            if isinstance(control, int) and not isinstance(control, bool):
                return max(1, min(10, control // 20))
            return 1
        return 0

    def _special_combat_state_resolution(
        self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime
    ) -> _BuiltPlan:
        spec = COMMAND_SPECS[command.command_type]
        if set(command.payload) != set(spec.required_fields):
            raise CommandRejectedError("special_combat_state_resolution_payload_fields_invalid")
        action = command.payload.get("action")
        actor_ref = _stable_id(command.payload.get("actor_ref"), "special_actor_ref_invalid")
        if actor_ref != command.actor_id:
            raise CommandRejectedError("special_combat_state_not_self_authorized")
        actor_path, actor = self._resolve_actor_for_write(actor_ref)
        if actor.get("life_status") not in ("active", "alive"):
            raise CommandRejectedError("special_combat_actor_not_active")
        context = self._special_combat_context()
        changed_paths: Dict[str, bytes] = {}
        consequence_refs: list[str] = []

        if action == "open_gate":
            gate = command.payload.get("gate")
            state = actor.get("eight_gates_state")
            if isinstance(gate, bool) or not isinstance(gate, int) or not 1 <= gate <= 8 or not isinstance(state, dict):
                raise CommandRejectedError("eight_gates_state_invalid")
            maximum = state.get("max_gate_qualified")
            mastery = state.get("gate_control_mastery")
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (maximum, mastery)) or gate > maximum:
                raise CommandRejectedError("eight_gate_not_qualified")
            rule = context["systems"].get("eight_gates", {})
            difficulty = rule.get("entry_difficulty", {}).get(str(gate)) if isinstance(rule, Mapping) else None
            attrs = actor.get("attributes") if isinstance(actor.get("attributes"), Mapping) else {}
            resources = actor.get("resources") if isinstance(actor.get("resources"), Mapping) else {}
            strain = resources.get("strain") if isinstance(resources, Mapping) else {}
            if not isinstance(difficulty, int) or isinstance(difficulty, bool):
                raise CommandRejectedError("special_combat_mechanics_invalid")
            endurance = attrs.get("endurance", 0); toughness = attrs.get("toughness", 0); composure = attrs.get("composure", 0)
            current_strain = strain.get("current", 0) if isinstance(strain, Mapping) else 0
            if any(isinstance(v, bool) or not isinstance(v, int) for v in (endurance, toughness, composure, current_strain)):
                raise CommandRejectedError("eight_gates_state_invalid")
            margin = mastery + (35 * endurance) // 100 + (25 * toughness) // 100 + (20 * composure) // 100 - difficulty - current_strain
            if margin < 0:
                raise CommandRejectedError("eight_gate_entry_failed")
            state["current_gate"] = gate
            changed_paths[actor_path] = _json_bytes(actor)
            consequence_refs.append(f"eight_gate:{actor_ref}:{gate}")

        elif action == "close_gates":
            state = actor.get("eight_gates_state")
            if not isinstance(state, dict):
                raise CommandRejectedError("eight_gates_state_invalid")
            state["current_gate"] = 0
            changed_paths[actor_path] = _json_bytes(actor)
            consequence_refs.append(f"eight_gate:{actor_ref}:closed")

        elif action == "jinchuriki_transform":
            target_state = command.payload.get("target_state")
            j_row = context.get("jinchuriki_by_host", {}).get(actor_ref)
            if not isinstance(target_state, str) or not isinstance(j_row, dict):
                raise CommandRejectedError("jinchuriki_state_invalid")
            unlocked = j_row.get("unlocked_states")
            if not isinstance(unlocked, list) or target_state not in unlocked:
                raise CommandRejectedError("jinchuriki_state_not_unlocked")
            if target_state != "base":
                fraction = j_row.get("current_biju_chakra_fraction")
                stability = j_row.get("seal_stability")
                if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or fraction <= 0:
                    raise CommandRejectedError("jinchuriki_biju_chakra_exhausted")
                if not isinstance(stability, int) or isinstance(stability, bool) or stability <= 0:
                    raise CommandRejectedError("jinchuriki_seal_unstable")
            j_row["current_state"] = target_state
            changed_paths[_JINCHURIKI_REGISTRY_PATH] = _json_bytes(context["jinchuriki"])
            consequence_refs.append(f"jinchuriki:{actor_ref}:{target_state}")

        elif action in ("puppet_deploy", "puppet_withdraw"):
            entity_ref = _stable_id(command.payload.get("entity_ref"), "puppet_ref_invalid", prefix="puppet.")
            rows = context["puppets"].get("puppets")
            target = next((row for row in rows if isinstance(row, dict) and row.get("puppet_id") == entity_ref), None)
            if not isinstance(target, dict) or target.get("owner_id") != actor_ref:
                raise CommandRejectedError("puppet_not_owned")
            if action == "puppet_deploy":
                if target.get("available") is not True:
                    raise CommandRejectedError("puppet_not_available")
                capacity = self._puppet_channel_capacity(actor)
                if capacity <= 0:
                    raise CommandRejectedError("puppet_control_not_qualified")
                used = 0
                for row in rows:
                    if not isinstance(row, Mapping) or row.get("owner_id") != actor_ref or row.get("deployed") is not True or row.get("puppet_id") == entity_ref:
                        continue
                    channels = row.get("control_channels_required", 0)
                    if isinstance(channels, int) and not isinstance(channels, bool):
                        used += max(0, channels)
                required = target.get("control_channels_required")
                if isinstance(required, bool) or not isinstance(required, int) or required < 1 or used + required > capacity:
                    raise CommandRejectedError("puppet_control_capacity_exceeded")
                target["deployed"] = True
            else:
                target["deployed"] = False
            changed_paths[_PUPPET_REGISTRY_PATH] = _json_bytes(context["puppets"])
            consequence_refs.append(f"puppet:{entity_ref}:{'deployed' if action == 'puppet_deploy' else 'withdrawn'}")

        elif action in ("summon_call", "summon_dismiss"):
            entity_ref = _stable_id(command.payload.get("entity_ref"), "summon_ref_invalid", prefix="summon.")
            profiles = context["summons"].get("profiles")
            summon = profiles.get(entity_ref) if isinstance(profiles, dict) else None
            if not isinstance(summon, dict) or summon.get("contract_owner") != actor_ref:
                raise CommandRejectedError("summon_contract_invalid")
            if action == "summon_call":
                if summon.get("available") is not True or summon.get("active") is True:
                    raise CommandRejectedError("summon_not_available")
                cost = summon.get("summoning_cost")
                resources = actor.get("resources")
                chakra = resources.get("chakra") if isinstance(resources, dict) else None
                current = chakra.get("current") if isinstance(chakra, dict) else None
                if isinstance(cost, bool) or not isinstance(cost, int) or cost < 1 or isinstance(current, bool) or not isinstance(current, int) or current < cost:
                    raise CommandRejectedError("summon_chakra_insufficient")
                chakra["current"] = current - cost
                summon["active"] = True
                changed_paths[actor_path] = _json_bytes(actor)
            else:
                summon["active"] = False
            changed_paths[_SUMMON_REGISTRY_PATH] = _json_bytes(context["summons"])
            consequence_refs.append(f"summon:{entity_ref}:{'called' if action == 'summon_call' else 'dismissed'}")
        else:
            raise CommandRejectedError("special_combat_action_invalid")

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="special_combat_state_changed",
            at=current_time,
            actor_refs=(actor_ref,),
            affected_owner_refs=tuple(changed_paths),
            material_consequence_refs=tuple(consequence_refs),
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.special_combat_state_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            **changed_paths,
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("special combat state write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            events = overlay.read_json("state/reg/world-events.json").get("events", [])
            if not any(isinstance(row, Mapping) and row.get("id") == event_id for row in events):
                raise ValueError("special combat semantic event missing")

        return _BuiltPlan(
            code="special_combat_state_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={"world_time": str(current_time), "action": action, "actor_ref": actor_ref, "consequence_refs": consequence_refs, "semantic_event_id": event_id},
            validator=validate,
        )
