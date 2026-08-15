"""Typed causal work for living-world institutions.

The generic autonomy engine chooses *when* an organization reviews itself.  This
layer decides *what concrete thing* a specialist organization is working on from
static deterministic program data, persists that work as a world-operation, and
routes resulting claims to explicit recipients.  Operations are references to
existing authorities, never duplicate owners of people, money, stock, territory,
projects, cases, or mission outcomes.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.commands.living_world_support import *
from shinobi_runtime.commands.mission_owner import MissionBrief
from shinobi_runtime.commands.paths import DOMAIN_REGISTRY_PATH
from shinobi_runtime.reducers import InformationClaim, Mission, MissionObjective, deliver_claim
from shinobi_runtime.information import InformationStore
from shinobi_runtime.membership_routes import team_refs_for_parent

_PROGRAM_PATH = "game/rules/autonomy/institutional-programs.json"
_MISSION_ARCHETYPES_PATH = "game/data/content/mission-archetypes.json"
_OPERATION_ROOT = "state/operation"
_OPERATIONAL_MECHANICS_PATH = "game/data/mechanics/operational-world.json"
_LEGAL_CASE_REGISTRY_PATH = "state/reg/legal-cases.json"
_RESEARCH_REGISTRY_PATH = "state/reg/research.json"
_SECURITY_NETWORK_REGISTRY_PATH = "state/reg/security-networks.json"
_MISSION_MARKET_RUNTIME_PATH = "state/reg/mission-markets.json"
_COMMERCE_REGISTRY_PATH = "state/reg/commerce.json"
_CUSTODY_REGISTRY_PATH = "state/reg/custody.json"
_BIOLOGICAL_REMAINS_REGISTRY_PATH = "state/reg/biological-remains.json"
_PUPPET_REGISTRY_PATH = "state/reg/puppets.json"
_DIPLOMACY_REGISTRY_PATH = "state/reg/diplomacy.json"
_CONFLICT_REGISTRY_PATH = "state/conflict/registry.json"
_SPECIAL_SYSTEMS_PATH = "game/data/mechanics/special-systems.json"
_INSTITUTION_BUNDLE_PATHS = (
    "state/world/institutions-konoha.json",
    "state/world/institutions-great-villages.json",
    "state/world/institutions-minor-and-civil.json",
)



class LivingWorldOperationsMixin:
    def _institutional_program_book(self) -> Mapping[str, Any]:
        try:
            record = self.repository.read_json(_PROGRAM_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institutional_autonomy_programs_invalid") from exc
        programs = record.get("programs") if isinstance(record, Mapping) else None
        profiles = record.get("institution_profiles") if isinstance(record, Mapping) else None
        if (
            record.get("schema") != "institutional-autonomy-programs"
            or not isinstance(programs, Mapping)
            or not isinstance(profiles, list)
        ):
            raise CommandRejectedError("institutional_autonomy_programs_invalid")
        return record

    def _institutional_programs(self) -> Mapping[str, Any]:
        return self._institutional_program_book()["programs"]

    def _institution_delegated_faction(self, institution_id: str) -> Optional[str]:
        mapping = self._institutional_program_book().get("delegated_institutions")
        if not isinstance(mapping, Mapping):
            raise CommandRejectedError("institutional_autonomy_programs_invalid")
        value = mapping.get(institution_id)
        return value if isinstance(value, str) and value else None

    def _institution_profile(self, institution: Mapping[str, Any]) -> Mapping[str, Any]:
        profiles = self._institutional_program_book().get("institution_profiles")
        if not isinstance(profiles, list):
            raise CommandRejectedError("institutional_autonomy_programs_invalid")
        branches = institution.get("branches", [])
        if branches is None:
            branches = []
        if not isinstance(branches, list) or any(not isinstance(value, str) for value in branches):
            raise CommandRejectedError("institution_autonomy_invalid")
        text_parts = [str(institution.get("id") or ""), str(institution.get("name") or ""), *branches]
        is_clan = isinstance(institution.get("specialization"), str)
        if is_clan:
            text_parts.extend(["__clan__", str(institution.get("specialization"))])
        haystack = " ".join(text_parts).lower().replace("-", "_")
        default = None
        for row in profiles:
            if not isinstance(row, Mapping):
                raise CommandRejectedError("institutional_autonomy_programs_invalid")
            terms = row.get("match_terms")
            if not isinstance(terms, list) or any(not isinstance(term, str) or not term for term in terms):
                raise CommandRejectedError("institutional_autonomy_programs_invalid")
            if "*" in terms:
                default = row
                continue
            if any(term.lower().replace("-", "_") in haystack for term in terms):
                return row
        if not isinstance(default, Mapping):
            raise CommandRejectedError("institutional_autonomy_programs_invalid")
        return default

    def _institutional_program(self, faction_id: str) -> Optional[Mapping[str, Any]]:
        row = self._institutional_programs().get(faction_id)
        return row if isinstance(row, Mapping) else None

    def _operational_world_mechanics(self) -> Mapping[str, Any]:
        try:
            record = self.repository.read_json(_OPERATIONAL_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("operational_world_mechanics_invalid") from exc
        if not isinstance(record, Mapping) or record.get("schema") != "operational-world-mechanics":
            raise CommandRejectedError("operational_world_mechanics_invalid")
        return record

    def _autonomous_operation_effect(self, operation_kind: str) -> Optional[Mapping[str, Any]]:
        effects = self._operational_world_mechanics().get("autonomous_effects")
        if not isinstance(effects, Mapping):
            raise CommandRejectedError("operational_world_mechanics_invalid")
        row = effects.get(operation_kind)
        return row if isinstance(row, Mapping) else None

    def _staged_registry(
        self,
        path: str,
        *,
        schema: str,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        current = record_writes.get(path)
        if current is None:
            try:
                current = copy.deepcopy(self.repository.read_json(path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError(f"{schema}_invalid") from exc
            record_writes[path] = current
        if not isinstance(current, dict) or current.get("schema") != schema:
            raise CommandRejectedError(f"{schema}_invalid")
        return current

    def _staged_stock_record(
        self,
        stock_ref: str,
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> tuple[str, Dict[str, Any], str]:
        path, fresh, owner_ref = self._stock_record(stock_ref)
        staged = record_writes.get(path)
        if staged is None:
            staged = fresh
            record_writes[path] = staged
        if not isinstance(staged, dict):
            raise CommandRejectedError("inventory_stock_invalid")
        return path, staged, owner_ref

    def _consume_staged_stock(
        self,
        stock: Dict[str, Any],
        item_ref: str,
        quantity: int,
        *,
        code: str,
    ) -> None:
        if quantity <= 0:
            return
        try:
            container, key = self._stock_item_key(stock, item_ref)
        except CommandRejectedError as exc:
            raise CommandRejectedError(code) from exc
        available = container.get(key, 0)
        if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
            raise CommandRejectedError(code)
        container[key] = available - quantity

    def _autonomous_operation_routes(self, operation: Mapping[str, Any]) -> list[str]:
        refs = [x for x in operation.get("route_refs", []) if isinstance(x, str) and x]
        subject_ref = operation.get("subject_ref")
        if operation.get("subject_kind") == "route" and isinstance(subject_ref, str) and subject_ref and subject_ref not in refs:
            refs.insert(0, subject_ref)
        return refs

    def _economy_item_base_price(self, item_ref: str) -> int:
        try:
            record = self.repository.read_json("game/data/mechanics/economy.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("economy_mechanics_invalid") from exc
        prices = record.get("item_prices") if isinstance(record, Mapping) else None
        row = prices.get(item_ref) if isinstance(prices, Mapping) else None
        value = row.get("base_price_ryo") if isinstance(row, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise CommandRejectedError("economy_item_price_invalid")
        return value

    def _staged_inventory_registry(self, *, record_writes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        registry = self._staged_registry(
            INVENTORY_REGISTRY_PATH, schema="inventory-registry", record_writes=record_writes
        )
        holders = registry.get("holders")
        if not isinstance(holders, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        return registry

    @staticmethod
    def _move_staged_currency(
        inventory: Dict[str, Any], source_ref: str, destination_ref: str, amount: int, *, code: str
    ) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise CommandRejectedError("commerce_amount_invalid")
        holders = inventory.get("holders")
        if not isinstance(holders, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        source = holders.get(source_ref)
        if not isinstance(source, dict):
            raise CommandRejectedError(code)
        balance = source.get("currency.ryo", 0)
        if isinstance(balance, bool) or not isinstance(balance, int) or balance < amount:
            raise CommandRejectedError(code)
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

    def _autonomous_commerce_profile(self, operation_kind: str) -> Optional[Mapping[str, Any]]:
        commerce = self._operational_world_mechanics().get("commerce")
        profiles = commerce.get("autonomous_shipments") if isinstance(commerce, Mapping) else None
        row = profiles.get(operation_kind) if isinstance(profiles, Mapping) else None
        return row if isinstance(row, Mapping) else None

    def _launch_autonomous_shipment(
        self,
        *,
        operation: Dict[str, Any],
        faction_id: str,
        at: CampaignTime,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        profile = self._autonomous_commerce_profile(str(operation.get("operation_kind") or ""))
        if not isinstance(profile, Mapping):
            return {"status": "blocked", "reason": "autonomous commerce profile missing", "refs": [], "affected_paths": []}
        source_stock_ref = profile.get("source_stock_ref")
        source_owner_ref = profile.get("source_owner_ref")
        origin_place_ref = profile.get("origin_place_ref")
        if not isinstance(source_stock_ref, str) or not source_stock_ref:
            return {"status": "blocked", "reason": "autonomous commerce has no conserved cargo source", "refs": [], "affected_paths": []}
        if source_owner_ref != faction_id:
            return {"status": "blocked", "reason": "autonomous commerce source owner mismatch", "refs": [], "affected_paths": []}
        route_sink_refs = profile.get("route_sink_refs")
        if not isinstance(route_sink_refs, Mapping):
            raise CommandRejectedError("operational_world_mechanics_invalid")
        route_ref = next((ref for ref in self._autonomous_operation_routes(operation) if ref in route_sink_refs), None)
        if not isinstance(route_ref, str):
            return {"status": "blocked", "reason": "no lawful stock-connected route for autonomous shipment", "refs": [], "affected_paths": []}
        route = self._real_route(route_ref)
        if not isinstance(origin_place_ref, str) or origin_place_ref not in (route.get("from"), route.get("to")):
            return {"status": "blocked", "reason": "autonomous commerce origin is not on selected route", "refs": [], "affected_paths": []}
        sink_ref = route_sink_refs.get(route_ref)
        commerce_mechanics = self._operational_world_mechanics().get("commerce")
        sinks = commerce_mechanics.get("aggregate_sinks") if isinstance(commerce_mechanics, Mapping) else None
        sink = sinks.get(sink_ref) if isinstance(sinks, Mapping) and isinstance(sink_ref, str) else None
        if not isinstance(sink, Mapping):
            raise CommandRejectedError("operational_world_mechanics_invalid")
        destination_place = route.get("to") if route.get("from") == origin_place_ref else route.get("from")
        if sink.get("place_ref") != destination_place:
            return {"status": "blocked", "reason": "autonomous commerce sink does not match route destination", "refs": [], "affected_paths": []}

        stock_path, stock, stock_owner = self._staged_stock_record(source_stock_ref, record_writes=record_writes)
        if stock_owner != source_owner_ref or stock.get("location_ref") != origin_place_ref:
            return {"status": "blocked", "reason": "autonomous commerce stock custody mismatch", "refs": [], "affected_paths": []}
        candidates = [x for x in profile.get("item_candidates", []) if isinstance(x, str) and x]
        quantity = profile.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise CommandRejectedError("operational_world_mechanics_invalid")
        available: list[str] = []
        for item_ref in candidates:
            try:
                container, key = self._stock_item_key(stock, item_ref)
            except CommandRejectedError:
                continue
            count = container.get(key, 0)
            if isinstance(count, int) and not isinstance(count, bool) and count >= quantity:
                available.append(item_ref)
        procurement_paths: list[str] = []
        if not available:
            procurement_stock_ref = profile.get("procurement_stock_ref")
            procurement_owner_ref = profile.get("procurement_owner_ref")
            if isinstance(procurement_stock_ref, str) and procurement_stock_ref and isinstance(procurement_owner_ref, str) and procurement_owner_ref:
                procurement_path, procurement_stock, procurement_owner = self._staged_stock_record(
                    procurement_stock_ref, record_writes=record_writes
                )
                if procurement_owner != procurement_owner_ref or procurement_stock.get("location_ref") != origin_place_ref:
                    return {"status": "blocked", "reason": "autonomous procurement custody mismatch", "refs": [], "affected_paths": []}
                procurable: list[str] = []
                for item_ref in candidates:
                    try:
                        container, key = self._stock_item_key(procurement_stock, item_ref)
                    except CommandRejectedError:
                        continue
                    count = container.get(key, 0)
                    if isinstance(count, int) and not isinstance(count, bool) and count >= quantity:
                        procurable.append(item_ref)
                if procurable:
                    identity = str(operation.get("operation_id") or operation.get("id") or "autonomous-commerce")
                    procured_item = procurable[_stable_roll(identity, "procurement", modulo=len(procurable))]
                    price = self._economy_item_base_price(procured_item) * quantity
                    buyer_account = self._funding_holder_for(faction_id)
                    seller_account = self._funding_holder_for(procurement_owner_ref)
                    inventory = self._staged_inventory_registry(record_writes=record_writes)
                    # Aggregate private-economy accounts may represent both sides
                    # of an ordinary gray-market sale. In that case currency is
                    # already conserved inside the same aggregate holder and only
                    # physical stock custody changes.
                    if buyer_account != seller_account:
                        try:
                            self._move_staged_currency(
                                inventory, buyer_account, seller_account, price,
                                code="autonomous procurement buyer lacks funds",
                            )
                        except CommandRejectedError:
                            return {"status": "blocked", "reason": "autonomous procurement lacks funds", "refs": [], "affected_paths": []}
                    self._consume_staged_stock(
                        procurement_stock, procured_item, quantity,
                        code="autonomous procurement cargo unavailable",
                    )
                    source_container, source_key = self._stock_item_key(stock, procured_item)
                    current = source_container.get(source_key, 0)
                    if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                        raise CommandRejectedError("inventory_stock_invalid")
                    source_container[source_key] = current + quantity
                    procurement_paths.extend([procurement_path, INVENTORY_REGISTRY_PATH])
                    available = [procured_item]
                    operation.setdefault("resource_refs", [])
                    for ref in (procurement_stock_ref, procured_item):
                        if ref not in operation["resource_refs"]:
                            operation["resource_refs"].append(ref)
                    operation["resource_refs"].sort()
        if not available:
            return {"status": "blocked", "reason": "no configured conserved cargo is available", "refs": [], "affected_paths": []}
        identity = str(operation.get("operation_id") or operation.get("id") or "autonomous-commerce")
        item_ref = available[_stable_roll(identity, "cargo", modulo=len(available))]
        unit_price = self._economy_item_base_price(item_ref)
        total_ryo = quantity * unit_price
        digest = hashlib.sha256(f"{identity}\x00{route_ref}\x00{item_ref}".encode()).hexdigest()[:20]
        contract_ref = f"trade.contract.autonomy.{digest}"
        shipment_ref = f"shipment.autonomy.{digest}"
        escrow_ref = f"escrow.trade.autonomy.{digest}"

        registry = self._staged_registry(_COMMERCE_REGISTRY_PATH, schema="commerce-registry", record_writes=record_writes)
        contracts = registry.get("contracts"); shipments = registry.get("shipments")
        if not isinstance(contracts, dict) or not isinstance(shipments, dict):
            raise CommandRejectedError("commerce-registry_invalid")
        existing = contracts.get(contract_ref)
        if isinstance(existing, Mapping):
            return {"status": "applied", "refs": [contract_ref, str(existing.get("shipment_ref") or shipment_ref)], "affected_paths": [_COMMERCE_REGISTRY_PATH]}

        payer_ref = sink.get("economy_ref")
        if not isinstance(payer_ref, str) or not payer_ref:
            raise CommandRejectedError("operational_world_mechanics_invalid")
        inventory = self._staged_inventory_registry(record_writes=record_writes)
        try:
            self._move_staged_currency(inventory, payer_ref, escrow_ref, total_ryo, code="autonomous commerce payer lacks funds")
        except CommandRejectedError:
            return {"status": "blocked", "reason": "autonomous commerce destination economy lacks funds", "refs": [], "affected_paths": []}
        self._consume_staged_stock(stock, item_ref, quantity, code="autonomous commerce cargo unavailable")
        seller_account_ref = self._funding_holder_for(source_owner_ref)
        contracts[contract_ref] = {
            "id": contract_ref, "status": "in_transit", "client_ref": payer_ref,
            "carrier_ref": faction_id, "source_holder_ref": source_stock_ref,
            "destination_holder_ref": str(sink_ref), "item_ref": item_ref,
            "quantity": quantity, "unit_price_ryo": unit_price, "total_ryo": total_ryo,
            "escrow_holder_ref": escrow_ref, "route_ref": route_ref,
            "contraband": bool(profile.get("contraband")), "opened_at": str(at),
            "accepted_at": str(at), "funded_at": str(at), "dispatched_at": str(at),
            "completed_at": None, "shipment_ref": shipment_ref,
            "classification": str(operation.get("classification") or "restricted"),
        }
        shipments[shipment_ref] = {
            "id": shipment_ref, "contract_ref": contract_ref, "cargo_holder_ref": shipment_ref,
            "item_ref": item_ref, "quantity": quantity, "route_ref": route_ref,
            "origin_holder_ref": source_stock_ref, "destination_holder_ref": str(sink_ref),
            "custodian_ref": faction_id, "status": "in_transit", "dispatched_at": str(at),
            "delivered_at": None, "seized_at": None,
            "classification": str(operation.get("classification") or "restricted"),
        }
        operation.setdefault("resource_refs", [])
        for ref in (item_ref, source_stock_ref, escrow_ref):
            if ref not in operation["resource_refs"]:
                operation["resource_refs"].append(ref)
        operation["resource_refs"].sort()
        metric = registry.setdefault("route_metrics", {}).setdefault(route_ref, {
            "route_ref": route_ref, "shipment_count": 0, "delivered_count": 0,
            "seizure_count": 0, "cargo_units": 0, "gross_ryo": 0, "last_activity_at": None,
        })
        metric["shipment_count"] = int(metric.get("shipment_count", 0)) + 1
        metric["cargo_units"] = int(metric.get("cargo_units", 0)) + quantity
        metric["last_activity_at"] = str(at)
        return {
            "status": "applied", "refs": [contract_ref, shipment_ref, f"seller_account:{seller_account_ref}"],
            "affected_paths": list(dict.fromkeys([*procurement_paths, stock_path, INVENTORY_REGISTRY_PATH, _COMMERCE_REGISTRY_PATH])),
        }

    def _inspect_autonomous_shipments(
        self,
        *,
        operation: Mapping[str, Any],
        faction_id: str,
        at: CampaignTime,
        evidence_event_ref: str,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        route_refs = self._autonomous_operation_routes(operation)
        if not route_refs:
            return {"status": "blocked", "reason": "commerce inspection has no route", "refs": [], "affected_paths": []}
        registry = self._staged_registry(_COMMERCE_REGISTRY_PATH, schema="commerce-registry", record_writes=record_writes)
        shipments = registry.get("shipments"); contracts = registry.get("contracts"); crossings = registry.get("crossings")
        if not isinstance(shipments, dict) or not isinstance(contracts, dict) or not isinstance(crossings, dict):
            raise CommandRejectedError("commerce-registry_invalid")
        candidates = [row for row in shipments.values() if isinstance(row, dict) and row.get("status") == "in_transit" and row.get("route_ref") in route_refs]
        if not candidates:
            return {"status": "blocked", "reason": "no in-transit shipment exists on inspected route", "refs": [], "affected_paths": []}
        candidates.sort(key=lambda row: str(row.get("id") or ""))
        shipment = candidates[0]
        shipment_ref = str(shipment.get("id"))
        contract = contracts.get(shipment.get("contract_ref"))
        if not isinstance(contract, dict):
            raise CommandRejectedError("commerce-registry_invalid")
        contraband = bool(contract.get("contraband"))
        digest = hashlib.sha256(f"{shipment_ref}\x00{faction_id}\x00{evidence_event_ref}".encode()).hexdigest()[:20]
        crossing_ref = f"crossing.autonomy.{digest}"
        result = "contraband_detected" if contraband else "cleared"
        # Counter-smuggling operations may seize only a shipment already marked
        # contraband. Ordinary crossing audits merely record detection/clearance.
        if contraband and str(operation.get("operation_kind")) == "counter_smuggling":
            result = "seized"
            shipment["status"] = "seized"; shipment["seized_at"] = str(at)
            contract["status"] = "seized"; contract["completed_at"] = str(at)
            escrow_ref = contract.get("escrow_holder_ref"); payer_ref = contract.get("client_ref")
            total = contract.get("total_ryo")
            if isinstance(escrow_ref, str) and isinstance(payer_ref, str) and isinstance(total, int) and not isinstance(total, bool) and total > 0:
                inventory = self._staged_inventory_registry(record_writes=record_writes)
                self._move_staged_currency(inventory, escrow_ref, payer_ref, total, code="commerce escrow invalid")
            metric = registry.setdefault("route_metrics", {}).setdefault(str(shipment.get("route_ref")), {
                "route_ref": str(shipment.get("route_ref")), "shipment_count": 0, "delivered_count": 0,
                "seizure_count": 0, "cargo_units": 0, "gross_ryo": 0, "last_activity_at": None,
            })
            metric["seizure_count"] = int(metric.get("seizure_count", 0)) + 1
            metric["last_activity_at"] = str(at)
        crossings[crossing_ref] = {
            "id": crossing_ref, "shipment_ref": shipment_ref, "route_ref": str(shipment.get("route_ref")),
            "authority_ref": faction_id, "inspected_at": str(at), "result": result,
            "evidence_event_ref": evidence_event_ref,
            "classification": str(operation.get("classification") or "restricted"),
        }
        affected = [_COMMERCE_REGISTRY_PATH]
        if result == "seized":
            affected.append(INVENTORY_REGISTRY_PATH)
            # A seizure is legal evidence, not an automatic conviction or
            # arrest. Persist an evidence-backed case against the actual
            # shipment custodian so later warrants/custody/disposition use the
            # legal domain instead of disappearing into the crossing receipt.
            subject_ref = shipment.get("custodian_ref")
            if isinstance(subject_ref, str) and subject_ref:
                try:
                    self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
                except CommandRejectedError:
                    subject_ref = None
            if isinstance(subject_ref, str):
                legal = self._staged_registry(
                    _LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes
                )
                cases = legal.get("cases")
                if not isinstance(cases, dict):
                    raise CommandRejectedError("legal-case-registry_invalid")
                case_digest = hashlib.sha256(f"{shipment_ref}\x00{faction_id}\x00smuggling".encode()).hexdigest()[:20]
                case_ref = f"case.smuggling.{case_digest}"
                case = cases.get(case_ref)
                if case is None:
                    case = {
                        "id": case_ref,
                        "case_kind": "smuggling_seizure",
                        "issuer_ref": faction_id,
                        "subject_ref": subject_ref,
                        "requester_ref": faction_id,
                        "status": "open",
                        "opened_at": str(at),
                        "updated_at": str(at),
                        "summary": f"Contraband shipment {shipment_ref} seized under crossing record {crossing_ref}.",
                        "visibility": str(operation.get("classification") or "restricted"),
                        "offense_refs": ["offense.smuggling"],
                        "evidence_refs": [evidence_event_ref],
                        "warrant": {"status": "none", "authority_ref": None, "issued_at": None},
                        "bounty": {
                            "status": "none", "payer_ref": None, "payer_holder_ref": None,
                            "escrow_holder_ref": None, "amount_ryo": 0, "hunter_refs": [],
                            "posted_at": None, "verified_evidence_refs": [], "settled_at": None,
                        },
                        "custody_ref": None,
                        "disposition": None,
                    }
                    cases[case_ref] = case
                elif isinstance(case, dict):
                    evidence = case.setdefault("evidence_refs", [])
                    if evidence_event_ref not in evidence:
                        evidence.append(evidence_event_ref); evidence.sort()
                    case["updated_at"] = str(at)
                else:
                    raise CommandRejectedError("legal-case-registry_invalid")
                operation.setdefault("case_refs", [])
                if case_ref not in operation["case_refs"]:
                    operation["case_refs"].append(case_ref); operation["case_refs"].sort()
                affected.append(_LEGAL_CASE_REGISTRY_PATH)
                return {
                    "status": "applied",
                    "refs": [crossing_ref, shipment_ref, case_ref, f"crossing_result:{result}"],
                    "affected_paths": list(dict.fromkeys(affected)),
                }
        return {"status": "applied", "refs": [crossing_ref, shipment_ref, f"crossing_result:{result}"], "affected_paths": affected}

    def _settle_due_autonomous_shipments(
        self,
        *,
        faction_id: str,
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[Mapping[str, Any]]:
        try:
            registry = self._staged_registry(_COMMERCE_REGISTRY_PATH, schema="commerce-registry", record_writes=record_writes)
        except CommandRejectedError:
            return []
        contracts = registry.get("contracts"); shipments = registry.get("shipments")
        if not isinstance(contracts, dict) or not isinstance(shipments, dict):
            raise CommandRejectedError("commerce-registry_invalid")
        settled: list[Mapping[str, Any]] = []
        for shipment_ref in sorted(shipments):
            shipment = shipments.get(shipment_ref)
            if not isinstance(shipment, dict) or shipment.get("status") != "in_transit" or shipment.get("custodian_ref") != faction_id:
                continue
            try:
                dispatched = CampaignTime.parse(str(shipment.get("dispatched_at")))
                route = self._real_route(str(shipment.get("route_ref")))
            except (TypeError, ValueError, CommandRejectedError):
                continue
            reference_days = route.get("reference_travel_days")
            if isinstance(reference_days, bool) or not isinstance(reference_days, (int, float)) or reference_days <= 0:
                continue
            due = dispatched.add_seconds(max(1, int(float(reference_days) * 86400)))
            if due > at:
                continue
            contract = contracts.get(shipment.get("contract_ref"))
            if not isinstance(contract, dict) or contract.get("status") != "in_transit":
                continue
            escrow_ref = contract.get("escrow_holder_ref"); total = contract.get("total_ryo")
            source_owner_ref = faction_id
            seller_account_ref = self._funding_holder_for(source_owner_ref)
            if not isinstance(escrow_ref, str) or isinstance(total, bool) or not isinstance(total, int) or total <= 0:
                raise CommandRejectedError("commerce-registry_invalid")
            inventory = self._staged_inventory_registry(record_writes=record_writes)
            self._move_staged_currency(inventory, escrow_ref, seller_account_ref, total, code="commerce escrow invalid")
            shipment["status"] = "delivered"; shipment["delivered_at"] = str(at)
            contract["status"] = "delivered"; contract["completed_at"] = str(at)
            metric = registry.setdefault("route_metrics", {}).setdefault(str(shipment.get("route_ref")), {
                "route_ref": str(shipment.get("route_ref")), "shipment_count": 0, "delivered_count": 0,
                "seizure_count": 0, "cargo_units": 0, "gross_ryo": 0, "last_activity_at": None,
            })
            metric["delivered_count"] = int(metric.get("delivered_count", 0)) + 1
            metric["gross_ryo"] = int(metric.get("gross_ryo", 0)) + total
            metric["last_activity_at"] = str(at)
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{shipment_ref}:delivered", kind="commerce_shipment_delivered", at=at,
                host_refs=(faction_id, shipment_ref), actor_refs=(faction_id,),
                affected_owner_refs=(_COMMERCE_REGISTRY_PATH, INVENTORY_REGISTRY_PATH),
                material_consequence_refs=(shipment_ref, str(contract.get("destination_holder_ref")), str(contract.get("item_ref"))),
                classification=str(contract.get("classification") or "restricted"), audience_refs=(), source_refs=(faction_id,),
            )
            settled.append({"shipment_ref": shipment_ref, "status": "delivered", "event_id": event_id})
        return settled

    def _autonomous_bounty_amount(self, case: Mapping[str, Any]) -> int:
        subject_ref = case.get("subject_ref")
        if not isinstance(subject_ref, str) or not subject_ref:
            raise CommandRejectedError("legal_bounty_subject_invalid")
        try:
            _path, _digest, subject = self._resolve_covered_owner_view(subject_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError as exc:
            raise CommandRejectedError("legal_bounty_subject_invalid") from exc
        rank = subject.get("official_rank_or_status") if isinstance(subject, Mapping) else None
        if not isinstance(rank, str) or not rank:
            career = subject.get("career_state") if isinstance(subject, Mapping) else None
            rank = career.get("current_rank_or_status") if isinstance(career, Mapping) else None
        bounty_rules = self._operational_world_mechanics().get("bounty")
        mapping = bounty_rules.get("rank_to_mission_rank") if isinstance(bounty_rules, Mapping) else None
        mission_rank = mapping.get(rank) if isinstance(mapping, Mapping) and isinstance(rank, str) else None
        amount_field = bounty_rules.get("amount_field") if isinstance(bounty_rules, Mapping) else None
        if mission_rank not in ("D", "C", "B", "A", "S") or amount_field not in ("client_fee_typical_ryo", "client_fee_max_ryo"):
            raise CommandRejectedError("legal_bounty_rank_unpriced")
        try:
            economy = self.repository.read_json("game/data/mechanics/economy.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("economy_mechanics_invalid") from exc
        rank_row = economy.get("mission_ranks", {}).get(mission_rank) if isinstance(economy, Mapping) else None
        amount = rank_row.get(amount_field) if isinstance(rank_row, Mapping) else None
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise CommandRejectedError("legal_bounty_rank_unpriced")
        return amount

    def _mission_fee_for_rank(self, mission_rank: str) -> int:
        if mission_rank not in ("D", "C", "B", "A", "S"):
            raise CommandRejectedError("mission_rank_invalid")
        try:
            economy = self.repository.read_json("game/data/mechanics/economy.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("economy_mechanics_invalid") from exc
        rank_row = economy.get("mission_ranks", {}).get(mission_rank) if isinstance(economy, Mapping) else None
        amount = rank_row.get("client_fee_typical_ryo") if isinstance(rank_row, Mapping) else None
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise CommandRejectedError("mission_rank_unpriced")
        return amount

    def _staged_domain_registry(self, *, record_writes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        staged = record_writes.get(DOMAIN_REGISTRY_PATH)
        if isinstance(staged, dict):
            return staged
        try:
            staged = copy.deepcopy(self.repository.read_json(DOMAIN_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("domain_registry_invalid") from exc
        if not isinstance(staged, dict) or not isinstance(staged.get("contracts"), list):
            raise CommandRejectedError("domain_registry_invalid")
        record_writes[DOMAIN_REGISTRY_PATH] = staged
        return staged

    def _staged_faction_owner_record(
        self,
        faction_id: str,
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> tuple[str, Dict[str, Any]]:
        try:
            path, _digest, view = self._resolve_covered_owner_view(faction_id, cache=_OwnerResolutionCache())
        except CommandRejectedError as exc:
            raise CommandRejectedError("faction_owner_invalid") from exc
        staged = record_writes.get(path)
        if staged is None:
            try:
                staged = copy.deepcopy(self.repository.read_json(path))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("faction_owner_invalid") from exc
            record_writes[path] = staged
        if not isinstance(staged, dict) or not isinstance(staged.get("faction"), dict):
            raise CommandRejectedError("faction_owner_invalid")
        return path, staged

    def _known_subject_claim_refs(
        self,
        operation: Mapping[str, Any],
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[str]:
        subject_ref = operation.get("subject_ref")
        if not isinstance(subject_ref, str) or not subject_ref:
            return []
        information = InformationStore(self.repository, record_writes)
        holders = sorted(set(
            ref for ref in operation.get("participant_refs", [])
            if isinstance(ref, str) and ref
        ))
        known: list[str] = []
        try:
            for holder in holders:
                known.extend(information.holder_subject_claim_refs(holder, subject_ref, limit=64))
        except ValueError:
            return []
        return sorted(set(known))

    def _subject_is_dead(self, subject_ref: str, *, record_writes: Dict[str, Dict[str, Any]]) -> bool:
        try:
            path, _digest, view = self._resolve_covered_owner_view(subject_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return False
        record = record_writes.get(path, view)
        if not isinstance(record, Mapping):
            return False
        life = record.get("life_status")
        if isinstance(life, str):
            return life == "dead"
        health = record.get("health")
        return isinstance(health, Mapping) and health.get("status") in ("dead", "deceased")

    def _bundled_institution_record(
        self,
        institution_id: str,
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        for path in _INSTITUTION_BUNDLE_PATHS:
            record = record_writes.get(path)
            if record is None:
                try:
                    record = self.repository.read_json(path)
                except (FileNotFoundError, ValueError):
                    continue
            payload = record.get("payload") if isinstance(record, Mapping) else None
            if not isinstance(payload, Mapping):
                continue
            for lane in ("institutions", "clans"):
                rows = payload.get(lane, [])
                if not isinstance(rows, list):
                    continue
                match = next((row for row in rows if isinstance(row, Mapping) and row.get("id") == institution_id), None)
                if isinstance(match, Mapping):
                    return match
        return None

    def _institution_force_ref(self, institution: Mapping[str, Any]) -> Optional[str]:
        doctrine_ref = institution.get("doctrine_id")
        if not isinstance(doctrine_ref, str) or not doctrine_ref.startswith("doc.force_") or not doctrine_ref.endswith(".field"):
            return None
        body = doctrine_ref[len("doc.force_"):-len(".field")]
        candidate = "force." + body.replace("_", ".")
        try:
            self._resolve_covered_owner(candidate, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return None
        return candidate

    def _institutional_assessment_refs(
        self,
        operation: Mapping[str, Any],
        rule: Mapping[str, Any],
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> tuple[list[str], Optional[str]]:
        institution_id = operation.get("owner_ref")
        if not isinstance(institution_id, str):
            return [], "institutional assessment requires an institution owner"
        institution = self._bundled_institution_record(institution_id, record_writes=record_writes)
        if not isinstance(institution, Mapping):
            # Specialist faction owners may lawfully execute the review delegated
            # from one bundled institution. Resolve that institution through the
            # static delegation map instead of treating the faction as a second
            # institutional authority. Prefer the institution whose profile owns
            # this exact operation kind so medical/mission/etc. delegation stays
            # deterministic when one faction fronts several related branches.
            delegated = self._institutional_program_book().get("delegated_institutions")
            operation_kind = str(operation.get("operation_kind") or "")
            candidates: list[tuple[str, Mapping[str, Any]]] = []
            if isinstance(delegated, Mapping):
                for candidate_id, faction_ref in delegated.items():
                    if faction_ref != institution_id or not isinstance(candidate_id, str):
                        continue
                    candidate = self._bundled_institution_record(candidate_id, record_writes=record_writes)
                    if not isinstance(candidate, Mapping):
                        continue
                    profile = self._institution_profile(candidate)
                    if str(profile.get("operation_kind") or "") == operation_kind:
                        candidates.append((candidate_id, candidate))
            if candidates:
                candidates.sort(key=lambda item: item[0])
                institution = candidates[0][1]
            else:
                return [], "institutional assessment owner is unresolved"
        kind = str(rule.get("assessment_kind") or "institutional")
        actor = operation.get("authority_ref") if isinstance(operation.get("authority_ref"), str) else None
        leader = institution.get("leader_id") if isinstance(institution.get("leader_id"), str) else actor
        force_ref = self._institution_force_ref(institution)
        refs: list[str] = []

        if kind in ("medical", "training", "military"):
            if isinstance(force_ref, str):
                refs.append(force_ref)
            if not refs:
                return [], f"{kind} assessment requires represented force or service capacity"

        elif kind == "intelligence":
            try:
                claim_refs = InformationStore(self.repository, record_writes).holder_recent_claim_refs(actor, limit=64) if isinstance(actor, str) else []
            except ValueError:
                claim_refs = []
            selected_claim = self._stable_program_pick(
                claim_refs, institution_id, operation.get("operation_id"), "intelligence-assessment"
            )
            if isinstance(selected_claim, str):
                refs.append(selected_claim)
            if not refs:
                return [], "intelligence assessment requires an existing claim known by its analyst"

        elif kind == "security":
            security = record_writes.get(_SECURITY_NETWORK_REGISTRY_PATH)
            if security is None:
                try:
                    security = self.repository.read_json(_SECURITY_NETWORK_REGISTRY_PATH)
                except (FileNotFoundError, ValueError):
                    security = {}
            sectors = security.get("sectors") if isinstance(security, Mapping) else None
            if isinstance(sectors, Mapping):
                refs.extend(sorted(ref for ref, row in sectors.items() if isinstance(ref, str) and isinstance(row, Mapping) and row.get("owner_ref") == institution_id))
            if not refs and isinstance(force_ref, str):
                refs.append(force_ref)
            if not refs:
                return [], "security assessment requires an established sector or represented force"

        elif kind == "technical":
            registry = record_writes.get(DOMAIN_REGISTRY_PATH)
            if registry is None:
                try:
                    registry = self.repository.read_json(DOMAIN_REGISTRY_PATH)
                except (FileNotFoundError, ValueError):
                    registry = {}
            projects = registry.get("projects") if isinstance(registry, Mapping) else None
            if isinstance(projects, list):
                for row in projects:
                    if not isinstance(row, Mapping) or row.get("status") not in ("active", "pending"):
                        continue
                    if row.get("institution_ref") == institution_id or (leader and row.get("authority_ref") == leader):
                        if isinstance(row.get("id"), str): refs.append(str(row["id"]))
            if not refs:
                return [], "technical assessment requires an existing lawful project or production schedule"

        elif kind == "mission":
            registry = record_writes.get(DOMAIN_REGISTRY_PATH)
            if registry is None:
                try: registry = self.repository.read_json(DOMAIN_REGISTRY_PATH)
                except (FileNotFoundError, ValueError): registry = {}
            active = registry.get("active_missions") if isinstance(registry, Mapping) else None
            if isinstance(active, list):
                refs.extend(sorted(ref for ref in active if isinstance(ref, str)))
            # Specialist mission-office factions own their active/wake mission
            # routing on faction.plan_state rather than the compact
            # domain registry. Pull those exact mission refs through the faction
            # owner so a review measures real unresolved work, not a clock tick.
            if institution_id.startswith("faction."):
                try:
                    faction_path, _digest, faction_view = self._resolve_covered_owner_view(
                        institution_id, cache=_OwnerResolutionCache()
                    )
                except CommandRejectedError:
                    faction_view = {}
                    faction_path = ""
                faction_record = record_writes.get(faction_path, faction_view) if faction_path else faction_view
                faction = faction_record.get("faction") if isinstance(faction_record, Mapping) else None
                plan = faction.get("plan_state") if isinstance(faction, Mapping) else None
                if isinstance(plan, Mapping):
                    for lane in ("autonomous_mission_refs", "wake_required_mission_refs"):
                        values = plan.get(lane)
                        if isinstance(values, list):
                            refs.extend(ref for ref in values if isinstance(ref, str) and ref.startswith("mission."))
            refs = sorted(set(refs))
            if not refs:
                return [], "mission assessment requires a concrete active mission or operational demand"

        elif kind == "trade":
            commerce = record_writes.get(_COMMERCE_REGISTRY_PATH)
            if commerce is None:
                try: commerce = self.repository.read_json(_COMMERCE_REGISTRY_PATH)
                except (FileNotFoundError, ValueError): commerce = {}
            for lane in ("contracts", "shipments", "crossings"):
                rows = commerce.get(lane) if isinstance(commerce, Mapping) else None
                if not isinstance(rows, Mapping): continue
                for ref, row in rows.items():
                    if not isinstance(ref, str) or not isinstance(row, Mapping): continue
                    values = set(v for v in row.values() if isinstance(v, str))
                    if institution_id in values or (leader and leader in values): refs.append(ref)
            if not refs:
                return [], "trade assessment requires a real contract, shipment, or crossing"

        elif kind == "administration":
            try: diplomacy = record_writes.get(_DIPLOMACY_REGISTRY_PATH) or self.repository.read_json(_DIPLOMACY_REGISTRY_PATH)
            except (FileNotFoundError, ValueError): diplomacy = {}
            agreements = diplomacy.get("agreements") if isinstance(diplomacy, Mapping) else None
            if isinstance(agreements, Mapping):
                for ref, row in agreements.items():
                    if not isinstance(ref, str) or not isinstance(row, Mapping): continue
                    parties = row.get("party_refs", [])
                    if institution_id in parties or (leader and leader in parties): refs.append(ref)
            incidents = diplomacy.get("incidents") if isinstance(diplomacy, Mapping) else None
            if isinstance(incidents, list):
                for row in incidents:
                    if not isinstance(row, Mapping): continue
                    parties = row.get("party_refs", [])
                    if institution_id in parties or (leader and leader in parties):
                        if isinstance(row.get("id"), str): refs.append(str(row["id"]))
            try: governance = record_writes.get("state/reg/governance.json") or self.repository.read_json("state/reg/governance.json")
            except (FileNotFoundError, ValueError): governance = {}
            jurisdictions = governance.get("jurisdictions") if isinstance(governance, Mapping) else None
            if isinstance(jurisdictions, Mapping):
                for ref, row in jurisdictions.items():
                    if isinstance(ref, str) and isinstance(row, Mapping) and row.get("administration_ref") in (institution_id, leader): refs.append(ref)
            if not refs:
                return [], "administrative assessment requires a real jurisdiction, agreement, or incident"

        elif kind == "communications":
            information = InformationStore(self.repository, record_writes)
            try:
                for holder in dict.fromkeys(ref for ref in (actor, institution_id) if isinstance(ref, str) and ref):
                    refs.extend(information.holder_delivery_refs(holder))
            except ValueError:
                refs = []
            if not refs:
                return [], "communications assessment requires an actual information delivery"

        elif kind == "clan":
            if isinstance(leader, str):
                refs.append(leader)
                try: family = self.repository.read_json("state/family/index.json")
                except (FileNotFoundError, ValueError): family = {}
                person = family.get("person_index", {}).get(leader) if isinstance(family, Mapping) and isinstance(family.get("person_index"), Mapping) else None
                if isinstance(person, Mapping):
                    for lane in ("kinships", "parentage", "households", "successions"):
                        refs.extend(ref for ref in person.get(lane, []) if isinstance(ref, str))
            if not refs:
                return [], "clan assessment requires a represented leader or established family fact"

        else:
            if isinstance(force_ref, str): refs.append(force_ref)
            if isinstance(leader, str): refs.append(leader)
            if not refs:
                return [], "institutional assessment has no represented causal owner to inspect"

        return sorted(set(refs)), None

    def _find_service_contract(
        self,
        *,
        seller_ref: str,
        buyer_ref: Optional[str],
        statuses: Sequence[str],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        registry = self._staged_domain_registry(record_writes=record_writes)
        rows = registry.get("contracts")
        if not isinstance(rows, list):
            raise CommandRejectedError("domain_registry_invalid")
        matches = [
            row for row in rows
            if isinstance(row, dict)
            and row.get("kind") == "mercenary_service"
            and row.get("seller_ref") == seller_ref
            and (buyer_ref is None or row.get("buyer_ref") == buyer_ref)
            and row.get("status") in statuses
        ]
        matches.sort(key=lambda row: (str(row.get("opened_at") or ""), str(row.get("id") or "")))
        return matches[0] if matches else None

    def _apply_service_contract_effect(
        self,
        *,
        operation: Dict[str, Any],
        faction_id: str,
        at: CampaignTime,
        rule: Mapping[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        action = rule.get("service_contract_action")
        rank = str(rule.get("mission_rank") or "A")
        client_ref = operation.get("client_ref") if isinstance(operation.get("client_ref"), str) else None
        if action == "offer":
            seller_ref = operation.get("subject_ref") if isinstance(operation.get("subject_ref"), str) else None
            buyer_ref = client_ref or faction_id
            if buyer_ref != faction_id or not isinstance(seller_ref, str) or not seller_ref.startswith("faction."):
                return {"status":"blocked","reason":"service contract offer requires the paying faction and a concrete provider","refs":[],"affected_paths":[]}
            try:
                self._resolve_covered_owner(seller_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                return {"status":"blocked","reason":"service contract provider is unresolved","refs":[],"affected_paths":[]}
            amount = self._mission_fee_for_rank(rank)
            digest = hashlib.sha256(f"{operation.get('operation_id')}\x00{buyer_ref}\x00{seller_ref}".encode()).hexdigest()[:20]
            contract_ref = f"contract.service.{digest}"
            registry = self._staged_domain_registry(record_writes=record_writes)
            rows = registry["contracts"]
            existing = next((row for row in rows if isinstance(row, dict) and row.get("id") == contract_ref), None)
            if existing is not None:
                return {"status":"applied","refs":[contract_ref],"affected_paths":[DOMAIN_REGISTRY_PATH]}
            payer_holder = self._funding_holder_for(buyer_ref)
            escrow_ref = f"escrow.service.{digest}"
            inventory = self._staged_inventory_registry(record_writes=record_writes)
            holders = inventory.get("holders") if isinstance(inventory, dict) else None
            if not isinstance(holders, dict):
                raise CommandRejectedError("inventory_registry_invalid")
            payer = holders.get(payer_holder)
            balance = payer.get("currency.ryo", 0) if isinstance(payer, Mapping) else 0
            escrow = holders.setdefault(escrow_ref, {})
            escrow_balance = escrow.get("currency.ryo", 0) if isinstance(escrow, Mapping) else None
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < amount:
                return {"status":"blocked","reason":"service contract client funds are insufficient","refs":[],"affected_paths":[]}
            if isinstance(escrow_balance, bool) or not isinstance(escrow_balance, int) or escrow_balance != 0:
                raise CommandRejectedError("service_contract_escrow_conflict")
            self._move_staged_currency(inventory, payer_holder, escrow_ref, amount, code="service contract escrow invalid")
            rows.append({
                "id":contract_ref,"kind":"mercenary_service","status":"offered","issuer_ref":buyer_ref,
                "counterparty_refs":sorted([buyer_ref,seller_ref]),
                "scope":str(operation.get("success_condition") or "Paid mercenary service under accepted terms."),
                "buyer_ref":buyer_ref,"seller_ref":seller_ref,"stock_ref":None,"item_ref":None,
                "quantity":None,"unit_price_ryo":None,"total_ryo":amount,"payment_holder_ref":escrow_ref,
                "opened_at":str(at),"accepted_at":None,"expires_at":str(at.add_seconds(30*24*60*60)),
                "next_due_at":None,"completed_at":None,"cancelled_at":None,"result":None,
            })
            rows.sort(key=lambda row: str(row.get("id") or ""))
            return {"status":"applied","refs":[contract_ref,escrow_ref],"affected_paths":[DOMAIN_REGISTRY_PATH,INVENTORY_REGISTRY_PATH]}
        if action == "accept":
            if not isinstance(client_ref, str):
                return {"status":"blocked","reason":"service contract acceptance requires an identified client","refs":[],"affected_paths":[]}
            row = self._find_service_contract(
                seller_ref=faction_id, buyer_ref=client_ref, statuses=("offered",), record_writes=record_writes,
            )
            if row is None:
                return {"status":"blocked","reason":"no funded service contract is available to accept","refs":[],"affected_paths":[]}
            row["status"] = "accepted"; row["accepted_at"] = str(at)
            return {"status":"applied","refs":[str(row["id"])],"affected_paths":[DOMAIN_REGISTRY_PATH]}
        raise CommandRejectedError("autonomous_effect_rule_invalid")

    def _operation_team_for_tasking(
        self,
        operation: Mapping[str, Any],
        faction_id: str,
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
        candidates = [ref for ref in operation.get("team_refs", []) if isinstance(ref, str) and ref]
        if not candidates:
            _path, owner = self._staged_faction_owner_record(faction_id, record_writes=record_writes)
            plan = owner.get("faction", {}).get("plan_state")
            if isinstance(plan, Mapping):
                candidates = [ref for ref in plan.get("autonomous_team_refs", []) if isinstance(ref, str) and ref]
        for team_ref in sorted(set(candidates)):
            try:
                path, _digest, view = self._resolve_covered_owner_view(team_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                continue
            team = record_writes.get(path)
            if team is None:
                team = copy.deepcopy(dict(view)); record_writes[path] = team
            if not isinstance(team, dict) or team.get("status") != "active":
                continue
            lifecycle = team.get("lifecycle") if isinstance(team.get("lifecycle"), Mapping) else {}
            if lifecycle.get("autonomy_owner_ref") not in (None, faction_id):
                continue
            assignment = team.get("current_assignment_ref")
            if isinstance(assignment, str) and assignment.startswith("mission."):
                try:
                    existing = MissionOwner.from_record(record_writes.get(mission_owner_path(assignment)) or self.repository.read_json(mission_owner_path(assignment)))
                except (FileNotFoundError, TypeError, ValueError):
                    existing = None
                if existing is not None and existing.mission.state not in ("succeeded","failed","aborted","expired"):
                    continue
            return team_ref, team, path
        return None, None, None

    def _create_task_mission(
        self,
        *,
        operation: Dict[str, Any],
        faction_id: str,
        at: CampaignTime,
        rule: Mapping[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        subject_ref = operation.get("subject_ref") if isinstance(operation.get("subject_ref"), str) else None
        objective_kind = str(rule.get("mission_objective_kind") or operation.get("objective_kind") or "investigate")
        mission_rank = str(rule.get("mission_rank") or "A")
        if not subject_ref:
            return {"status":"blocked","reason":"mission tasking requires a concrete subject","refs":[],"affected_paths":[]}
        try:
            self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return {"status":"blocked","reason":"mission tasking subject is unresolved","refs":[],"affected_paths":[]}
        contract = None
        if bool(rule.get("requires_existing_contract")):
            client_ref = operation.get("client_ref") if isinstance(operation.get("client_ref"), str) else None
            contract = self._find_service_contract(
                seller_ref=faction_id, buyer_ref=client_ref, statuses=("accepted","in_progress"), record_writes=record_writes,
            )
            if contract is None:
                return {"status":"blocked","reason":"mission tasking requires an accepted funded service contract","refs":[],"affected_paths":[]}
        team_ref, team, team_path = self._operation_team_for_tasking(operation, faction_id, record_writes=record_writes)
        if not isinstance(team_ref, str) or not isinstance(team, dict) or not isinstance(team_path, str):
            return {"status":"blocked","reason":"mission tasking requires an available eligible team","refs":[],"affected_paths":[]}
        participants = tuple(sorted(ref for ref in team.get("member_refs", []) if isinstance(ref, str) and ref))
        if not participants:
            return {"status":"blocked","reason":"mission tasking team has no participants","refs":[],"affected_paths":[]}
        digest = hashlib.sha256(f"{operation.get('operation_id')}\x00mission".encode()).hexdigest()[:20]
        mission_id = f"mission.autonomy.{digest}"
        path = mission_owner_path(mission_id)
        if path in record_writes or self.repository.read_optional_bytes(path) is not None:
            return {"status":"deferred","refs":[mission_id],"affected_paths":[path]}
        objective = MissionObjective(objective_id=f"objective.autonomy.{digest}", kind=objective_kind, required=True)
        mission = Mission(mission_id=mission_id, state="active", participant_refs=participants, objectives=(objective,), settlement_terms=())
        authority_ref = team.get("assignment_authority_ref") if isinstance(team.get("assignment_authority_ref"), str) else faction_id
        issuer_ref = contract.get("buyer_ref") if isinstance(contract, Mapping) and isinstance(contract.get("buyer_ref"), str) else faction_id
        try:
            funding_holder = self._funding_holder_for(str(issuer_ref))
        except CommandRejectedError:
            funding_holder = self._funding_holder_for(faction_id)
        owner = MissionOwner(
            mission=mission, issuer_ref=str(issuer_ref), authority_ref=str(authority_ref), mission_rank=mission_rank,
            funding_holder_ref=funding_holder, escrow_holder_ref=None, opened_at=at, authorized_at=at,
            starts_at=at, deadline_at=None, next_due_at=at.add_seconds(7*24*60*60),
            operation_ref=str(operation.get("operation_id")), closed_at=None,
            briefing=self._operation_brief(operation, mission_id=mission_id, objective_kind=objective_kind, at=at),
        )
        record_writes[path] = dict(owner.to_record())
        team["current_assignment_ref"] = mission_id
        record_writes[team_path] = team
        faction_path, faction_owner = self._staged_faction_owner_record(faction_id, record_writes=record_writes)
        plan = faction_owner["faction"].get("plan_state")
        if not isinstance(plan, dict):
            raise CommandRejectedError("faction_owner_invalid")
        refs = plan.setdefault("autonomous_mission_refs", [])
        if mission_id not in refs: refs.append(mission_id); refs.sort()
        memory = self._faction_memory(faction_id, at=at, record_writes=record_writes)
        mapping = memory.setdefault("active_mission_team_refs", {})
        if isinstance(mapping, dict): mapping[mission_id] = team_ref
        operation.setdefault("mission_refs", [])
        if mission_id not in operation["mission_refs"]: operation["mission_refs"].append(mission_id); operation["mission_refs"].sort()
        affected = [path, team_path, faction_path]
        refs_out = [mission_id, team_ref]
        if isinstance(contract, dict):
            contract["status"] = "in_progress"; contract["next_due_at"] = str(at.add_seconds(7*24*60*60))
            cref = contract.get("id")
            if isinstance(cref, str):
                refs_out.append(cref)
                operation.setdefault("result_refs", [])
                if cref not in operation["result_refs"]: operation["result_refs"].append(cref); operation["result_refs"].sort()
            affected.append(DOMAIN_REGISTRY_PATH)
        return {"status":"deferred","refs":refs_out,"affected_paths":sorted(set(affected))}

    def _settle_service_contract_for_operation(
        self,
        operation: Mapping[str, Any],
        *,
        succeeded: bool,
        at: CampaignTime,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[str]:
        contract_refs = [ref for ref in operation.get("result_refs", []) if isinstance(ref, str) and ref.startswith("contract.service.")]
        if not contract_refs:
            return []
        registry = self._staged_domain_registry(record_writes=record_writes)
        rows = registry.get("contracts")
        if not isinstance(rows, list):
            raise CommandRejectedError("domain_registry_invalid")
        inventory = self._staged_inventory_registry(record_writes=record_writes)
        settled: list[str] = []
        for contract_ref in sorted(set(contract_refs)):
            row = next((item for item in rows if isinstance(item, dict) and item.get("id") == contract_ref), None)
            if not isinstance(row, dict) or row.get("kind") != "mercenary_service":
                continue
            if row.get("status") in ("completed", "cancelled"):
                settled.append(contract_ref); continue
            if row.get("status") not in ("accepted", "in_progress"):
                raise CommandRejectedError("service_contract_state_invalid")
            escrow_ref = row.get("payment_holder_ref"); amount = row.get("total_ryo")
            buyer_ref = row.get("buyer_ref"); seller_ref = row.get("seller_ref")
            if not isinstance(escrow_ref, str) or isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                raise CommandRejectedError("service_contract_escrow_invalid")
            destination_owner = seller_ref if succeeded else buyer_ref
            if not isinstance(destination_owner, str):
                raise CommandRejectedError("service_contract_party_invalid")
            destination_holder = self._funding_holder_for(destination_owner)
            self._move_staged_currency(inventory, escrow_ref, destination_holder, amount, code="service contract escrow invalid")
            row["next_due_at"] = None
            if succeeded:
                row["status"] = "completed"; row["completed_at"] = str(at); row["result"] = "service_completed_and_escrow_released"
            else:
                row["status"] = "cancelled"; row["cancelled_at"] = str(at); row["result"] = "service_failed_or_aborted_and_escrow_returned"
            settled.extend([contract_ref, escrow_ref, destination_holder])
        return sorted(set(settled))

    def _capture_mission_subject(
        self,
        *,
        operation: Mapping[str, Any],
        mission_id: str,
        faction_id: str,
        at: CampaignTime,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[str]:
        subject_ref = operation.get("subject_ref")
        places = [ref for ref in operation.get("place_refs", []) if isinstance(ref, str) and ref]
        if not isinstance(subject_ref, str) or not places:
            return []
        try:
            person_path, _digest, view = self._resolve_covered_owner_view(subject_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError:
            return []
        if not isinstance(view, Mapping) or view.get("schema") not in ("shinobi_character", "person"):
            return []
        custody = self._staged_registry(_CUSTODY_REGISTRY_PATH, schema="custody-registry", record_writes=record_writes)
        records = custody.get("records")
        if not isinstance(records, dict):
            raise CommandRejectedError("custody_registry_invalid")
        for ref, row in records.items():
            if isinstance(row, Mapping) and row.get("subject_ref") == subject_ref and row.get("status") in ("captured_pending_placement", "detained"):
                return [str(ref)]
        person = record_writes.get(person_path)
        if person is None:
            person = copy.deepcopy(dict(view)); record_writes[person_path] = person
        try:
            apply_personnel_effect(
                person,
                effect=SimpleNamespace(after_resources=(), after_personnel=PersonnelState(total=1, active=0, captured=1)),
                event_marker=f"{mission_id}:capture",
            )
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("autonomous_capture_health_invalid") from exc
        capture_info = None
        try:
            population = copy.deepcopy(record_writes.get(POPULATION_REGISTRY_PATH) or self.repository.read_json(POPULATION_REGISTRY_PATH))
            capture_info = self._reconcile_rostered_person_capture(
                population, person_ref=subject_ref,
                force_writes=record_writes, team_writes=record_writes, formation_writes=record_writes,
            )
            if capture_info is not None:
                record_writes[POPULATION_REGISTRY_PATH] = population
        except FileNotFoundError:
            capture_info = None
        team_ref = next((ref for ref in operation.get("team_refs", []) if isinstance(ref, str)), None)
        custodian_ref = faction_id
        if isinstance(team_ref, str):
            try:
                _tp, _td, team = self._resolve_covered_owner_view(team_ref, cache=_OwnerResolutionCache())
                if isinstance(team, Mapping) and isinstance(team.get("leader_ref"), str):
                    custodian_ref = str(team["leader_ref"])
            except CommandRejectedError:
                pass
        digest = hashlib.sha256(f"{mission_id}\x00{subject_ref}".encode()).hexdigest()[:20]
        custody_ref = f"custody.mission.{digest}"
        records[custody_ref] = {
            "id":custody_ref,"subject_kind":"exact","subject_ref":subject_ref,
            "force_ref":capture_info.get("force_ref") if isinstance(capture_info, Mapping) else None,
            "count":1,"custodian_ref":custodian_ref,"place_ref":places[0],
            "status":"captured_pending_placement","captured_at":str(at),"detained_at":None,
            "source_combat_ref":None,"updated_at":str(at),
            "summary":f"{subject_ref} is in {custodian_ref}'s custody after mission {mission_id}; secure detention placement remains unresolved.",
            "visibility":str(operation.get("classification") or "restricted"),
        }
        return [custody_ref, subject_ref, *([str(capture_info.get("force_ref"))] if isinstance(capture_info, Mapping) and isinstance(capture_info.get("force_ref"), str) else [])]

    def _recover_mission_remains(
        self,
        *,
        operation: Mapping[str, Any],
        mission_id: str,
        faction_id: str,
        at: CampaignTime,
        evidence_ref: Optional[str],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[str]:
        subject_ref = operation.get("subject_ref")
        places = [ref for ref in operation.get("place_refs", []) if isinstance(ref, str) and ref.startswith("place.")]
        if not isinstance(subject_ref, str) or not places:
            return []
        if not self._subject_is_dead(subject_ref, record_writes=record_writes):
            return []
        registry = self._staged_registry(
            _BIOLOGICAL_REMAINS_REGISTRY_PATH, schema="biological-remains-registry", record_writes=record_writes,
        )
        records = registry.get("records")
        if not isinstance(records, dict):
            raise CommandRejectedError("biological_remains_registry_invalid")
        existing = next((
            (ref, row) for ref, row in records.items()
            if isinstance(ref, str) and isinstance(row, Mapping)
            and row.get("subject_ref") == subject_ref
            and row.get("status") in ("recovered", "transferred")
        ), None)
        if existing is not None:
            return [existing[0]]
        custodian_ref = faction_id
        for team_ref in operation.get("team_refs", []):
            if not isinstance(team_ref, str):
                continue
            try:
                _tp, _td, team = self._resolve_covered_owner_view(team_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                continue
            if isinstance(team, Mapping) and isinstance(team.get("leader_ref"), str):
                custodian_ref = str(team["leader_ref"])
                break
        digest = hashlib.sha256(f"{subject_ref}\x00biological-remains".encode()).hexdigest()[:20]
        remains_ref = f"remains.{digest}"
        evidence_refs = sorted(set(
            ref for ref in (mission_id, evidence_ref) if isinstance(ref, str) and ref
        ))
        records[remains_ref] = {
            "id": remains_ref,
            "subject_ref": subject_ref,
            "source_mission_ref": mission_id,
            "custodian_ref": custodian_ref,
            "place_ref": places[0],
            "status": "recovered",
            "recovered_at": str(at),
            "evidence_refs": evidence_refs,
            "classification": str(operation.get("classification") or "restricted"),
        }
        return [remains_ref]

    def _clear_task_mission_assignment(
        self,
        operation: Mapping[str, Any],
        *,
        mission_id: str,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[str]:
        touched: list[str] = []
        for team_ref in operation.get("team_refs", []):
            if not isinstance(team_ref, str): continue
            try:
                path, _digest, view = self._resolve_covered_owner_view(team_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                continue
            team = record_writes.get(path)
            if team is None:
                team = copy.deepcopy(dict(view))
            if isinstance(team, dict) and team.get("current_assignment_ref") == mission_id:
                team["current_assignment_ref"] = None; record_writes[path] = team; touched.append(team_ref)
        return touched

    def _eligible_autonomous_bounty_team(self, faction_id: str) -> Optional[str]:
        bounty_rules = self._operational_world_mechanics().get("bounty")
        eligible_orgs = bounty_rules.get("eligible_hunter_organization_refs") if isinstance(bounty_rules, Mapping) else None
        if not isinstance(eligible_orgs, list) or faction_id not in eligible_orgs:
            return None
        try:
            refs = team_refs_for_parent(self.repository, faction_id)
        except ValueError as exc:
            raise CommandRejectedError("membership_routes_invalid") from exc
        candidates: list[str] = []
        for team_ref in refs:
            if not isinstance(team_ref, str):
                continue
            try:
                _path, _digest, team = self._resolve_covered_owner_view(team_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                continue
            if not isinstance(team, Mapping) or team.get("status") != "active":
                continue
            if team.get("parent_institution_ref") != faction_id:
                continue
            lifecycle = team.get("lifecycle")
            if isinstance(lifecycle, Mapping) and lifecycle.get("autonomy_owner_ref") not in (None, faction_id):
                continue
            if team.get("current_assignment_ref") not in (None, ""):
                continue
            candidates.append(team_ref)
        return sorted(candidates)[0] if candidates else None

    def _apply_autonomous_bounty_funding(
        self,
        *,
        operation: Mapping[str, Any],
        faction_id: str,
        at: CampaignTime,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        case_ref = operation.get("subject_ref")
        if not isinstance(case_ref, str) or not case_ref.startswith("case."):
            return {"status": "blocked", "reason": "bounty funding requires a legal case", "refs": [], "affected_paths": []}
        registry = self._staged_registry(_LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes)
        cases = registry.get("cases")
        case = cases.get(case_ref) if isinstance(cases, Mapping) else None
        if not isinstance(case, dict):
            return {"status": "blocked", "reason": "bounty funding case is unavailable", "refs": [], "affected_paths": []}
        if case.get("requester_ref") != faction_id or case.get("bounty", {}).get("status") != "none":
            return {"status": "blocked", "reason": "bounty request is not owned by reviewing authority", "refs": [], "affected_paths": []}
        if not case.get("evidence_refs"):
            return {"status": "blocked", "reason": "bounty request lacks verification evidence", "refs": [], "affected_paths": []}
        if case.get("warrant", {}).get("status") != "active" and case.get("case_kind") != "contractual_bounty_target":
            return {"status": "blocked", "reason": "bounty request lacks warrant or contractual basis", "refs": [], "affected_paths": []}
        try:
            amount = self._autonomous_bounty_amount(case)
            payer_holder_ref = self._funding_holder_for(faction_id)
        except CommandRejectedError as exc:
            return {"status": "blocked", "reason": str(exc), "refs": [], "affected_paths": []}
        inventory = self._staged_inventory_registry(record_writes=record_writes)
        escrow_ref = f"escrow.legal.{case_ref.removeprefix('case.')}"
        try:
            self._move_staged_currency(inventory, payer_holder_ref, escrow_ref, amount, code="legal bounty funds insufficient")
        except CommandRejectedError:
            return {"status": "blocked", "reason": "legal bounty funds insufficient", "refs": [], "affected_paths": []}
        case["bounty"] = {
            "status": "posted", "payer_ref": faction_id, "payer_holder_ref": payer_holder_ref,
            "escrow_holder_ref": escrow_ref, "amount_ryo": amount, "hunter_refs": [],
            "posted_at": str(at), "verified_evidence_refs": [], "settled_at": None,
        }
        case["status"] = "bounty_posted"; case["updated_at"] = str(at)
        return {
            "status": "applied", "refs": [case_ref, escrow_ref, f"bounty_amount:{amount}"],
            "affected_paths": [_LEGAL_CASE_REGISTRY_PATH, INVENTORY_REGISTRY_PATH],
        }

    def _apply_autonomous_bounty_acceptance(
        self,
        *,
        operation: Mapping[str, Any],
        faction_id: str,
        at: CampaignTime,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        case_ref = operation.get("subject_ref")
        if not isinstance(case_ref, str) or not case_ref.startswith("case."):
            return {"status": "blocked", "reason": "bounty acceptance requires a legal case", "refs": [], "affected_paths": []}
        registry = self._staged_registry(_LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes)
        cases = registry.get("cases")
        case = cases.get(case_ref) if isinstance(cases, Mapping) else None
        bounty = case.get("bounty") if isinstance(case, Mapping) else None
        if not isinstance(case, dict) or not isinstance(bounty, dict) or bounty.get("status") not in ("posted", "claimed"):
            return {"status": "blocked", "reason": "no funded bounty is available", "refs": [], "affected_paths": []}
        team_ref = self._eligible_autonomous_bounty_team(faction_id)
        if not isinstance(team_ref, str):
            return {"status": "blocked", "reason": "no eligible hunter team is available", "refs": [], "affected_paths": []}
        hunters = bounty.get("hunter_refs")
        if not isinstance(hunters, list):
            raise CommandRejectedError("legal-case-registry_invalid")
        if team_ref not in hunters:
            hunters.append(team_ref); hunters.sort()
        case["updated_at"] = str(at)
        return {
            "status": "applied", "refs": [case_ref, team_ref],
            "affected_paths": [_LEGAL_CASE_REGISTRY_PATH],
        }

    def _active_institution_members(self, faction_id: str, *, record_writes: Dict[str, Dict[str, Any]]) -> list[str]:
        """Return active exact-team members lawfully attached to one institution/faction."""
        try:
            active = team_refs_for_parent(self.repository, faction_id)
        except ValueError as exc:
            raise CommandRejectedError("membership_routes_invalid") from exc
        members: list[str] = []
        cache = _OwnerResolutionCache()
        for team_ref in active:
            if not isinstance(team_ref, str):
                continue
            try:
                path, _digest, team = self._resolve_covered_owner_view(team_ref, cache=cache)
            except CommandRejectedError:
                continue
            staged = record_writes.get(path)
            if isinstance(staged, Mapping):
                team = staged
            if not isinstance(team, Mapping) or team.get("status") != "active":
                continue
            if team.get("parent_institution_ref") != faction_id:
                continue
            lifecycle = team.get("lifecycle")
            if isinstance(lifecycle, Mapping) and lifecycle.get("autonomy_owner_ref") not in (None, faction_id):
                continue
            members.extend(ref for ref in team.get("member_refs", []) if isinstance(ref, str))
        return sorted(set(members))

    def _apply_autonomous_operation_effect(
        self,
        *,
        operation: Dict[str, Any],
        faction_id: str,
        actor: str,
        at: CampaignTime,
        evidence_event_ref: str,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Apply one specialist consequence using existing conserved authorities.

        The world-operation is a work envelope, never a second owner of cases,
        research, security, markets, cargo, people, or money.  A configured
        effect therefore mutates the domain registry that already owns the fact.
        Missing prerequisites fail closed and are reported to the operation; no
        stock, evidence, subjects, cargo, or legal authority are invented.
        """
        effect = self._autonomous_operation_effect(str(operation.get("operation_kind") or ""))
        if not isinstance(effect, Mapping):
            return {"status": "none", "refs": [], "affected_paths": []}
        kind = str(effect.get("effect_kind") or "")
        subject_ref = operation.get("subject_ref") if isinstance(operation.get("subject_ref"), str) else None
        place_refs = [x for x in operation.get("place_refs", []) if isinstance(x, str) and x]
        route_refs = [x for x in operation.get("route_refs", []) if isinstance(x, str) and x]
        refs: list[str] = []
        affected: list[str] = []

        if kind == "legal_bounty_funding":
            return self._apply_autonomous_bounty_funding(
                operation=operation, faction_id=faction_id, at=at, record_writes=record_writes,
            )

        if kind == "legal_bounty_assignment":
            return self._apply_autonomous_bounty_acceptance(
                operation=operation, faction_id=faction_id, at=at, record_writes=record_writes,
            )

        if kind == "legal_case":
            if not subject_ref:
                return {"status": "blocked", "reason": "legal_case_subject_missing", "refs": [], "affected_paths": []}
            try:
                self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                return {"status": "blocked", "reason": "legal_case_subject_unresolved", "refs": [], "affected_paths": []}
            registry = self._staged_registry(
                _LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes
            )
            cases = registry.get("cases")
            if not isinstance(cases, dict):
                raise CommandRejectedError("legal-case-registry_invalid")
            digest = hashlib.sha256(
                f"{faction_id}\x00{effect.get('case_kind')}\x00{subject_ref}".encode()
            ).hexdigest()[:20]
            case_ref = f"case.autonomy.{digest}"
            case = cases.get(case_ref)
            if case is None:
                case = {
                    "id": case_ref,
                    "case_kind": str(effect.get("case_kind") or "autonomous_case"),
                    "issuer_ref": faction_id,
                    "subject_ref": subject_ref,
                    "requester_ref": operation.get("client_ref") if isinstance(operation.get("client_ref"), str) else faction_id,
                    "status": "open",
                    "opened_at": str(at),
                    "updated_at": str(at),
                    "summary": f"Autonomous {operation.get('operation_kind')} case concerning {subject_ref}.",
                    "visibility": str(operation.get("classification") or "restricted"),
                    "offense_refs": [],
                    "evidence_refs": [evidence_event_ref],
                    "warrant": {"status": "none", "authority_ref": None, "issued_at": None},
                    "bounty": {
                        "status": "none", "payer_ref": None, "payer_holder_ref": None, "escrow_holder_ref": None,
                        "amount_ryo": 0, "hunter_refs": [], "posted_at": None,
                        "verified_evidence_refs": [], "settled_at": None,
                    },
                    "custody_ref": None,
                    "disposition": None,
                }
                cases[case_ref] = case
            elif not isinstance(case, dict):
                raise CommandRejectedError("legal-case-registry_invalid")
            else:
                evidence = case.setdefault("evidence_refs", [])
                if evidence_event_ref not in evidence:
                    evidence.append(evidence_event_ref); evidence.sort()
                case["updated_at"] = str(at)
            if bool(effect.get("issue_warrant")) and case.get("warrant", {}).get("status") == "none":
                case["warrant"] = {"status": "active", "authority_ref": faction_id, "issued_at": str(at)}
                case["status"] = "warranted"
            refs.append(case_ref); affected.append(_LEGAL_CASE_REGISTRY_PATH)
            operation.setdefault("case_refs", [])
            if case_ref not in operation["case_refs"]:
                operation["case_refs"].append(case_ref); operation["case_refs"].sort()
            return {"status": "applied", "refs": refs, "affected_paths": affected}

        if kind == "institutional_assessment":
            refs, blocked = self._institutional_assessment_refs(operation, effect, record_writes=record_writes)
            if blocked:
                return {"status": "blocked", "reason": blocked, "refs": [], "affected_paths": []}
            affected_paths: list[str] = []
            # Mission-office reviews may translate a real unresolved mission
            # backlog into one market-demand signal.  The review completion
            # event is the evidence; no demand moves merely because time passed.
            if str(effect.get("assessment_kind") or "") == "mission":
                signal_map = {
                    "investigate": "investigation", "protect": "protection",
                    "capture": "capture", "deliver": "escort", "escort": "escort",
                }
                signal_ref = None
                for mission_ref in sorted(ref for ref in refs if isinstance(ref, str) and ref.startswith("mission.")):
                    try:
                        mission_record = record_writes.get(mission_owner_path(mission_ref))
                        if mission_record is None:
                            mission_record = self.repository.read_json(mission_owner_path(mission_ref))
                    except (FileNotFoundError, ValueError, CommandRejectedError):
                        continue
                    objectives = mission_record.get("objectives") if isinstance(mission_record, Mapping) else None
                    if not isinstance(objectives, list):
                        continue
                    for objective in objectives:
                        if not isinstance(objective, Mapping) or objective.get("status") not in ("pending", "in_progress"):
                            continue
                        mapped = signal_map.get(str(objective.get("kind") or ""))
                        if mapped:
                            signal_ref = mapped
                            break
                    if signal_ref:
                        break
                if signal_ref:
                    market_registry = self._staged_registry(
                        _MISSION_MARKET_RUNTIME_PATH, schema="mission-market-runtime", record_writes=record_writes
                    )
                    markets = market_registry.get("markets")
                    market = markets.get("market_konoha_missions") if isinstance(markets, Mapping) else None
                    scores = market.get("demand_scores") if isinstance(market, dict) else None
                    if isinstance(scores, dict) and signal_ref in scores:
                        step = int(self._operational_world_mechanics().get("mission_market", {}).get("signal_delta_milli", 0))
                        if step <= 0:
                            raise CommandRejectedError("operational_world_mechanics_invalid")
                        scores[signal_ref] = max(0, min(1000, int(scores[signal_ref]) + step))
                        evidence = market.setdefault("evidence_refs", [])
                        if evidence_event_ref not in evidence:
                            evidence.append(evidence_event_ref); evidence.sort()
                        market["last_updated_at"] = str(at)
                        refs = [*refs, f"market_konoha_missions:demand:{signal_ref}:{scores[signal_ref]}"]
                        affected_paths.append(_MISSION_MARKET_RUNTIME_PATH)
            return {"status": "applied", "refs": sorted(set(refs)), "affected_paths": affected_paths}

        if kind == "information_observation":
            # The operation completion claim is the observation product.  This
            # effect adds no parallel truth ledger; preflight enforces lawful
            # prior knowledge when the template requires it.
            return {"status":"applied","refs":[f"observation:{operation.get('operation_id')}", evidence_event_ref],"affected_paths":[]}

        if kind == "service_contract":
            return self._apply_service_contract_effect(
                operation=operation, faction_id=faction_id, at=at, rule=effect, record_writes=record_writes,
            )

        if kind == "mission_tasking":
            return self._create_task_mission(
                operation=operation, faction_id=faction_id, at=at, rule=effect, record_writes=record_writes,
            )

        if kind == "puppet_readiness":
            stock_ref = effect.get("stock_ref")
            if not isinstance(stock_ref, str) or not stock_ref:
                raise CommandRejectedError("operational_world_mechanics_invalid")
            eligible = set(self._active_institution_members(faction_id, record_writes=record_writes))
            if not eligible:
                return {"status": "blocked", "reason": "puppet readiness requires an active specialist team", "refs": [], "affected_paths": []}
            registry = self._staged_registry(_PUPPET_REGISTRY_PATH, schema="puppet-registry", record_writes=record_writes)
            puppets = registry.get("puppets")
            if not isinstance(puppets, list):
                raise CommandRejectedError("puppet-registry_invalid")
            try:
                systems = self.repository.read_json(_SPECIAL_SYSTEMS_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("special_combat_mechanics_invalid") from exc
            maintenance = systems.get("puppets", {}).get("maintenance") if isinstance(systems, Mapping) else None
            if not isinstance(maintenance, Mapping):
                raise CommandRejectedError("special_combat_mechanics_invalid")
            stock_path, stock, _stock_owner = self._staged_stock_record(stock_ref, record_writes=record_writes)
            stock_key = maintenance.get("field_kit_stock_key")
            if not isinstance(stock_key, str) or not stock_key:
                raise CommandRejectedError("special_combat_mechanics_invalid")
            available_kits = stock.get(stock_key, 0)
            if isinstance(available_kits, bool) or not isinstance(available_kits, int) or available_kits <= 0:
                return {"status": "blocked", "reason": "puppet readiness requires an available reusable field kit", "refs": [], "affected_paths": []}
            repair = maintenance.get("repair_damage_per_review")
            per_specialist = maintenance.get("assets_per_specialist_per_review")
            if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (repair, per_specialist)):
                raise CommandRejectedError("special_combat_mechanics_invalid")
            staffed_workstations = min(len(eligible), available_kits)
            throughput = staffed_workstations * per_specialist
            if throughput <= 0:
                return {"status": "blocked", "reason": "puppet readiness requires an equipped active specialist", "refs": [], "affected_paths": []}
            candidates = []
            for row in puppets:
                if not isinstance(row, dict) or row.get("owner_id") not in eligible:
                    continue
                if maintenance.get("require_available") is True and row.get("available") is not True:
                    continue
                if maintenance.get("require_withdrawn") is True and row.get("deployed") is True:
                    continue
                damage = row.get("current_damage", 0)
                if isinstance(damage, int) and not isinstance(damage, bool) and damage > 0:
                    candidates.append(row)
            candidates.sort(key=lambda row: str(row.get("puppet_id") or ""))
            repaired_refs: list[str] = []
            for row in candidates[:throughput]:
                row["current_damage"] = max(0, int(row.get("current_damage", 0)) - repair)
                pref = row.get("puppet_id")
                if isinstance(pref, str):
                    repaired_refs.append(pref)
            # The field kit is reusable equipment, so readiness requires custody but does not consume it.
            readiness_ref = f"puppet_readiness:{faction_id}:{staffed_workstations}:{throughput}:{len(candidates)}:{len(repaired_refs)}"
            affected = [_PUPPET_REGISTRY_PATH] if repaired_refs else []
            return {"status": "applied", "refs": [readiness_ref, *repaired_refs], "affected_paths": affected}

        if kind == "diplomacy_incident":
            registry = self._staged_registry(_DIPLOMACY_REGISTRY_PATH, schema="diplomacy-registry", record_writes=record_writes)
            incidents = registry.get("incidents")
            if not isinstance(incidents, list):
                raise CommandRejectedError("diplomacy-registry_invalid")
            parties = [faction_id]
            for ref in (operation.get("client_ref"), subject_ref):
                if isinstance(ref, str) and (ref.startswith("faction") or ref.startswith("institution.")):
                    parties.append(ref)
            parties.extend(
                ref for ref in operation.get("opposition_refs", [])
                if isinstance(ref, str) and (ref.startswith("faction") or ref.startswith("institution."))
            )
            parties = sorted(set(parties))
            if len(parties) < 2:
                return {"status": "blocked", "reason": "diplomacy_incident_requires_two_parties", "refs": [], "affected_paths": []}
            digest = hashlib.sha256(f"{operation.get('operation_id')}\x00{evidence_event_ref}".encode()).hexdigest()[:20]
            incident_ref = f"incident.autonomy.{digest}"
            if not any(isinstance(row, Mapping) and row.get("id") == incident_ref for row in incidents):
                incidents.append({
                    "id": incident_ref, "at": str(at), "party_refs": parties,
                    "kind": str(operation.get("operation_kind") or "administrative_review"),
                    "evidence_ref": evidence_event_ref,
                    "summary": f"Recorded {operation.get('operation_kind')} disposition from established institutional authority and evidence.",
                    "visibility": str(operation.get("classification") or "restricted"),
                })
            return {
                "status": "applied", "refs": [incident_ref], "affected_paths": [_DIPLOMACY_REGISTRY_PATH],
                "semantic_event_kind": "diplomacy_record_incident",
                "semantic_event_identity": incident_ref,
                "semantic_host_refs": parties,
                "semantic_material_refs": [incident_ref],
            }

        if kind == "research":
            research_kind = effect.get("research_kind")
            mechanics = self._operational_world_mechanics().get("research_kinds")
            row = mechanics.get(research_kind) if isinstance(mechanics, Mapping) else None
            if not isinstance(research_kind, str) or not isinstance(row, Mapping):
                raise CommandRejectedError("operational_world_mechanics_invalid")
            effective_subject_ref = subject_ref
            custody_ref = None
            if str(operation.get("operation_kind") or "") == "custody_interrogation":
                case_ref = subject_ref
                legal = self._staged_registry(_LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes)
                cases = legal.get("cases")
                case = cases.get(case_ref) if isinstance(cases, Mapping) and isinstance(case_ref, str) else None
                custody_ref = case.get("custody_ref") if isinstance(case, Mapping) else None
                if not isinstance(custody_ref, str) or not custody_ref:
                    return {"status": "blocked", "reason": "custody_interrogation_requires_detained_case", "refs": [], "affected_paths": []}
                try:
                    custody_registry = self.repository.read_json(_CUSTODY_REGISTRY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("custody_registry_invalid") from exc
                custody_records = custody_registry.get("records") if isinstance(custody_registry, Mapping) else None
                custody = custody_records.get(custody_ref) if isinstance(custody_records, Mapping) else None
                if not isinstance(custody, Mapping) or custody.get("status") != "detained":
                    return {"status": "blocked", "reason": "custody_interrogation_requires_active_custody", "refs": [], "affected_paths": []}
                effective_subject_ref = custody.get("subject_ref") or (case.get("subject_ref") if isinstance(case, Mapping) else None)
                if not isinstance(effective_subject_ref, str) or not effective_subject_ref:
                    return {"status": "blocked", "reason": "custody_interrogation_subject_missing", "refs": [], "affected_paths": []}
            if not place_refs:
                return {"status": "blocked", "reason": "research_place_missing", "refs": [], "affected_paths": []}
            stock_ref = effect.get("stock_ref")
            if not isinstance(stock_ref, str) or not stock_ref:
                return {"status": "blocked", "reason": "research_stock_missing", "refs": [], "affected_paths": []}
            registry = self._staged_registry(_RESEARCH_REGISTRY_PATH, schema="research-registry", record_writes=record_writes)
            projects = registry.get("projects")
            if not isinstance(projects, dict):
                raise CommandRejectedError("research-registry_invalid")
            identity = f"{faction_id}\x00{research_kind}\x00{place_refs[0]}\x00{effective_subject_ref or ''}"
            project_ref = f"research.autonomy.{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
            project = projects.get(project_ref)
            required_hours = int(row.get("required_active_hours", 0))
            if required_hours <= 0:
                raise CommandRejectedError("operational_world_mechanics_invalid")
            step = max(1, min(1000, (40 * 1000) // required_hours))
            if project is None:
                stock_path, stock, _stock_owner = self._staged_stock_record(stock_ref, record_writes=record_writes)
                costs = dict(row.get("material_costs") or {})
                stock_slots: list[tuple[Dict[str, Any], str, int]] = []
                for item_ref, quantity in costs.items():
                    try:
                        container, key = self._stock_item_key(stock, str(item_ref))
                    except CommandRejectedError:
                        return {"status": "blocked", "reason": f"research_stock_untracked:{item_ref}", "refs": [], "affected_paths": []}
                    available = container.get(key, 0)
                    if isinstance(available, bool) or not isinstance(available, int) or available < int(quantity):
                        return {"status": "blocked", "reason": f"research_materials_insufficient:{item_ref}", "refs": [], "affected_paths": []}
                    stock_slots.append((container, key, int(quantity)))
                for container, key, quantity in stock_slots:
                    container[key] -= quantity
                project = {
                    "id": project_ref, "institution_ref": faction_id, "lead_ref": actor,
                    "place_ref": place_refs[0], "project_kind": research_kind,
                    "subject_ref": effective_subject_ref, "custody_ref": custody_ref, "stock_ref": stock_ref, "material_costs": costs,
                    "status": "active",
                    "hypothesis": f"Resolve {operation.get('operation_kind')} through persisted evidence and conserved materials.",
                    "opened_at": str(at), "last_advanced_at": str(at),
                    "next_due_at": str(at.add_seconds(7 * 24 * 60 * 60)),
                    "progress_milli": step, "risk_milli": int(row.get("risk_milli", 0)),
                    "result_claim_refs": [], "evidence_refs": [evidence_event_ref],
                    "classification": str(operation.get("classification") or "restricted"),
                    "candidate_kind": None, "candidate_ref": None, "prototype_status": "none",
                    "prototype_next_test_at": None, "prototype_test_refs": [],
                    "successful_test_count": 0, "failed_test_count": 0, "approved_at": None,
                }
                projects[project_ref] = project
                affected.extend([_RESEARCH_REGISTRY_PATH, stock_path])
            elif not isinstance(project, dict):
                raise CommandRejectedError("research-registry_invalid")
            else:
                project.setdefault("candidate_kind", None); project.setdefault("candidate_ref", None)
                project.setdefault("prototype_status", "none"); project.setdefault("prototype_next_test_at", None)
                project.setdefault("prototype_test_refs", []); project.setdefault("successful_test_count", 0)
                project.setdefault("failed_test_count", 0); project.setdefault("approved_at", None)
                evidence = project.setdefault("evidence_refs", [])
                if evidence_event_ref not in evidence:
                    evidence.append(evidence_event_ref); evidence.sort()
                    project["progress_milli"] = min(1000, int(project.get("progress_milli", 0)) + step)
                project["last_advanced_at"] = str(at)
                if project["progress_milli"] >= 1000:
                    project["status"] = "succeeded"; project["next_due_at"] = None
                else:
                    project["status"] = "active"; project["next_due_at"] = str(at.add_seconds(7 * 24 * 60 * 60))
                affected.append(_RESEARCH_REGISTRY_PATH)
            refs.append(project_ref)
            operation.setdefault("project_refs", [])
            if project_ref not in operation["project_refs"]:
                operation["project_refs"].append(project_ref); operation["project_refs"].sort()
            return {"status": "applied", "refs": refs, "affected_paths": affected}

        if kind == "security":
            if not place_refs:
                return {"status": "blocked", "reason": "security_place_missing", "refs": [], "affected_paths": []}
            stock_ref = effect.get("stock_ref")
            if not isinstance(stock_ref, str) or not stock_ref:
                return {"status": "blocked", "reason": "security_stock_missing", "refs": [], "affected_paths": []}
            registry = self._staged_registry(
                _SECURITY_NETWORK_REGISTRY_PATH, schema="security-network-registry", record_writes=record_writes
            )
            sectors = registry.get("sectors")
            if not isinstance(sectors, dict):
                raise CommandRejectedError("security-network-registry_invalid")
            digest = hashlib.sha256(f"{faction_id}\x00{place_refs[0]}".encode()).hexdigest()[:20]
            sector_ref = f"security.autonomy.{digest}"
            sector = sectors.get(sector_ref)
            security_rules = self._operational_world_mechanics().get("security")
            if not isinstance(security_rules, Mapping):
                raise CommandRejectedError("operational_world_mechanics_invalid")
            if sector is None:
                stock_path, stock, _stock_owner = self._staged_stock_record(stock_ref, record_writes=record_writes)
                security_costs = (
                    ("item_barrier_anchor_set", int(security_rules.get("barrier_anchor_sets_per_step", 0))),
                    ("item_sensor_relay_set", int(security_rules.get("sensor_relays_per_step", 0))),
                )
                stock_slots: list[tuple[Dict[str, Any], str, int]] = []
                for item_ref, quantity in security_costs:
                    if quantity <= 0:
                        continue
                    try:
                        container, key = self._stock_item_key(stock, item_ref)
                    except CommandRejectedError:
                        return {"status": "blocked", "reason": f"security_stock_untracked:{item_ref}", "refs": [], "affected_paths": []}
                    available = container.get(key, 0)
                    if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
                        return {"status": "blocked", "reason": f"security_resources_insufficient:{item_ref}", "refs": [], "affected_paths": []}
                    stock_slots.append((container, key, quantity))
                for container, key, quantity in stock_slots:
                    container[key] -= quantity
                sector = {
                    "id": sector_ref, "owner_ref": faction_id, "place_ref": place_refs[0],
                    "route_refs": sorted(set(route_refs)), "authorized_owner_refs": [faction_id],
                    "detection_channels": ["barrier", "chakra"], "stock_ref": stock_ref,
                    "coverage_milli": int(security_rules.get("coverage_step_milli", 250)),
                    "detection_milli": int(security_rules.get("detection_step_milli", 250)),
                    "response_milli": 250, "status": "active",
                    "established_at": str(at), "last_reviewed_at": str(at),
                    "classification": str(operation.get("classification") or "restricted"),
                }
                sectors[sector_ref] = sector
                affected.extend([_SECURITY_NETWORK_REGISTRY_PATH, stock_path])
            elif not isinstance(sector, dict):
                raise CommandRejectedError("security-network-registry_invalid")
            else:
                sector["last_reviewed_at"] = str(at)
                affected.append(_SECURITY_NETWORK_REGISTRY_PATH)
            refs.append(sector_ref)
            operation.setdefault("result_refs", [])
            if sector_ref not in operation["result_refs"]:
                operation["result_refs"].append(sector_ref); operation["result_refs"].sort()
            return {"status": "applied", "refs": refs, "affected_paths": affected}

        if kind == "mission_market":
            registry = self._staged_registry(
                _MISSION_MARKET_RUNTIME_PATH, schema="mission-market-runtime", record_writes=record_writes
            )
            markets = registry.get("markets")
            market_ref = effect.get("market_ref")
            axis = effect.get("market_axis")
            signal_ref = effect.get("market_signal_ref")
            direction = effect.get("market_direction")
            market = markets.get(market_ref) if isinstance(markets, Mapping) else None
            if not isinstance(market, dict) or axis not in ("demand", "competition") or direction not in (-1, 1):
                raise CommandRejectedError("operational_world_mechanics_invalid")
            scores = market.get("demand_scores" if axis == "demand" else "competition_scores")
            if not isinstance(scores, dict) or signal_ref not in scores:
                return {"status": "blocked", "reason": "mission_market_signal_unavailable", "refs": [], "affected_paths": []}
            step = int(self._operational_world_mechanics().get("mission_market", {}).get("signal_delta_milli", 0))
            if step <= 0:
                raise CommandRejectedError("operational_world_mechanics_invalid")
            scores[signal_ref] = max(0, min(1000, int(scores[signal_ref]) + int(direction) * step))
            evidence = market.setdefault("evidence_refs", [])
            if evidence_event_ref not in evidence:
                evidence.append(evidence_event_ref); evidence.sort()
            market["last_updated_at"] = str(at)
            refs.append(f"{market_ref}:{axis}:{signal_ref}:{scores[signal_ref]}")
            affected.append(_MISSION_MARKET_RUNTIME_PATH)
            return {"status": "applied", "refs": refs, "affected_paths": affected}

        if kind == "commerce_shipment":
            return self._launch_autonomous_shipment(
                operation=operation, faction_id=faction_id, at=at, record_writes=record_writes,
            )

        if kind == "commerce_inspection":
            return self._inspect_autonomous_shipments(
                operation=operation, faction_id=faction_id, at=at, evidence_event_ref=evidence_event_ref,
                record_writes=record_writes,
            )

        if kind == "commerce_metric":
            registry = self._staged_registry(_COMMERCE_REGISTRY_PATH, schema="commerce-registry", record_writes=record_writes)
            metrics = registry.get("route_metrics")
            if not isinstance(metrics, dict) or not route_refs:
                return {"status": "blocked", "reason": "commerce_route_missing", "refs": [], "affected_paths": []}
            for route_ref in route_refs:
                try:
                    self._real_route(route_ref)
                except CommandRejectedError:
                    return {"status": "blocked", "reason": "commerce_route_invalid", "refs": [], "affected_paths": []}
                metric = metrics.setdefault(route_ref, {
                    "route_ref": route_ref, "shipment_count": 0, "delivered_count": 0,
                    "seizure_count": 0, "cargo_units": 0, "gross_ryo": 0,
                    "last_activity_at": None,
                })
                if not isinstance(metric, dict):
                    raise CommandRejectedError("commerce-registry_invalid")
                # A generic institutional review is evidence about route activity,
                # not proof that conserved cargo changed hands.  Real shipment and
                # seizure counters remain owned by commerce_resolution.
                metric["last_activity_at"] = str(at)
                refs.append(route_ref)
            affected.append(_COMMERCE_REGISTRY_PATH)
            return {"status": "applied", "refs": refs, "affected_paths": affected}

        raise CommandRejectedError("operational_world_autonomous_effect_invalid")

    def _emit_autonomous_effect_semantic_event(
        self,
        *,
        effect_result: Mapping[str, Any],
        operation: Mapping[str, Any],
        actor: str,
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        evidence_event_ref: str,
    ) -> Optional[str]:
        kind = effect_result.get("semantic_event_kind")
        identity = effect_result.get("semantic_event_identity")
        if not isinstance(kind, str) or not kind or not isinstance(identity, str) or not identity:
            return None
        hosts = effect_result.get("semantic_host_refs", [])
        consequences = effect_result.get("semantic_material_refs", [])
        affected = effect_result.get("affected_paths", [])
        if not isinstance(hosts, list) or not isinstance(consequences, list) or not isinstance(affected, list):
            raise CommandRejectedError("autonomous_effect_semantic_event_invalid")
        event_id = self._append_internal_event(
            world_events, command=command, identity=f"{identity}:domain", kind=kind, at=at,
            host_refs=tuple(ref for ref in hosts if isinstance(ref, str)), actor_refs=(actor,),
            place_refs=tuple(ref for ref in operation.get("place_refs", []) if isinstance(ref, str)),
            causal_refs=(evidence_event_ref,),
            affected_owner_refs=tuple(ref for ref in affected if isinstance(ref, str)),
            material_consequence_refs=tuple(ref for ref in consequences if isinstance(ref, str)),
            classification=str(operation.get("classification") or "restricted"),
            audience_refs=tuple(ref for ref in hosts if isinstance(ref, str)), source_refs=(actor,),
            reducer_ref="shinobi_runtime.commands.living_world_operations.autonomous_effect",
        )
        return event_id

    def _mission_archetype(self, archetype_ref: Optional[str]) -> Optional[Mapping[str, Any]]:
        if not isinstance(archetype_ref, str) or not archetype_ref:
            return None
        try:
            record = self.repository.read_json(_MISSION_ARCHETYPES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("mission_archetype_catalog_invalid") from exc
        rows = record.get("archetypes") if isinstance(record, Mapping) else None
        if not isinstance(rows, list):
            raise CommandRejectedError("mission_archetype_catalog_invalid")
        match = next((row for row in rows if isinstance(row, Mapping) and row.get("id") == archetype_ref), None)
        if not isinstance(match, Mapping):
            raise CommandRejectedError("mission_archetype_unresolved")
        return match

    @staticmethod
    def _stable_program_pick(values: Sequence[str], *identity: object) -> Optional[str]:
        clean = [value for value in values if isinstance(value, str) and value]
        if not clean:
            return None
        return clean[_stable_roll(*identity, modulo=len(clean))]

    def _program_template(
        self, faction_id: str, at: CampaignTime, *, salt: str = "", require_autonomous_effect: bool = False
    ) -> Optional[Mapping[str, Any]]:
        program = self._institutional_program(faction_id)
        templates = program.get("operation_templates") if isinstance(program, Mapping) else None
        if not isinstance(templates, list) or not templates:
            return None
        clean = [row for row in templates if isinstance(row, Mapping) and isinstance(row.get("id"), str)]
        if require_autonomous_effect:
            clean = [
                row for row in clean
                if isinstance(self._autonomous_operation_effect(str(row.get("operation_kind") or "")), Mapping)
            ]
        if not clean:
            return None
        return clean[_stable_roll(faction_id, at, salt, modulo=len(clean))]

    def _operation_spec_from_template(
        self,
        *,
        faction_id: str,
        actor: str,
        at: CampaignTime,
        template: Mapping[str, Any],
        identity_lane: str = "operation",
    ) -> Dict[str, Any]:
        program = self._institutional_program(faction_id)
        if not isinstance(program, Mapping):
            raise CommandRejectedError("institutional_autonomy_program_missing")
        template_id = str(template.get("id") or "")
        if not template_id:
            raise CommandRejectedError("institutional_autonomy_template_invalid")
        pick = lambda field: self._stable_program_pick(
            template.get(field, []) if isinstance(template.get(field), list) else [],
            faction_id, at, template_id, field,
        )
        subject_kind = str(template.get("subject_kind") or "none")
        subject_ref = pick("subject_candidates")
        # Empty static candidate lists mean "route from live lawful work", not
        # "invent a subject". Information analysis may select only a claim
        # already known by the assigned analyst. Legal/custody work remains
        # blocked until a concrete case is explicitly present.
        if subject_ref is None and subject_kind == "information":
            try:
                candidates = InformationStore(self.repository).holder_recent_claim_refs(actor, limit=64)
            except ValueError:
                candidates = []
            if candidates:
                subject_ref = self._stable_program_pick(candidates, faction_id, at, template_id, "known_information")
        if subject_ref is None and subject_kind == "case":
            try:
                legal = self.repository.read_json(_LEGAL_CASE_REGISTRY_PATH)
            except (FileNotFoundError, ValueError):
                legal = {}
            cases = legal.get("cases") if isinstance(legal, Mapping) else None
            operation_kind = str(template.get("operation_kind") or "")
            candidates: list[str] = []
            if isinstance(cases, Mapping):
                for case_ref, case in cases.items():
                    if not isinstance(case_ref, str) or not isinstance(case, Mapping):
                        continue
                    if case.get("status") in ("resolved", "dismissed"):
                        continue
                    bounty = case.get("bounty") if isinstance(case.get("bounty"), Mapping) else {}
                    if operation_kind == "bounty_funding_review":
                        if case.get("requester_ref") == faction_id and bounty.get("status") == "none" and case.get("evidence_refs"):
                            candidates.append(case_ref)
                    elif operation_kind == "bounty_acceptance_review":
                        if bounty.get("status") in ("posted", "claimed") and faction_id in self._operational_world_mechanics().get("bounty", {}).get("eligible_hunter_organization_refs", []):
                            candidates.append(case_ref)
                    elif operation_kind == "custody_interrogation":
                        if isinstance(case.get("custody_ref"), str) and case.get("custody_ref"):
                            candidates.append(case_ref)
                    elif case.get("issuer_ref") == faction_id or case.get("requester_ref") == faction_id:
                        candidates.append(case_ref)
            if candidates:
                subject_ref = self._stable_program_pick(sorted(candidates), faction_id, at, template_id, "lawful_case")
        place_ref = pick("place_candidates")
        route_ref = pick("route_candidates")
        client_ref = pick("client_candidates")
        opposition_ref = pick("opposition_candidates")
        recipients = [x for x in template.get("report_recipient_candidates", []) if isinstance(x, str) and x]
        resources = [x for x in template.get("resource_refs", []) if isinstance(x, str) and x]
        classification = str(template.get("classification") or "restricted")
        if classification not in ("public", "restricted", "secret"):
            raise CommandRejectedError("institutional_autonomy_template_invalid")
        suffix = hashlib.sha256(f"{faction_id}\x00{at}\x00{template_id}\x00{identity_lane}".encode()).hexdigest()[:20]
        operation_prefix = "operation.institution" if identity_lane == "institution" else "operation.autonomy"
        return {
            "schema": "world-operation",
            "operation_id": f"{operation_prefix}.{suffix}",
            "domain": str(program.get("domain") or "institutional"),
            "operation_kind": str(template.get("operation_kind") or template_id),
            "owner_ref": faction_id,
            "authority_ref": actor,
            "opened_at": str(at),
            "next_due_at": str(at.add_seconds(7 * 24 * 60 * 60)),
            "closed_at": None,
            "status": "active",
            "archetype_ref": template.get("mission_archetype_ref") if isinstance(template.get("mission_archetype_ref"), str) else None,
            "subject_kind": subject_kind,
            "subject_ref": subject_ref,
            "client_ref": client_ref,
            "place_refs": [place_ref] if place_ref else [],
            "route_refs": [route_ref] if route_ref else [],
            "opposition_refs": [opposition_ref] if opposition_ref else [],
            "resource_refs": sorted(set(resources)),
            "team_refs": [],
            "participant_refs": [],
            "blocked_reason": None,
            "mission_refs": [],
            "project_refs": [],
            "case_refs": [],
            "evidence_refs": [],
            "claim_refs": [],
            "delivery_refs": [],
            "report_recipient_refs": list(dict.fromkeys(recipients)),
            "progress_milli": 100,
            "success_condition": str(template.get("success_condition") or "Complete the declared operation with persisted evidence."),
            "failure_condition": str(template.get("failure_condition") or "The declared operation becomes impossible or materially incomplete."),
            "classification": classification,
            "result_refs": [],
        }

    @staticmethod
    def _world_operation_path(operation_id: str) -> str:
        component = re.sub(r"[^a-z0-9._-]", "_", operation_id.lower())
        return f"{_OPERATION_ROOT}/{component}.json"

    def _write_world_operation(
        self,
        operation: Mapping[str, Any],
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> str:
        operation_id = operation.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.startswith("operation."):
            raise CommandRejectedError("world_operation_invalid")
        path = self._world_operation_path(operation_id)
        existing = record_writes.get(path)
        if existing is None and self.repository.read_optional_bytes(path) is not None:
            try:
                existing = self.repository.read_json(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("world_operation_invalid") from exc
        if isinstance(existing, Mapping):
            if existing.get("operation_id") != operation_id:
                raise CommandRejectedError("world_operation_conflict")
            record_writes[path] = copy.deepcopy(dict(existing))
        else:
            record_writes[path] = copy.deepcopy(dict(operation))
        return path

    def _operation_queue(self, faction_id: str, *, at: CampaignTime, record_writes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        memory = self._faction_memory(faction_id, at=at, record_writes=record_writes)
        queue = memory.setdefault(
            "work_queue",
            {"pending_refs": [], "active_refs": [], "completed_recent_refs": [], "cursor": 0},
        )
        if not isinstance(queue, dict):
            raise CommandRejectedError("faction_operational_memory_invalid")
        for key in ("pending_refs", "active_refs", "completed_recent_refs"):
            if not isinstance(queue.get(key), list):
                raise CommandRejectedError("faction_operational_memory_invalid")
        cursor = queue.get("cursor")
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise CommandRejectedError("faction_operational_memory_invalid")
        return queue

    def _queue_operation(self, operation_id: str, *, faction_id: str, at: CampaignTime, record_writes: Dict[str, Dict[str, Any]]) -> None:
        queue = self._operation_queue(faction_id, at=at, record_writes=record_writes)
        if operation_id not in queue["active_refs"]:
            queue["active_refs"].append(operation_id)
            queue["active_refs"].sort()

    def _complete_operation(
        self,
        operation_id: str,
        *,
        faction_id: str,
        at: CampaignTime,
        succeeded: bool,
        result_refs: Sequence[str],
        evidence_refs: Sequence[str] = (),
        claim_refs: Sequence[str] = (),
        delivery_refs: Sequence[str] = (),
        record_writes: Dict[str, Dict[str, Any]],
    ) -> None:
        path = self._world_operation_path(operation_id)
        raw = record_writes.get(path)
        if raw is None:
            try:
                raw = self.repository.read_json(path)
            except (FileNotFoundError, ValueError):
                return
        if not isinstance(raw, Mapping) or raw.get("schema") != "world-operation":
            raise CommandRejectedError("world_operation_invalid")
        op = copy.deepcopy(dict(raw))
        op["status"] = "succeeded" if succeeded else "failed"
        op["progress_milli"] = 1000
        op["next_due_at"] = None
        op["closed_at"] = str(at)
        for field, refs in (("result_refs", result_refs), ("evidence_refs", evidence_refs), ("claim_refs", claim_refs), ("delivery_refs", delivery_refs)):
            existing = op.setdefault(field, [])
            for ref in refs:
                if isinstance(ref, str) and ref and ref not in existing:
                    existing.append(ref)
            existing.sort()
        record_writes[path] = op
        queue = self._operation_queue(faction_id, at=at, record_writes=record_writes)
        queue["active_refs"][:] = [ref for ref in queue["active_refs"] if ref != operation_id]
        if operation_id not in queue["completed_recent_refs"]:
            queue["completed_recent_refs"].append(operation_id)
            del queue["completed_recent_refs"][:-64]
        queue["cursor"] = int(queue.get("cursor", 0)) + 1

    def _autonomous_delivery(
        self,
        *,
        claim_id: str,
        sender_ref: str,
        recipient_ref: str,
        at: CampaignTime,
        record_writes: Dict[str, Dict[str, Any]],
        channel: str,
        confidence_milli: int,
    ) -> Optional[str]:
        information = InformationStore(self.repository, record_writes)
        try:
            claim_record = information.claim(claim_id)
            if not isinstance(claim_record, Mapping) or not information.holder_knows(sender_ref, claim_id):
                return None
            claim = InformationClaim(
                claim_id=str(claim_record.get("claim_id")),
                subject_ref=str(claim_record.get("subject_ref")),
                source_ref=str(claim_record.get("source_ref")),
                collected_at=CampaignTime.parse(claim_record.get("collected_at")),
                epistemic_kind=str(claim_record.get("epistemic_kind")),
                confidence_milli=int(claim_record.get("confidence_milli")),
                evidence_refs=tuple(claim_record.get("evidence_refs", [])),
            )
            suffix = hashlib.sha256(f"{claim_id}\x00{sender_ref}\x00{recipient_ref}\x00{at}\x00{channel}".encode()).hexdigest()[:24]
            delivery = deliver_claim(
                claim,
                delivery_id=f"delivery.autonomy.{suffix}",
                sender_ref=sender_ref,
                recipient_ref=recipient_ref,
                channel=channel,
                delivered_at=at,
                channel_confidence_milli=confidence_milli,
            )
            information.add_delivery(dict(delivery.to_record()))
            information.grant(recipient_ref, claim_id)
            return delivery.delivery_id
        except (TypeError, ValueError):
            return None

    def _operation_assignment_pool(self, faction_record: Mapping[str, Any], *, record_writes: Dict[str, Dict[str, Any]]) -> tuple[list[str], list[str]]:
        faction = faction_record.get("faction") if isinstance(faction_record, Mapping) else None
        if not isinstance(faction, Mapping):
            return [], []
        plan = faction.get("plan_state") if isinstance(faction.get("plan_state"), Mapping) else {}
        team_refs: list[str] = []
        people: list[str] = []
        busy = set()
        faction_id = faction.get("id")
        if isinstance(faction_id, str):
            try:
                memory = self._faction_memory(faction_id, at=CampaignTime.parse(faction.get("plan_state", {}).get("last_review_at") or "SE-0061-01-01T00:00:00"), record_writes=record_writes)
                mapping = memory.get("active_mission_team_refs")
                if isinstance(mapping, Mapping):
                    busy.update(ref for ref in mapping.values() if isinstance(ref, str))
            except Exception:
                pass
        team_index = None
        try:
            team_index = self.repository.read_json("state/index/owners/team.json").get("owners", {})
        except (FileNotFoundError, ValueError):
            team_index = {}
        for team_ref in plan.get("autonomous_team_refs", []) if isinstance(plan, Mapping) else []:
            if not isinstance(team_ref, str) or team_ref in busy:
                continue
            path = team_index.get(team_ref) if isinstance(team_index, Mapping) else None
            if not isinstance(path, str):
                continue
            raw = record_writes.get(path)
            if raw is None:
                try: raw = self.repository.read_json(path)
                except (FileNotFoundError, ValueError): continue
            if not isinstance(raw, Mapping) or raw.get("status") != "active":
                continue
            team_refs.append(team_ref)
            people.extend(x for x in raw.get("member_refs", []) if isinstance(x, str))
        if not people:
            for field in ("leadership_ids", "key_member_ids"):
                people.extend(x for x in faction.get(field, []) if isinstance(x, str))
        return list(dict.fromkeys(team_refs)), list(dict.fromkeys(people))

    def _operation_lane_capacity(self, faction_record: Mapping[str, Any], *, record_writes: Dict[str, Dict[str, Any]]) -> tuple[int, list[str], list[str]]:
        teams, people = self._operation_assignment_pool(faction_record, record_writes=record_writes)
        # Capacity is derived from actual available teams and persisted named staff.
        # This bounds work *per review*, never fictional demand or lifetime activity.
        lanes = len(teams)
        if people:
            lanes += max(1, (len(people) + 1) // 2) if not teams else max(0, len(people) // 4)
        return max(1, lanes), teams, people

    def _completion_claim_and_deliveries(
        self,
        *,
        operation: Dict[str, Any],
        faction_id: str,
        actor: str,
        at: CampaignTime,
        event_id: str,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> tuple[str, list[str]]:
        information = InformationStore(self.repository, record_writes)
        digest = hashlib.sha256(f"{operation['operation_id']}\x00completion".encode()).hexdigest()[:20]
        claim_id = f"claim.operation.{digest}"
        subject = operation.get("subject_ref") or operation["operation_id"]
        claim_record = {
            "claim_id": claim_id, "subject_ref": str(subject), "source_ref": actor,
            "collected_at": str(at), "epistemic_kind": "report", "confidence_milli": 850,
            "evidence_refs": [event_id],
        }
        try:
            information.add_claim(claim_record)
            information.grant(actor, claim_id)
        except ValueError as exc:
            raise CommandRejectedError("information_registry_invalid") from exc
        delivery_ids: list[str] = []
        for recipient in operation.get("report_recipient_refs", []):
            if not isinstance(recipient, str) or recipient == actor:
                continue
            did = self._autonomous_delivery(
                claim_id=claim_id, sender_ref=actor, recipient_ref=recipient, at=at,
                record_writes=record_writes, channel=f"{operation.get('domain','institutional')}_operation_result",
                confidence_milli=900,
            )
            if did:
                delivery_ids.append(did)
        return claim_id, delivery_ids

    def _autonomous_effect_preflight(
        self,
        operation: Mapping[str, Any],
        *,
        record_writes: Dict[str, Dict[str, Any]],
    ) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
        """Fail closed before a queued operation claims domain completion."""
        rule = self._autonomous_operation_effect(str(operation.get("operation_kind") or ""))
        if not isinstance(rule, Mapping):
            return None, None
        kind = str(rule.get("effect_kind") or "")
        subject_ref = operation.get("subject_ref") if isinstance(operation.get("subject_ref"), str) else None
        place_refs = [x for x in operation.get("place_refs", []) if isinstance(x, str) and x]
        route_refs = [x for x in operation.get("route_refs", []) if isinstance(x, str) and x]

        if kind == "legal_bounty_funding":
            case_ref = subject_ref
            if not isinstance(case_ref, str) or not case_ref.startswith("case."):
                return rule, "bounty funding requires a legal case"
            registry = self._staged_registry(_LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes)
            cases = registry.get("cases")
            case = cases.get(case_ref) if isinstance(cases, Mapping) else None
            if not isinstance(case, Mapping) or case.get("requester_ref") != operation.get("owner_ref"):
                return rule, "bounty request is not owned by reviewing authority"
            if not case.get("evidence_refs") or case.get("bounty", {}).get("status") != "none":
                return rule, "bounty request is not ready for funding"
            try:
                amount = self._autonomous_bounty_amount(case)
                payer_holder_ref = self._funding_holder_for(str(operation.get("owner_ref")))
                inventory = self._staged_inventory_registry(record_writes=record_writes)
            except CommandRejectedError as exc:
                return rule, str(exc)
            holders = inventory.get("holders")
            payer = holders.get(payer_holder_ref) if isinstance(holders, Mapping) else None
            balance = payer.get("currency.ryo", 0) if isinstance(payer, Mapping) else 0
            if isinstance(balance, bool) or not isinstance(balance, int) or balance < amount:
                return rule, "legal bounty funds insufficient"
            return rule, None

        if kind == "legal_bounty_assignment":
            case_ref = subject_ref
            if not isinstance(case_ref, str) or not case_ref.startswith("case."):
                return rule, "bounty acceptance requires a legal case"
            registry = self._staged_registry(_LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes)
            cases = registry.get("cases")
            case = cases.get(case_ref) if isinstance(cases, Mapping) else None
            bounty = case.get("bounty") if isinstance(case, Mapping) and isinstance(case.get("bounty"), Mapping) else None
            if not isinstance(bounty, Mapping) or bounty.get("status") not in ("posted", "claimed"):
                return rule, "no funded bounty is available"
            if self._eligible_autonomous_bounty_team(str(operation.get("owner_ref"))) is None:
                return rule, "no eligible hunter team is available"
            return rule, None

        if kind == "legal_case":
            if not subject_ref:
                return rule, "legal case requires a concrete subject"
            try:
                self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                return rule, "legal case subject is not materialized"
            return rule, None

        if kind == "institutional_assessment":
            _refs, blocked = self._institutional_assessment_refs(operation, rule, record_writes=record_writes)
            return rule, blocked

        if kind == "information_observation":
            if bool(rule.get("requires_known_subject")) and not self._known_subject_claim_refs(operation, record_writes=record_writes):
                return rule, "information observation requires an assigned participant who already knows a sourced claim about the subject"
            return rule, None

        if kind == "service_contract":
            action = rule.get("service_contract_action")
            client_ref = operation.get("client_ref") if isinstance(operation.get("client_ref"), str) else None
            if action == "offer":
                seller_ref = subject_ref
                buyer_ref = client_ref or str(operation.get("owner_ref") or "")
                if buyer_ref != operation.get("owner_ref") or not isinstance(seller_ref, str) or not seller_ref.startswith("faction."):
                    return rule, "service contract offer requires a concrete provider and the paying faction as client"
                try:
                    self._resolve_covered_owner(seller_ref, cache=_OwnerResolutionCache())
                    amount = self._mission_fee_for_rank(str(rule.get("mission_rank") or "A"))
                    payer_holder = self._funding_holder_for(str(buyer_ref))
                    inventory = self._staged_inventory_registry(record_writes=record_writes)
                except CommandRejectedError:
                    return rule, "service contract funding or provider is unavailable"
                payer = inventory.get("holders", {}).get(payer_holder) if isinstance(inventory.get("holders"), Mapping) else None
                balance = payer.get("currency.ryo", 0) if isinstance(payer, Mapping) else 0
                if isinstance(balance, bool) or not isinstance(balance, int) or balance < amount:
                    return rule, "service contract client funds are insufficient"
                return rule, None
            if action == "accept":
                if not isinstance(client_ref, str) or self._find_service_contract(
                    seller_ref=str(operation.get("owner_ref") or ""), buyer_ref=client_ref, statuses=("offered",), record_writes=record_writes,
                ) is None:
                    return rule, "no funded service contract is available to accept"
                return rule, None
            raise CommandRejectedError("autonomous_effect_rule_invalid")

        if kind == "mission_tasking":
            if not subject_ref:
                return rule, "mission tasking requires a concrete subject"
            try:
                self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                return rule, "mission tasking subject is unresolved"
            if bool(rule.get("requires_known_subject")) and not self._known_subject_claim_refs(operation, record_writes=record_writes):
                return rule, "mission tasking requires sourced participant knowledge about the subject"
            if bool(rule.get("requires_subject_dead")) and not self._subject_is_dead(subject_ref, record_writes=record_writes):
                return rule, "recovery task requires the subject to be established dead"
            if bool(rule.get("requires_existing_contract")):
                client_ref = operation.get("client_ref") if isinstance(operation.get("client_ref"), str) else None
                if self._find_service_contract(
                    seller_ref=str(operation.get("owner_ref") or ""), buyer_ref=client_ref,
                    statuses=("accepted","in_progress"), record_writes=record_writes,
                ) is None:
                    return rule, "mission tasking requires an accepted funded service contract"
            team_ref, _team, _path = self._operation_team_for_tasking(
                operation, str(operation.get("owner_ref") or ""), record_writes=record_writes,
            )
            if not isinstance(team_ref, str):
                return rule, "mission tasking requires an available eligible team"
            return rule, None

        if kind == "puppet_readiness":
            eligible = self._active_institution_members(str(operation.get("owner_ref") or ""), record_writes=record_writes)
            if not eligible:
                return rule, "puppet readiness requires an active specialist team"
            stock_ref = rule.get("stock_ref")
            if not isinstance(stock_ref, str):
                raise CommandRejectedError("autonomous_effect_rule_invalid")
            try:
                _path, stock, _owner = self._staged_stock_record(stock_ref, record_writes=record_writes)
                systems = self.repository.read_json(_SPECIAL_SYSTEMS_PATH)
            except (CommandRejectedError, FileNotFoundError, ValueError):
                return rule, "puppet readiness resources are unavailable"
            maintenance = systems.get("puppets", {}).get("maintenance") if isinstance(systems, Mapping) else None
            key = maintenance.get("field_kit_stock_key") if isinstance(maintenance, Mapping) else None
            if not isinstance(key, str) or not isinstance(stock.get(key), int) or isinstance(stock.get(key), bool) or stock.get(key, 0) <= 0:
                return rule, "puppet readiness requires an available reusable field kit"
            return rule, None

        if kind == "diplomacy_incident":
            try:
                staged = record_writes.get(_DIPLOMACY_REGISTRY_PATH) or self.repository.read_json(_DIPLOMACY_REGISTRY_PATH)
            except (FileNotFoundError, ValueError):
                return rule, "diplomacy registry is unavailable"
            if not isinstance(staged, Mapping) or not isinstance(staged.get("incidents"), list):
                return rule, "diplomacy registry is unavailable"
            parties = {str(operation.get("owner_ref") or "")}
            for ref in (operation.get("client_ref"), subject_ref):
                if isinstance(ref, str) and (ref.startswith("faction") or ref.startswith("institution.")):
                    parties.add(ref)
            parties.update(
                ref for ref in operation.get("opposition_refs", [])
                if isinstance(ref, str) and (ref.startswith("faction") or ref.startswith("institution."))
            )
            parties.discard("")
            if len(parties) < 2:
                return rule, "diplomacy incident requires two established parties"
            return rule, None

        if kind == "research":
            research_kind = rule.get("research_kind")
            stock_ref = rule.get("stock_ref")
            research_rules = self._operational_world_mechanics().get("research_kinds")
            research_rule = research_rules.get(research_kind) if isinstance(research_rules, Mapping) else None
            if not isinstance(research_kind, str) or not isinstance(stock_ref, str) or not isinstance(research_rule, Mapping):
                raise CommandRejectedError("autonomous_effect_rule_invalid")
            effective_subject_ref = subject_ref
            if str(operation.get("operation_kind") or "") == "custody_interrogation":
                legal = self._staged_registry(_LEGAL_CASE_REGISTRY_PATH, schema="legal-case-registry", record_writes=record_writes)
                cases = legal.get("cases")
                case = cases.get(subject_ref) if isinstance(cases, Mapping) and isinstance(subject_ref, str) else None
                custody_ref = case.get("custody_ref") if isinstance(case, Mapping) else None
                if not isinstance(custody_ref, str) or not custody_ref:
                    return rule, "custody interrogation requires a detained legal case"
                try:
                    custody_registry = self.repository.read_json(_CUSTODY_REGISTRY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("custody_registry_invalid") from exc
                records = custody_registry.get("records") if isinstance(custody_registry, Mapping) else None
                custody = records.get(custody_ref) if isinstance(records, Mapping) else None
                if not isinstance(custody, Mapping) or custody.get("status") != "detained":
                    return rule, "custody interrogation requires active custody"
                effective_subject_ref = custody.get("subject_ref") or (case.get("subject_ref") if isinstance(case, Mapping) else None)
                if not isinstance(effective_subject_ref, str) or not effective_subject_ref:
                    return rule, "custody interrogation subject is unresolved"
            if not place_refs:
                return rule, "research requires a registered place"
            identity = f"{operation.get('owner_ref')}\x00{research_kind}\x00{place_refs[0]}\x00{effective_subject_ref or ''}"
            project_ref = f"research.autonomy.{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
            staged = record_writes.get(_RESEARCH_REGISTRY_PATH)
            if staged is None:
                try:
                    staged = self.repository.read_json(_RESEARCH_REGISTRY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("research-registry_invalid") from exc
            projects = staged.get("projects") if isinstance(staged, Mapping) else None
            if isinstance(projects, Mapping) and project_ref in projects:
                return rule, None
            try:
                _path, stock, _owner = self._staged_stock_record(stock_ref, record_writes=record_writes)
            except CommandRejectedError:
                return rule, "research stock is unavailable"
            for item_ref, quantity in (research_rule.get("material_costs") or {}).items():
                try:
                    container, key = self._stock_item_key(stock, str(item_ref))
                except CommandRejectedError:
                    return rule, f"required research stock is not tracked: {item_ref}"
                available = container.get(key, 0)
                if isinstance(available, bool) or not isinstance(available, int) or available < int(quantity):
                    return rule, f"insufficient research stock: {item_ref}"
            return rule, None

        if kind == "security":
            stock_ref = rule.get("stock_ref")
            if not isinstance(stock_ref, str):
                raise CommandRejectedError("autonomous_effect_rule_invalid")
            if not place_refs:
                return rule, "security work requires a registered place"
            digest = hashlib.sha256(f"{operation.get('owner_ref')}\x00{place_refs[0]}".encode()).hexdigest()[:20]
            sector_ref = f"security.autonomy.{digest}"
            staged = record_writes.get(_SECURITY_NETWORK_REGISTRY_PATH)
            if staged is None:
                try:
                    staged = self.repository.read_json(_SECURITY_NETWORK_REGISTRY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("security-network-registry_invalid") from exc
            sectors = staged.get("sectors") if isinstance(staged, Mapping) else None
            if isinstance(sectors, Mapping) and sector_ref in sectors:
                return rule, None
            try:
                _path, stock, _owner = self._staged_stock_record(stock_ref, record_writes=record_writes)
            except CommandRejectedError:
                return rule, "security stock is unavailable"
            security_rules = self._operational_world_mechanics().get("security")
            if not isinstance(security_rules, Mapping):
                raise CommandRejectedError("operational_world_mechanics_invalid")
            for item_ref, quantity in (
                ("item_barrier_anchor_set", int(security_rules.get("barrier_anchor_sets_per_step", 0))),
                ("item_sensor_relay_set", int(security_rules.get("sensor_relays_per_step", 0))),
            ):
                if quantity <= 0:
                    continue
                try:
                    container, key = self._stock_item_key(stock, item_ref)
                except CommandRejectedError:
                    return rule, f"required security stock is not tracked: {item_ref}"
                available = container.get(key, 0)
                if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
                    return rule, f"insufficient security stock: {item_ref}"
            return rule, None

        if kind == "mission_market":
            market_ref = rule.get("market_ref")
            staged = record_writes.get(_MISSION_MARKET_RUNTIME_PATH)
            if staged is None:
                try:
                    staged = self.repository.read_json(_MISSION_MARKET_RUNTIME_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("mission-market-runtime_invalid") from exc
            markets = staged.get("markets") if isinstance(staged, Mapping) else None
            if not isinstance(market_ref, str) or not isinstance(markets, Mapping) or market_ref not in markets:
                return rule, "mission market is unavailable"
            return rule, None

        if kind == "commerce_shipment":
            profile = self._autonomous_commerce_profile(str(operation.get("operation_kind") or ""))
            if not isinstance(profile, Mapping):
                return rule, "autonomous commerce profile missing"
            source_stock_ref = profile.get("source_stock_ref")
            if not isinstance(source_stock_ref, str) or not source_stock_ref:
                return rule, "autonomous commerce has no conserved cargo source"
            route_sink_refs = profile.get("route_sink_refs")
            if not isinstance(route_sink_refs, Mapping):
                raise CommandRejectedError("operational_world_mechanics_invalid")
            routes = self._autonomous_operation_routes(operation)
            if not any(ref in route_sink_refs for ref in routes):
                return rule, "no lawful stock-connected route for autonomous shipment"
            try:
                _path, stock, owner = self._staged_stock_record(source_stock_ref, record_writes=record_writes)
            except CommandRejectedError:
                return rule, "autonomous commerce stock is unavailable"
            if owner != profile.get("source_owner_ref"):
                return rule, "autonomous commerce source owner mismatch"
            quantity = profile.get("quantity")
            candidates = [x for x in profile.get("item_candidates", []) if isinstance(x, str) and x]
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                raise CommandRejectedError("operational_world_mechanics_invalid")
            has_cargo = False
            for item_ref in candidates:
                try:
                    container, key = self._stock_item_key(stock, item_ref)
                except CommandRejectedError:
                    continue
                value = container.get(key, 0)
                if isinstance(value, int) and not isinstance(value, bool) and value >= quantity:
                    has_cargo = True
                    break
            if not has_cargo:
                procurement_stock_ref = profile.get("procurement_stock_ref")
                procurement_owner_ref = profile.get("procurement_owner_ref")
                if not isinstance(procurement_stock_ref, str) or not procurement_stock_ref or not isinstance(procurement_owner_ref, str) or not procurement_owner_ref:
                    return rule, "no configured conserved cargo is available"
                try:
                    _path, procurement, procurement_owner = self._staged_stock_record(
                        procurement_stock_ref, record_writes=record_writes
                    )
                except CommandRejectedError:
                    return rule, "autonomous procurement stock is unavailable"
                if procurement_owner != procurement_owner_ref:
                    return rule, "autonomous procurement source owner mismatch"
                procurable = False
                for item_ref in candidates:
                    try:
                        container, key = self._stock_item_key(procurement, item_ref)
                    except CommandRejectedError:
                        continue
                    value = container.get(key, 0)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= quantity:
                        procurable = True
                        break
                if not procurable:
                    return rule, "no configured conserved procurement cargo is available"
            return rule, None

        if kind == "commerce_inspection":
            routes = self._autonomous_operation_routes(operation)
            if not routes:
                return rule, "commerce inspection has no route"
            registry = self._staged_registry(_COMMERCE_REGISTRY_PATH, schema="commerce-registry", record_writes=record_writes)
            shipments = registry.get("shipments")
            if not isinstance(shipments, Mapping):
                raise CommandRejectedError("commerce-registry_invalid")
            if not any(
                isinstance(row, Mapping) and row.get("status") == "in_transit" and row.get("route_ref") in routes
                for row in shipments.values()
            ):
                return rule, "no in-transit shipment exists on inspected route"
            return rule, None

        if kind == "commerce_metric":
            if not route_refs and operation.get("subject_kind") == "route" and subject_ref:
                route_refs = [subject_ref]
            if not route_refs:
                return rule, "commerce work requires a registered route"
            for route_ref in route_refs:
                try:
                    self._real_route(route_ref)
                except CommandRejectedError:
                    return rule, f"commerce route is unresolved: {route_ref}"
            return rule, None

        raise CommandRejectedError("autonomous_effect_rule_invalid")

    def _process_institutional_work_queue(
        self,
        *,
        faction_id: str,
        actor: str,
        at: CampaignTime,
        command: CommandEnvelope,
        faction_record: Dict[str, Any],
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[Mapping[str, Any]]:
        shipment_settlements = self._settle_due_autonomous_shipments(
            faction_id=faction_id, at=at, command=command, world_events=world_events, record_writes=record_writes,
        )
        queue = self._operation_queue(faction_id, at=at, record_writes=record_writes)
        lanes, available_teams, available_people = self._operation_lane_capacity(faction_record, record_writes=record_writes)
        # Activate as much queued work as current causal capacity can host.  The
        # pending list itself is not truncated; backlog is a real state.
        active_ids = [x for x in queue["active_refs"] if isinstance(x, str)]
        while queue["pending_refs"] and len(active_ids) < lanes:
            op_id = queue["pending_refs"].pop(0)
            path = self._world_operation_path(op_id)
            try: op = copy.deepcopy(record_writes.get(path) or self.repository.read_json(path))
            except (FileNotFoundError, ValueError): continue
            if not isinstance(op, dict) or op.get("status") != "pending": continue
            team_ref = available_teams[len(active_ids) % len(available_teams)] if available_teams else None
            op["team_refs"] = [team_ref] if team_ref else []
            if team_ref:
                try:
                    team_index = self.repository.read_json("state/index/owners/team.json").get("owners", {})
                    tp = team_index.get(team_ref)
                    tr = record_writes.get(tp) if isinstance(tp, str) else None
                    if tr is None and isinstance(tp, str): tr = self.repository.read_json(tp)
                    participants = tr.get("member_refs", []) if isinstance(tr, Mapping) else []
                except (FileNotFoundError, ValueError): participants = []
            else:
                participants = available_people
            op["participant_refs"] = [x for x in participants if isinstance(x, str)]
            op["status"] = "active"; op["blocked_reason"] = None; op["next_due_at"] = str(at)
            record_writes[path] = op
            active_ids.append(op_id)
            if op_id not in queue["active_refs"]: queue["active_refs"].append(op_id)
        queue["active_refs"].sort()

        results: list[Mapping[str, Any]] = list(shipment_settlements)
        for op_id in list(queue["active_refs"]):
            if len(results) >= lanes: break
            path = self._world_operation_path(op_id)
            try: op = copy.deepcopy(record_writes.get(path) or self.repository.read_json(path))
            except (FileNotFoundError, ValueError): continue
            if not isinstance(op, dict) or op.get("status") != "active": continue
            # A linked mission is the causal owner of field resolution.  The
            # generic institutional queue may keep the operation visible and
            # staffed, but must never complete a capture, escort, recovery,
            # covert action, or other field objective independently of the
            # mission that actually carries its risks and consequences.
            linked_missions = [ref for ref in op.get("mission_refs", []) if isinstance(ref, str) and ref]
            if linked_missions:
                results.append({
                    "operation_ref": op_id,
                    "status": "mission_linked",
                    "progress_milli": int(op.get("progress_milli", 0)),
                    "mission_refs": linked_missions,
                })
                continue
            try:
                due = CampaignTime.parse(op.get("next_due_at")) if op.get("next_due_at") else at
            except (TypeError, ValueError):
                due = at
            if due > at: continue
            participant_count = len([x for x in op.get("participant_refs", []) if isinstance(x, str)])
            team_count = len([x for x in op.get("team_refs", []) if isinstance(x, str)])
            step = min(600, 180 + 45 * participant_count + 120 * team_count)
            before = int(op.get("progress_milli", 0))
            after = min(1000, before + step)
            op["progress_milli"] = after
            op["next_due_at"] = None if after >= 1000 else str(at.add_seconds(7 * 24 * 60 * 60))
            record_writes[path] = op
            if after < 1000:
                results.append({"operation_ref":op_id,"status":"active","progress_milli":after})
                continue
            effect_rule, blocked_reason = self._autonomous_effect_preflight(op, record_writes=record_writes)
            if blocked_reason:
                op["progress_milli"] = min(950, max(before, 900))
                op["blocked_reason"] = blocked_reason
                op["next_due_at"] = str(at.add_seconds(7 * 24 * 60 * 60))
                record_writes[path] = op
                results.append({"operation_ref":op_id,"status":"blocked","progress_milli":op["progress_milli"],"blocked_reason":blocked_reason})
                continue
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{op_id}:complete", kind="institutional_operation_completed", at=at,
                host_refs=(faction_id,op_id), actor_refs=tuple(op.get("participant_refs") or [actor]),
                affected_owner_refs=(path,), material_consequence_refs=(op_id,), classification=str(op.get("classification") or "restricted"),
                audience_refs=tuple(op.get("report_recipient_refs", [])), source_refs=(actor,),
            )
            claim_id, deliveries = self._completion_claim_and_deliveries(
                operation=op, faction_id=faction_id, actor=actor, at=at, event_id=event_id, record_writes=record_writes,
            )
            effect_result = self._apply_autonomous_operation_effect(
                operation=op, faction_id=faction_id, actor=actor, at=at,
                evidence_event_ref=event_id, record_writes=record_writes,
            )
            domain_event_id = self._emit_autonomous_effect_semantic_event(
                effect_result=effect_result, operation=op, actor=actor, at=at, command=command,
                world_events=world_events, evidence_event_ref=event_id,
            )
            effect_status = str(effect_result.get("status") or "none")
            effect_refs = [ref for ref in effect_result.get("refs", []) if isinstance(ref, str)]
            if isinstance(domain_event_id, str):
                effect_refs.append(domain_event_id)
            affected_paths = [ref for ref in effect_result.get("affected_paths", []) if isinstance(ref, str)]
            if effect_status == "deferred":
                op["progress_milli"] = 1000
                op["next_due_at"] = None
                op["blocked_reason"] = None
                record_writes[path] = op
                for event in world_events.get("events", []):
                    if isinstance(event, dict) and event.get("id") == event_id:
                        event["kind"] = "institutional_operation_tasked"
                        consequences = event.setdefault("material_consequence_refs", [])
                        for ref in effect_refs:
                            if ref not in consequences: consequences.append(ref)
                        consequences.sort()
                        owners = event.setdefault("affected_owner_refs", [])
                        for ref in affected_paths:
                            if ref not in owners: owners.append(ref)
                        owners.sort()
                        break
                results.append({
                    "operation_ref":op_id,"status":"mission_linked","progress_milli":1000,
                    "event_id":event_id,"claim_id":claim_id,"delivery_refs":deliveries,
                    "domain_effect_refs":effect_refs,"mission_refs":list(op.get("mission_refs", [])),
                })
                continue
            if effect_status == "blocked":
                # The generic work happened, but its specialized objective could not
                # lawfully settle. Preserve the evidence and fail the operation rather
                # than manufacturing the missing resource/capability.
                op["blocked_reason"] = str(effect_result.get("reason") or "domain effect blocked")
            record_writes[path] = op
            # Enrich the already-staged completion event with the exact domain owners.
            for event in world_events.get("events", []):
                if isinstance(event, dict) and event.get("id") == event_id:
                    owners = event.setdefault("affected_owner_refs", [])
                    for ref in affected_paths:
                        if ref not in owners:
                            owners.append(ref)
                    owners.sort()
                    consequences = event.setdefault("material_consequence_refs", [])
                    for ref in effect_refs:
                        if ref not in consequences:
                            consequences.append(ref)
                    consequences.sort()
                    break
            succeeded = effect_status != "blocked"
            relationship_refs = self._update_faction_relationship_evidence(
                faction_id=faction_id, operation=op, faction_record=faction_record, record_writes=record_writes,
            )
            if relationship_refs:
                for event in world_events.get("events", []):
                    if isinstance(event, dict) and event.get("id") == event_id:
                        consequences = event.setdefault("material_consequence_refs", [])
                        for ref in relationship_refs:
                            marker = f"relationship_activity:{ref}"
                            if marker not in consequences:
                                consequences.append(marker)
                        consequences.sort()
                        break
            self._complete_operation(
                op_id, faction_id=faction_id, at=at, succeeded=succeeded,
                result_refs=[event_id, claim_id, *deliveries, *effect_refs], evidence_refs=[event_id], claim_refs=[claim_id], delivery_refs=deliveries,
                record_writes=record_writes,
            )
            results.append({
                "operation_ref": op_id, "status": "succeeded" if succeeded else "failed",
                "progress_milli": 1000, "event_id": event_id, "claim_id": claim_id,
                "delivery_refs": deliveries, "domain_effect_refs": effect_refs,
                "blocked_reason": None if succeeded else op.get("blocked_reason"),
            })
        return results

    def _update_faction_relationship_evidence(
        self,
        *,
        faction_id: str,
        operation: Mapping[str, Any],
        faction_record: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> list[str]:
        """Increase salience of already-established inter-faction relationships.

        Routine operational contact is evidence that an existing relationship is
        active; it is not authority to invent a new relationship or change its
        stance. Political stance changes remain explicit diplomacy/governance work.
        """
        own = faction_record.get("faction") if isinstance(faction_record.get("faction"), dict) else None
        if not isinstance(own, dict):
            return []
        counterparties: list[str] = []
        for key in ("client_ref",):
            ref = operation.get(key)
            if isinstance(ref, str) and ref.startswith("faction.") and ref != faction_id:
                counterparties.append(ref)
        for key in ("opposition_refs",):
            values = operation.get(key)
            if isinstance(values, list):
                counterparties.extend(ref for ref in values if isinstance(ref, str) and ref.startswith("faction.") and ref != faction_id)
        touched: list[str] = []

        def bump(container: Dict[str, Any], target: str) -> bool:
            rows = container.get("relationships")
            if not isinstance(rows, list):
                return False
            for row in rows:
                if isinstance(row, dict) and row.get("target_id") == target:
                    value = row.get("intensity")
                    if isinstance(value, int) and not isinstance(value, bool):
                        row["intensity"] = min(100, value + 1)
                        return True
            return False

        for target in sorted(set(counterparties)):
            if bump(own, target):
                touched.append(faction_id)
            try:
                path, _digest, _view = self._resolve_covered_owner_view(target, cache=_OwnerResolutionCache())
            except CommandRejectedError:
                continue
            other = record_writes.get(path)
            if other is None:
                try:
                    loaded = self.repository.read_json(path)
                except (FileNotFoundError, ValueError):
                    continue
                if not isinstance(loaded, Mapping):
                    continue
                other = copy.deepcopy(dict(loaded))
            other_faction = other.get("faction") if isinstance(other, dict) else None
            if isinstance(other_faction, dict) and bump(other_faction, faction_id):
                record_writes[path] = other
                touched.append(target)
        return sorted(set(touched))

    def _operation_brief(self, operation: Mapping[str, Any], *, mission_id: str, objective_kind: str, at: CampaignTime) -> Optional[MissionBrief]:
        places = [ref for ref in operation.get("place_refs", []) if isinstance(ref, str)]
        if not places:
            return None
        subject_kind = str(operation.get("subject_kind") or "information")
        if subject_kind == "none":
            subject_kind = "information"
        subject_ref = operation.get("subject_ref") if isinstance(operation.get("subject_ref"), str) else None
        route_id = next((ref for ref in operation.get("route_refs", []) if isinstance(ref, str)), None)
        opposition = [ref for ref in operation.get("opposition_refs", []) if isinstance(ref, str)]
        threat = (
            "Declared opposing actor(s): " + ", ".join(opposition) + ". Their current location, intent, and strength remain unknown unless separately evidenced."
            if opposition else
            "No specific opposing actor is established by this assignment; verify threats from evidence rather than assumption."
        )
        archetype = self._mission_archetype(operation.get("archetype_ref"))
        archetype_constraints: tuple[str, ...] = ()
        if isinstance(archetype, Mapping):
            description = archetype.get("description")
            region = archetype.get("region")
            assignment_scope = archetype.get("assignment_scope")
            if not all(isinstance(value, str) and value for value in (description, region, assignment_scope)):
                raise CommandRejectedError("mission_archetype_catalog_invalid")
            archetype_constraints = (
                f"Archetype tasking: {description}",
                f"Intended assignment region: {region}; this is scope, not proof of the subject's current position.",
                f"Assignment scope: {assignment_scope}.",
            )
        return MissionBrief(
            briefing_id=f"briefing.{mission_id.removeprefix('mission.')}",
            objective_kind=objective_kind,
            subject_kind=subject_kind,
            subject_ref=subject_ref,
            subject_label=subject_ref or str(operation.get("operation_kind") or "operational objective"),
            report_place_ref=places[0],
            origin_place_ref=places[0],
            destination_place_ref=None,
            route_id=route_id,
            threat_summary=threat,
            threat_source_ref=opposition[0] if opposition else None,
            intelligence_constraints=(
                "The selected operating area is not proof of the subject's current location.",
                "Treat only persisted claims, observations, and evidence as known facts.",
                *archetype_constraints,
            ),
            report_at=at,
            depart_by=None,
            completion_condition=str(operation.get("success_condition") or "Complete the declared mission objective."),
        )

    def _enrich_autonomous_mission(
        self,
        *,
        result: Mapping[str, Any],
        faction_id: str,
        actor: str,
        at: CampaignTime,
        template: Mapping[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        mission_id = result.get("mission_id")
        if not isinstance(mission_id, str) or result.get("state") != "active":
            return result
        spec = self._operation_spec_from_template(faction_id=faction_id, actor=actor, at=at, template=template, identity_lane="mission")
        spec["mission_refs"] = [mission_id]
        operation_id = spec["operation_id"]
        self._write_world_operation(spec, record_writes=record_writes)
        self._queue_operation(operation_id, faction_id=faction_id, at=at, record_writes=record_writes)
        path = mission_owner_path(mission_id)
        raw = record_writes.get(path)
        if not isinstance(raw, Mapping):
            try:
                raw = self.repository.read_json(path)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("autonomous_mission_invalid") from exc
        try:
            owner = MissionOwner.from_record(raw)
            brief = owner.briefing or self._operation_brief(spec, mission_id=mission_id, objective_kind=owner.mission.objectives[0].kind, at=at)
            owner = replace(owner, operation_ref=operation_id, briefing=brief)
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("autonomous_mission_invalid") from exc
        record_writes[path] = dict(owner.to_record())
        return {**dict(result), "operation_ref": operation_id, "operation_kind": spec["operation_kind"], "subject_ref": spec["subject_ref"], "place_refs": list(spec["place_refs"]), "route_refs": list(spec["route_refs"]), "opposition_refs": list(spec["opposition_refs"]), "report_recipient_refs": list(spec["report_recipient_refs"])}

    def _concrete_information_report(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Optional[Mapping[str, Any]]:
        """Deliver already-known information; never manufacture intelligence."""
        faction_id = decision.payload.get("faction_id") if hasattr(decision, "payload") else None
        if not isinstance(faction_id, str):
            return None
        template = self._program_template(faction_id, at, salt="report")
        if not isinstance(template, Mapping):
            return None
        actor = str(decision.actor_ref)
        information = InformationStore(self.repository, record_writes)
        subject_candidates = {ref for ref in template.get("subject_candidates", []) if isinstance(ref, str) and ref}
        try:
            if subject_candidates:
                known_refs = sorted({
                    claim_ref
                    for subject_ref in subject_candidates
                    for claim_ref in information.holder_subject_claim_refs(actor, subject_ref, limit=64)
                })
            else:
                known_refs = information.holder_recent_claim_refs(actor, limit=64)
        except ValueError as exc:
            raise CommandRejectedError("information_registry_invalid") from exc

        eligible: list[str] = []
        claim_records: Dict[str, Mapping[str, Any]] = {}
        for claim_ref in known_refs:
            try:
                claim = information.claim(claim_ref)
            except ValueError as exc:
                raise CommandRejectedError("information_registry_invalid") from exc
            if not isinstance(claim, Mapping):
                continue
            claim_records[claim_ref] = claim
            if subject_candidates and claim.get("subject_ref") not in subject_candidates:
                continue
            eligible.append(claim_ref)
        if not eligible and subject_candidates:
            return None
        if not eligible:
            eligible = sorted(claim_records)
        if not eligible:
            return None

        claim_id = self._stable_program_pick(sorted(set(eligible)), faction_id, at, "information_report")
        if not isinstance(claim_id, str):
            return None
        claim = claim_records[claim_id]
        subject_ref = str(claim.get("subject_ref") or claim_id)
        spec = self._operation_spec_from_template(
            faction_id=faction_id, actor=actor, at=at, template=template, identity_lane="report"
        )
        delivery_ids: list[str] = []
        for recipient in spec["report_recipient_refs"]:
            if recipient == actor:
                continue
            delivery_id = self._autonomous_delivery(
                claim_id=claim_id, sender_ref=actor, recipient_ref=recipient, at=at,
                record_writes=record_writes, channel=f"{spec['domain']}_institutional_report",
                confidence_milli=850,
            )
            if delivery_id:
                delivery_ids.append(delivery_id)
        if not delivery_ids:
            return None

        spec["subject_kind"] = "information"
        spec["subject_ref"] = claim_id
        spec["claim_refs"] = [claim_id]
        spec["delivery_refs"] = delivery_ids
        spec["evidence_refs"] = list(dict.fromkeys([
            ref for ref in claim.get("evidence_refs", []) if isinstance(ref, str)
        ]))
        spec["status"] = "succeeded"
        spec["progress_milli"] = 1000
        spec["next_due_at"] = None
        spec["closed_at"] = str(at)
        spec["result_refs"] = [claim_id, *delivery_ids]
        op_path = self._write_world_operation(spec, record_writes=record_writes)
        event_id = self._append_internal_event(
            world_events, command=command, identity=f"{spec['operation_id']}:{claim_id}:report",
            kind="autonomous_information_report", at=at,
            host_refs=(faction_id, spec["operation_id"]), actor_refs=(actor,),
            affected_owner_refs=tuple([op_path, *sorted(path for path in record_writes if path == "state/reg/information-deliveries.json" or path.startswith("state/reg/information/"))]),
            material_consequence_refs=(claim_id, *delivery_ids),
            classification=spec["classification"],
            audience_refs=tuple(spec["report_recipient_refs"]), knowledge_refs=(claim_id,),
            source_refs=tuple(dict.fromkeys([actor, str(claim.get("source_ref") or actor), *spec["evidence_refs"]])),
        )
        return {
            "kind":"information_report", "event_id":event_id, "claim_id":claim_id,
            "subject_ref":subject_ref, "operation_ref":spec["operation_id"],
            "delivery_refs":delivery_ids, "report_recipient_refs":list(spec["report_recipient_refs"]),
            "basis":"existing_actor_knowledge",
        }


    def _settle_one_world_operation(
        self,
        *,
        faction_id: str,
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        """Progress one queued operation without imposing a fictional workload cap.

        The queue may grow arbitrarily.  Each faction review settles one coherent
        unit of causal work so transaction cost remains bounded.  Pending work is
        activated first; a later due review completes it and routes a sourced
        claim to declared recipients.  Domain-specific systems may additionally
        attach case/project/stock consequences, but the generic operation never
        fabricates those authorities.
        """
        queue = self._operation_queue(faction_id, at=at, record_writes=record_writes)

        # Prefer continuing already-active due work before starting something new.
        candidates: list[tuple[str, Dict[str, Any], str]] = []
        for lane in ("active_refs", "pending_refs"):
            for operation_id in list(queue.get(lane, [])):
                if not isinstance(operation_id, str):
                    continue
                path = self._world_operation_path(operation_id)
                raw = record_writes.get(path)
                if raw is None:
                    try:
                        raw = self.repository.read_json(path)
                    except (FileNotFoundError, ValueError):
                        continue
                if not isinstance(raw, Mapping) or raw.get("schema") != "world-operation":
                    raise CommandRejectedError("world_operation_invalid")
                op = copy.deepcopy(dict(raw))
                due_raw = op.get("next_due_at")
                if isinstance(due_raw, str):
                    try:
                        due = CampaignTime.parse(due_raw)
                    except ValueError as exc:
                        raise CommandRejectedError("world_operation_invalid") from exc
                    if due > at:
                        continue
                candidates.append((operation_id, op, lane))
            if candidates:
                break
        if not candidates:
            return None

        operation_id, op, lane = sorted(candidates, key=lambda row: row[0])[0]
        path = self._world_operation_path(operation_id)
        actor = str(op.get("authority_ref") or faction_id)

        if lane == "pending_refs" or op.get("status") == "pending":
            queue["pending_refs"][:] = [ref for ref in queue["pending_refs"] if ref != operation_id]
            if operation_id not in queue["active_refs"]:
                queue["active_refs"].append(operation_id)
                queue["active_refs"].sort()
            op["status"] = "active"
            op["progress_milli"] = max(250, int(op.get("progress_milli") or 0))
            op["next_due_at"] = str(at.add_seconds(7 * 24 * 60 * 60))
            record_writes[path] = op
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{operation_id}:{at}:activated",
                kind="institutional_operation_activated", at=at,
                host_refs=(faction_id, operation_id), actor_refs=(actor,),
                affected_owner_refs=(path,), material_consequence_refs=(operation_id,),
                classification=str(op.get("classification") or "restricted"),
                audience_refs=(), source_refs=(actor,),
            )
            return {
                "kind": "world_operation_progress", "operation_ref": operation_id,
                "status": "active", "progress_milli": op["progress_milli"],
                "event_id": event_id,
            }

        effect_rule, blocked_reason = self._autonomous_effect_preflight(op, record_writes=record_writes)
        if blocked_reason:
            op["progress_milli"] = min(950, max(int(op.get("progress_milli") or 0), 900))
            op["blocked_reason"] = blocked_reason
            op["next_due_at"] = str(at.add_seconds(7 * 24 * 60 * 60))
            record_writes[path] = op
            return {"kind":"world_operation_progress","operation_ref":operation_id,"status":"blocked","progress_milli":op["progress_milli"],"blocked_reason":blocked_reason}

        subject_ref = op.get("subject_ref") if isinstance(op.get("subject_ref"), str) else operation_id
        suffix = hashlib.sha256(f"{operation_id}\x00{at}\x00completion".encode()).hexdigest()[:20]
        claim_id = f"claim.operation.{suffix}"
        evidence_event_id = self._append_internal_event(
            world_events, command=command, identity=f"{operation_id}:{at}:evidence",
            kind="institutional_operation_evidence_generated", at=at,
            host_refs=(faction_id, operation_id), actor_refs=(actor,), affected_owner_refs=(path,),
            material_consequence_refs=(operation_id,), classification=str(op.get("classification") or "restricted"),
            audience_refs=(), source_refs=(actor,),
        )
        evidence_refs = sorted(set([
            *[ref for ref in op.get("evidence_refs", []) if isinstance(ref, str)],
            evidence_event_id,
        ]))
        claim_record = {
            "claim_id": claim_id,
            "subject_ref": str(subject_ref),
            "source_ref": actor,
            "collected_at": str(at),
            "epistemic_kind": "report",
            "confidence_milli": 750,
            "evidence_refs": evidence_refs,
        }
        information = InformationStore(self.repository, record_writes)
        try:
            information.add_claim(claim_record)
            information.grant(actor, claim_id)
        except ValueError as exc:
            raise CommandRejectedError("information_registry_invalid") from exc

        delivery_ids: list[str] = []
        for recipient in op.get("report_recipient_refs", []):
            if not isinstance(recipient, str) or not recipient or recipient == actor:
                continue
            delivery_id = self._autonomous_delivery(
                claim_id=claim_id, sender_ref=actor, recipient_ref=recipient, at=at,
                record_writes=record_writes, channel=f"{op.get('domain', 'institutional')}_operation_result",
                confidence_milli=900,
            )
            if delivery_id:
                delivery_ids.append(delivery_id)
        information_paths = sorted(path_ref for path_ref in record_writes if path_ref == "state/reg/information-deliveries.json" or path_ref.startswith("state/reg/information/"))
        effect_result = self._apply_autonomous_operation_effect(
            operation=op, faction_id=faction_id, actor=actor, at=at,
            evidence_event_ref=evidence_event_id, record_writes=record_writes,
        )
        domain_event_id = self._emit_autonomous_effect_semantic_event(
            effect_result=effect_result, operation=op, actor=actor, at=at, command=command,
            world_events=world_events, evidence_event_ref=evidence_event_id,
        )
        effect_refs = [ref for ref in effect_result.get("refs", []) if isinstance(ref, str)]
        if isinstance(domain_event_id, str):
            effect_refs.append(domain_event_id)
        if str(effect_result.get("status") or "none") == "deferred":
            op["progress_milli"] = 1000; op["next_due_at"] = None; op["blocked_reason"] = None
            record_writes[path] = op
            tasked_event_id = self._append_internal_event(
                world_events, command=command, identity=f"{operation_id}:{at}:tasked",
                kind="institutional_operation_tasked", at=at, host_refs=(faction_id, operation_id),
                actor_refs=(actor,), affected_owner_refs=tuple([path, *information_paths, *[ref for ref in effect_result.get("affected_paths", []) if isinstance(ref, str)]]),
                material_consequence_refs=tuple([operation_id, claim_id, *delivery_ids, *effect_refs]),
                classification=str(op.get("classification") or "restricted"), audience_refs=tuple(op.get("report_recipient_refs", [])),
                knowledge_refs=(claim_id,), source_refs=(actor,),
            )
            return {
                "kind":"world_operation_progress","operation_ref":operation_id,"status":"mission_linked",
                "progress_milli":1000,"event_id":tasked_event_id,"claim_id":claim_id,
                "delivery_refs":delivery_ids,"domain_effect_refs":effect_refs,"mission_refs":list(op.get("mission_refs", [])),
                "report_recipient_refs":[ref for ref in op.get("report_recipient_refs", []) if isinstance(ref, str)],
                "classification":str(op.get("classification") or "restricted"),
            }
        record_writes[path] = op
        event_id = self._append_internal_event(
            world_events, command=command, identity=f"{operation_id}:{at}:completed",
            kind="institutional_operation_completed", at=at,
            host_refs=(faction_id, operation_id), actor_refs=(actor,),
            affected_owner_refs=tuple([path, *information_paths]),
            material_consequence_refs=(operation_id, claim_id, *delivery_ids, *effect_refs),
            classification=str(op.get("classification") or "restricted"),
            audience_refs=tuple(op.get("report_recipient_refs", [])),
            knowledge_refs=(claim_id,), source_refs=(actor,),
        )
        try:
            operational_mechanics = self.repository.read_json("game/data/mechanics/operational-world.json")
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("operational_world_mechanics_invalid") from exc
        signal_map = operational_mechanics.get("operation_reputation_signals") if isinstance(operational_mechanics, Mapping) else None
        signal_ref = signal_map.get(str(op.get("operation_kind"))) if isinstance(signal_map, Mapping) else None
        reputation_subject = op.get("owner_ref") if isinstance(op.get("owner_ref"), str) else actor
        if isinstance(signal_ref, str):
            for recipient in op.get("report_recipient_refs", []):
                if not isinstance(recipient, str) or recipient in (reputation_subject, actor):
                    continue
                self._apply_autonomous_reputation_signal(
                    subject_ref=reputation_subject, audience_id=recipient, source_event_ref=event_id,
                    source_event_kind="institutional_operation_completed", signal_ref=signal_ref,
                    classification=str(op.get("classification") or "restricted"), at=at, record_writes=record_writes,
                )
        self._complete_operation(
            operation_id, faction_id=faction_id, at=at, succeeded=True,
            result_refs=(evidence_event_id, event_id, claim_id, *delivery_ids, *effect_refs), evidence_refs=(evidence_event_id, event_id,),
            claim_refs=(claim_id,), delivery_refs=tuple(delivery_ids), record_writes=record_writes,
        )
        return {
            "kind": "world_operation_progress", "operation_ref": operation_id,
            "status": "succeeded", "progress_milli": 1000,
            "event_id": event_id, "claim_id": claim_id,
            "delivery_refs": delivery_ids, "domain_effect_refs": effect_refs,
            "report_recipient_refs": [ref for ref in op.get("report_recipient_refs", []) if isinstance(ref, str)],
            "classification": str(op.get("classification") or "restricted"),
        }

    def _review_sovereign_diplomacy(
        self,
        *,
        institution: Mapping[str, Any],
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Optional[Mapping[str, Any]]:
        """Resolve one bounded diplomatic choice for a sovereign institution review.

        Agreements remain exact diplomacy-registry authority and still require
        persisted consent from every party.  This review merely lets established
        sovereign institutions originate proposals or answer proposals addressed
        to them from a static lawful option space.
        """
        institution_id = institution.get("id")
        if not isinstance(institution_id, str):
            return None
        policy = self._sovereign_diplomacy_policy()
        profiles = policy.get("profiles")
        if not isinstance(profiles, Mapping):
            return None
        faction_id = None
        profile = None
        for candidate_ref, candidate in profiles.items():
            institution_refs = candidate.get("institution_refs", []) if isinstance(candidate, Mapping) else []
            if isinstance(candidate_ref, str) and isinstance(institution_refs, list) and institution_id in institution_refs:
                faction_id = candidate_ref
                profile = candidate
                break
        if not isinstance(faction_id, str) or not isinstance(profile, Mapping):
            return None
        leader = institution.get("leader_id") if isinstance(institution.get("leader_id"), str) else faction_id
        registry = self._staged_registry(
            _DIPLOMACY_REGISTRY_PATH, schema="diplomacy-registry", record_writes=record_writes
        )
        agreements = registry.get("agreements")
        incidents = registry.get("incidents")
        if not isinstance(agreements, dict) or not isinstance(incidents, list):
            raise CommandRejectedError("diplomacy-registry_invalid")

        try:
            conflict_registry = self.repository.read_json(_CONFLICT_REGISTRY_PATH)
        except (FileNotFoundError, ValueError):
            conflict_registry = {"records": {}}
        conflicts = conflict_registry.get("records") if isinstance(conflict_registry, Mapping) else None
        conflicts = conflicts if isinstance(conflicts, Mapping) else {}

        def pair_in_active_conflict(left: str, right: str) -> bool:
            for row in conflicts.values():
                if not isinstance(row, Mapping) or row.get("status") != "active":
                    continue
                sides = row.get("side_refs")
                if not isinstance(sides, Sequence) or isinstance(sides, (str, bytes, bytearray)):
                    continue
                side_set = {ref for ref in sides if isinstance(ref, str)}
                if left not in side_set or right not in side_set:
                    continue
                alignments = row.get("support_alignments")
                alignment_map = alignments if isinstance(alignments, Mapping) else {}
                def root(ref: str) -> str:
                    seen = set()
                    current = ref
                    while current not in seen and isinstance(alignment_map.get(current), str):
                        seen.add(current)
                        current = str(alignment_map[current])
                    return current
                if root(left) != root(right):
                    return True
            return False

        def append_incident(kind: str, agreement_ref: str, parties: Sequence[str]) -> str:
            digest = hashlib.sha256(f"{faction_id}\x00{agreement_ref}\x00{kind}\x00{at}".encode()).hexdigest()[:20]
            incident_ref = f"incident.diplomacy.{digest}"
            if not any(isinstance(row, Mapping) and row.get("id") == incident_ref for row in incidents):
                incidents.append({
                    "id": incident_ref,
                    "at": str(at),
                    "party_refs": sorted(set(ref for ref in parties if isinstance(ref, str) and ref)),
                    "kind": kind,
                    "evidence_ref": agreement_ref,
                    "summary": f"{faction_id} recorded {kind.replace('_', ' ')} for {agreement_ref} through its sovereign review.",
                    "visibility": "public",
                })
            return incident_ref

        # First priority is answering an existing proposal addressed to this power.
        for agreement_ref, row in sorted(agreements.items()):
            if not isinstance(row, dict) or row.get("status") != "proposed":
                continue
            parties = row.get("party_refs")
            consents = row.get("consent_refs")
            rejections = row.get("rejection_refs")
            if (
                not isinstance(parties, list) or faction_id not in parties
                or not isinstance(consents, list) or not isinstance(rejections, list)
                or faction_id in consents or faction_id in rejections
            ):
                continue
            counterpart_refs = [ref for ref in parties if isinstance(ref, str) and ref != faction_id]
            active_war = any(pair_in_active_conflict(faction_id, other) for other in counterpart_refs)
            cooperation = int(profile.get("cooperation_milli", 0))
            historical_noise = _stable_roll(faction_id, agreement_ref, "proposal-response", modulo=201) - 100
            score = cooperation + historical_noise
            threshold = int(policy.get("proposal_accept_threshold_milli", 500))
            agreement_type = str(row.get("agreement_type") or "")
            if agreement_type == "alliance":
                threshold = min(1000, threshold + 100)
                for counterpart in counterpart_refs:
                    pair = {faction_id, counterpart}
                    active_types = {
                        str(existing.get("agreement_type"))
                        for existing in agreements.values()
                        if isinstance(existing, Mapping) and existing.get("status") == "active"
                        and isinstance(existing.get("party_refs"), list) and set(existing.get("party_refs")) == pair
                    }
                    if not {"trade", "nonaggression"}.issubset(active_types):
                        score = -1
                        break
            accepted = not active_war and score >= threshold
            if accepted:
                consents.append(faction_id)
                consents.sort()
                if set(consents) == set(parties):
                    row["status"] = "active"
                    row["effective_at"] = str(at)
                kind = "agreement_autonomously_accepted"
            else:
                rejections.append(faction_id)
                rejections.sort()
                row["status"] = "rejected"
                row["ended_at"] = str(at)
                kind = "agreement_autonomously_rejected"
            incident_ref = append_incident(kind, agreement_ref, parties)
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{agreement_ref}:{faction_id}:{kind}:{at}",
                kind="autonomous_diplomacy_response", at=at, host_refs=tuple(parties),
                actor_refs=(leader,), affected_owner_refs=(_DIPLOMACY_REGISTRY_PATH,),
                material_consequence_refs=(agreement_ref, incident_ref, f"status:{row.get('status')}"),
                classification="public", audience_refs=tuple(parties), source_refs=(leader,),
            )
            return {
                "kind": "diplomacy_response", "agreement_ref": agreement_ref,
                "status": row.get("status"), "accepted": accepted, "event_id": event_id,
                "incident_ref": incident_ref,
            }

        interval = int(policy.get("proposal_interval_reviews", 3))
        review_slot = at.year * 12 + at.month
        if interval <= 0 or _stable_roll(faction_id, review_slot, "diplomacy-cadence", modulo=interval) != 0:
            return None
        partners = [ref for ref in profile.get("partner_refs", []) if isinstance(ref, str) and ref != faction_id]
        cycle = [kind for kind in profile.get("proposal_cycle", []) if kind in ("trade", "nonaggression", "alliance")]
        if not partners or not cycle:
            return None

        def active_types_with(partner: str) -> set[str]:
            target = {faction_id, partner}
            return {
                str(row.get("agreement_type"))
                for row in agreements.values()
                if isinstance(row, Mapping) and row.get("status") == "active"
                and isinstance(row.get("party_refs"), list) and set(row.get("party_refs")) == target
            }

        def proposed_types_with(partner: str) -> set[str]:
            target = {faction_id, partner}
            return {
                str(row.get("agreement_type"))
                for row in agreements.values()
                if isinstance(row, Mapping) and row.get("status") == "proposed"
                and isinstance(row.get("party_refs"), list) and set(row.get("party_refs")) == target
            }

        candidates_with_kind: list[tuple[str, str]] = []
        for partner in partners:
            if pair_in_active_conflict(faction_id, partner):
                continue
            active_types = active_types_with(partner)
            proposed_types = proposed_types_with(partner)
            preferred = None
            for kind in cycle:
                if kind in active_types or kind in proposed_types:
                    continue
                if kind == "alliance" and not {"trade", "nonaggression"}.issubset(active_types):
                    continue
                preferred = kind
                break
            if preferred is not None:
                candidates_with_kind.append((partner, preferred))
        if not candidates_with_kind:
            return None
        candidates_with_kind.sort(key=lambda item: (_stable_roll(faction_id, item[0], review_slot, item[1], modulo=1_000_003), item[0], item[1]))
        partner, agreement_type = candidates_with_kind[0]
        digest = hashlib.sha256(f"{faction_id}\x00{partner}\x00{agreement_type}\x00{at}".encode()).hexdigest()[:20]
        agreement_ref = f"agreement.autonomy.{digest}"
        parties = sorted((faction_id, partner))
        provisions = {"tariff_multiplier_milli": 750, "place_refs": [], "route_refs": []} if agreement_type == "trade" else {}
        agreements[agreement_ref] = {
            "id": agreement_ref,
            "agreement_type": agreement_type,
            "party_refs": parties,
            "status": "proposed",
            "proposed_by": faction_id,
            "consent_refs": [faction_id],
            "rejection_refs": [],
            "terms": [f"Autonomous {agreement_type.replace('_', ' ')} proposal from {faction_id} to {partner}."],
            "opened_at": str(at),
            "effective_at": None,
            "ended_at": None,
            "evidence_refs": [],
            "visibility": "public",
            "provisions": provisions,
        }
        incident_ref = append_incident("agreement_autonomously_proposed", agreement_ref, parties)
        event_id = self._append_internal_event(
            world_events, command=command, identity=f"{agreement_ref}:proposed", kind="autonomous_diplomacy_proposed",
            at=at, host_refs=tuple(parties), actor_refs=(leader,), affected_owner_refs=(_DIPLOMACY_REGISTRY_PATH,),
            material_consequence_refs=(agreement_ref, incident_ref), classification="public",
            audience_refs=tuple(parties), source_refs=(leader,),
        )
        return {
            "kind": "diplomacy_proposal", "agreement_ref": agreement_ref,
            "agreement_type": agreement_type, "partner_ref": partner,
            "status": "proposed", "event_id": event_id, "incident_ref": incident_ref,
        }

    def _review_bundled_institution_operation(
        self,
        *,
        institution: Dict[str, Any],
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
    ) -> Mapping[str, Any]:
        """Turn bundled institution/clan priority churn into typed causal work.

        The institution's existing ``active_goal_ids`` is the bounded routing
        projection.  Exact operation truth lives in ``state/operation``.  No
        separate per-institution scheduler host or shadow institution state is
        created.
        """
        institution_id = institution.get("id")
        settlement = institution.get("settlement")
        if not isinstance(institution_id, str) or not isinstance(settlement, dict):
            raise CommandRejectedError("institution_autonomy_invalid")
        self._review_sovereign_diplomacy(
            institution=institution, at=at, command=command, world_events=world_events, record_writes=record_writes,
        )
        delegated_faction = self._institution_delegated_faction(institution_id)
        if delegated_faction is not None:
            return {
                "institution_ref": institution_id,
                "status": "delegated",
                "delegated_faction_ref": delegated_faction,
            }
        goals = settlement.get("active_goal_ids")
        standing = settlement.get("standing_order_ids")
        if not isinstance(goals, list) or not isinstance(standing, list):
            raise CommandRejectedError("institution_autonomy_invalid")
        leader = institution.get("leader_id") if isinstance(institution.get("leader_id"), str) else None

        current_ref = next((ref for ref in goals if isinstance(ref, str) and ref.startswith("operation.institution.")), None)
        if isinstance(current_ref, str):
            path = self._world_operation_path(current_ref)
            raw = record_writes.get(path)
            if raw is None:
                try:
                    raw = self.repository.read_json(path)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("world_operation_invalid") from exc
            if not isinstance(raw, Mapping) or raw.get("schema") != "world-operation":
                raise CommandRejectedError("world_operation_invalid")
            op = copy.deepcopy(dict(raw))
            due_raw = op.get("next_due_at")
            due = None
            if isinstance(due_raw, str):
                try:
                    due = CampaignTime.parse(due_raw)
                except ValueError as exc:
                    raise CommandRejectedError("world_operation_invalid") from exc
            if due is not None and due > at:
                return {"operation_ref": current_ref, "status": op.get("status"), "progress_milli": op.get("progress_milli", 0)}

            _effect_rule, blocked_reason = self._autonomous_effect_preflight(op, record_writes=record_writes)
            if blocked_reason:
                op["status"] = "blocked"
                op["progress_milli"] = min(950, max(int(op.get("progress_milli") or 0), 900))
                op["blocked_reason"] = blocked_reason
                op["next_due_at"] = str(at.add_seconds(30 * 24 * 60 * 60))
                record_writes[path] = op
                return {
                    "operation_ref": current_ref, "status": "blocked",
                    "progress_milli": op["progress_milli"], "blocked_reason": blocked_reason,
                }

            actor = str(op.get("authority_ref") or institution_id)
            suffix = hashlib.sha256(f"{current_ref}\x00{at}\x00institution-complete".encode()).hexdigest()[:20]
            claim_id = f"claim.institution.{suffix}"
            information = InformationStore(self.repository, record_writes)
            try:
                information.add_claim({
                    "claim_id": claim_id, "subject_ref": institution_id, "source_ref": actor,
                    "collected_at": str(at), "epistemic_kind": "report", "confidence_milli": 800,
                    "evidence_refs": [],
                })
                information.grant(actor, claim_id)
            except ValueError as exc:
                raise CommandRejectedError("information_registry_invalid") from exc
            delivery_ids: list[str] = []
            if leader and leader != actor:
                delivery_id = self._autonomous_delivery(
                    claim_id=claim_id, sender_ref=actor, recipient_ref=leader, at=at,
                    record_writes=record_writes, channel=f"{op.get('domain', 'institutional')}_institution_review", confidence_milli=900,
                )
                if delivery_id:
                    delivery_ids.append(delivery_id)
            information_paths = sorted(path_ref for path_ref in record_writes if path_ref == "state/reg/information-deliveries.json" or path_ref.startswith("state/reg/information/"))
            event_id = self._append_internal_event(
                world_events, command=command, identity=f"{current_ref}:{at}:institution-completed",
                kind="institutional_work_completed", at=at, host_refs=(institution_id, current_ref),
                actor_refs=(actor,), affected_owner_refs=tuple([path, *information_paths]),
                material_consequence_refs=(current_ref, claim_id, *delivery_ids),
                classification=str(op.get("classification") or "restricted"),
                audience_refs=tuple([leader] if leader else []), knowledge_refs=(claim_id,), source_refs=(actor,),
            )
            effect_result = self._apply_autonomous_operation_effect(
                operation=op, faction_id=institution_id, actor=actor, at=at,
                evidence_event_ref=event_id, record_writes=record_writes,
            )
            domain_event_id = self._emit_autonomous_effect_semantic_event(
                effect_result=effect_result, operation=op, actor=actor, at=at, command=command,
                world_events=world_events, evidence_event_ref=event_id,
            )
            effect_status = str(effect_result.get("status") or "none")
            effect_refs = [ref for ref in effect_result.get("refs", []) if isinstance(ref, str)]
            if isinstance(domain_event_id, str):
                effect_refs.append(domain_event_id)
            if effect_status == "blocked":
                op["status"] = "blocked"
                op["progress_milli"] = 950
                op["blocked_reason"] = str(effect_result.get("reason") or "institutional assessment blocked")
                op["next_due_at"] = str(at.add_seconds(30 * 24 * 60 * 60))
                record_writes[path] = op
                for event in world_events.get("events", []):
                    if isinstance(event, dict) and event.get("id") == event_id:
                        event["kind"] = "institutional_work_blocked"
                        break
                return {
                    "operation_ref": current_ref, "status": "blocked", "progress_milli": 950,
                    "event_id": event_id, "claim_id": claim_id, "delivery_refs": delivery_ids,
                    "blocked_reason": op["blocked_reason"],
                }
            for event in world_events.get("events", []):
                if isinstance(event, dict) and event.get("id") == event_id:
                    consequences = event.setdefault("material_consequence_refs", [])
                    for ref in effect_refs:
                        if ref not in consequences: consequences.append(ref)
                    consequences.sort()
                    break
            op["status"] = "succeeded"
            op["progress_milli"] = 1000
            op["next_due_at"] = None
            op["closed_at"] = str(at)
            for field, refs in (("result_refs", (event_id, claim_id, *delivery_ids, *effect_refs)), ("claim_refs", (claim_id,)), ("delivery_refs", tuple(delivery_ids)), ("evidence_refs", (event_id,))):
                target = op.setdefault(field, [])
                for ref in refs:
                    if isinstance(ref, str) and ref not in target:
                        target.append(ref)
                target.sort()
            record_writes[path] = op
            goals[:] = [ref for ref in goals if ref != current_ref]
            completed_marker = f"completed:{current_ref}"
            if completed_marker not in standing:
                standing.append(completed_marker)
            return {
                "operation_ref": current_ref, "completed_operation_ref": current_ref,
                "status": "succeeded", "progress_milli": 1000, "event_id": event_id,
                "claim_id": claim_id, "delivery_refs": delivery_ids, "domain_effect_refs": effect_refs,
            }

        profile = self._institution_profile(institution)
        actor = leader or institution_id
        template = self._program_template(institution_id, at, salt="bundled", require_autonomous_effect=True)
        if isinstance(template, Mapping):
            spec = self._operation_spec_from_template(
                faction_id=institution_id, actor=actor, at=at, template=template, identity_lane="institution",
            )
            spec["status"] = "active"
            spec["progress_milli"] = 250
            spec["next_due_at"] = str(at.add_seconds(30 * 24 * 60 * 60))
            if leader and leader not in spec.get("report_recipient_refs", []):
                spec.setdefault("report_recipient_refs", []).append(leader)
                spec["report_recipient_refs"].sort()
        else:
            suffix = hashlib.sha256(f"{institution_id}\x00{at}\x00institution-work".encode()).hexdigest()[:20]
            operation_id = f"operation.institution.{suffix}"
            spec = {
                "schema": "world-operation", "operation_id": operation_id,
                "domain": str(profile.get("domain") or "institutional"),
                "operation_kind": str(profile.get("operation_kind") or "institutional_duty_review"),
                "owner_ref": institution_id, "authority_ref": actor, "opened_at": str(at),
                "next_due_at": str(at.add_seconds(30 * 24 * 60 * 60)), "closed_at": None,
                "status": "active", "archetype_ref": None, "subject_kind": "institution",
                "subject_ref": institution_id, "client_ref": None, "place_refs": [], "route_refs": [],
                "opposition_refs": [], "resource_refs": [], "team_refs": [], "participant_refs": [],
                "blocked_reason": None, "mission_refs": [], "project_refs": [],
                "case_refs": [], "evidence_refs": [], "claim_refs": [], "delivery_refs": [],
                "report_recipient_refs": [leader] if leader else [], "progress_milli": 250,
                "success_condition": str(profile.get("success_condition")),
                "failure_condition": str(profile.get("failure_condition")),
                "classification": str(profile.get("classification") or "restricted"), "result_refs": [],
            }
        operation_id = str(spec["operation_id"])
        path = self._write_world_operation(spec, record_writes=record_writes)
        goals.append(operation_id)
        event_id = self._append_internal_event(
            world_events, command=command, identity=f"{operation_id}:opened", kind="institutional_work_opened",
            at=at, host_refs=(institution_id, operation_id), actor_refs=(actor,), affected_owner_refs=(path,),
            material_consequence_refs=(operation_id, spec["operation_kind"]), classification=spec["classification"],
            audience_refs=(), source_refs=(actor,),
        )
        return {
            "operation_ref": operation_id, "status": "active", "progress_milli": 250,
            "operation_kind": spec["operation_kind"], "domain": spec["domain"], "event_id": event_id,
        }

    def _queue_institutional_operation_review(
        self,
        *,
        decision: Any,
        faction_id: str,
        at: CampaignTime,
        command: CommandEnvelope,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Optional[Mapping[str, Any]]:
        processed = self._process_institutional_work_queue(
            faction_id=faction_id, actor=str(decision.actor_ref), at=at, command=command,
            faction_record=faction_record, world_events=world_events, record_writes=record_writes,
        )
        template = self._program_template(faction_id, at, salt="priority", require_autonomous_effect=True)
        if not isinstance(template, Mapping):
            return None
        actor = str(decision.actor_ref)
        spec = self._operation_spec_from_template(
            faction_id=faction_id, actor=actor, at=at, template=template, identity_lane="priority"
        )
        spec["status"] = "pending"
        spec["progress_milli"] = 0
        op_path = self._write_world_operation(spec, record_writes=record_writes)
        queue = self._operation_queue(faction_id, at=at, record_writes=record_writes)
        operation_id = spec["operation_id"]
        if operation_id not in queue["pending_refs"]:
            queue["pending_refs"].append(operation_id)
            queue["pending_refs"].sort()
        event_id = self._append_internal_event(
            world_events, command=command, identity=f"{operation_id}:queued", kind="institutional_operation_queued", at=at,
            host_refs=(faction_id, operation_id), actor_refs=(actor,), affected_owner_refs=(op_path,),
            material_consequence_refs=(operation_id,), classification=spec["classification"], audience_refs=(), source_refs=(actor,),
        )
        return {
            "kind": "institution_priority_review",
            "event_id": event_id,
            "operation_ref": operation_id,
            "operation_kind": spec["operation_kind"],
            "status": "pending",
            "subject_ref": spec.get("subject_ref"),
            "place_refs": list(spec.get("place_refs", [])),
            "route_refs": list(spec.get("route_refs", [])),
            "processed_operations": processed,
        }

    def _apply_autonomous_decision(
        self,
        *,
        decision: Any,
        at: CampaignTime,
        command: CommandEnvelope,
        scheduler: CausalSchedulerRegistry,
        world_events: Dict[str, Any],
        record_writes: Dict[str, Dict[str, Any]],
        faction_record: Dict[str, Any],
    ) -> Mapping[str, Any]:
        faction_id = decision.payload.get("faction_id") if hasattr(decision, "payload") else None
        if decision.kind == "institution_priority_review" and isinstance(faction_id, str):
            queued = self._queue_institutional_operation_review(
                decision=decision, faction_id=faction_id, at=at, command=command,
                world_events=world_events, record_writes=record_writes, faction_record=faction_record,
            )
            if queued is not None:
                return queued
        if decision.kind == "information_report" and isinstance(faction_id, str):
            concrete = self._concrete_information_report(
                decision=decision, at=at, command=command, world_events=world_events,
                record_writes=record_writes, faction_record=faction_record,
            )
            if concrete is not None:
                return concrete

        template = None
        routed_decision = decision
        if decision.kind == "mission_generate" and isinstance(faction_id, str):
            template = self._program_template(faction_id, at, salt="mission")
            if isinstance(template, Mapping):
                payload = dict(decision.payload)
                objective_kind = template.get("objective_kind")
                if isinstance(objective_kind, str) and objective_kind:
                    payload["mission_objective_cycle"] = [objective_kind]
                routed_decision = AutonomousDecision(
                    kind=decision.kind, actor_ref=decision.actor_ref, reason=decision.reason,
                    payload=payload, material=decision.material,
                )

        result = super()._apply_autonomous_decision(
            decision=routed_decision,
            at=at,
            command=command,
            scheduler=scheduler,
            world_events=world_events,
            record_writes=record_writes,
            faction_record=faction_record,
        )

        if decision.kind == "mission_generate" and isinstance(faction_id, str) and isinstance(template, Mapping):
            if isinstance(result, Mapping) and result.get("kind") != "player_mission_offer" and result.get("skipped") is None:
                result = self._enrich_autonomous_mission(
                    result=result, faction_id=faction_id, actor=str(decision.actor_ref), at=at,
                    template=template, record_writes=record_writes,
                )

        if decision.kind == "mission_advance" and isinstance(faction_id, str) and isinstance(result, Mapping):
            mission_id = result.get("mission_id")
            outcome = result.get("outcome")
            if isinstance(mission_id, str) and isinstance(outcome, str):
                path = mission_owner_path(mission_id)
                raw = record_writes.get(path)
                try:
                    owner = MissionOwner.from_record(raw if isinstance(raw, Mapping) else self.repository.read_json(path))
                except (FileNotFoundError, TypeError, ValueError):
                    owner = None
                operation_id = owner.operation_ref if owner is not None else None
                claim_id = result.get("claim_id") if isinstance(result.get("claim_id"), str) else None
                delivery_ids = []
                if isinstance(operation_id, str):
                    op_path = self._world_operation_path(operation_id)
                    op = record_writes.get(op_path)
                    if op is None:
                        try:
                            op = copy.deepcopy(self.repository.read_json(op_path))
                            record_writes[op_path] = op
                        except (FileNotFoundError, ValueError):
                            op = None
                    if isinstance(op, Mapping) and claim_id:
                        sender = str(decision.actor_ref)
                        for recipient in op.get("report_recipient_refs", []):
                            if not isinstance(recipient, str) or recipient == sender:
                                continue
                            delivery_id = self._autonomous_delivery(
                                claim_id=claim_id, sender_ref=sender, recipient_ref=recipient, at=at,
                                record_writes=record_writes, channel=f"{op.get('domain','institutional')}_mission_result",
                                confidence_milli=900,
                            )
                            if delivery_id:
                                delivery_ids.append(delivery_id)
                    mission_succeeded = outcome == "succeeded"
                    domain_refs: list[str] = []
                    if isinstance(op, Mapping):
                        domain_refs.extend(self._settle_service_contract_for_operation(
                            op, succeeded=mission_succeeded, at=at, record_writes=record_writes,
                        ))
                        objective_kind = owner.mission.objectives[0].kind if owner is not None and owner.mission.objectives else None
                        if mission_succeeded and objective_kind in ("capture", "restrain"):
                            domain_refs.extend(self._capture_mission_subject(
                                operation=op, mission_id=mission_id, faction_id=faction_id, at=at, record_writes=record_writes,
                            ))
                        if mission_succeeded and objective_kind == "recover":
                            domain_refs.extend(self._recover_mission_remains(
                                operation=op, mission_id=mission_id, faction_id=faction_id, at=at,
                                evidence_ref=result.get("event_id") if isinstance(result.get("event_id"), str) else None,
                                record_writes=record_writes,
                            ))
                        domain_refs.extend(self._clear_task_mission_assignment(
                            op, mission_id=mission_id, record_writes=record_writes,
                        ))
                    settlement_event_id = None
                    if domain_refs:
                        settlement_event_id = self._append_internal_event(
                            world_events, command=command, identity=f"{mission_id}:{at}:linked-domain-settlement",
                            kind="mission_linked_domain_settlement", at=at, host_refs=(faction_id, mission_id, operation_id),
                            actor_refs=tuple(owner.mission.participant_refs if owner is not None else ()),
                            affected_owner_refs=tuple(sorted(set([DOMAIN_REGISTRY_PATH, _CUSTODY_REGISTRY_PATH, _BIOLOGICAL_REMAINS_REGISTRY_PATH, INVENTORY_REGISTRY_PATH, *[path for path in record_writes if path.startswith("state/force/") or path.startswith("state/char/") or path.startswith("state/team/")]]))),
                            material_consequence_refs=tuple(sorted(set(domain_refs))),
                            classification=str(op.get("classification") or "restricted") if isinstance(op, Mapping) else "restricted",
                            audience_refs=tuple(op.get("report_recipient_refs", [])) if isinstance(op, Mapping) else (),
                            source_refs=tuple(ref for ref in (result.get("event_id"), claim_id) if isinstance(ref, str)),
                        )
                    self._complete_operation(
                        operation_id, faction_id=faction_id, at=at,
                        succeeded=mission_succeeded,
                        result_refs=[ref for ref in (result.get("event_id"), claim_id, settlement_event_id, *delivery_ids, *domain_refs) if isinstance(ref, str)],
                        evidence_refs=[ref for ref in (result.get("event_id"), settlement_event_id) if isinstance(ref, str)],
                        claim_refs=[claim_id] if claim_id else [], delivery_refs=delivery_ids,
                        record_writes=record_writes,
                    )
                    result = {**dict(result), "operation_ref": operation_id, "delivery_refs": delivery_ids, "linked_domain_refs": sorted(set(domain_refs))}
        return result


__all__ = ["LivingWorldOperationsMixin"]
