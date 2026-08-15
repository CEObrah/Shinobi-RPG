"""Persistent operational battlefield control above exact combat resolution.

This layer owns battlefield geometry, sector assignments, timed redeployment,
command pressure, delayed reports, and player-facing operational boundaries.
It never owns manpower, casualties, strategic territory, or exact combat
outcomes. Those remain in formation/force/combat/conflict authorities.
"""

from __future__ import annotations

import copy
import heapq
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import CONFLICT_REGISTRY_PATH as _CONFLICT_REGISTRY_PATH
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


_BATTLEFIELD_MECHANICS_PATH = "game/data/mechanics/battlefield-operations.json"
_VALID_ORDERS = frozenset({"hold", "attack", "breakthrough", "delay", "reserve", "withdraw"})
_VALID_PACES = frozenset({"forced", "standard", "cautious"})


def _seconds_between(start: CampaignTime, end: CampaignTime) -> int:
    return max(0, int((_campaign_datetime(end) - _campaign_datetime(start)).total_seconds()))


def _campaign_add_seconds(value: CampaignTime, seconds: int) -> CampaignTime:
    return value.add_seconds(max(0, int(seconds)))


class OperationalBattlefieldCommandsMixin:
    """Battlefield state and deterministic operational-time helpers."""

    def _battlefield_mechanics(self) -> Mapping[str, Any]:
        try:
            mechanics = self.repository.read_json(_BATTLEFIELD_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("battlefield_mechanics_invalid") from exc
        if not isinstance(mechanics, Mapping):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        return mechanics

    @staticmethod
    def _battlefield_ref_key(battlefield_ref: str, key: str) -> str:
        return f"{battlefield_ref}.sector.{key}"

    @staticmethod
    def _battlefield_edges(battlefield: Mapping[str, Any]) -> Dict[str, Dict[str, int]]:
        graph: Dict[str, Dict[str, int]] = {}
        for row in battlefield.get("sector_edges", []):
            if not isinstance(row, Mapping):
                continue
            a, b, distance = row.get("a"), row.get("b"), row.get("distance_units")
            if not isinstance(a, str) or not isinstance(b, str) or isinstance(distance, bool) or not isinstance(distance, int) or distance <= 0:
                continue
            graph.setdefault(a, {})[b] = distance
            graph.setdefault(b, {})[a] = distance
        return graph

    @classmethod
    def _battlefield_shortest_path(cls, battlefield: Mapping[str, Any], source: str, target: str) -> Tuple[list[str], int]:
        if source == target:
            return [source], 0
        graph = cls._battlefield_edges(battlefield)
        if source not in graph or target not in graph:
            raise CommandRejectedError("battlefield_sector_path_unresolved")
        queue: list[tuple[int, str, tuple[str, ...]]] = [(0, source, (source,))]
        best: Dict[str, int] = {}
        while queue:
            distance, node, path = heapq.heappop(queue)
            if node in best and best[node] <= distance:
                continue
            best[node] = distance
            if node == target:
                return list(path), distance
            for nxt, edge_distance in graph.get(node, {}).items():
                heapq.heappush(queue, (distance + edge_distance, nxt, (*path, nxt)))
        raise CommandRejectedError("battlefield_sector_path_unresolved")

    def _battlefield_formation_bundle(self, formation_ref: str) -> Tuple[str, str, Dict[str, Any], Mapping[str, Any]]:
        formation_path, force_ref, formation_view = self._formation_by_id(formation_ref)
        formation = copy.deepcopy(dict(formation_view))
        cache = _OwnerResolutionCache()
        _force_path, _force_digest, force_view = self._resolve_covered_owner_view(force_ref, cache=cache)
        if not isinstance(force_view, Mapping) or force_view.get("schema") != "force":
            raise CommandRejectedError("battlefield_force_unresolved")
        return formation_path, force_ref, formation, force_view

    def _battlefield_require_formation_authority(
        self,
        *,
        actor_ref: str,
        formation_ref: str,
        current_time: CampaignTime,
    ) -> Tuple[str, str, Dict[str, Any], Mapping[str, Any], str]:
        formation_path, force_ref, formation, force_view = self._battlefield_formation_bundle(formation_ref)
        personnel = formation.get("personnel_total")
        if isinstance(personnel, bool) or not isinstance(personnel, int) or personnel <= 0:
            raise CommandRejectedError("battlefield_formation_invalid")
        cache = _OwnerResolutionCache()
        authority = self._domain_authority(cache=cache)
        grant = authority.force_grant(grantor_ref=actor_ref, force_record=force_view)
        if grant.allowed:
            basis = grant.basis
        else:
            decision = authority.force_command(
                commander_ref=actor_ref,
                force_ref=force_ref,
                operational_attachment_ref=formation_ref,
                named_actor_refs=(),
                committed_count=personnel,
                effective_at=str(current_time),
            )
            if not decision.allowed:
                raise CommandRejectedError("battlefield_formation_authority_denied")
            basis = decision.basis
        side_ref = force_view.get("owner_ref") if isinstance(force_view.get("owner_ref"), str) and force_view.get("owner_ref") else force_ref
        return formation_path, force_ref, formation, force_view, str(basis)

    @staticmethod
    def _battlefield_side_for_force(force_ref: str, force_view: Mapping[str, Any]) -> str:
        owner_ref = force_view.get("owner_ref")
        return owner_ref if isinstance(owner_ref, str) and owner_ref else force_ref

    @staticmethod
    def _battlefield_movement_score(formation: Mapping[str, Any]) -> int:
        total = 0
        weighted = 0
        for component in formation.get("components", []):
            if not isinstance(component, Mapping):
                continue
            count = component.get("count")
            capability = component.get("capability_state")
            fundamentals = capability.get("fundamentals") if isinstance(capability, Mapping) else None
            movement = fundamentals.get("movement") if isinstance(fundamentals, Mapping) else None
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                continue
            if isinstance(movement, bool) or not isinstance(movement, (int, float)):
                continue
            total += count
            weighted += int(round(float(movement) * count))
        if total:
            return max(1, min(100, weighted // total))
        readiness = formation.get("readiness", 50)
        return max(1, min(100, int(readiness) if isinstance(readiness, int) and not isinstance(readiness, bool) else 50))

    @staticmethod
    def _battlefield_sensory_score(formation: Mapping[str, Any]) -> int:
        total = 0
        weighted = 0
        for component in formation.get("components", []):
            if not isinstance(component, Mapping):
                continue
            count = component.get("count")
            capability = component.get("capability_state")
            methods = capability.get("methods") if isinstance(capability, Mapping) else None
            sensory = methods.get("sensory") if isinstance(methods, Mapping) else None
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
                continue
            if isinstance(sensory, bool) or not isinstance(sensory, (int, float)):
                continue
            total += count
            weighted += int(round(float(sensory) * count))
        return 0 if not total else max(0, min(100, weighted // total))

    def _battlefield_leg_seconds(self, formation: Mapping[str, Any], distance_units: int, pace: str) -> int:
        mechanics = self._battlefield_mechanics()
        rules = mechanics.get("redeployment")
        if not isinstance(rules, Mapping):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        seconds_by_pace = rules.get("seconds_per_distance_unit")
        size_bands = rules.get("size_bands")
        reference = rules.get("movement_reference")
        floor = rules.get("movement_floor_milli")
        ceiling = rules.get("movement_ceiling_milli")
        if (
            not isinstance(seconds_by_pace, Mapping)
            or pace not in seconds_by_pace
            or not isinstance(size_bands, Sequence)
            or isinstance(reference, bool) or not isinstance(reference, int) or reference <= 0
            or isinstance(floor, bool) or not isinstance(floor, int) or floor <= 0
            or isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < floor
        ):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        base_seconds = seconds_by_pace.get(pace)
        personnel = formation.get("personnel_total")
        if isinstance(base_seconds, bool) or not isinstance(base_seconds, int) or base_seconds <= 0:
            raise CommandRejectedError("battlefield_mechanics_invalid")
        if isinstance(personnel, bool) or not isinstance(personnel, int) or personnel <= 0:
            raise CommandRejectedError("battlefield_formation_invalid")
        size_milli = None
        for band in size_bands:
            if not isinstance(band, Mapping):
                continue
            cap, value = band.get("max_personnel"), band.get("time_milli")
            if isinstance(cap, int) and not isinstance(cap, bool) and personnel <= cap and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                size_milli = value
                break
        if size_milli is None:
            raise CommandRejectedError("battlefield_mechanics_invalid")
        movement = self._battlefield_movement_score(formation)
        movement_milli = max(floor, min(ceiling, (movement * 1000) // reference))
        seconds = math.ceil(base_seconds * distance_units * size_milli * 1000 / (1000 * movement_milli))
        return max(30, int(seconds))

    def _battlefield_effective_power(self, formation_ref: str, order: str) -> int:
        _path, _force_ref, formation, _force = self._battlefield_formation_bundle(formation_ref)
        mechanics = self._battlefield_mechanics()
        orders = mechanics.get("orders")
        order_rule = orders.get(order) if isinstance(orders, Mapping) else None
        power_milli = order_rule.get("power_milli") if isinstance(order_rule, Mapping) else None
        if isinstance(power_milli, bool) or not isinstance(power_milli, int) or power_milli <= 0:
            raise CommandRejectedError("battlefield_mechanics_invalid")
        personnel = formation.get("personnel_total")
        readiness = formation.get("readiness")
        cohesion = formation.get("cohesion")
        morale = formation.get("morale")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (personnel, readiness, cohesion, morale)):
            raise CommandRejectedError("battlefield_formation_invalid")
        capability_total = capability_weight = 0
        for component in formation.get("components", []):
            if not isinstance(component, Mapping):
                continue
            count = component.get("count")
            cap = component.get("capability_state")
            fundamentals = cap.get("fundamentals") if isinstance(cap, Mapping) else None
            if isinstance(count, bool) or not isinstance(count, int) or count <= 0 or not isinstance(fundamentals, Mapping):
                continue
            values = [fundamentals.get(k) for k in ("combat", "tactics", "team_coordination")]
            numeric = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
            if not numeric:
                continue
            capability_total += count
            capability_weight += int(round(sum(numeric) / len(numeric) * count))
        capability = capability_weight // capability_total if capability_total else 50
        condition_milli = max(100, min(1000, (max(0, readiness) + max(0, cohesion) + max(0, morale)) * 10 // 3))
        capability_milli = max(500, min(1500, 500 + capability * 10))
        return max(1, personnel * condition_milli * capability_milli * power_milli // 1_000_000)

    def _battlefield_actor_sides(
        self,
        actor_ref: str,
        conflict: Mapping[str, Any],
        battlefield: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> set[str]:
        result: set[str] = set()
        sides = [side for side in battlefield.get("side_refs", []) if isinstance(side, str)]
        cache = _OwnerResolutionCache()
        authority = self._domain_authority(cache=cache)
        for side in sides:
            try:
                decision = authority.owner_leadership(holder_ref=actor_ref, owner_ref=side)
            except Exception:
                decision = None
            if decision is not None and decision.allowed:
                result.add(side)
        for assignment in battlefield.get("assignments", {}).values():
            if not isinstance(assignment, Mapping):
                continue
            side_ref = assignment.get("side_ref")
            formation_ref = assignment.get("formation_ref")
            if side_ref in result or not isinstance(side_ref, str) or not isinstance(formation_ref, str):
                continue
            try:
                _path, force_ref, formation, force_view = self._battlefield_formation_bundle(formation_ref)
                personnel = int(formation.get("personnel_total", 0))
                grant = authority.force_grant(grantor_ref=actor_ref, force_record=force_view)
                allowed = grant.allowed
                if not allowed:
                    allowed = authority.force_command(
                        commander_ref=actor_ref,
                        force_ref=force_ref,
                        operational_attachment_ref=formation_ref,
                        named_actor_refs=(),
                        committed_count=max(0, personnel),
                        effective_at=str(current_time),
                    ).allowed
                if allowed:
                    result.add(side_ref)
            except (CommandRejectedError, ValueError, TypeError):
                continue
        return result

    @staticmethod
    def _battlefield_find(
        registry: Mapping[str, Any], conflict_ref: str, front_ref: str, battlefield_ref: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        records = registry.get("records")
        conflict = records.get(conflict_ref) if isinstance(records, Mapping) else None
        fronts = conflict.get("fronts") if isinstance(conflict, Mapping) else None
        front = fronts.get(front_ref) if isinstance(fronts, Mapping) else None
        battlefields = front.get("battlefields") if isinstance(front, Mapping) else None
        battlefield = battlefields.get(battlefield_ref) if isinstance(battlefields, Mapping) else None
        if not isinstance(conflict, dict) or not isinstance(front, dict) or not isinstance(battlefield, dict):
            raise CommandRejectedError("battlefield_unresolved")
        return conflict, front, battlefield

    @staticmethod
    def _battlefield_remove_from_sectors(battlefield: Dict[str, Any], formation_ref: str) -> None:
        for sector in battlefield.get("sectors", {}).values():
            if not isinstance(sector, dict):
                continue
            refs = sector.get("formation_refs")
            if isinstance(refs, list) and formation_ref in refs:
                sector["formation_refs"] = [ref for ref in refs if ref != formation_ref]

    def _battlefield_start_leg(
        self,
        battlefield: Dict[str, Any],
        assignment: Dict[str, Any],
        formation: Mapping[str, Any],
        *,
        at: CampaignTime,
        next_index: int,
    ) -> None:
        path = assignment.get("path_sector_refs")
        pace = assignment.get("pace")
        if not isinstance(path, list) or not all(isinstance(ref, str) for ref in path) or pace not in _VALID_PACES:
            raise CommandRejectedError("battlefield_redeployment_invalid")
        if next_index <= 0 or next_index >= len(path):
            raise CommandRejectedError("battlefield_redeployment_invalid")
        graph = self._battlefield_edges(battlefield)
        source, target = path[next_index - 1], path[next_index]
        distance = graph.get(source, {}).get(target)
        if not isinstance(distance, int) or distance <= 0:
            raise CommandRejectedError("battlefield_sector_path_unresolved")
        seconds = self._battlefield_leg_seconds(formation, distance, str(pace))
        assignment["path_index"] = next_index - 1
        assignment["leg_eta_at"] = str(_campaign_add_seconds(at, seconds))
        assignment["transit_from_sector_ref"] = source
        assignment["transit_to_sector_ref"] = target
        assignment["sector_ref"] = None
        assignment["status"] = "redeploying"
        assignment["updated_at"] = str(at)

    def _battlefield_report_latency(self, battlefield: Mapping[str, Any], sector_ref: str, side_ref: str) -> int:
        mechanics = self._battlefield_mechanics()
        rules = mechanics.get("information")
        if not isinstance(rules, Mapping):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        threshold = rules.get("sensor_method_threshold")
        sensor_latency = rules.get("sensor_latency_seconds")
        messenger_latency = rules.get("messenger_latency_seconds")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (threshold, sensor_latency, messenger_latency)):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        sector = battlefield.get("sectors", {}).get(sector_ref)
        refs = sector.get("formation_refs") if isinstance(sector, Mapping) else None
        max_sensory = 0
        for formation_ref in refs or []:
            assignment = battlefield.get("assignments", {}).get(formation_ref)
            if not isinstance(assignment, Mapping) or assignment.get("side_ref") != side_ref:
                continue
            try:
                _path, _force, formation, _force_view = self._battlefield_formation_bundle(formation_ref)
            except CommandRejectedError:
                continue
            max_sensory = max(max_sensory, self._battlefield_sensory_score(formation))
        return sensor_latency if max_sensory >= threshold else messenger_latency

    def _battlefield_command_latency(
        self,
        *,
        actor_ref: str,
        battlefield: Mapping[str, Any],
        assignment: Mapping[str, Any],
    ) -> int:
        sector_ref = assignment.get("sector_ref")
        side_ref = assignment.get("side_ref")
        formation_ref = assignment.get("formation_ref")
        if not isinstance(sector_ref, str) or not isinstance(side_ref, str) or not isinstance(formation_ref, str):
            raise CommandRejectedError("battlefield_command_route_invalid")
        try:
            _path, _force_ref, formation, _force_view = self._battlefield_formation_bundle(formation_ref)
        except CommandRejectedError as exc:
            raise CommandRejectedError("battlefield_command_route_invalid") from exc
        # A commander physically embodied in the formation can act immediately.
        # Otherwise an order uses the same lawful sensor/messenger path as a
        # report from that sector. This models Naruto-style rapid communications
        # without teleporting command decisions across a Fourth-War battlefield.
        if formation.get("commander_ref") == actor_ref:
            return 0
        return self._battlefield_report_latency(battlefield, sector_ref, side_ref)

    @staticmethod
    def _battlefield_report_id(battlefield_ref: str, sector_ref: str, side_ref: str, level: str, at: CampaignTime) -> str:
        stamp = str(at).replace("-", "").replace(":", "").replace("T", "").replace("Z", "")
        safe_side = side_ref.replace(":", ".").replace("/", ".")
        safe_sector = sector_ref.rsplit(".", 1)[-1]
        return f"report.{battlefield_ref}.{safe_sector}.{safe_side}.{level}.{stamp}"

    def _battlefield_queue_report(
        self,
        battlefield: Dict[str, Any],
        *,
        sector_ref: str,
        side_ref: str,
        level: str,
        pressure_milli: int,
        at: CampaignTime,
        summary: str,
    ) -> Dict[str, Any]:
        report_id = self._battlefield_report_id(str(battlefield["id"]), sector_ref, side_ref, level, at)
        reports = battlefield.setdefault("reports", [])
        for row in reports:
            if isinstance(row, Mapping) and row.get("id") == report_id:
                return dict(row)
        latency = self._battlefield_report_latency(battlefield, sector_ref, side_ref)
        record = {
            "id": report_id,
            "sector_ref": sector_ref,
            "target_side_ref": side_ref,
            "level": level,
            "pressure_milli": max(0, min(1000, int(pressure_milli))),
            "created_at": str(at),
            "deliver_at": str(_campaign_add_seconds(at, latency)),
            # Always enter the delivery queue, even at zero latency. The same
            # settlement boundary then records delivery and exposes it exactly
            # once; pre-marking it delivered would bypass the player-facing
            # delivery pass below.
            "delivered_at": None,
            "status": "queued",
            "summary": summary,
        }
        reports.append(record)
        reports.sort(key=lambda row: (str(row.get("created_at", "")), str(row.get("id", ""))))
        return record

    def _battlefield_sector_rates(self, battlefield: Mapping[str, Any], sector: Mapping[str, Any]) -> Dict[str, int]:
        mechanics = self._battlefield_mechanics()
        pressure_rules = mechanics.get("pressure")
        order_rules = mechanics.get("orders")
        if not isinstance(pressure_rules, Mapping) or not isinstance(order_rules, Mapping):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        base = pressure_rules.get("base_milli_per_hour")
        imbalance_weight = pressure_rules.get("imbalance_weight_milli")
        recovery = pressure_rules.get("recovery_milli_per_hour")
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (base, imbalance_weight, recovery)):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        sides = [side for side in battlefield.get("side_refs", []) if isinstance(side, str)]
        powers = {side: 0 for side in sides}
        pressure_factors = {side: 1000 for side in sides}
        formation_refs = sector.get("formation_refs")
        for formation_ref in formation_refs or []:
            assignment = battlefield.get("assignments", {}).get(formation_ref)
            if not isinstance(assignment, Mapping) or assignment.get("status") not in ("holding", "contact"):
                continue
            side_ref = assignment.get("side_ref")
            order = assignment.get("order", "hold")
            if side_ref not in powers or order not in _VALID_ORDERS:
                continue
            powers[side_ref] += self._battlefield_effective_power(formation_ref, str(order))
            rule = order_rules.get(order)
            factor = rule.get("pressure_milli") if isinstance(rule, Mapping) else None
            if isinstance(factor, int) and not isinstance(factor, bool) and factor > 0:
                pressure_factors[side_ref] = max(pressure_factors[side_ref], factor)
        active = [side for side in sides if powers.get(side, 0) > 0]
        if len(active) < 2:
            return {side: (-recovery if powers.get(side, 0) > 0 else 0) for side in sides}
        total = sum(powers.values())
        rates: Dict[str, int] = {}
        for side in sides:
            enemy_power = sum(power for other, power in powers.items() if other != side)
            if powers.get(side, 0) <= 0:
                rates[side] = max(base, base * 2)
                continue
            enemy_share_milli = (enemy_power * 1000) // max(1, total)
            own_share_milli = (powers[side] * 1000) // max(1, total)
            imbalance = max(0, enemy_share_milli - own_share_milli)
            enemy_pressure = max(
                (pressure_factors[other] for other in sides if other != side and powers.get(other, 0) > 0),
                default=1000,
            )
            rate = base * max(250, enemy_share_milli * 2) // 1000
            rate = rate * (1000 + imbalance * imbalance_weight // 1000) // 1000
            rate = rate * enemy_pressure // 1000
            rates[side] = max(1, rate)
        return rates

    def _battlefield_next_boundary_time(
        self,
        *,
        actor_ref: str,
        current_time: CampaignTime,
        requested_target: CampaignTime,
        registry: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[Optional[CampaignTime], Optional[Dict[str, Any]]]:
        if registry is None:
            try:
                registry = self.repository.read_json(_CONFLICT_REGISTRY_PATH)
            except FileNotFoundError:
                # Minimal/test campaigns may lawfully omit strategic conflicts.
                # Absence means there is no operational battlefield work to settle.
                return None, None
            except ValueError as exc:
                raise CommandRejectedError("conflict_registry_invalid") from exc
        records = registry.get("records") if isinstance(registry, Mapping) else None
        if not isinstance(records, Mapping):
            raise CommandRejectedError("conflict_registry_invalid")
        mechanics = self._battlefield_mechanics()
        pressure_rules = mechanics.get("pressure")
        critical = pressure_rules.get("critical_milli") if isinstance(pressure_rules, Mapping) else None
        collapse = pressure_rules.get("collapse_milli") if isinstance(pressure_rules, Mapping) else None
        if any(isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= 1000 for v in (critical, collapse)) or critical >= collapse:
            raise CommandRejectedError("battlefield_mechanics_invalid")
        candidates: list[tuple[CampaignTime, Dict[str, Any]]] = []
        for conflict_ref, conflict in records.items():
            if not isinstance(conflict, Mapping) or conflict.get("status") != "active":
                continue
            for front_ref, front in (conflict.get("fronts") or {}).items():
                if not isinstance(front, Mapping) or front.get("status") != "active":
                    continue
                for battlefield_ref, battlefield in (front.get("battlefields") or {}).items():
                    if not isinstance(battlefield, Mapping) or battlefield.get("status") != "active":
                        continue
                    actor_sides = self._battlefield_actor_sides(actor_ref, conflict, battlefield, current_time)
                    for assignment in (battlefield.get("assignments") or {}).values():
                        if not isinstance(assignment, Mapping):
                            continue
                        if assignment.get("status") == "redeploying":
                            eta_text = assignment.get("leg_eta_at")
                            try:
                                eta = CampaignTime.parse(eta_text)
                            except (TypeError, ValueError):
                                raise CommandRejectedError("battlefield_redeployment_invalid")
                            if current_time < eta <= requested_target:
                                candidates.append((eta, {"kind": "redeployment_leg", "conflict_ref": conflict_ref, "front_ref": front_ref, "battlefield_ref": battlefield_ref, "formation_ref": assignment.get("formation_ref")}))
                        command_eta_text = assignment.get("command_eta_at")
                        if isinstance(command_eta_text, str) and (assignment.get("pending_order") in _VALID_ORDERS or isinstance(assignment.get("pending_redeployment"), Mapping)):
                            try:
                                command_eta = CampaignTime.parse(command_eta_text)
                            except ValueError as exc:
                                raise CommandRejectedError("battlefield_command_route_invalid") from exc
                            if current_time < command_eta <= requested_target:
                                candidates.append((command_eta, {"kind": "command_delivery", "conflict_ref": conflict_ref, "front_ref": front_ref, "battlefield_ref": battlefield_ref, "formation_ref": assignment.get("formation_ref")}))
                    for report in battlefield.get("reports", []):
                        if not isinstance(report, Mapping) or report.get("status") != "queued" or report.get("target_side_ref") not in actor_sides:
                            continue
                        try:
                            due = CampaignTime.parse(report.get("deliver_at"))
                        except (TypeError, ValueError):
                            raise CommandRejectedError("battlefield_report_invalid")
                        if current_time < due <= requested_target:
                            candidates.append((due, {"kind": "report_delivery", "conflict_ref": conflict_ref, "front_ref": front_ref, "battlefield_ref": battlefield_ref, "report_id": report.get("id")}))
                    for sector_ref, sector in (battlefield.get("sectors") or {}).items():
                        if not isinstance(sector, Mapping):
                            continue
                        rates = self._battlefield_sector_rates(battlefield, sector)
                        pressure = sector.get("pressure_milli")
                        reported = sector.get("reported_levels")
                        if not isinstance(pressure, Mapping) or not isinstance(reported, Mapping):
                            raise CommandRejectedError("battlefield_sector_invalid")
                        for side_ref in battlefield.get("side_refs", []):
                            if not isinstance(side_ref, str):
                                continue
                            value = pressure.get(side_ref, 0)
                            rate = rates.get(side_ref, 0)
                            levels = reported.get(side_ref, [])
                            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000 or not isinstance(levels, list):
                                raise CommandRejectedError("battlefield_sector_invalid")
                            if rate <= 0:
                                continue
                            for level, threshold in (("critical", critical), ("collapse", collapse)):
                                if level in levels or value >= threshold:
                                    continue
                                seconds = math.ceil((threshold - value) * 3600 / rate)
                                due = _campaign_add_seconds(current_time, seconds)
                                if current_time < due <= requested_target:
                                    candidates.append((due, {"kind": "pressure_threshold", "level": level, "side_ref": side_ref, "sector_ref": sector_ref, "conflict_ref": conflict_ref, "front_ref": front_ref, "battlefield_ref": battlefield_ref}))
        if not candidates:
            return None, None
        candidates.sort(key=lambda item: (str(item[0]), str(item[1].get("battlefield_ref", "")), str(item[1].get("kind", ""))))
        return candidates[0]

    def _settle_battlefields(
        self,
        registry: Dict[str, Any],
        *,
        actor_ref: str,
        start_time: CampaignTime,
        end_time: CampaignTime,
    ) -> Dict[str, Any]:
        elapsed_seconds = _seconds_between(start_time, end_time)
        records = registry.get("records")
        if not isinstance(records, dict):
            raise CommandRejectedError("conflict_registry_invalid")
        mechanics = self._battlefield_mechanics()
        pressure_rules = mechanics.get("pressure")
        critical = pressure_rules.get("critical_milli") if isinstance(pressure_rules, Mapping) else None
        collapse = pressure_rules.get("collapse_milli") if isinstance(pressure_rules, Mapping) else None
        recovery = pressure_rules.get("recovery_milli_per_hour") if isinstance(pressure_rules, Mapping) else None
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (critical, collapse, recovery)):
            raise CommandRejectedError("battlefield_mechanics_invalid")
        reviews: list[Dict[str, Any]] = []
        delivered_to_actor: list[Dict[str, Any]] = []
        player_decisions: list[Dict[str, Any]] = []
        changed = False

        for conflict_ref, conflict in records.items():
            if not isinstance(conflict, dict) or conflict.get("status") != "active":
                continue
            fronts = conflict.get("fronts")
            if not isinstance(fronts, dict):
                raise CommandRejectedError("conflict_registry_invalid")
            for front_ref, front in fronts.items():
                if not isinstance(front, dict) or front.get("status") != "active":
                    continue
                battlefields = front.get("battlefields")
                if not isinstance(battlefields, dict):
                    continue
                for battlefield_ref, battlefield in battlefields.items():
                    if not isinstance(battlefield, dict) or battlefield.get("status") != "active":
                        continue
                    actor_sides = self._battlefield_actor_sides(actor_ref, conflict, battlefield, start_time)
                    battlefield_changes: list[Dict[str, Any]] = []
                    # Pressure evolves continuously while formations remain in sectors.
                    for sector_ref, sector in (battlefield.get("sectors") or {}).items():
                        if not isinstance(sector, dict):
                            raise CommandRejectedError("battlefield_sector_invalid")
                        pressure = sector.get("pressure_milli")
                        reported = sector.get("reported_levels")
                        if not isinstance(pressure, dict) or not isinstance(reported, dict):
                            raise CommandRejectedError("battlefield_sector_invalid")
                        rates = self._battlefield_sector_rates(battlefield, sector)
                        old_values = dict(pressure)
                        for side_ref in battlefield.get("side_refs", []):
                            if not isinstance(side_ref, str):
                                continue
                            old = pressure.get(side_ref, 0)
                            rate = rates.get(side_ref, 0)
                            if isinstance(old, bool) or not isinstance(old, int) or not 0 <= old <= 1000:
                                raise CommandRejectedError("battlefield_sector_invalid")
                            if elapsed_seconds > 0:
                                if rate >= 0:
                                    new = min(1000, old + (rate * elapsed_seconds) // 3600)
                                else:
                                    new = max(0, old - (abs(rate) * elapsed_seconds) // 3600)
                                pressure[side_ref] = new
                            else:
                                new = old
                            levels = reported.setdefault(side_ref, [])
                            if not isinstance(levels, list):
                                raise CommandRejectedError("battlefield_sector_invalid")
                            for level, threshold in (("critical", critical), ("collapse", collapse)):
                                if old < threshold <= new and level not in levels:
                                    levels.append(level)
                                    levels.sort()
                                    report = self._battlefield_queue_report(
                                        battlefield,
                                        sector_ref=sector_ref,
                                        side_ref=side_ref,
                                        level=level,
                                        pressure_milli=new,
                                        at=end_time,
                                        summary=(
                                            f"{sector.get('name', sector_ref)} reached {level} operational pressure for {side_ref}. "
                                            "This is battlefield command information, not a casualty or strategic-territory result."
                                        ),
                                    )
                                    battlefield_changes.append({"kind": "pressure_threshold", "sector_ref": sector_ref, "side_ref": side_ref, "level": level, "report_id": report["id"]})
                                    changed = True
                        if pressure != old_values:
                            sector["last_changed_at"] = str(end_time)
                            changed = True
                        sector["status"] = "critical" if any(int(pressure.get(side, 0)) >= critical for side in battlefield.get("side_refs", []) if isinstance(side, str)) else "active"

                    # Orders from Wei or another remote commander take effect only
                    # when the saved command route reaches the formation. Pressure
                    # up to this boundary therefore used the previous standing order.
                    for formation_ref, assignment in sorted((battlefield.get("assignments") or {}).items()):
                        if not isinstance(formation_ref, str) or not isinstance(assignment, dict):
                            continue
                        eta_text = assignment.get("command_eta_at")
                        if not isinstance(eta_text, str):
                            continue
                        try:
                            command_eta = CampaignTime.parse(eta_text)
                        except ValueError as exc:
                            raise CommandRejectedError("battlefield_command_route_invalid") from exc
                        if command_eta > end_time:
                            continue
                        pending_redeployment = assignment.get("pending_redeployment")
                        pending_order = assignment.get("pending_order")
                        if isinstance(pending_redeployment, Mapping):
                            target_sector_ref = pending_redeployment.get("target_sector_ref")
                            pace = pending_redeployment.get("pace")
                            order = pending_redeployment.get("order")
                            source_sector_ref = assignment.get("sector_ref")
                            if (
                                not isinstance(target_sector_ref, str)
                                or target_sector_ref not in (battlefield.get("sectors") or {})
                                or pace not in _VALID_PACES
                                or order not in _VALID_ORDERS
                                or not isinstance(source_sector_ref, str)
                            ):
                                raise CommandRejectedError("battlefield_command_route_invalid")
                            _fp, _force_ref, formation, _force_view = self._battlefield_formation_bundle(formation_ref)
                            path, _distance = self._battlefield_shortest_path(battlefield, source_sector_ref, target_sector_ref)
                            self._battlefield_remove_from_sectors(battlefield, formation_ref)
                            assignment["path_sector_refs"] = path
                            assignment["target_sector_ref"] = target_sector_ref
                            assignment["pace"] = pace
                            assignment["order"] = order
                            assignment["pending_redeployment"] = None
                            assignment["pending_order"] = None
                            assignment["command_eta_at"] = None
                            self._battlefield_start_leg(battlefield, assignment, formation, at=end_time, next_index=1)
                            battlefield_changes.append({"kind": "redeployment_order_received", "formation_ref": formation_ref, "target_sector_ref": target_sector_ref, "leg_eta_at": assignment.get("leg_eta_at")})
                            changed = True
                        elif pending_order in _VALID_ORDERS:
                            assignment["order"] = pending_order
                            assignment["pending_order"] = None
                            assignment["command_eta_at"] = None
                            assignment["updated_at"] = str(end_time)
                            battlefield_changes.append({"kind": "order_received", "formation_ref": formation_ref, "order": pending_order})
                            changed = True

                    # Autonomous commanders retain bounded delegated initiative.
                    # They may commit one of their own saved reserve formations to
                    # a pressured sector, but never move a player-controlled formation
                    # or create casualties/territory outside the exact combat owners.
                    initiative = mechanics.get("delegated_initiative")
                    if isinstance(initiative, Mapping) and initiative.get("enabled") is True:
                        trigger = int(initiative.get("reserve_commit_pressure_milli", critical))
                        pace = str(initiative.get("reserve_pace", "standard"))
                        reserve_order = str(initiative.get("reserve_order", "attack"))
                        max_commits = max(0, int(initiative.get("max_autonomous_commits_per_boundary", 1)))
                        if pace not in _VALID_PACES or reserve_order not in _VALID_ORDERS or not 0 <= trigger <= 1000:
                            raise CommandRejectedError("battlefield_mechanics_invalid")
                        candidates: list[tuple[int, str, str]] = []
                        for sector_ref, sector in sorted((battlefield.get("sectors") or {}).items()):
                            if not isinstance(sector_ref, str) or not isinstance(sector, Mapping):
                                continue
                            pressure = sector.get("pressure_milli") if isinstance(sector.get("pressure_milli"), Mapping) else {}
                            for side_ref in battlefield.get("side_refs", []):
                                if not isinstance(side_ref, str):
                                    continue
                                value = int(pressure.get(side_ref, 0))
                                if value < trigger:
                                    continue
                                enemy_present = any(
                                    isinstance(other, Mapping)
                                    and other.get("status") in ("holding", "contact")
                                    and other.get("sector_ref") == sector_ref
                                    and other.get("side_ref") != side_ref
                                    for other in (battlefield.get("assignments") or {}).values()
                                )
                                if enemy_present:
                                    candidates.append((-value, side_ref, sector_ref))
                        commits = 0
                        for negative_pressure, side_ref, target_sector_ref in sorted(candidates):
                            if commits >= max_commits:
                                break
                            reserve_ref = None
                            for formation_ref, assignment in sorted((battlefield.get("assignments") or {}).items()):
                                if not isinstance(formation_ref, str) or not isinstance(assignment, Mapping):
                                    continue
                                if (
                                    assignment.get("side_ref") != side_ref
                                    or assignment.get("status") != "holding"
                                    or assignment.get("order") != "reserve"
                                    or assignment.get("pending_redeployment") is not None
                                    or assignment.get("command_eta_at") is not None
                                ):
                                    continue
                                try:
                                    self._battlefield_require_formation_authority(
                                        actor_ref=actor_ref, formation_ref=formation_ref, current_time=end_time
                                    )
                                except CommandRejectedError:
                                    reserve_ref = formation_ref
                                    break
                            if reserve_ref is None:
                                continue
                            assignment = battlefield["assignments"][reserve_ref]
                            source_sector_ref = assignment.get("sector_ref")
                            if not isinstance(source_sector_ref, str) or source_sector_ref == target_sector_ref:
                                continue
                            try:
                                path, _distance = self._battlefield_shortest_path(battlefield, source_sector_ref, target_sector_ref)
                            except CommandRejectedError:
                                continue
                            _fp, _force_ref, formation, _force_view = self._battlefield_formation_bundle(reserve_ref)
                            self._battlefield_remove_from_sectors(battlefield, reserve_ref)
                            assignment["path_sector_refs"] = path
                            assignment["target_sector_ref"] = target_sector_ref
                            assignment["pace"] = pace
                            assignment["order"] = reserve_order
                            self._battlefield_start_leg(battlefield, assignment, formation, at=end_time, next_index=1)
                            battlefield_changes.append({
                                "kind": "delegated_reserve_commitment",
                                "formation_ref": reserve_ref,
                                "side_ref": side_ref,
                                "target_sector_ref": target_sector_ref,
                                "pressure_milli": -negative_pressure,
                                "leg_eta_at": assignment.get("leg_eta_at"),
                            })
                            changed = True
                            commits += 1

                    # Complete any redeployment leg whose ETA has arrived.
                    for formation_ref, assignment in list((battlefield.get("assignments") or {}).items()):
                        if not isinstance(assignment, dict) or assignment.get("status") != "redeploying":
                            continue
                        try:
                            eta = CampaignTime.parse(assignment.get("leg_eta_at"))
                        except (TypeError, ValueError) as exc:
                            raise CommandRejectedError("battlefield_redeployment_invalid") from exc
                        if eta > end_time:
                            continue
                        path = assignment.get("path_sector_refs")
                        index = assignment.get("path_index")
                        if not isinstance(path, list) or isinstance(index, bool) or not isinstance(index, int) or index < 0 or index + 1 >= len(path):
                            raise CommandRejectedError("battlefield_redeployment_invalid")
                        arrived_sector_ref = path[index + 1]
                        sector = battlefield.get("sectors", {}).get(arrived_sector_ref)
                        if not isinstance(sector, dict):
                            raise CommandRejectedError("battlefield_sector_invalid")
                        refs = sector.setdefault("formation_refs", [])
                        enemy_present = any(
                            isinstance(other_ref, str)
                            and isinstance((battlefield.get("assignments") or {}).get(other_ref), Mapping)
                            and battlefield["assignments"][other_ref].get("side_ref") != assignment.get("side_ref")
                            and battlefield["assignments"][other_ref].get("status") in ("holding", "contact")
                            for other_ref in refs
                        )
                        target_ref = assignment.get("target_sector_ref")
                        if enemy_present:
                            refs.append(formation_ref) if formation_ref not in refs else None
                            refs.sort()
                            assignment["sector_ref"] = arrived_sector_ref
                            assignment["status"] = "contact"
                            assignment["leg_eta_at"] = None
                            assignment["transit_from_sector_ref"] = None
                            assignment["transit_to_sector_ref"] = None
                            assignment["updated_at"] = str(end_time)
                            report = self._battlefield_queue_report(
                                battlefield,
                                sector_ref=arrived_sector_ref,
                                side_ref=str(assignment.get("side_ref")),
                                level="intercept",
                                pressure_milli=int(sector.get("pressure_milli", {}).get(assignment.get("side_ref"), 0)),
                                at=end_time,
                                summary=f"{formation_ref} encountered opposing formations while redeploying through {sector.get('name', arrived_sector_ref)}.",
                            )
                            battlefield_changes.append({"kind": "intercept_contact", "formation_ref": formation_ref, "sector_ref": arrived_sector_ref, "report_id": report["id"]})
                            changed = True
                        elif arrived_sector_ref == target_ref:
                            refs.append(formation_ref) if formation_ref not in refs else None
                            refs.sort()
                            assignment["sector_ref"] = arrived_sector_ref
                            assignment["status"] = "holding"
                            assignment["path_index"] = index + 1
                            assignment["leg_eta_at"] = None
                            assignment["transit_from_sector_ref"] = None
                            assignment["transit_to_sector_ref"] = None
                            assignment["updated_at"] = str(end_time)
                            battlefield_changes.append({"kind": "redeployment_arrived", "formation_ref": formation_ref, "sector_ref": arrived_sector_ref})
                            changed = True
                        else:
                            _fp, _fr, formation, _fv = self._battlefield_formation_bundle(formation_ref)
                            self._battlefield_start_leg(battlefield, assignment, formation, at=end_time, next_index=index + 2)
                            battlefield_changes.append({"kind": "redeployment_leg_completed", "formation_ref": formation_ref, "through_sector_ref": arrived_sector_ref, "next_eta_at": assignment.get("leg_eta_at")})
                            changed = True

                    # Deliver reports whose real communication delay has elapsed.
                    for report in battlefield.get("reports", []):
                        if not isinstance(report, dict) or report.get("status") != "queued":
                            continue
                        try:
                            deliver_at = CampaignTime.parse(report.get("deliver_at"))
                        except (TypeError, ValueError) as exc:
                            raise CommandRejectedError("battlefield_report_invalid") from exc
                        if deliver_at <= end_time:
                            report["status"] = "delivered"
                            report["delivered_at"] = str(deliver_at)
                            changed = True
                            battlefield_changes.append({"kind": "report_delivered", "report_id": report.get("id"), "side_ref": report.get("target_side_ref")})
                            if report.get("target_side_ref") in actor_sides:
                                delivered_to_actor.append(copy.deepcopy(report))
                                if report.get("level") in ("collapse", "intercept"):
                                    player_decisions.append({
                                        "battlefield_ref": battlefield_ref,
                                        "sector_ref": report.get("sector_ref"),
                                        "report_id": report.get("id"),
                                        "level": report.get("level"),
                                    })
                    battlefield["last_settled_at"] = str(end_time)
                    if battlefield_changes:
                        battlefield["updated_at"] = str(end_time)
                        reviews.append({"conflict_ref": conflict_ref, "front_ref": front_ref, "battlefield_ref": battlefield_ref, "changes": battlefield_changes})

        return {
            "changed": changed,
            "reviews": reviews,
            "delivered_reports": delivered_to_actor,
            "player_decisions": player_decisions,
        }

    def _battlefield_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        spec = COMMAND_SPECS[command.command_type]
        allowed = set(spec.required_fields) | set(spec.optional_fields)
        if set(command.payload) - allowed or any(key not in command.payload for key in spec.required_fields):
            raise CommandRejectedError("battlefield_resolution_payload_fields_invalid")
        action = command.payload.get("action")
        if action not in ("open", "assign", "redeploy", "set_order", "close"):
            raise CommandRejectedError("battlefield_action_invalid")
        conflict_ref = _stable_id(command.payload.get("conflict_ref"), "battlefield_conflict_invalid", prefix="conflict.")
        front_ref = _stable_id(command.payload.get("front_ref"), "battlefield_front_invalid", prefix="front.")
        battlefield_ref = _stable_id(command.payload.get("battlefield_ref"), "battlefield_ref_invalid", prefix="battlefield.")
        try:
            registry = copy.deepcopy(self.repository.read_json(_CONFLICT_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("conflict_registry_invalid") from exc
        if not isinstance(registry, dict):
            raise CommandRejectedError("conflict_registry_invalid")
        records = registry.get("records")
        conflict = records.get(conflict_ref) if isinstance(records, dict) else None
        fronts = conflict.get("fronts") if isinstance(conflict, Mapping) else None
        front = fronts.get(front_ref) if isinstance(fronts, Mapping) else None
        if not isinstance(conflict, dict) or not isinstance(front, dict):
            raise CommandRejectedError("battlefield_front_unresolved")
        if conflict.get("status") != "active" or front.get("status") != "active":
            raise CommandRejectedError("battlefield_front_inactive")
        battlefields = front.setdefault("battlefields", {})
        if not isinstance(battlefields, dict):
            raise CommandRejectedError("battlefield_registry_invalid")
        authority_basis = None
        affected_ref = battlefield_ref
        event_kind = None

        if action == "open":
            if battlefield_ref in battlefields:
                raise CommandRejectedError("battlefield_exists")
            name = command.payload.get("name")
            place_ref = command.payload.get("place_ref")
            side_refs = command.payload.get("side_refs")
            layout_ref = command.payload.get("layout_ref")
            if not isinstance(name, str) or not name or len(name) > 200:
                raise CommandRejectedError("battlefield_name_invalid")
            if not isinstance(place_ref, str) or place_ref not in front.get("place_refs", []):
                raise CommandRejectedError("battlefield_place_invalid")
            if not isinstance(side_refs, Sequence) or isinstance(side_refs, (str, bytes, bytearray)) or len(side_refs) != 2 or len(set(side_refs)) != 2:
                raise CommandRejectedError("battlefield_sides_invalid")
            if any(not isinstance(side, str) or side not in conflict.get("side_refs", []) for side in side_refs):
                raise CommandRejectedError("battlefield_sides_invalid")
            authorized = []
            for side in side_refs:
                decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(holder_ref=command.actor_id, owner_ref=side)
                if decision.allowed:
                    authorized.append(decision.basis)
            if not authorized:
                raise CommandRejectedError("battlefield_authority_denied")
            authority_basis = authorized[0]
            mechanics = self._battlefield_mechanics()
            layouts = mechanics.get("layouts")
            layout = layouts.get(layout_ref) if isinstance(layouts, Mapping) else None
            if not isinstance(layout, Mapping):
                raise CommandRejectedError("battlefield_layout_invalid")
            sector_rows = layout.get("sectors")
            edge_rows = layout.get("edges")
            if not isinstance(sector_rows, list) or not isinstance(edge_rows, list):
                raise CommandRejectedError("battlefield_layout_invalid")
            sectors: Dict[str, Any] = {}
            key_to_ref: Dict[str, str] = {}
            for row in sector_rows:
                if not isinstance(row, Mapping) or not isinstance(row.get("key"), str) or not isinstance(row.get("name"), str):
                    raise CommandRejectedError("battlefield_layout_invalid")
                key = row["key"]
                sector_ref = self._battlefield_ref_key(battlefield_ref, key)
                key_to_ref[key] = sector_ref
                sectors[sector_ref] = {
                    "id": sector_ref,
                    "name": row["name"],
                    "status": "active",
                    "formation_refs": [],
                    "control_ref": None,
                    "pressure_milli": {str(side_refs[0]): 0, str(side_refs[1]): 0},
                    "reported_levels": {str(side_refs[0]): [], str(side_refs[1]): []},
                    "last_changed_at": str(current_time),
                }
            edges = []
            for row in edge_rows:
                if not isinstance(row, Mapping):
                    raise CommandRejectedError("battlefield_layout_invalid")
                a, b, distance = row.get("a"), row.get("b"), row.get("distance_units")
                if a not in key_to_ref or b not in key_to_ref or isinstance(distance, bool) or not isinstance(distance, int) or distance <= 0:
                    raise CommandRejectedError("battlefield_layout_invalid")
                edges.append({"a": key_to_ref[a], "b": key_to_ref[b], "distance_units": distance})
            battlefield = {
                "id": battlefield_ref,
                "name": name,
                "status": "active",
                "place_ref": place_ref,
                "side_refs": [str(side_refs[0]), str(side_refs[1])],
                "layout_ref": layout_ref,
                "opened_at": str(current_time),
                "closed_at": None,
                "last_settled_at": str(current_time),
                "updated_at": str(current_time),
                "sectors": sectors,
                "sector_edges": edges,
                "assignments": {},
                "reports": [],
            }
            # Bootstrap every exact front formation physically present at this
            # battlefield anchor, including opposing formations. This is neutral
            # system-derived geometry, not the player choosing enemy deployment.
            # Later movement uses timed redeployment and autonomous commanders
            # can react under their own authority.
            frontline_keys = [str(key) for key in layout.get("frontline_keys", []) if str(key) in key_to_ref]
            if not frontline_keys:
                excluded = {str(layout.get("reserve_key", "")), str(layout.get("command_key", ""))}
                frontline_keys = [key for key in key_to_ref if key not in excluded]
            if not frontline_keys:
                raise CommandRejectedError("battlefield_layout_invalid")
            reserve_ref = key_to_ref.get(str(layout.get("reserve_key", "")))
            graph = self._location_graph()
            battle_anchor = graph.anchor(place_ref)
            by_side: Dict[str, list[tuple[str, str]]] = {str(side_refs[0]): [], str(side_refs[1]): []}
            for formation_ref in sorted(str(ref) for ref in front.get("formation_refs", []) if isinstance(ref, str)):
                _fp, force_ref, formation, force_view = self._battlefield_formation_bundle(formation_ref)
                formation_anchor = graph.anchor(formation.get("location_ref")) if isinstance(formation.get("location_ref"), str) else None
                if battle_anchor is None or formation_anchor != battle_anchor:
                    continue
                side_ref = self._battlefield_side_for_force(force_ref, force_view)
                if side_ref in by_side:
                    by_side[side_ref].append((formation_ref, force_ref))
            for side_ref, rows in sorted(by_side.items()):
                for index, (formation_ref, force_ref) in enumerate(rows):
                    use_reserve = isinstance(reserve_ref, str) and len(rows) >= 4 and index == len(rows) - 1
                    sector_ref = reserve_ref if use_reserve else key_to_ref[frontline_keys[index % len(frontline_keys)]]
                    order = "reserve" if use_reserve else "hold"
                    battlefield["assignments"][formation_ref] = {
                        "formation_ref": formation_ref,
                        "force_ref": force_ref,
                        "side_ref": side_ref,
                        "sector_ref": sector_ref,
                        "status": "holding",
                        "order": order,
                        "pending_order": None,
                        "pending_redeployment": None,
                        "command_eta_at": None,
                        "pace": None,
                        "target_sector_ref": None,
                        "path_sector_refs": [],
                        "path_index": None,
                        "leg_eta_at": None,
                        "transit_from_sector_ref": None,
                        "transit_to_sector_ref": None,
                        "assigned_at": str(current_time),
                        "updated_at": str(current_time),
                    }
                    sectors[sector_ref].setdefault("formation_refs", []).append(formation_ref)
            for sector in sectors.values():
                if isinstance(sector, dict) and isinstance(sector.get("formation_refs"), list):
                    sector["formation_refs"] = sorted(set(str(ref) for ref in sector["formation_refs"]))
            battlefields[battlefield_ref] = battlefield
            event_kind = "battlefield_opened"
        else:
            battlefield = battlefields.get(battlefield_ref)
            if not isinstance(battlefield, dict):
                raise CommandRejectedError("battlefield_unresolved")
            if battlefield.get("status") != "active" and action != "close":
                raise CommandRejectedError("battlefield_inactive")
            assignments = battlefield.get("assignments")
            sectors = battlefield.get("sectors")
            if not isinstance(assignments, dict) or not isinstance(sectors, dict):
                raise CommandRejectedError("battlefield_registry_invalid")
            if action == "assign":
                formation_ref = _stable_id(command.payload.get("formation_ref"), "battlefield_formation_invalid", prefix="formation.")
                sector_ref = _stable_id(command.payload.get("sector_ref"), "battlefield_sector_invalid", prefix=battlefield_ref + ".sector.")
                order = command.payload.get("order", "hold")
                if sector_ref not in sectors or order not in _VALID_ORDERS:
                    raise CommandRejectedError("battlefield_assignment_invalid")
                if formation_ref not in front.get("formation_refs", []):
                    raise CommandRejectedError("battlefield_formation_not_on_front")
                existing = assignments.get(formation_ref)
                if isinstance(existing, Mapping):
                    if (
                        battlefield.get("last_settled_at") != battlefield.get("opened_at")
                        or existing.get("status") != "holding"
                        or existing.get("command_eta_at") is not None
                    ):
                        raise CommandRejectedError("battlefield_assigned_formation_requires_redeployment")
                    self._battlefield_remove_from_sectors(battlefield, formation_ref)
                _path, force_ref, formation, force_view, authority_basis = self._battlefield_require_formation_authority(
                    actor_ref=command.actor_id, formation_ref=formation_ref, current_time=current_time
                )
                side_ref = self._battlefield_side_for_force(force_ref, force_view)
                if side_ref not in battlefield.get("side_refs", []):
                    raise CommandRejectedError("battlefield_formation_not_a_side")
                graph = self._location_graph()
                formation_anchor = graph.anchor(formation.get("location_ref")) if isinstance(formation.get("location_ref"), str) else None
                battle_anchor = graph.anchor(battlefield.get("place_ref")) if isinstance(battlefield.get("place_ref"), str) else None
                if formation_anchor is None or battle_anchor is None or formation_anchor != battle_anchor:
                    raise CommandRejectedError("battlefield_formation_not_present")
                for other_front in conflict.get("fronts", {}).values():
                    if not isinstance(other_front, Mapping):
                        continue
                    for other_battlefield_ref, other_battlefield in (other_front.get("battlefields") or {}).items():
                        if other_battlefield_ref == battlefield_ref or not isinstance(other_battlefield, Mapping) or other_battlefield.get("status") != "active":
                            continue
                        if formation_ref in (other_battlefield.get("assignments") or {}):
                            raise CommandRejectedError("battlefield_formation_already_committed")
                assignments[formation_ref] = {
                    "formation_ref": formation_ref,
                    "force_ref": force_ref,
                    "side_ref": side_ref,
                    "sector_ref": sector_ref,
                    "status": "holding",
                    "order": order,
                    "pending_order": None,
                    "pending_redeployment": None,
                    "command_eta_at": None,
                    "pace": None,
                    "target_sector_ref": None,
                    "path_sector_refs": [],
                    "path_index": None,
                    "leg_eta_at": None,
                    "transit_from_sector_ref": None,
                    "transit_to_sector_ref": None,
                    "assigned_at": str(current_time),
                    "updated_at": str(current_time),
                }
                refs = sectors[sector_ref].setdefault("formation_refs", [])
                if formation_ref not in refs:
                    refs.append(formation_ref); refs.sort()
                event_kind = "battlefield_formation_assigned"
                affected_ref = formation_ref
            elif action == "redeploy":
                formation_ref = _stable_id(command.payload.get("formation_ref"), "battlefield_formation_invalid", prefix="formation.")
                target_sector_ref = _stable_id(command.payload.get("target_sector_ref"), "battlefield_sector_invalid", prefix=battlefield_ref + ".sector.")
                pace = command.payload.get("pace", "standard")
                order = command.payload.get("order", "hold")
                assignment = assignments.get(formation_ref)
                if not isinstance(assignment, dict) or target_sector_ref not in sectors or pace not in _VALID_PACES or order not in _VALID_ORDERS:
                    raise CommandRejectedError("battlefield_redeployment_invalid")
                if assignment.get("status") == "redeploying" or assignment.get("command_eta_at") is not None:
                    raise CommandRejectedError("battlefield_formation_already_redeploying")
                source_sector_ref = assignment.get("sector_ref")
                if not isinstance(source_sector_ref, str) or source_sector_ref not in sectors or source_sector_ref == target_sector_ref:
                    raise CommandRejectedError("battlefield_redeployment_invalid")
                _path, _force_ref, formation, _force_view, authority_basis = self._battlefield_require_formation_authority(
                    actor_ref=command.actor_id, formation_ref=formation_ref, current_time=current_time
                )
                latency = self._battlefield_command_latency(
                    actor_ref=command.actor_id, battlefield=battlefield, assignment=assignment
                )
                if latency <= 0:
                    path, _distance = self._battlefield_shortest_path(battlefield, source_sector_ref, target_sector_ref)
                    self._battlefield_remove_from_sectors(battlefield, formation_ref)
                    assignment["path_sector_refs"] = path
                    assignment["target_sector_ref"] = target_sector_ref
                    assignment["pace"] = pace
                    assignment["order"] = order
                    assignment["pending_redeployment"] = None
                    assignment["pending_order"] = None
                    assignment["command_eta_at"] = None
                    self._battlefield_start_leg(battlefield, assignment, formation, at=current_time, next_index=1)
                    event_kind = "battlefield_redeployment_started"
                else:
                    assignment["pending_redeployment"] = {
                        "target_sector_ref": target_sector_ref,
                        "pace": pace,
                        "order": order,
                    }
                    assignment["pending_order"] = None
                    assignment["command_eta_at"] = str(_campaign_add_seconds(current_time, latency))
                    assignment["updated_at"] = str(current_time)
                    event_kind = "battlefield_redeployment_order_issued"
                affected_ref = formation_ref
            elif action == "set_order":
                formation_ref = _stable_id(command.payload.get("formation_ref"), "battlefield_formation_invalid", prefix="formation.")
                order = command.payload.get("order")
                assignment = assignments.get(formation_ref)
                if not isinstance(assignment, dict) or order not in _VALID_ORDERS or assignment.get("status") == "redeploying" or assignment.get("command_eta_at") is not None:
                    raise CommandRejectedError("battlefield_order_invalid")
                _path, _force_ref, _formation, _force_view, authority_basis = self._battlefield_require_formation_authority(
                    actor_ref=command.actor_id, formation_ref=formation_ref, current_time=current_time
                )
                latency = self._battlefield_command_latency(
                    actor_ref=command.actor_id, battlefield=battlefield, assignment=assignment
                )
                if latency <= 0:
                    assignment["order"] = order
                    assignment["pending_order"] = None
                    assignment["command_eta_at"] = None
                    event_kind = "battlefield_order_changed"
                else:
                    assignment["pending_order"] = order
                    assignment["pending_redeployment"] = None
                    assignment["command_eta_at"] = str(_campaign_add_seconds(current_time, latency))
                    event_kind = "battlefield_order_issued"
                assignment["updated_at"] = str(current_time)
                affected_ref = formation_ref
            elif action == "close":
                if conflict.get("status") == "active" and front.get("status") == "active":
                    raise CommandRejectedError("battlefield_close_requires_inactive_front_or_conflict")
                battlefield["status"] = "closed"
                battlefield["closed_at"] = str(current_time)
                battlefield["updated_at"] = str(current_time)
                event_kind = "battlefield_closed"
            else:
                raise CommandRejectedError("battlefield_action_invalid")

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind=str(event_kind),
            at=current_time,
            host_refs=(conflict_ref, front_ref, battlefield_ref),
            actor_refs=(command.actor_id,),
            affected_owner_refs=(_CONFLICT_REGISTRY_PATH,),
            material_consequence_refs=(affected_ref,),
            classification="restricted",
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.battlefield_resolution",
        )
        scene = copy.deepcopy(self._scene_base(current_time))
        decision = scene.get("decision_required")
        if isinstance(decision, str) and decision.startswith("battlefield:"):
            scene["decision_required"] = None
            scene["time_passage_allowed"] = True
        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            self.scene_path: _json_bytes(scene),
            _CONFLICT_REGISTRY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("battlefield write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_CONFLICT_REGISTRY_PATH)
            records_after = staged.get("records") if isinstance(staged, Mapping) else None
            conflict_after = records_after.get(conflict_ref) if isinstance(records_after, Mapping) else None
            front_after = conflict_after.get("fronts", {}).get(front_ref) if isinstance(conflict_after, Mapping) else None
            battlefield_after = front_after.get("battlefields", {}).get(battlefield_ref) if isinstance(front_after, Mapping) else None
            if not isinstance(battlefield_after, Mapping):
                raise ValueError("battlefield after-image missing")

        battlefield = battlefields[battlefield_ref]
        return _BuiltPlan(
            code="battlefield_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "action": action,
                "conflict_ref": conflict_ref,
                "front_ref": front_ref,
                "battlefield_ref": battlefield_ref,
                "battlefield_status": battlefield.get("status"),
                "affected_ref": affected_ref,
                "authority_basis": authority_basis,
                "semantic_event_id": event_id,
                "world_time": str(current_time),
            },
            validator=validate,
        )
