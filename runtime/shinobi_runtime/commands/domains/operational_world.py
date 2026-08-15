"""Conserved commerce, research, security-network, and mission-market commands.

These domains turn generic autonomous labels into exact operational state while
preserving the existing authorities for inventory, money, world events, places,
routes, custody, and information.  They deliberately do not invent hidden
outcomes: cargo movement is conserved, research progression is deterministic,
security alarms require evidence, and mission-market demand is evidence-driven.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes, _stable_id
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import CUSTODY_REGISTRY_PATH as _CUSTODY_PATH
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.reducers import InformationClaim, deliver_claim
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.information import InformationStore
from shinobi_runtime.security import apply_route_security_detection
from shinobi_runtime.diplomacy import DIPLOMACY_PATH as _DIPLOMACY_PATH, trade_tariff_multiplier_milli
from shinobi_runtime.tx.manifest import TransactionManifest

_COMMERCE_PATH = "state/reg/commerce.json"
_RESEARCH_PATH = "state/reg/research.json"
_SECURITY_PATH = "state/reg/security-networks.json"
_MISSION_MARKET_PATH = "state/reg/mission-markets.json"
_INVENTORY_PATH = "state/inventory/registry.json"
_ECONOMY_WORLD_PATH = "state/world/economies-and-mission-markets.json"
_GOVERNANCE_PATH = "state/reg/governance.json"
_MECHANICS_PATH = "game/data/mechanics/operational-world.json"


class OperationalWorldCommandsMixin:
    @staticmethod
    def _op_visibility(value: Any) -> str:
        if value not in ("public", "restricted", "secret"):
            raise CommandRejectedError("operational_visibility_invalid")
        return str(value)

    @staticmethod
    def _positive_int(value: Any, code: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise CommandRejectedError(code)
        return value

    def _operational_mechanics(self) -> Mapping[str, Any]:
        try:
            row = self.repository.read_json(_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("operational_world_mechanics_invalid") from exc
        if not isinstance(row, Mapping) or row.get("schema") != "operational-world-mechanics":
            raise CommandRejectedError("operational_world_mechanics_invalid")
        return row

    def _operational_registry(self, path: str, schema: str, map_key: str) -> Dict[str, Any]:
        try:
            row = copy.deepcopy(self.repository.read_json(path))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError(f"{schema}_invalid") from exc
        if not isinstance(row, dict) or row.get("schema") != schema or not isinstance(row.get(map_key), dict):
            raise CommandRejectedError(f"{schema}_invalid")
        return row

    def _operational_authority(self, actor_ref: str, owner_ref: str) -> str:
        owner_ref = _stable_id(owner_ref, "operational_owner_invalid")
        if actor_ref == owner_ref:
            return "self"
        try:
            decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=actor_ref, owner_ref=owner_ref
            )
        except CommandRejectedError as exc:
            raise CommandRejectedError("operational_authority_denied") from exc
        if not decision.allowed:
            raise CommandRejectedError("operational_authority_denied")
        return decision.basis

    def _operational_controls_owner(self, actor_ref: str, owner_ref: str) -> bool:
        if actor_ref == owner_ref:
            return True
        try:
            decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=actor_ref, owner_ref=owner_ref
            )
            if decision.allowed:
                return True
        except CommandRejectedError:
            pass
        try:
            _path, _digest, view = self._resolve_covered_owner_view(owner_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return False
        parent_ref = view.get("owner_ref") if isinstance(view, Mapping) else None
        if not isinstance(parent_ref, str) or not parent_ref or parent_ref == owner_ref:
            return False
        try:
            decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=actor_ref, owner_ref=parent_ref
            )
        except CommandRejectedError:
            return False
        return bool(decision.allowed)

    def _real_route(self, route_ref: str) -> Mapping[str, Any]:
        route_ref = _stable_id(route_ref, "commerce_route_invalid", prefix="route_")
        match = next((row for row in self._location_graph().routes if row.get("id") == route_ref), None)
        if not isinstance(match, Mapping):
            raise CommandRejectedError("commerce_route_invalid")
        return match

    def _real_event(self, event_ref: str, code: str = "operational_evidence_invalid") -> str:
        event_ref = _stable_id(event_ref, code, prefix="event.")
        if self._world_event_by_id(event_ref) is None:
            raise CommandRejectedError(code)
        return event_ref

    @staticmethod
    def _inventory_item_total(holders: Mapping[str, Any], item_ref: str) -> int:
        total = 0
        for row in holders.values():
            if not isinstance(row, Mapping):
                continue
            value = row.get(item_ref, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CommandRejectedError("inventory_registry_invalid")
            total += value
        return total

    @staticmethod
    def _inventory_currency_total(holders: Mapping[str, Any]) -> int:
        total = 0
        for row in holders.values():
            if not isinstance(row, Mapping):
                continue
            value = row.get("currency.ryo", 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CommandRejectedError("inventory_registry_invalid")
            total += value
        return total

    @staticmethod
    def _move_inventory_item(holders: Dict[str, Any], source_ref: str, destination_ref: str, item_ref: str, quantity: int) -> None:
        source = holders.get(source_ref)
        if not isinstance(source, dict):
            raise CommandRejectedError("commerce_source_holder_missing")
        carried = source.get(item_ref, 0)
        if isinstance(carried, bool) or not isinstance(carried, int) or carried < quantity:
            raise CommandRejectedError("commerce_cargo_insufficient")
        destination = holders.setdefault(destination_ref, {})
        if not isinstance(destination, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        current = destination.get(item_ref, 0)
        if isinstance(current, bool) or not isinstance(current, int) or current < 0:
            raise CommandRejectedError("inventory_registry_invalid")
        source[item_ref] = carried - quantity
        if source[item_ref] == 0:
            source.pop(item_ref)
        destination[item_ref] = current + quantity

    @staticmethod
    def _move_currency(holders: Dict[str, Any], source_ref: str, destination_ref: str, amount: int, *, insufficient_code: str) -> None:
        source = holders.get(source_ref)
        if not isinstance(source, dict):
            raise CommandRejectedError(insufficient_code)
        balance = source.get("currency.ryo", 0)
        if isinstance(balance, bool) or not isinstance(balance, int) or balance < amount:
            raise CommandRejectedError(insufficient_code)
        destination = holders.setdefault(destination_ref, {})
        if not isinstance(destination, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        dest_balance = destination.get("currency.ryo", 0)
        if isinstance(dest_balance, bool) or not isinstance(dest_balance, int) or dest_balance < 0:
            raise CommandRejectedError("inventory_registry_invalid")
        source["currency.ryo"] = balance - amount
        if source["currency.ryo"] == 0:
            source.pop("currency.ryo")
        destination["currency.ryo"] = dest_balance + amount

    def _operational_claim_delivery(
        self,
        *,
        claim_id: str,
        subject_ref: str,
        source_ref: str,
        at: CampaignTime,
        evidence_refs: Sequence[str],
        recipient_refs: Sequence[str],
        channel: str,
        confidence_milli: int = 850,
    ) -> tuple[Dict[str, Dict[str, Any]], list[str]]:
        staged: Dict[str, Dict[str, Any]] = {}
        information = InformationStore(self.repository, staged)
        evidence = tuple(sorted(set(ref for ref in evidence_refs if isinstance(ref, str) and ref)))
        try:
            claim = InformationClaim(
                claim_id=claim_id,
                subject_ref=subject_ref,
                source_ref=source_ref,
                collected_at=at,
                epistemic_kind="report",
                confidence_milli=confidence_milli,
                evidence_refs=evidence,
            )
            record = {
                "claim_id": claim.claim_id,
                "subject_ref": claim.subject_ref,
                "source_ref": claim.source_ref,
                "collected_at": str(claim.collected_at),
                "epistemic_kind": claim.epistemic_kind,
                "confidence_milli": claim.confidence_milli,
                "evidence_refs": list(claim.evidence_refs),
            }
            existing = information.claim(claim_id)
            if existing is not None and existing != record:
                raise CommandRejectedError("information_claim_conflict")
            information.add_claim(record)
            information.grant(source_ref, claim_id)
            delivery_ids: list[str] = []
            for recipient_ref in sorted(set(recipient_refs)):
                if not isinstance(recipient_ref, str) or not recipient_ref or recipient_ref == source_ref:
                    continue
                suffix = hashlib.sha256(f"{claim_id}\x00{source_ref}\x00{recipient_ref}\x00{at}\x00{channel}".encode()).hexdigest()[:24]
                delivery = deliver_claim(
                    claim,
                    delivery_id=f"delivery.operational.{suffix}",
                    sender_ref=source_ref,
                    recipient_ref=recipient_ref,
                    channel=channel,
                    delivered_at=at,
                    channel_confidence_milli=900,
                )
                information.add_delivery(dict(delivery.to_record()))
                information.grant(recipient_ref, claim_id)
                delivery_ids.append(delivery.delivery_id)
        except CommandRejectedError:
            raise
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("information_registry_invalid") from exc
        return staged, delivery_ids

    def _commerce_route_taxes(
        self, route_ref: str, gross_ryo: int, *, commerce_party_refs: Sequence[str] = ()
    ) -> list[Dict[str, Any]]:
        """Derive conserved route taxes from active jurisdiction policy.

        A jurisdiction taxes commerce only when its place (or a founded
        settlement's route anchor) touches the exact contract route. The
        payment is split directly from escrow at settlement, so tax policy
        cannot manufacture currency and cannot be bypassed by choosing a
        different payer holder.
        """
        try:
            governance = self.repository.read_json(_GOVERNANCE_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("governance_registry_invalid") from exc
        jurisdictions = governance.get("jurisdictions") if isinstance(governance, Mapping) else None
        if not isinstance(jurisdictions, Mapping):
            raise CommandRejectedError("governance_registry_invalid")
        route = self._real_route(route_ref)
        endpoints = {value for value in (route.get("from"), route.get("to")) if isinstance(value, str)}
        graph = self._location_graph()
        try:
            diplomacy = self.repository.read_json(_DIPLOMACY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("diplomacy_registry_invalid") from exc
        if not isinstance(diplomacy, Mapping):
            raise CommandRejectedError("diplomacy_registry_invalid")
        settlements: list[Dict[str, Any]] = []
        for jurisdiction_ref, row in sorted(jurisdictions.items()):
            if not isinstance(jurisdiction_ref, str) or not isinstance(row, Mapping):
                continue
            tax_milli = row.get("tax_milli", 0)
            if isinstance(tax_milli, bool) or not isinstance(tax_milli, int) or tax_milli < 0 or tax_milli > 1000:
                raise CommandRejectedError("governance_registry_invalid")
            if tax_milli <= 0:
                continue
            treasury_ref = row.get("treasury_holder_ref")
            place_ref = row.get("place_ref")
            if not isinstance(treasury_ref, str) or not treasury_ref or not isinstance(place_ref, str):
                raise CommandRejectedError("governance_tax_requires_treasury")
            try:
                anchor_ref = graph.anchor(place_ref)
            except (KeyError, TypeError, ValueError):
                raise CommandRejectedError("governance_place_invalid")
            if anchor_ref not in endpoints:
                continue
            treaty_multiplier, treaty_refs = trade_tariff_multiplier_milli(
                diplomacy,
                taxing_party_refs=(
                    row.get("sovereign_ref") if isinstance(row.get("sovereign_ref"), str) else "",
                    row.get("administration_ref") if isinstance(row.get("administration_ref"), str) else "",
                ),
                commerce_party_refs=commerce_party_refs,
                place_ref=place_ref,
                route_ref=route_ref,
            )
            amount = (gross_ryo * tax_milli * treaty_multiplier) // 1_000_000
            if amount <= 0:
                continue
            settlement = {
                "jurisdiction_ref": jurisdiction_ref,
                "treasury_holder_ref": treasury_ref,
                "tax_milli": tax_milli,
                "amount_ryo": amount,
            }
            if treaty_multiplier != 1000 or treaty_refs:
                settlement["treaty_tariff_multiplier_milli"] = treaty_multiplier
                settlement["treaty_agreement_refs"] = treaty_refs
            settlements.append(settlement)
        if sum(int(row["amount_ryo"]) for row in settlements) > gross_ryo:
            raise CommandRejectedError("commerce_route_tax_overcommitted")
        return settlements

    @staticmethod
    def _metric(registry: Dict[str, Any], route_ref: str) -> Dict[str, Any]:
        metrics = registry["route_metrics"]
        row = metrics.setdefault(route_ref, {
            "route_ref": route_ref, "shipment_count": 0, "delivered_count": 0,
            "seizure_count": 0, "cargo_units": 0, "gross_ryo": 0,
            "last_activity_at": None,
        })
        if not isinstance(row, dict):
            raise CommandRejectedError("commerce_registry_invalid")
        return row

    def _commerce_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        action = command.payload.get("action")
        registry = self._operational_registry(_COMMERCE_PATH, "commerce-registry", "contracts")
        if not all(isinstance(registry.get(key), dict) for key in ("shipments", "crossings", "route_metrics")):
            raise CommandRejectedError("commerce-registry_invalid")
        contracts: Dict[str, Any] = registry["contracts"]
        shipments: Dict[str, Any] = registry["shipments"]
        crossings: Dict[str, Any] = registry["crossings"]
        try:
            inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_registry_invalid") from exc
        holders = inventory.get("holders") if isinstance(inventory, dict) else None
        if not isinstance(holders, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        before_currency = self._inventory_currency_total(holders)
        item_ref_for_conservation: Optional[str] = None
        before_items: Optional[int] = None
        classification = "restricted"
        tax_settlements: list[Dict[str, Any]] = []
        result: Dict[str, Any] = {"command_type": command.command_type, "action": action}

        contract_ref_raw = command.payload.get("contract_ref")
        contract_ref = _stable_id(contract_ref_raw, "commerce_contract_ref_invalid", prefix="trade.contract.")

        if action == "offer_contract":
            if contract_ref in contracts:
                raise CommandRejectedError("commerce_contract_exists")
            client_ref = _stable_id(command.payload.get("client_ref"), "commerce_client_invalid")
            carrier_ref = _stable_id(command.payload.get("carrier_ref"), "commerce_carrier_invalid")
            source_ref = _stable_id(command.payload.get("source_holder_ref"), "commerce_source_holder_invalid")
            destination_ref = _stable_id(command.payload.get("destination_holder_ref"), "commerce_destination_holder_invalid")
            item_ref = _stable_id(command.payload.get("item_ref"), "commerce_item_invalid")
            quantity = self._positive_int(command.payload.get("quantity"), "commerce_quantity_invalid")
            unit_price = self._positive_int(command.payload.get("unit_price_ryo"), "commerce_price_invalid")
            route_ref = _stable_id(command.payload.get("route_ref"), "commerce_route_invalid", prefix="route_")
            self._real_route(route_ref)
            classification = self._op_visibility(command.payload.get("classification"))
            contraband = command.payload.get("contraband")
            if not isinstance(contraband, bool):
                raise CommandRejectedError("commerce_contraband_invalid")
            self._operational_authority(command.actor_id, client_ref)
            if not self._inventory_holder_authorized(command.actor_id, source_ref):
                raise CommandRejectedError("commerce_source_not_authorized")
            if not isinstance(holders.get(source_ref), Mapping) or holders[source_ref].get(item_ref, 0) < quantity:
                raise CommandRejectedError("commerce_cargo_insufficient")
            total = quantity * unit_price
            escrow_ref = f"escrow.trade.{contract_ref.removeprefix('trade.contract.')}"
            contracts[contract_ref] = {
                "id": contract_ref, "status": "offered", "client_ref": client_ref,
                "carrier_ref": carrier_ref, "source_holder_ref": source_ref,
                "destination_holder_ref": destination_ref, "item_ref": item_ref,
                "quantity": quantity, "unit_price_ryo": unit_price, "total_ryo": total,
                "escrow_holder_ref": escrow_ref, "route_ref": route_ref,
                "contraband": contraband, "opened_at": str(current_time),
                "accepted_at": None, "funded_at": None, "dispatched_at": None,
                "completed_at": None, "shipment_ref": None, "classification": classification,
                "tax_settlements": [],
            }
        else:
            contract = contracts.get(contract_ref)
            if not isinstance(contract, dict):
                raise CommandRejectedError("commerce_contract_not_found")
            classification = str(contract.get("classification") or "restricted")
            client_ref = str(contract.get("client_ref") or "")
            carrier_ref = str(contract.get("carrier_ref") or "")
            item_ref = str(contract.get("item_ref") or "")
            quantity = contract.get("quantity")
            total = contract.get("total_ryo")
            if not client_ref or not carrier_ref or not item_ref or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or isinstance(total, bool) or not isinstance(total, int) or total <= 0:
                raise CommandRejectedError("commerce_contract_invalid")
            item_ref_for_conservation = item_ref
            before_items = self._inventory_item_total(holders, item_ref)
            if action == "accept_contract":
                if contract.get("status") != "offered":
                    raise CommandRejectedError("commerce_contract_not_offered")
                self._operational_authority(command.actor_id, carrier_ref)
                contract["status"] = "accepted"; contract["accepted_at"] = str(current_time)
            elif action == "fund_contract":
                if contract.get("status") != "accepted":
                    raise CommandRejectedError("commerce_contract_not_accepted")
                self._operational_authority(command.actor_id, client_ref)
                escrow = str(contract.get("escrow_holder_ref") or "")
                if not escrow or holders.get(escrow, {}).get("currency.ryo", 0) not in (0, None):
                    raise CommandRejectedError("commerce_escrow_not_empty")
                self._move_currency(holders, self._funding_holder_for(client_ref), escrow, total, insufficient_code="commerce_funds_insufficient")
                contract["status"] = "funded"; contract["funded_at"] = str(current_time)
            elif action == "dispatch":
                if contract.get("status") != "funded":
                    raise CommandRejectedError("commerce_contract_not_funded")
                self._operational_authority(command.actor_id, carrier_ref)
                source_ref = str(contract.get("source_holder_ref") or "")
                if not self._inventory_holder_authorized(command.actor_id, source_ref) and command.actor_id != carrier_ref:
                    raise CommandRejectedError("commerce_dispatch_not_authorized")
                shipment_ref = f"shipment.{contract_ref.removeprefix('trade.contract.')}"
                if shipment_ref in shipments:
                    raise CommandRejectedError("commerce_shipment_exists")
                cargo_holder = shipment_ref
                self._move_inventory_item(holders, source_ref, cargo_holder, item_ref, quantity)
                shipments[shipment_ref] = {
                    "id": shipment_ref, "contract_ref": contract_ref, "cargo_holder_ref": cargo_holder,
                    "item_ref": item_ref, "quantity": quantity, "route_ref": contract["route_ref"],
                    "origin_holder_ref": source_ref, "destination_holder_ref": contract["destination_holder_ref"],
                    "custodian_ref": carrier_ref, "status": "in_transit", "dispatched_at": str(current_time),
                    "delivered_at": None, "seized_at": None, "classification": classification,
                }
                contract["status"] = "in_transit"; contract["dispatched_at"] = str(current_time); contract["shipment_ref"] = shipment_ref
                metric = self._metric(registry, contract["route_ref"]); metric["shipment_count"] += 1; metric["cargo_units"] += quantity; metric["last_activity_at"] = str(current_time)
                result["shipment_ref"] = shipment_ref
            elif action == "inspect":
                shipment_ref = _stable_id(command.payload.get("shipment_ref"), "commerce_shipment_ref_invalid", prefix="shipment.")
                shipment = shipments.get(shipment_ref)
                if not isinstance(shipment, dict) or shipment.get("contract_ref") != contract_ref or shipment.get("status") != "in_transit":
                    raise CommandRejectedError("commerce_shipment_invalid")
                authority_ref = _stable_id(command.payload.get("authority_ref"), "commerce_inspection_authority_invalid")
                self._operational_authority(command.actor_id, authority_ref)
                crossing_ref = _stable_id(command.payload.get("crossing_ref"), "commerce_crossing_ref_invalid", prefix="crossing.")
                if crossing_ref in crossings:
                    raise CommandRejectedError("commerce_crossing_exists")
                evidence_raw = command.payload.get("evidence_event_ref")
                evidence_ref = None if evidence_raw is None else self._real_event(evidence_raw, "commerce_inspection_evidence_invalid")
                detected = bool(contract.get("contraband"))
                crossings[crossing_ref] = {
                    "id": crossing_ref, "shipment_ref": shipment_ref, "route_ref": shipment["route_ref"],
                    "authority_ref": authority_ref, "inspected_at": str(current_time),
                    "result": "contraband_detected" if detected else "cleared",
                    "evidence_event_ref": evidence_ref, "classification": classification,
                }
                result.update({"crossing_ref": crossing_ref, "inspection_result": crossings[crossing_ref]["result"]})
            elif action == "seize":
                crossing_ref = _stable_id(command.payload.get("crossing_ref"), "commerce_crossing_ref_invalid", prefix="crossing.")
                crossing = crossings.get(crossing_ref)
                if not isinstance(crossing, dict) or crossing.get("result") != "contraband_detected":
                    raise CommandRejectedError("commerce_seizure_not_authorized")
                self._operational_authority(command.actor_id, str(crossing.get("authority_ref") or ""))
                shipment_ref = str(crossing.get("shipment_ref") or "")
                shipment = shipments.get(shipment_ref)
                if not isinstance(shipment, dict) or shipment.get("status") != "in_transit":
                    raise CommandRejectedError("commerce_shipment_invalid")
                seizure_holder = _stable_id(command.payload.get("seizure_holder_ref"), "commerce_seizure_holder_invalid")
                self._move_inventory_item(holders, shipment["cargo_holder_ref"], seizure_holder, item_ref, quantity)
                escrow = str(contract.get("escrow_holder_ref") or "")
                if isinstance(holders.get(escrow), Mapping) and holders[escrow].get("currency.ryo", 0) == total:
                    self._move_currency(holders, escrow, self._funding_holder_for(client_ref), total, insufficient_code="commerce_escrow_invalid")
                shipment["status"] = "seized"; shipment["seized_at"] = str(current_time)
                contract["status"] = "seized"; contract["completed_at"] = str(current_time)
                crossing["result"] = "seized"
                metric = self._metric(registry, shipment["route_ref"]); metric["seizure_count"] += 1; metric["last_activity_at"] = str(current_time)
            elif action == "deliver":
                if contract.get("status") != "in_transit":
                    raise CommandRejectedError("commerce_contract_not_in_transit")
                self._operational_authority(command.actor_id, carrier_ref)
                shipment_ref = str(contract.get("shipment_ref") or "")
                shipment = shipments.get(shipment_ref)
                if not isinstance(shipment, dict) or shipment.get("status") != "in_transit":
                    raise CommandRejectedError("commerce_shipment_invalid")
                self._move_inventory_item(holders, shipment["cargo_holder_ref"], shipment["destination_holder_ref"], item_ref, quantity)
                escrow = str(contract.get("escrow_holder_ref") or "")
                tax_settlements = self._commerce_route_taxes(
                    str(shipment["route_ref"]), total, commerce_party_refs=(client_ref, carrier_ref)
                )
                tax_total = sum(int(row["amount_ryo"]) for row in tax_settlements)
                for settlement in tax_settlements:
                    self._move_currency(
                        holders, escrow, str(settlement["treasury_holder_ref"]), int(settlement["amount_ryo"]),
                        insufficient_code="commerce_escrow_invalid",
                    )
                carrier_net = total - tax_total
                if carrier_net > 0:
                    self._move_currency(holders, escrow, self._funding_holder_for(carrier_ref), carrier_net, insufficient_code="commerce_escrow_invalid")
                contract["tax_settlements"] = copy.deepcopy(tax_settlements)
                shipment["status"] = "delivered"; shipment["delivered_at"] = str(current_time)
                contract["status"] = "delivered"; contract["completed_at"] = str(current_time)
                metric = self._metric(registry, shipment["route_ref"]); metric["delivered_count"] += 1; metric["gross_ryo"] += total; metric["last_activity_at"] = str(current_time)
                result.update({"tax_ryo": tax_total, "carrier_net_ryo": carrier_net, "tax_settlements": copy.deepcopy(tax_settlements)})
            elif action == "cancel":
                if contract.get("status") not in ("offered", "accepted", "funded"):
                    raise CommandRejectedError("commerce_contract_not_cancellable")
                self._operational_authority(command.actor_id, client_ref)
                if contract.get("status") == "funded":
                    self._move_currency(holders, str(contract["escrow_holder_ref"]), self._funding_holder_for(client_ref), total, insufficient_code="commerce_escrow_invalid")
                contract["status"] = "cancelled"; contract["completed_at"] = str(current_time)
            else:
                raise CommandRejectedError("commerce_action_invalid")

        if self._inventory_currency_total(holders) != before_currency:
            raise CommandRejectedError("commerce_currency_conservation_failed")
        if item_ref_for_conservation is not None and self._inventory_item_total(holders, item_ref_for_conservation) != before_items:
            raise CommandRejectedError("commerce_cargo_conservation_failed")

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind=f"commerce_{action}", at=current_time,
            actor_refs=(command.actor_id,), affected_owner_refs=(_COMMERCE_PATH, _INVENTORY_PATH),
            material_consequence_refs=tuple([contract_ref, *[f"tax:{row['jurisdiction_ref']}:{row['amount_ryo']}" for row in tax_settlements]]), classification=classification,
            audience_refs=(command.actor_id,), reducer_ref="shinobi_runtime.commands.domains.operational_world.commerce_resolution",
        )
        security_records: Dict[str, Dict[str, Any]] = {}
        security_detections: list[Dict[str, Any]] = []
        if action == "dispatch":
            dispatched = contracts[contract_ref]
            shipment_ref = dispatched.get("shipment_ref")
            shipment = shipments.get(shipment_ref) if isinstance(shipment_ref, str) else None
            if isinstance(shipment, Mapping):
                security_detections = apply_route_security_detection(
                    self, command=command, at=current_time, route_ref=str(shipment.get("route_ref") or ""),
                    subject_ref=str(shipment_ref), crossing_ref=event_id, world_events=world_events,
                    staged_records=security_records, intrusion=bool(dispatched.get("contraband")),
                    concealment_milli=0, subject_owner_refs=(str(client_ref), str(carrier_ref)),
                )
        writes = {self.meta_path:_json_bytes(self._meta_after(meta,command,world_time=current_time)), _COMMERCE_PATH:_json_bytes(registry), _INVENTORY_PATH:_json_bytes(inventory), **self._world_event_writes(world_events)}
        for path, record in security_records.items():
            writes[path] = _json_bytes(record)
        writes = self._prune_noop_writes(writes); expected_paths = tuple(sorted(writes))
        result.update({"contract_ref":contract_ref,"status":contracts[contract_ref]["status"],"semantic_event_id":event_id})
        if security_detections:
            result["security_detections"] = security_detections

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths: raise ValueError("commerce write set changed after planning")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=current_time)
            if overlay.read_json(_COMMERCE_PATH) != registry or overlay.read_json(_INVENTORY_PATH) != inventory: raise ValueError("commerce after-image mismatch")
            for path, record in security_records.items():
                if path in expected_paths and overlay.read_json(path) != record:
                    raise ValueError("commerce security after-image mismatch")
        return _BuiltPlan(code="commerce_resolution_ready",affected_refs=expected_paths,writes=writes,result=result,validator=validate)

    def _research_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        action = command.payload.get("action")
        registry = self._operational_registry(_RESEARCH_PATH, "research-registry", "projects")
        projects: Dict[str, Any] = registry["projects"]
        project_ref = _stable_id(command.payload.get("project_ref"), "research_project_ref_invalid", prefix="research.")
        stock_path: Optional[str] = None
        stock: Optional[Dict[str, Any]] = None
        classification = "restricted"
        prototype_test_outcome: Optional[str] = None
        prototype_test_roll: Optional[int] = None
        prototype_test_threshold: Optional[int] = None

        def research_kind_rule(project: Mapping[str, Any]) -> Mapping[str, Any]:
            mechanics = self._operational_mechanics()
            kinds = mechanics.get("research_kinds") if isinstance(mechanics, Mapping) else None
            row = kinds.get(project.get("project_kind")) if isinstance(kinds, Mapping) else None
            if not isinstance(row, Mapping):
                raise CommandRejectedError("research_kind_invalid")
            return row

        def validation_rule() -> Mapping[str, Any]:
            mechanics = self._operational_mechanics()
            row = mechanics.get("research_validation") if isinstance(mechanics, Mapping) else None
            if not isinstance(row, Mapping):
                raise CommandRejectedError("research_validation_mechanics_invalid")
            values = (
                row.get("minimum_successful_tests"), row.get("test_interval_days"),
                row.get("success_threshold_base_milli"), row.get("risk_weight_milli"),
            )
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
                raise CommandRejectedError("research_validation_mechanics_invalid")
            if values[0] <= 0 or values[1] <= 0 or values[2] >= 1000 or values[3] > 1000:
                raise CommandRejectedError("research_validation_mechanics_invalid")
            return row

        def validate_candidate(project: Mapping[str, Any], candidate_kind: str, candidate_ref: str) -> None:
            kind_rule = research_kind_rule(project)
            allowed = kind_rule.get("allowed_candidate_kinds")
            if not isinstance(allowed, list) or candidate_kind not in allowed:
                raise CommandRejectedError("research_candidate_kind_not_supported")
            if candidate_kind == "technique":
                if not candidate_ref or "/" in candidate_ref or ".." in candidate_ref:
                    raise CommandRejectedError("research_candidate_invalid")
                path = f"game/data/tech/records/{candidate_ref}.json"
                try:
                    record = self.repository.read_json(path)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("research_candidate_unresolved") from exc
                if not isinstance(record, Mapping) or record.get("method_id") != candidate_ref:
                    raise CommandRejectedError("research_candidate_unresolved")
            elif candidate_kind == "manufacturing_recipe":
                try:
                    mechanics = self.repository.read_json("game/data/mechanics/institution-projects.json")
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("research_candidate_unresolved") from exc
                recipes = mechanics.get("manufacturing_recipes") if isinstance(mechanics, Mapping) else None
                if not isinstance(recipes, Mapping) or not isinstance(recipes.get(candidate_ref), Mapping):
                    raise CommandRejectedError("research_candidate_unresolved")
            else:
                raise CommandRejectedError("research_candidate_invalid")

        if action == "start":
            if project_ref in projects:
                raise CommandRejectedError("research_project_exists")
            institution_ref = _stable_id(command.payload.get("institution_ref"), "research_institution_invalid")
            self._operational_authority(command.actor_id, institution_ref)
            lead_ref = _stable_id(command.payload.get("lead_ref"), "research_lead_invalid")
            try:
                self._resolve_covered_owner(lead_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError as exc:
                raise CommandRejectedError("research_lead_invalid") from exc
            place_ref = _stable_id(command.payload.get("place_ref"), "research_place_invalid", prefix="place.")
            if self._location_graph().place(place_ref) is None:
                raise CommandRejectedError("research_place_invalid")
            project_kind = command.payload.get("project_kind")
            mechanics = self._operational_mechanics(); kinds = mechanics.get("research_kinds")
            kind = kinds.get(project_kind) if isinstance(kinds, Mapping) else None
            if not isinstance(kind, Mapping):
                raise CommandRejectedError("research_kind_invalid")
            subject_raw = command.payload.get("subject_ref")
            subject_ref = None if subject_raw is None else _stable_id(subject_raw, "research_subject_invalid")
            custody_raw = command.payload.get("custody_ref")
            custody_ref = None if custody_raw is None else _stable_id(custody_raw, "research_custody_invalid", prefix="custody.")
            if subject_ref is not None:
                try:
                    self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
                except CommandRejectedError as exc:
                    raise CommandRejectedError("research_subject_invalid") from exc
                if command.actor_id != subject_ref:
                    if custody_ref is None:
                        raise CommandRejectedError("research_subject_consent_or_custody_required")
                    try:
                        custody = self.repository.read_json(_CUSTODY_PATH)
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("research_custody_invalid") from exc
                    record = custody.get("records", {}).get(custody_ref) if isinstance(custody, Mapping) else None
                    if not isinstance(record, Mapping) or record.get("subject_ref") != subject_ref or record.get("status") != "detained":
                        raise CommandRejectedError("research_custody_invalid")
                    custodian = str(record.get("custodian_ref") or "")
                    if custodian != institution_ref:
                        self._operational_authority(command.actor_id, custodian)
            if project_kind == "biological" and subject_ref is None:
                raise CommandRejectedError("research_subject_required")
            hypothesis = command.payload.get("hypothesis")
            if not isinstance(hypothesis, str) or not hypothesis.strip() or len(hypothesis) > 1000:
                raise CommandRejectedError("research_hypothesis_invalid")
            classification = self._op_visibility(command.payload.get("classification"))
            costs = dict(kind.get("material_costs") or {})
            stock_raw = command.payload.get("stock_ref")
            stock_ref = None if stock_raw is None else _stable_id(stock_raw, "research_stock_invalid", prefix="stock.")
            if costs:
                if stock_ref is None:
                    raise CommandRejectedError("research_stock_required")
                stock_path, stock, stock_owner = self._stock_record(stock_ref)
                if not self._operational_controls_owner(command.actor_id, stock_owner):
                    raise CommandRejectedError("research_stock_not_authorized")
                for item_ref, qty in costs.items():
                    container, key = self._stock_item_key(stock, item_ref); available = container.get(key, 0)
                    if isinstance(available, bool) or not isinstance(available, int) or available < qty:
                        raise CommandRejectedError("research_materials_insufficient")
                for item_ref, qty in costs.items():
                    container, key = self._stock_item_key(stock, item_ref); container[key] -= qty
            projects[project_ref] = {
                "id": project_ref, "institution_ref": institution_ref, "lead_ref": lead_ref, "place_ref": place_ref,
                "project_kind": project_kind, "subject_ref": subject_ref, "custody_ref": custody_ref, "stock_ref": stock_ref,
                "material_costs": costs, "status": "active", "hypothesis": hypothesis.strip(), "opened_at": str(current_time),
                "last_advanced_at": str(current_time), "next_due_at": str(current_time.add_seconds(7 * 24 * 60 * 60)),
                "progress_milli": 0, "risk_milli": int(kind.get("risk_milli", 0)), "result_claim_refs": [],
                "evidence_refs": [], "classification": classification,
                "candidate_kind": None, "candidate_ref": None, "prototype_status": "none", "prototype_next_test_at": None,
                "prototype_test_refs": [], "successful_test_count": 0, "failed_test_count": 0, "approved_at": None,
            }
        else:
            project = projects.get(project_ref)
            if not isinstance(project, dict):
                raise CommandRejectedError("research_project_not_found")
            classification = str(project.get("classification") or "restricted")
            self._operational_authority(command.actor_id, str(project.get("institution_ref") or ""))
            # Old exact projects can be upgraded in place without changing their research result.
            project.setdefault("candidate_kind", None); project.setdefault("candidate_ref", None)
            project.setdefault("prototype_status", "none"); project.setdefault("prototype_next_test_at", None)
            project.setdefault("prototype_test_refs", []); project.setdefault("successful_test_count", 0)
            project.setdefault("failed_test_count", 0); project.setdefault("approved_at", None)
            if action == "advance":
                if project.get("status") != "active":
                    raise CommandRejectedError("research_project_not_active")
                due_raw = project.get("next_due_at")
                try:
                    due = CampaignTime.parse(due_raw)
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("research_project_invalid") from exc
                if current_time < due:
                    raise CommandRejectedError("research_not_due")
                kind = research_kind_rule(project)
                required_hours = int(kind.get("required_active_hours", 0))
                if required_hours <= 0:
                    raise CommandRejectedError("research_kind_invalid")
                step = max(1, min(1000, (40 * 1000) // required_hours))
                project["progress_milli"] = min(1000, int(project.get("progress_milli", 0)) + step)
                project["last_advanced_at"] = str(current_time)
                evidence_ref = self._real_event(command.payload.get("evidence_ref"), "research_evidence_invalid")
                if evidence_ref in project["evidence_refs"]:
                    raise CommandRejectedError("research_evidence_already_used")
                project["evidence_refs"].append(evidence_ref); project["evidence_refs"].sort()
                if project["progress_milli"] >= 1000:
                    project["status"] = "succeeded"; project["next_due_at"] = None
                else:
                    project["next_due_at"] = str(current_time.add_seconds(7 * 24 * 60 * 60))
            elif action == "cancel":
                if project.get("status") not in ("active", "blocked"):
                    raise CommandRejectedError("research_project_not_cancellable")
                project["status"] = "cancelled"; project["next_due_at"] = None; project["last_advanced_at"] = str(current_time)
            elif action == "prototype":
                if project.get("status") != "succeeded" or not project.get("result_claim_refs"):
                    raise CommandRejectedError("research_result_required_for_prototype")
                if project.get("prototype_status") != "none":
                    raise CommandRejectedError("research_prototype_already_exists")
                candidate_kind = command.payload.get("candidate_kind")
                candidate_ref = command.payload.get("candidate_ref")
                if candidate_kind not in ("technique", "manufacturing_recipe") or not isinstance(candidate_ref, str) or not candidate_ref:
                    raise CommandRejectedError("research_candidate_invalid")
                validate_candidate(project, str(candidate_kind), candidate_ref)
                rules = validation_rule(); interval = int(rules["test_interval_days"]) * 24 * 60 * 60
                project["candidate_kind"] = candidate_kind; project["candidate_ref"] = candidate_ref
                project["prototype_status"] = "built"; project["prototype_next_test_at"] = str(current_time.add_seconds(interval))
                project["last_advanced_at"] = str(current_time)
            elif action == "test_prototype":
                if project.get("status") != "succeeded" or project.get("prototype_status") not in ("built", "testing"):
                    raise CommandRejectedError("research_prototype_not_testable")
                due_raw = project.get("prototype_next_test_at")
                try:
                    due = CampaignTime.parse(due_raw)
                except (TypeError, ValueError) as exc:
                    raise CommandRejectedError("research_prototype_invalid") from exc
                if current_time < due:
                    raise CommandRejectedError("research_prototype_test_not_due")
                rules = validation_rule()
                attempts = int(project.get("successful_test_count", 0)) + int(project.get("failed_test_count", 0))
                digest = hashlib.sha256(f"{project_ref}\x00{project.get('candidate_ref')}\x00{attempts + 1}".encode()).digest()
                prototype_test_roll = int.from_bytes(digest[:4], "big") % 1000
                risk = int(project.get("risk_milli", 0))
                prototype_test_threshold = min(950, int(rules["success_threshold_base_milli"]) + (risk * int(rules["risk_weight_milli"]) // 1000))
                if prototype_test_roll >= prototype_test_threshold:
                    project["successful_test_count"] = int(project.get("successful_test_count", 0)) + 1
                    prototype_test_outcome = "success"
                else:
                    project["failed_test_count"] = int(project.get("failed_test_count", 0)) + 1
                    prototype_test_outcome = "failure"
                if int(project["successful_test_count"]) >= int(rules["minimum_successful_tests"]):
                    project["prototype_status"] = "validated"; project["prototype_next_test_at"] = None
                else:
                    project["prototype_status"] = "testing"
                    project["prototype_next_test_at"] = str(current_time.add_seconds(int(rules["test_interval_days"]) * 24 * 60 * 60))
                project["last_advanced_at"] = str(current_time)
            elif action == "approve":
                if project.get("status") != "succeeded" or project.get("prototype_status") != "validated":
                    raise CommandRejectedError("research_prototype_not_validated")
                project["prototype_status"] = "approved"; project["approved_at"] = str(current_time)
                project["last_advanced_at"] = str(current_time)
            else:
                raise CommandRejectedError("research_action_invalid")

        project = projects[project_ref]
        world_events = self._world_events()
        material = [project_ref, f"status:{project['status']}", f"progress:{project['progress_milli']}"]
        if project.get("candidate_ref"):
            material.extend((f"candidate:{project.get('candidate_kind')}:{project.get('candidate_ref')}", f"prototype:{project.get('prototype_status')}"))
        if prototype_test_outcome is not None:
            material.extend((f"prototype_test:{prototype_test_outcome}", f"roll:{prototype_test_roll}", f"threshold:{prototype_test_threshold}"))
        event_id = self._append_semantic_event(
            world_events, command=command, kind=f"research_{action}", at=current_time,
            host_refs=(project["institution_ref"],), actor_refs=(command.actor_id,), place_refs=(project["place_ref"],),
            causal_refs=tuple(project["evidence_refs"]), affected_owner_refs=tuple(x for x in (_RESEARCH_PATH, stock_path) if x),
            material_consequence_refs=tuple(material), classification=classification, audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.domains.operational_world.research_resolution",
        )
        if action == "test_prototype":
            refs = project.get("prototype_test_refs")
            if not isinstance(refs, list):
                raise CommandRejectedError("research_prototype_invalid")
            if event_id not in refs:
                refs.append(event_id); refs.sort()
        information = None
        delivery_ids: list[str] = []
        if project["status"] == "succeeded" and not project["result_claim_refs"]:
            digest = hashlib.sha256(f"{project_ref}\x00result".encode()).hexdigest()[:20]
            claim_id = f"claim.research.{digest}"
            evidence_refs = tuple(sorted(set([*project["evidence_refs"], event_id])))
            information, delivery_ids = self._operational_claim_delivery(
                claim_id=claim_id, subject_ref=str(project.get("subject_ref") or project_ref), source_ref=str(project["lead_ref"]),
                at=current_time, evidence_refs=evidence_refs, recipient_refs=(str(project["institution_ref"]),),
                channel="research_result", confidence_milli=850,
            )
            project["result_claim_refs"].append(claim_id)
            project["evidence_refs"].append(event_id); project["evidence_refs"] = sorted(set(project["evidence_refs"]))
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _RESEARCH_PATH: _json_bytes(registry), **self._world_event_writes(world_events),
        }
        if stock_path and stock is not None:
            writes[stock_path] = _json_bytes(stock)
        if information is not None:
            writes.update({path: _json_bytes(record) for path, record in information.items()})
        writes = self._prune_noop_writes(writes); expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("research write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            if overlay.read_json(_RESEARCH_PATH) != registry:
                raise ValueError("research after-image mismatch")
            if information is not None:
                for path, record in information.items():
                    if overlay.read_json(path) != record:
                        raise ValueError("research information after-image mismatch")

        return _BuiltPlan(
            code="research_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "action": action, "project_ref": project_ref,
                "status": project["status"], "progress_milli": project["progress_milli"],
                "claim_refs": list(project["result_claim_refs"]), "delivery_refs": delivery_ids,
                "candidate_kind": project.get("candidate_kind"), "candidate_ref": project.get("candidate_ref"),
                "prototype_status": project.get("prototype_status"), "successful_test_count": project.get("successful_test_count"),
                "failed_test_count": project.get("failed_test_count"), "prototype_test_outcome": prototype_test_outcome,
                "semantic_event_id": event_id,
            }, validator=validate,
        )

    def _security_network_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        action = command.payload.get("action")
        registry = self._operational_registry(_SECURITY_PATH, "security-network-registry", "sectors")
        sectors = registry["sectors"]
        alarms = registry.get("alarms")
        if not isinstance(alarms, dict):
            raise CommandRejectedError("security-network-registry_invalid")
        classification = "restricted"
        subject_ref: Optional[str] = None
        stock_path: Optional[str] = None
        stock: Optional[Dict[str, Any]] = None
        information: Optional[Dict[str, Any]] = None
        delivery_ids: list[str] = []
        mechanics = self._operational_mechanics().get("security", {})
        if not isinstance(mechanics, Mapping):
            raise CommandRejectedError("operational_world_mechanics_invalid")

        def load_stock(stock_ref: str, owner_ref: str) -> Dict[str, Any]:
            nonlocal stock_path, stock
            stock_path, stock, stock_owner = self._stock_record(stock_ref)
            if stock_owner != owner_ref:
                # A security owner may lawfully draw from a force/organization stock only
                # when the acting leader also has authority over that stock owner.
                if not self._operational_controls_owner(command.actor_id, stock_owner):
                    raise CommandRejectedError("security_stock_not_authorized")
            return stock

        def consume(stock_record: Dict[str, Any], item_ref: str, quantity: int) -> None:
            if quantity <= 0:
                return
            container, key = self._stock_item_key(stock_record, item_ref)
            available = container.get(key, 0)
            if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
                raise CommandRejectedError("security_resources_insufficient")
            container[key] = available - quantity

        if action == "establish_sector":
            sector_ref = _stable_id(command.payload.get("sector_ref"), "security_sector_ref_invalid", prefix="security.")
            if sector_ref in sectors:
                raise CommandRejectedError("security_sector_exists")
            owner_ref = _stable_id(command.payload.get("owner_ref"), "security_owner_invalid")
            self._operational_authority(command.actor_id, owner_ref)
            place_ref = _stable_id(command.payload.get("place_ref"), "security_place_invalid", prefix="place.")
            if self._location_graph().place(place_ref) is None:
                raise CommandRejectedError("security_place_invalid")
            route_refs = command.payload.get("route_refs")
            if not isinstance(route_refs, Sequence) or isinstance(route_refs, (str, bytes, bytearray)) or any(not isinstance(x, str) for x in route_refs):
                raise CommandRejectedError("security_routes_invalid")
            for route_ref in route_refs:
                self._real_route(route_ref)
            stock_ref = _stable_id(command.payload.get("stock_ref"), "security_stock_invalid", prefix="stock.")
            stock_record = load_stock(stock_ref, owner_ref)
            consume(stock_record, "item_barrier_anchor_set", int(mechanics.get("barrier_anchor_sets_per_step", 0)))
            consume(stock_record, "item_sensor_relay_set", int(mechanics.get("sensor_relays_per_step", 0)))
            classification = self._op_visibility(command.payload.get("classification"))
            detection_channels = command.payload.get("detection_channels")
            # Establishment currently consumes barrier anchors and sensor relays only.
            # Do not let a caller conjure patrol, hound, auditory, or tracking channels
            # without a separate resource-backed owner for those capabilities.
            materialized_detection_channels = {"chakra", "barrier"}
            if detection_channels is None:
                detection_channels = ["barrier", "chakra"]
            if (
                not isinstance(detection_channels, Sequence)
                or isinstance(detection_channels, (str, bytes, bytearray))
                or not detection_channels
                or any(not isinstance(value, str) or value not in materialized_detection_channels for value in detection_channels)
                or len(detection_channels) != len(set(detection_channels))
            ):
                raise CommandRejectedError("security_detection_channel_not_materialized")
            detection_channels = sorted(detection_channels)
            sectors[sector_ref] = {
                "id": sector_ref,
                "owner_ref": owner_ref,
                "place_ref": place_ref,
                "route_refs": sorted(set(route_refs)),
                "authorized_owner_refs": [owner_ref],
                "detection_channels": detection_channels,
                "stock_ref": stock_ref,
                "coverage_milli": int(mechanics.get("coverage_step_milli", 250)),
                "detection_milli": int(mechanics.get("detection_step_milli", 250)),
                "response_milli": 250,
                "status": "active",
                "established_at": str(current_time),
                "last_reviewed_at": str(current_time),
                "classification": classification,
            }
            result_ref = sector_ref
        elif action == "improve_sector":
            sector_ref = _stable_id(command.payload.get("sector_ref"), "security_sector_ref_invalid", prefix="security.")
            sector = sectors.get(sector_ref)
            if not isinstance(sector, dict):
                raise CommandRejectedError("security_sector_not_found")
            owner_ref = str(sector.get("owner_ref") or "")
            self._operational_authority(command.actor_id, owner_ref)
            classification = str(sector.get("classification") or "restricted")
            evidence_ref = self._real_event(command.payload.get("evidence_ref"), "security_improvement_evidence_invalid")
            axis = command.payload.get("axis")
            if axis == "coverage":
                field = "coverage_milli"
                step = int(mechanics.get("coverage_step_milli", 0))
                item_ref = "item_barrier_anchor_set"
                item_qty = int(mechanics.get("barrier_anchor_sets_per_step", 0))
            elif axis == "detection":
                field = "detection_milli"
                step = int(mechanics.get("detection_step_milli", 0))
                item_ref = "item_sensor_relay_set"
                item_qty = int(mechanics.get("sensor_relays_per_step", 0))
            elif axis == "response":
                field = "response_milli"
                step = min(int(mechanics.get("coverage_step_milli", 0)), int(mechanics.get("detection_step_milli", 0)))
                item_ref = ""
                item_qty = 0
            else:
                raise CommandRejectedError("security_axis_invalid")
            if step <= 0:
                raise CommandRejectedError("operational_world_mechanics_invalid")
            if item_qty:
                stock_ref = str(sector.get("stock_ref") or "")
                if not stock_ref:
                    raise CommandRejectedError("security_stock_invalid")
                stock_record = load_stock(stock_ref, owner_ref)
                consume(stock_record, item_ref, item_qty)
            sector[field] = min(1000, int(sector.get(field, 0)) + step)
            sector["last_reviewed_at"] = str(current_time)
            result_ref = sector_ref
            subject_ref = evidence_ref
        elif action == "raise_alarm":
            alarm_ref = _stable_id(command.payload.get("alarm_ref"), "security_alarm_ref_invalid", prefix="alarm.")
            if alarm_ref in alarms:
                raise CommandRejectedError("security_alarm_exists")
            sector_ref = _stable_id(command.payload.get("sector_ref"), "security_sector_ref_invalid", prefix="security.")
            sector = sectors.get(sector_ref)
            if not isinstance(sector, dict) or sector.get("status") == "inactive":
                raise CommandRejectedError("security_sector_not_active")
            self._operational_authority(command.actor_id, str(sector.get("owner_ref") or ""))
            classification = str(sector.get("classification") or "restricted")
            subject_ref = _stable_id(command.payload.get("subject_ref"), "security_alarm_subject_invalid")
            evidence_ref = self._real_event(command.payload.get("evidence_ref"), "security_alarm_requires_evidence")
            recipients = command.payload.get("recipient_refs")
            if not isinstance(recipients, Sequence) or isinstance(recipients, (str, bytes, bytearray)) or not recipients or any(not isinstance(x, str) or not x for x in recipients):
                raise CommandRejectedError("security_alarm_recipients_invalid")
            recipients = sorted(set(recipients))
            alarms[alarm_ref] = {
                "id": alarm_ref,
                "sector_ref": sector_ref,
                "subject_ref": subject_ref,
                "evidence_ref": evidence_ref,
                "status": "open",
                "opened_at": str(current_time),
                "resolved_at": None,
                "recipient_refs": recipients,
                "classification": classification,
            }
            digest = hashlib.sha256(f"{alarm_ref}\x00{evidence_ref}".encode()).hexdigest()[:20]
            information, delivery_ids = self._operational_claim_delivery(
                claim_id=f"claim.security.{digest}",
                subject_ref=subject_ref,
                source_ref=command.actor_id,
                at=current_time,
                evidence_refs=(evidence_ref,),
                recipient_refs=recipients,
                channel="security_alarm",
                confidence_milli=900,
            )
            result_ref = alarm_ref
        else:
            alarm_ref = _stable_id(command.payload.get("alarm_ref"), "security_alarm_ref_invalid", prefix="alarm.")
            alarm = alarms.get(alarm_ref)
            if not isinstance(alarm, dict):
                raise CommandRejectedError("security_alarm_not_found")
            sector = sectors.get(alarm.get("sector_ref"))
            if not isinstance(sector, dict):
                raise CommandRejectedError("security_sector_not_found")
            self._operational_authority(command.actor_id, str(sector.get("owner_ref") or ""))
            classification = str(alarm.get("classification") or "restricted")
            if action == "acknowledge_alarm":
                if alarm.get("status") != "open":
                    raise CommandRejectedError("security_alarm_not_open")
                alarm["status"] = "acknowledged"
            elif action in ("resolve_alarm", "mark_false_alarm"):
                if alarm.get("status") not in ("open", "acknowledged"):
                    raise CommandRejectedError("security_alarm_not_resolvable")
                evidence_ref = self._real_event(command.payload.get("evidence_ref"), "security_alarm_resolution_requires_evidence")
                alarm["status"] = "resolved" if action == "resolve_alarm" else "false_alarm"
                alarm["resolved_at"] = str(current_time)
                subject_ref = evidence_ref
            else:
                raise CommandRejectedError("security_action_invalid")
            result_ref = alarm_ref

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind=f"security_{action}",
            at=current_time,
            actor_refs=(command.actor_id,),
            causal_refs=tuple(x for x in (subject_ref,) if isinstance(x, str) and x.startswith("event.")),
            affected_owner_refs=tuple(x for x in (_SECURITY_PATH, stock_path, *((information or {}).keys())) if x),
            material_consequence_refs=tuple([result_ref, *delivery_ids]),
            classification=classification,
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.domains.operational_world.security_network_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _SECURITY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        if stock_path and stock is not None:
            writes[stock_path] = _json_bytes(stock)
        if information is not None:
            writes.update({path: _json_bytes(record) for path, record in information.items()})
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("security write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            if overlay.read_json(_SECURITY_PATH) != registry:
                raise ValueError("security after-image mismatch")
            if information is not None:
                for path, record in information.items():
                    if overlay.read_json(path) != record:
                        raise ValueError("security information after-image mismatch")

        return _BuiltPlan(
            code="security_network_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "action": action,
                "result_ref": result_ref,
                "delivery_refs": delivery_ids,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _mission_market_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        registry=self._operational_registry(_MISSION_MARKET_PATH,"mission-market-runtime","markets"); markets=registry["markets"]
        market_ref=_stable_id(command.payload.get("market_ref"),"mission_market_ref_invalid",prefix="market_"); market=markets.get(market_ref)
        if not isinstance(market,dict): raise CommandRejectedError("mission_market_not_found")
        # Ownership remains in the existing economy/world registry; this runtime registry stores only evidence-driven scores.
        try: world=self.repository.read_json(_ECONOMY_WORLD_PATH)
        except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("mission_market_world_invalid") from exc
        rows=world.get("payload",{}).get("economies_and_mission_markets",{}).get("markets",[]) if isinstance(world,Mapping) else []
        owner_ref=next((row.get("owner_id") for row in rows if isinstance(row,Mapping) and row.get("id")==market_ref),None)
        if not isinstance(owner_ref,str): raise CommandRejectedError("mission_market_world_invalid")
        self._operational_authority(command.actor_id,owner_ref)
        evidence_ref=self._real_event(command.payload.get("evidence_ref"),"mission_market_evidence_invalid")
        axis=command.payload.get("axis"); key=_stable_id(command.payload.get("signal_ref"),"mission_market_signal_invalid")
        direction=command.payload.get("direction")
        if direction not in (-1,1): raise CommandRejectedError("mission_market_direction_invalid")
        if axis=="demand": scores=market.get("demand_scores")
        elif axis=="competition": scores=market.get("competition_scores")
        else: raise CommandRejectedError("mission_market_axis_invalid")
        if not isinstance(scores,dict) or key not in scores: raise CommandRejectedError("mission_market_signal_invalid")
        step=int(self._operational_mechanics().get("mission_market",{}).get("signal_delta_milli",0))
        if step<=0: raise CommandRejectedError("operational_world_mechanics_invalid")
        scores[key]=max(0,min(1000,int(scores[key])+direction*step))
        if evidence_ref not in market["evidence_refs"]: market["evidence_refs"].append(evidence_ref); market["evidence_refs"].sort()
        market["last_updated_at"]=str(current_time)
        world_events=self._world_events(); event_id=self._append_semantic_event(world_events,command=command,kind="mission_market_signal",at=current_time,host_refs=(owner_ref,),actor_refs=(command.actor_id,),causal_refs=(evidence_ref,),affected_owner_refs=(_MISSION_MARKET_PATH,),material_consequence_refs=(f"{market_ref}:{axis}:{key}:{scores[key]}",),classification="restricted",audience_refs=(command.actor_id,owner_ref),reducer_ref="shinobi_runtime.commands.domains.operational_world.mission_market_resolution")
        writes={self.meta_path:_json_bytes(self._meta_after(meta,command,world_time=current_time)),_MISSION_MARKET_PATH:_json_bytes(registry),**self._world_event_writes(world_events)}; writes=self._prune_noop_writes(writes); expected_paths=tuple(sorted(writes))
        def validate(overlay:StagedOverlay,manifest:TransactionManifest)->None:
            if overlay.changed_paths!=expected_paths: raise ValueError("mission market write set changed after planning")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=current_time)
            if overlay.read_json(_MISSION_MARKET_PATH)!=registry: raise ValueError("mission market after-image mismatch")
        return _BuiltPlan(code="mission_market_resolution_ready",affected_refs=expected_paths,writes=writes,result={"command_type":command.command_type,"market_ref":market_ref,"axis":axis,"signal_ref":key,"score":scores[key],"evidence_ref":evidence_ref,"semantic_event_id":event_id},validator=validate)


__all__ = ["OperationalWorldCommandsMixin"]
