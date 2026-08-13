"""Economy, inventory, commerce, and institutional-project command domain."""

from __future__ import annotations

import copy
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Dict, Mapping, Optional, Tuple

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.core import (
    _BuiltPlan, _OwnerResolutionCache, _campaign_datetime, _declared_payload, _exact_payload, _json_bytes, _stable_id,
)
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest


from shinobi_runtime.commands.paths import (
    DOMAIN_REGISTRY_PATH as _DOMAIN_REGISTRY_PATH,
    INVENTORY_REGISTRY_PATH as _INVENTORY_REGISTRY_PATH,
    LOADOUT_INDEX_PATH as _LOADOUT_INDEX_PATH,
    STOCK_ALIAS_PATH as _STOCK_ALIAS_PATH,
    STOCK_OWNER_PATHS as _STOCK_OWNER_PATHS,
    INSTITUTION_PROJECT_MECHANICS_PATH as _INSTITUTION_PROJECT_MECHANICS_PATH,
    ECONOMY_MECHANICS_PATH as _ECONOMY_MECHANICS_PATH,
    ITEM_INDEX_PATH as _ITEM_INDEX_PATH,
    ECONOMY_WORLD_PATH as _ECONOMY_WORLD_PATH,
    ROUTES_PATH as _ROUTES_PATH,
)


class EconomyCommandsMixin:
    def _inventory_holder_authorized(self, actor_ref: str, holder_ref: str) -> bool:
        if actor_ref == holder_ref:
            return True
        if holder_ref.startswith("team."):
            try:
                _path, team = self._exact_team(holder_ref)
            except CommandRejectedError:
                team = None
            if isinstance(team, Mapping) and actor_ref in (team.get("leader_ref"), team.get("deputy_ref")):
                return True
        if holder_ref.startswith("force."):
            try:
                _path, _digest, force = self._resolve_covered_owner_view(holder_ref, cache=_OwnerResolutionCache())
                decision = self._domain_authority(cache=_OwnerResolutionCache()).force_grant(
                    grantor_ref=actor_ref, force_record=force
                )
                if decision.allowed:
                    return True
            except CommandRejectedError:
                pass
        try:
            decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=actor_ref, owner_ref=holder_ref
            )
        except CommandRejectedError:
            return False
        return bool(decision.allowed)


    def _economy_mechanics(self) -> Mapping[str, Any]:
        try:
            record = self.repository.read_json(_ECONOMY_MECHANICS_PATH)
            item_index = self.repository.read_json(_ITEM_INDEX_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("economy_mechanics_invalid") from exc
        if not isinstance(record, Mapping) or record.get("schema") != "economy-mechanics":
            raise CommandRejectedError("economy_mechanics_invalid")

        item_prices = record.get("item_prices")
        catalog_items = item_index.get("items") if isinstance(item_index, Mapping) else None
        if not isinstance(item_prices, Mapping) or not isinstance(catalog_items, Mapping):
            raise CommandRejectedError("economy_mechanics_invalid")
        if set(item_prices) != set(catalog_items):
            raise CommandRejectedError("economy_item_price_coverage_invalid")
        for row in item_prices.values():
            if not isinstance(row, Mapping):
                raise CommandRejectedError("economy_mechanics_invalid")
            base = row.get("base_price_ryo")
            access = row.get("market_access")
            if isinstance(base, bool) or not isinstance(base, int) or base < 0:
                raise CommandRejectedError("economy_mechanics_invalid")
            if access not in ("open", "controlled", "institutional_only", "not_for_sale"):
                raise CommandRejectedError("economy_mechanics_invalid")

        services = record.get("service_prices")
        if not isinstance(services, Mapping) or not services:
            raise CommandRejectedError("economy_mechanics_invalid")
        for row in services.values():
            price = row.get("base_price_ryo") if isinstance(row, Mapping) else None
            if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
                raise CommandRejectedError("economy_mechanics_invalid")

        ranks = record.get("mission_ranks")
        if not isinstance(ranks, Mapping) or set(ranks) != {"D", "C", "B", "A", "S"}:
            raise CommandRejectedError("economy_mechanics_invalid")
        for rank in ("D", "C", "B", "A", "S"):
            row = ranks.get(rank)
            if not isinstance(row, Mapping):
                raise CommandRejectedError("economy_mechanics_invalid")
            required = (
                "client_fee_min_ryo", "client_fee_typical_ryo", "client_fee_max_ryo",
                "participant_bonus_min_ryo", "participant_bonus_typical_ryo", "participant_bonus_max_ryo",
                "operational_allowance_typical_ryo",
            )
            values = [row.get(key) for key in required]
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
                raise CommandRejectedError("economy_mechanics_invalid")
            fee_min, fee_typ, fee_max, bonus_min, bonus_typ, bonus_max, allowance = values
            if not (fee_min <= fee_typ <= fee_max and bonus_min <= bonus_typ <= bonus_max):
                raise CommandRejectedError("economy_mission_price_band_invalid")
            if fee_typ < 4 * bonus_typ + allowance or fee_max < 4 * bonus_max + allowance:
                raise CommandRejectedError("economy_mission_price_coverage_invalid")
        return record


    def _economy_world(self) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            record = copy.deepcopy(self.repository.read_json(_ECONOMY_WORLD_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("economy_state_invalid") from exc
        payload = record.get("payload") if isinstance(record, dict) else None
        economy = payload.get("economies_and_mission_markets") if isinstance(payload, dict) else None
        finance = economy.get("finance") if isinstance(economy, dict) else None
        if not isinstance(finance, dict) or finance.get("currency_ref") != "currency.ryo":
            raise CommandRejectedError("economy_state_invalid")
        return record, finance


    def _funding_holder_for(self, owner_ref: str, *, finance: Optional[Mapping[str, Any]] = None) -> str:
        if finance is None:
            _economy, finance_record = self._economy_world()
            finance = finance_record
        exact = finance.get("funding_exact") if isinstance(finance, Mapping) else None
        prefixes = finance.get("funding_prefixes") if isinstance(finance, Mapping) else None
        if isinstance(exact, Mapping):
            mapped = exact.get(owner_ref)
            if isinstance(mapped, str) and mapped:
                return mapped
        if isinstance(prefixes, Mapping):
            for prefix in sorted((key for key in prefixes if isinstance(key, str)), key=len, reverse=True):
                if owner_ref.startswith(prefix):
                    mapped = prefixes.get(prefix)
                    if isinstance(mapped, str) and mapped:
                        return mapped
        # Exact persons/clients may finance their own mission or project if they
        # already own a real conserved inventory account.
        try:
            inventory = self.repository.read_json(_INVENTORY_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("economy_funding_unresolved") from exc
        holders = inventory.get("holders") if isinstance(inventory, Mapping) else None
        if isinstance(holders, Mapping) and isinstance(holders.get(owner_ref), Mapping):
            return owner_ref
        raise CommandRejectedError("economy_funding_unresolved")


    def _stock_record(self, stock_ref: str) -> Tuple[str, Dict[str, Any], str]:
        try:
            registry = self.repository.read_json(_STOCK_OWNER_PATHS)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_stock_registry_invalid") from exc
        stocks = registry.get("stocks") if isinstance(registry, Mapping) else None
        path = stocks.get(stock_ref) if isinstance(stocks, Mapping) else None
        if not isinstance(path, str) or not path:
            raise CommandRejectedError("inventory_stock_unresolved")
        try:
            record = copy.deepcopy(self.repository.read_json(path))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_stock_unresolved") from exc
        schema = record.get("schema")
        if schema == "stock":
            owner_ref = record.get("owner")
        elif schema == "shinobi-stock":
            owner_ref = record.get("stock_for")
        else:
            raise CommandRejectedError("inventory_stock_invalid")
        if not isinstance(owner_ref, str) or not owner_ref:
            raise CommandRejectedError("inventory_stock_invalid")
        return path, record, owner_ref


    def _stock_item_key(self, stock: Mapping[str, Any], item_ref: str) -> Tuple[Dict[str, Any], str]:
        try:
            aliases_record = self.repository.read_json(_STOCK_ALIAS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_stock_aliases_invalid") from exc
        aliases = aliases_record.get("aliases") if isinstance(aliases_record, Mapping) else None
        candidates = aliases.get(item_ref) if isinstance(aliases, Mapping) else None
        if not isinstance(candidates, list):
            candidates = []
        if stock.get("schema") == "stock":
            items = stock.get("items")
            if not isinstance(items, dict):
                raise CommandRejectedError("inventory_stock_invalid")
            for key in [item_ref, *candidates]:
                if key in items:
                    return items, key
        else:
            if not isinstance(stock, dict):
                raise CommandRejectedError("inventory_stock_invalid")
            for key in [item_ref, *candidates]:
                if key in stock and isinstance(stock.get(key), int) and not isinstance(stock.get(key), bool):
                    return stock, key
        raise CommandRejectedError("inventory_item_not_tracked_by_stock")


    def _loadout_quantities(self, loadout_ref: str, *, trail: Tuple[str, ...] = ()) -> Dict[str, int]:
        if loadout_ref in trail:
            raise CommandRejectedError("inventory_loadout_cycle")
        try:
            index = self.repository.read_json(_LOADOUT_INDEX_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_loadout_index_invalid") from exc
        routes = index.get("loadouts") if isinstance(index, Mapping) else None
        path = routes.get(loadout_ref) if isinstance(routes, Mapping) else None
        if not isinstance(path, str):
            raise CommandRejectedError("inventory_loadout_unresolved")
        try:
            record = self.repository.read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_loadout_unresolved") from exc
        loadout = record.get("loadout") if isinstance(record, Mapping) else None
        if not isinstance(loadout, Mapping) or loadout.get("id") != loadout_ref:
            raise CommandRejectedError("inventory_loadout_invalid")
        quantities: Dict[str, int] = {}
        parent = loadout.get("inherits")
        if isinstance(parent, str) and parent:
            quantities.update(self._loadout_quantities(parent, trail=trail + (loadout_ref,)))
        items = loadout.get("items")
        if not isinstance(items, list):
            raise CommandRejectedError("inventory_loadout_invalid")
        for item in items:
            if not isinstance(item, Mapping):
                raise CommandRejectedError("inventory_loadout_invalid")
            item_ref = item.get("item_id")
            quantity = item.get("quantity")
            if not isinstance(item_ref, str) or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                raise CommandRejectedError("inventory_loadout_invalid")
            quantities[item_ref] = quantity
        return quantities


    def _purchase_contract_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        """Create or transition one purchase agreement without moving goods yet.

        The stock owner is always the seller. Offer establishes exact goods,
        quantity, buyer, and price. Accept requires the exact buyer and current
        funds. `inventory_resolution` with action `purchase` performs the
        separately conserved stock/currency exchange and completes the contract.
        """

        _exact_payload(
            command.payload,
            (
                "action", "contract_ref", "buyer_ref", "stock_ref", "item_ref",
                "quantity", "unit_price_ryo", "summary", "visibility",
            ),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("offer", "accept", "cancel"):
            raise CommandRejectedError("purchase_contract_action_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("purchase_contract_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("purchase_contract_visibility_invalid")

        try:
            registry = copy.deepcopy(self.repository.read_json(_DOMAIN_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_contract_registry_invalid") from exc
        contracts = registry.get("contracts") if isinstance(registry, dict) else None
        if not isinstance(contracts, list):
            raise CommandRejectedError("inventory_contract_registry_invalid")

        contract_ref_raw = command.payload["contract_ref"]
        buyer_raw = command.payload["buyer_ref"]
        stock_raw = command.payload["stock_ref"]
        item_raw = command.payload["item_ref"]
        quantity_raw = command.payload["quantity"]
        unit_price_raw = command.payload["unit_price_ryo"]
        material: list[str] = []

        if action == "offer":
            if contract_ref_raw is not None:
                raise CommandRejectedError("purchase_contract_offer_ref_must_be_null")
            buyer_ref = _stable_id(buyer_raw, "purchase_contract_buyer_invalid")
            stock_ref = _stable_id(stock_raw, "inventory_stock_invalid", prefix="stock.")
            item_ref = _stable_id(item_raw, "inventory_item_invalid")
            if isinstance(quantity_raw, bool) or not isinstance(quantity_raw, int) or quantity_raw <= 0:
                raise CommandRejectedError("inventory_quantity_invalid")
            if isinstance(unit_price_raw, bool) or not isinstance(unit_price_raw, int) or unit_price_raw <= 0:
                raise CommandRejectedError("purchase_contract_price_invalid")
            self._item_market_price(item_ref, unit_price_raw)
            stock_path, stock, seller_ref = self._stock_record(stock_ref)
            decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=seller_ref
            )
            if command.actor_id != seller_ref and not decision.allowed:
                raise CommandRejectedError("purchase_contract_seller_not_authorized")
            container, key = self._stock_item_key(stock, item_ref)
            available = container.get(key)
            if isinstance(available, bool) or not isinstance(available, int) or available < quantity_raw:
                raise CommandRejectedError("inventory_stock_insufficient")
            contract_ref = "contract.purchase." + command.digest[:24]
            if any(isinstance(row, Mapping) and row.get("id") == contract_ref for row in contracts):
                raise CommandRejectedError("purchase_contract_identity_conflict")
            total = quantity_raw * unit_price_raw
            contract = {
                "id": contract_ref,
                "status": "offered",
                "issuer_ref": seller_ref,
                "counterparty_refs": [buyer_ref],
                "scope": "ordinary_stock_purchase",
                "opened_at": str(current_time),
                "expires_at": None,
                "next_due_at": None,
                "result": None,
                "kind": "purchase",
                "buyer_ref": buyer_ref,
                "seller_ref": seller_ref,
                "stock_ref": stock_ref,
                "item_ref": item_ref,
                "quantity": quantity_raw,
                "unit_price_ryo": unit_price_raw,
                "total_ryo": total,
                "accepted_at": None,
                "completed_at": None,
                "cancelled_at": None,
            }
            contracts.append(contract)
            material.append(f"purchase_contract:offered:{contract_ref}:{item_ref}:{quantity_raw}:{total}ryo")
        else:
            if any(value is not None for value in (buyer_raw, stock_raw, item_raw, quantity_raw, unit_price_raw)):
                raise CommandRejectedError("purchase_contract_transition_fields_invalid")
            contract_ref = _stable_id(contract_ref_raw, "inventory_contract_invalid", prefix="contract.purchase.")
            matches = [row for row in contracts if isinstance(row, dict) and row.get("id") == contract_ref]
            if len(matches) != 1:
                raise CommandRejectedError("inventory_contract_unresolved")
            contract = matches[0]
            if contract.get("kind") != "purchase":
                raise CommandRejectedError("inventory_contract_mismatch")
            buyer_ref = contract.get("buyer_ref")
            seller_ref = contract.get("seller_ref")
            if not isinstance(buyer_ref, str) or not isinstance(seller_ref, str):
                raise CommandRejectedError("inventory_contract_invalid")
            if action == "accept":
                if contract.get("status") != "offered":
                    raise CommandRejectedError("purchase_contract_not_offered")
                if command.actor_id != buyer_ref:
                    raise CommandRejectedError("purchase_contract_buyer_mismatch")
                try:
                    inventory = self.repository.read_json(_INVENTORY_REGISTRY_PATH)
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("inventory_registry_invalid") from exc
                holders = inventory.get("holders") if isinstance(inventory, Mapping) else None
                buyer = holders.get(buyer_ref) if isinstance(holders, Mapping) else None
                total = contract.get("total_ryo")
                balance = buyer.get("currency.ryo", 0) if isinstance(buyer, Mapping) else 0
                if (
                    isinstance(total, bool) or not isinstance(total, int) or total <= 0
                    or isinstance(balance, bool) or not isinstance(balance, int) or balance < total
                ):
                    raise CommandRejectedError("inventory_currency_insufficient")
                # Availability is checked again at purchase because an offer does
                # not reserve stock. Accepted contracts therefore cannot create
                # goods if stock is later consumed elsewhere.
                stock_ref = contract.get("stock_ref")
                item_ref = contract.get("item_ref")
                quantity = contract.get("quantity")
                if not isinstance(stock_ref, str) or not isinstance(item_ref, str) or isinstance(quantity, bool) or not isinstance(quantity, int):
                    raise CommandRejectedError("inventory_contract_invalid")
                _stock_path, stock, current_seller = self._stock_record(stock_ref)
                if current_seller != seller_ref:
                    raise CommandRejectedError("inventory_contract_mismatch")
                container, key = self._stock_item_key(stock, item_ref)
                available = container.get(key)
                if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
                    raise CommandRejectedError("inventory_stock_insufficient")
                contract["status"] = "accepted"
                contract["accepted_at"] = str(current_time)
                material.append(f"purchase_contract:accepted:{contract_ref}")
            else:
                if contract.get("status") not in ("offered", "accepted"):
                    raise CommandRejectedError("purchase_contract_not_cancellable")
                seller_decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                    holder_ref=command.actor_id, owner_ref=seller_ref
                )
                if command.actor_id not in (buyer_ref, seller_ref) and not seller_decision.allowed:
                    raise CommandRejectedError("purchase_contract_cancel_not_authorized")
                contract["status"] = "cancelled"
                contract["cancelled_at"] = str(current_time)
                contract["result"] = summary.strip()
                material.append(f"purchase_contract:cancelled:{contract_ref}")

        contracts.sort(key=lambda row: str(row.get("id", "")) if isinstance(row, Mapping) else "")
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="purchase_contract_changed",
            at=current_time,
            actor_refs=(command.actor_id,),
            affected_owner_refs=(_DOMAIN_REGISTRY_PATH,),
            material_consequence_refs=tuple(material),
            classification=visibility,
            audience_refs=tuple(sorted({command.actor_id, buyer_ref, seller_ref})),
            reducer_ref="shinobi_runtime.commands.purchase_contract_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _DOMAIN_REGISTRY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        expected_contract = copy.deepcopy(contract)

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("purchase contract write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_DOMAIN_REGISTRY_PATH)
            staged_contracts = staged.get("contracts", []) if isinstance(staged, Mapping) else []
            found = [row for row in staged_contracts if isinstance(row, Mapping) and row.get("id") == contract_ref]
            if len(found) != 1 or dict(found[0]) != expected_contract:
                raise ValueError("purchase contract after-image mismatch")

        return _BuiltPlan(
            code="purchase_contract_resolution_ready",
            affected_refs=expected_paths,
            writes=writes,
            result={
                "command_type": command.command_type,
                "action": action,
                "contract_ref": contract_ref,
                "status": contract.get("status"),
                "buyer_ref": buyer_ref,
                "seller_ref": seller_ref,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )


    def _service_purchase_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        """Purchase a registered service with conserved personal currency."""

        _declared_payload(command.payload, command.command_type)
        service_ref = _stable_id(command.payload["service_ref"], "service_ref_invalid", prefix="service.")
        seller_ref = _stable_id(command.payload["seller_ref"], "service_seller_invalid")
        if seller_ref == command.actor_id:
            raise CommandRejectedError("service_self_payment_invalid")
        quantity = command.payload["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or quantity > 1000:
            raise CommandRejectedError("service_quantity_invalid")
        unit_price, price_record = self._service_market_price(service_ref, None)
        total = unit_price * quantity
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("service_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("service_visibility_invalid")

        try:
            inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_registry_invalid") from exc
        holders = inventory.get("holders") if isinstance(inventory, dict) else None
        payer = holders.get(command.actor_id) if isinstance(holders, dict) else None
        if not isinstance(holders, dict) or not isinstance(payer, dict):
            raise CommandRejectedError("inventory_currency_account_missing")
        seller_payment_ref = self._funding_holder_for(seller_ref)
        seller = holders.setdefault(seller_payment_ref, {})
        if not isinstance(seller, dict):
            raise CommandRejectedError("inventory_currency_account_invalid")
        payer_balance = payer.get("currency.ryo", 0)
        seller_balance = seller.get("currency.ryo", 0)
        if (
            isinstance(payer_balance, bool) or not isinstance(payer_balance, int) or payer_balance < total
            or isinstance(seller_balance, bool) or not isinstance(seller_balance, int) or seller_balance < 0
        ):
            raise CommandRejectedError("inventory_currency_insufficient")
        before_total = sum(
            value.get("currency.ryo", 0)
            for value in holders.values()
            if isinstance(value, Mapping) and isinstance(value.get("currency.ryo", 0), int) and not isinstance(value.get("currency.ryo", 0), bool)
        )
        payer["currency.ryo"] = payer_balance - total
        seller["currency.ryo"] = seller_balance + total
        after_total = sum(
            value.get("currency.ryo", 0)
            for value in holders.values()
            if isinstance(value, Mapping) and isinstance(value.get("currency.ryo", 0), int) and not isinstance(value.get("currency.ryo", 0), bool)
        )
        if after_total != before_total:
            raise CommandRejectedError("inventory_currency_conservation_failed")

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind="service_purchased", at=current_time,
            actor_refs=(command.actor_id,), affected_owner_refs=(_INVENTORY_REGISTRY_PATH,),
            material_consequence_refs=(f"service:{service_ref}:{quantity}:{total}ryo:{command.actor_id}->{seller_payment_ref}",),
            classification=visibility, audience_refs=(command.actor_id, seller_ref),
            reducer_ref="shinobi_runtime.commands.service_purchase_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _INVENTORY_REGISTRY_PATH: _json_bytes(inventory),
            **self._world_event_writes(world_events),
        }
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))
        expected_payer = payer["currency.ryo"]
        expected_seller = seller["currency.ryo"]

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("service purchase write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_INVENTORY_REGISTRY_PATH)
            staged_holders = staged.get("holders", {})
            if staged_holders.get(command.actor_id, {}).get("currency.ryo") != expected_payer:
                raise ValueError("service payer balance mismatch")
            if staged_holders.get(seller_payment_ref, {}).get("currency.ryo") != expected_seller:
                raise ValueError("service seller balance mismatch")

        return _BuiltPlan(
            code="service_purchase_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={
                "command_type": command.command_type, "service_ref": service_ref,
                "seller_ref": seller_ref, "payment_holder_ref": seller_payment_ref,
                "quantity": quantity, "unit_price_ryo": unit_price, "total_ryo": total,
                "pricing_unit": price_record.get("pricing_unit"),
                "semantic_event_id": event_id,
            }, validator=validate,
        )


    def _inventory_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _exact_payload(
            command.payload,
            ("action", "item_ref", "quantity", "stock_ref", "holder_ref", "loadout_ref", "contract_ref", "summary", "visibility"),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("issue", "return", "consume", "refit", "purchase"):
            raise CommandRejectedError("inventory_action_invalid")
        holder_ref = _stable_id(command.payload["holder_ref"], "inventory_holder_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary or len(summary) > 1000:
            raise CommandRejectedError("inventory_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("inventory_visibility_invalid")
        item_raw = command.payload["item_ref"]
        item_ref = None if item_raw is None else _stable_id(item_raw, "inventory_item_invalid")
        quantity_raw = command.payload["quantity"]
        quantity = None
        if quantity_raw is not None:
            if isinstance(quantity_raw, bool) or not isinstance(quantity_raw, int) or quantity_raw <= 0:
                raise CommandRejectedError("inventory_quantity_invalid")
            quantity = quantity_raw
        stock_raw = command.payload["stock_ref"]
        stock_ref = None if stock_raw is None else _stable_id(stock_raw, "inventory_stock_invalid", prefix="stock.")
        loadout_raw = command.payload["loadout_ref"]
        loadout_ref = None if loadout_raw is None else _stable_id(loadout_raw, "inventory_loadout_invalid")
        contract_raw = command.payload["contract_ref"]
        contract_ref = None if contract_raw is None else _stable_id(contract_raw, "inventory_contract_invalid")

        try:
            inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("inventory_registry_invalid") from exc
        holdings = inventory.get("holders") if isinstance(inventory, dict) else None
        if not isinstance(holdings, dict):
            raise CommandRejectedError("inventory_registry_invalid")
        holder = holdings.setdefault(holder_ref, {})
        if not isinstance(holder, dict):
            raise CommandRejectedError("inventory_holder_invalid")

        stock_path: Optional[str] = None
        stock: Optional[Dict[str, Any]] = None
        stock_owner: Optional[str] = None
        domain_registry: Optional[Dict[str, Any]] = None
        actor_authority = "holder_self" if command.actor_id == holder_ref else "delegated"
        consequences: list[str] = []

        if action in ("issue", "return", "refit", "purchase"):
            if stock_ref is None:
                raise CommandRejectedError("inventory_stock_required")
            stock_path, stock, stock_owner = self._stock_record(stock_ref)

        if action == "issue":
            if item_ref is None or quantity is None or loadout_ref is not None or contract_ref is not None:
                raise CommandRejectedError("inventory_action_fields_invalid")
            decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=stock_owner
            )
            if not decision.allowed:
                raise CommandRejectedError("inventory_issue_not_authorized")
            actor_authority = decision.basis
            container, key = self._stock_item_key(stock, item_ref)
            available = container.get(key)
            if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
                raise CommandRejectedError("inventory_stock_insufficient")
            current = holder.get(item_ref, 0)
            if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                raise CommandRejectedError("inventory_registry_invalid")
            container[key] = available - quantity
            holder[item_ref] = current + quantity
            consequences.append(f"issue:{item_ref}:{quantity}:{stock_ref}->{holder_ref}")

        elif action == "return":
            if item_ref is None or quantity is None or loadout_ref is not None or contract_ref is not None:
                raise CommandRejectedError("inventory_action_fields_invalid")
            if not self._inventory_holder_authorized(command.actor_id, holder_ref):
                raise CommandRejectedError("inventory_holder_not_authorized")
            carried = holder.get(item_ref, 0)
            if isinstance(carried, bool) or not isinstance(carried, int) or carried < quantity:
                raise CommandRejectedError("inventory_quantity_insufficient")
            container, key = self._stock_item_key(stock, item_ref)
            stock_count = container.get(key, 0)
            if isinstance(stock_count, bool) or not isinstance(stock_count, int) or stock_count < 0:
                raise CommandRejectedError("inventory_stock_invalid")
            holder[item_ref] = carried - quantity
            if holder[item_ref] == 0:
                holder.pop(item_ref)
            container[key] = stock_count + quantity
            consequences.append(f"return:{item_ref}:{quantity}:{holder_ref}->{stock_ref}")

        elif action == "consume":
            if item_ref is None or quantity is None or stock_ref is not None or loadout_ref is not None or contract_ref is not None:
                raise CommandRejectedError("inventory_action_fields_invalid")
            if not self._inventory_holder_authorized(command.actor_id, holder_ref):
                raise CommandRejectedError("inventory_holder_not_authorized")
            carried = holder.get(item_ref, 0)
            if isinstance(carried, bool) or not isinstance(carried, int) or carried < quantity:
                raise CommandRejectedError("inventory_quantity_insufficient")
            holder[item_ref] = carried - quantity
            if holder[item_ref] == 0:
                holder.pop(item_ref)
            consequences.append(f"consume:{item_ref}:{quantity}:{holder_ref}")

        elif action == "refit":
            if item_ref is not None or quantity is not None or loadout_ref is None or contract_ref is not None:
                raise CommandRejectedError("inventory_action_fields_invalid")
            char_path, char = self._resolve_actor_for_write(holder_ref)
            if char.get("schema") != "shinobi_character":
                raise CommandRejectedError("inventory_refit_requires_exact_character")
            stock_decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
                holder_ref=command.actor_id, owner_ref=stock_owner
            )
            if command.actor_id != holder_ref and not stock_decision.allowed:
                raise CommandRejectedError("inventory_refit_not_authorized")
            if stock_decision.allowed:
                actor_authority = stock_decision.basis
            old_ref = char.get("equipment_loadout_id")
            old_quantities = self._loadout_quantities(old_ref) if isinstance(old_ref, str) and old_ref else {}
            target_quantities = self._loadout_quantities(loadout_ref)
            for ref in sorted(set(old_quantities) | set(target_quantities)):
                current = holder.get(ref, 0)
                if isinstance(current, bool) or not isinstance(current, int) or current < 0:
                    raise CommandRejectedError("inventory_registry_invalid")
                old_standard = old_quantities.get(ref, 0)
                target = target_quantities.get(ref, 0)
                # Preserve mission-specific extras above the old standard.
                extras = max(0, current - old_standard)
                desired = target + extras
                delta = desired - current
                if delta > 0:
                    container, key = self._stock_item_key(stock, ref)
                    available = container.get(key)
                    if isinstance(available, bool) or not isinstance(available, int) or available < delta:
                        raise CommandRejectedError("inventory_stock_insufficient")
                    container[key] = available - delta
                    holder[ref] = current + delta
                elif delta < 0:
                    container, key = self._stock_item_key(stock, ref)
                    returned = -delta
                    stock_count = container.get(key, 0)
                    if isinstance(stock_count, bool) or not isinstance(stock_count, int) or stock_count < 0:
                        raise CommandRejectedError("inventory_stock_invalid")
                    holder[ref] = current - returned
                    if holder[ref] == 0:
                        holder.pop(ref)
                    container[key] = stock_count + returned
            char["equipment_loadout_id"] = loadout_ref
            consequences.append(f"refit:{holder_ref}:{old_ref}->{loadout_ref}")

        else:  # purchase
            if item_ref is None or quantity is None or loadout_ref is not None:
                raise CommandRejectedError("inventory_action_fields_invalid")
            if command.actor_id != holder_ref:
                raise CommandRejectedError("inventory_purchase_buyer_mismatch")

            # Routine open-market retail does not need an offer/accept contract
            # or a shop-owner wallet. A null contract_ref means buy from the
            # registered public stock at the authoritative base price. Scarce,
            # controlled, negotiated, or private sales still use a contract.
            seller_ref = stock_owner
            if contract_ref is None:
                if stock.get("no_internal_shop") is not False:
                    raise CommandRejectedError("inventory_retail_purchase_unavailable")
                mechanics = self._economy_mechanics()
                price_row = mechanics.get("item_prices", {}).get(item_ref)
                if not isinstance(price_row, Mapping) or price_row.get("market_access") != "open":
                    raise CommandRejectedError("inventory_retail_contract_required")
                unit_price = price_row.get("base_price_ryo")
                if isinstance(unit_price, bool) or not isinstance(unit_price, int) or unit_price <= 0:
                    raise CommandRejectedError("economy_mechanics_invalid")
                total = unit_price * quantity
            else:
                try:
                    domain_registry = copy.deepcopy(self.repository.read_json(_DOMAIN_REGISTRY_PATH))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("inventory_contract_registry_invalid") from exc
                contracts = domain_registry.get("contracts") if isinstance(domain_registry, dict) else None
                if not isinstance(contracts, list):
                    raise CommandRejectedError("inventory_contract_registry_invalid")
                matches = [x for x in contracts if isinstance(x, dict) and x.get("id") == contract_ref]
                if len(matches) != 1:
                    raise CommandRejectedError("inventory_contract_unresolved")
                contract = matches[0]
                if (
                    contract.get("kind") != "purchase"
                    or contract.get("status") not in ("accepted", "authorized")
                    or contract.get("buyer_ref") != holder_ref
                    or contract.get("stock_ref") != stock_ref
                    or contract.get("item_ref") != item_ref
                    or contract.get("quantity") != quantity
                ):
                    raise CommandRejectedError("inventory_contract_mismatch")
                total = contract.get("total_ryo")
                seller_ref = contract.get("seller_ref")
                if isinstance(total, bool) or not isinstance(total, int) or total <= 0 or not isinstance(seller_ref, str):
                    raise CommandRejectedError("inventory_contract_invalid")

            buyer_ryo = holder.get("currency.ryo", 0)
            if isinstance(buyer_ryo, bool) or not isinstance(buyer_ryo, int) or buyer_ryo < total:
                raise CommandRejectedError("inventory_currency_insufficient")
            container, key = self._stock_item_key(stock, item_ref)
            available = container.get(key)
            if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
                raise CommandRejectedError("inventory_stock_insufficient")
            carried = holder.get(item_ref, 0)
            if isinstance(carried, bool) or not isinstance(carried, int) or carried < 0:
                raise CommandRejectedError("inventory_registry_invalid")
            container[key] = available - quantity
            holder[item_ref] = carried + quantity
            holder["currency.ryo"] = buyer_ryo - total
            if holder["currency.ryo"] == 0:
                holder.pop("currency.ryo")

            # Sale authority and money custody are intentionally separate. A
            # named merchant may authorize a sale, but ordinary proceeds settle
            # into an aggregate local/private economy account rather than a
            # permanent personal merchant ledger.
            seller_payment_ref = self._funding_holder_for(seller_ref)
            seller = holdings.setdefault(seller_payment_ref, {})
            if not isinstance(seller, dict):
                raise CommandRejectedError("inventory_registry_invalid")
            seller_ryo = seller.get("currency.ryo", 0)
            if isinstance(seller_ryo, bool) or not isinstance(seller_ryo, int) or seller_ryo < 0:
                raise CommandRejectedError("inventory_registry_invalid")
            seller["currency.ryo"] = seller_ryo + total
            if contract_ref is not None:
                contract["status"] = "completed"
                contract["completed_at"] = str(current_time)
                contract["payment_holder_ref"] = seller_payment_ref
                consequences.append(f"purchase:{item_ref}:{quantity}:{total}ryo:{holder_ref}->{seller_payment_ref}")
            else:
                consequences.append(f"retail_purchase:{item_ref}:{quantity}:{total}ryo:{holder_ref}->{seller_payment_ref}")

        world_events = self._world_events()
        affected = [_INVENTORY_REGISTRY_PATH]
        if stock_path is not None:
            affected.append(stock_path)
        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _INVENTORY_REGISTRY_PATH: _json_bytes(inventory),
        }
        if stock_path is not None and stock is not None:
            writes[stock_path] = _json_bytes(stock)
        # Refit character after-image is local to this branch.
        if action == "refit":
            writes[char_path] = _json_bytes(char)
            affected.append(char_path)
        if domain_registry is not None:
            writes[_DOMAIN_REGISTRY_PATH] = _json_bytes(domain_registry)
            affected.append(_DOMAIN_REGISTRY_PATH)
        event_id = self._append_semantic_event(
            world_events, command=command, kind="inventory_changed", at=current_time,
            host_refs=(), actor_refs=(command.actor_id,), affected_owner_refs=tuple(affected),
            material_consequence_refs=tuple(consequences), classification=visibility,
            audience_refs=(command.actor_id, holder_ref), reducer_ref="shinobi_runtime.commands.inventory_resolution",
        )
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected_paths = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected_paths:
                raise ValueError("inventory write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_INVENTORY_REGISTRY_PATH)
            if staged != inventory:
                raise ValueError("inventory registry after-image mismatch")
            if stock_path is not None and stock is not None and overlay.read_json(stock_path) != stock:
                raise ValueError("inventory stock after-image mismatch")

        return _BuiltPlan(
            code="inventory_resolution_ready", affected_refs=expected_paths, writes=writes,
            result={"command_type": command.command_type, "action": action, "holder_ref": holder_ref,
                    "item_ref": item_ref, "quantity": quantity, "loadout_ref": loadout_ref,
                    "authority_basis": actor_authority, "consequences": consequences,
                    "semantic_event_id": event_id}, validator=validate,
        )


    def _institution_project_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        """Start, advance, or cancel one real institutional facility project."""

        _exact_payload(
            command.payload,
            (
                "action", "project_ref", "institution_ref", "project_type", "place_ref",
                "stock_ref", "target_time", "active_hours", "summary", "visibility",
            ),
            command.command_type,
        )
        action = command.payload["action"]
        if action not in ("start", "advance", "cancel"):
            raise CommandRejectedError("institution_project_action_invalid")
        institution_ref = _stable_id(command.payload["institution_ref"], "institution_project_institution_invalid")
        summary = command.payload["summary"]
        visibility = command.payload["visibility"]
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
            raise CommandRejectedError("institution_project_summary_invalid")
        if visibility not in ("public", "restricted", "secret"):
            raise CommandRejectedError("institution_project_visibility_invalid")
        authority = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
            holder_ref=command.actor_id, owner_ref=institution_ref
        )
        if not authority.allowed:
            raise CommandRejectedError("institution_project_not_authorized")

        try:
            mechanics = self.repository.read_json(_INSTITUTION_PROJECT_MECHANICS_PATH)
            registry = copy.deepcopy(self.repository.read_json(_DOMAIN_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_project_registry_invalid") from exc
        project_types = mechanics.get("project_types") if isinstance(mechanics, Mapping) else None
        projects = registry.get("projects") if isinstance(registry, dict) else None
        if not isinstance(project_types, Mapping) or not isinstance(projects, list):
            raise CommandRejectedError("institution_project_registry_invalid")

        project: Dict[str, Any]
        project_ref: str
        project_rule: Mapping[str, Any]
        stock_path: Optional[str] = None
        stock: Optional[Dict[str, Any]] = None
        project_inventory: Optional[Dict[str, Any]] = None
        funding_holder_ref: Optional[str] = None
        currency_cost_ryo = 0
        routes: Optional[Dict[str, Any]] = None
        completed_module: Optional[Mapping[str, Any]] = None
        base: Optional[_BuiltPlan] = None
        world_time = current_time
        resource_consequences: list[str] = []

        if action == "start":
            if command.payload["project_ref"] is not None or command.payload["target_time"] is not None or command.payload["active_hours"] is not None:
                raise CommandRejectedError("institution_project_fields_invalid")
            project_type = command.payload["project_type"]
            place_ref_raw = command.payload["place_ref"]
            stock_ref_raw = command.payload["stock_ref"]
            if not isinstance(project_type, str) or project_type not in project_types:
                raise CommandRejectedError("institution_project_type_invalid")
            place_ref = _stable_id(place_ref_raw, "institution_project_place_invalid", prefix="place.")
            stock_ref = _stable_id(stock_ref_raw, "institution_project_stock_invalid", prefix="stock.")
            project_rule = project_types[project_type]
            if not isinstance(project_rule, Mapping):
                raise CommandRejectedError("institution_project_mechanics_invalid")
            try:
                routes = copy.deepcopy(self.repository.read_json(_ROUTES_PATH))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("institution_project_place_invalid") from exc
            payload = routes.get("payload") if isinstance(routes, dict) else None
            places = payload.get("places") if isinstance(payload, dict) else None
            if not isinstance(places, list) or not any(isinstance(row, Mapping) and row.get("id") == place_ref for row in places):
                raise CommandRejectedError("institution_project_place_invalid")
            if any(
                isinstance(row, Mapping)
                and row.get("kind") == "institution_project"
                and row.get("status") == "active"
                and row.get("subject_ref") == place_ref
                and row.get("project_type") == project_type
                for row in projects
            ):
                raise CommandRejectedError("institution_project_duplicate_active")

            required_work = project_rule.get("required_work_units")
            work_rate = project_rule.get("work_units_per_active_hour")
            costs = project_rule.get("resource_costs")
            currency_cost_ryo = project_rule.get("currency_cost_ryo")
            module_kind = project_rule.get("module_kind")
            required_modules = project_rule.get("requires_modules", [])
            if (
                isinstance(required_work, bool) or not isinstance(required_work, int) or required_work <= 0
                or isinstance(work_rate, bool) or not isinstance(work_rate, int) or work_rate <= 0
                or isinstance(currency_cost_ryo, bool) or not isinstance(currency_cost_ryo, int) or currency_cost_ryo <= 0
                or not isinstance(costs, Mapping)
                or not isinstance(module_kind, str) or not module_kind
                or not isinstance(required_modules, list)
                or any(not isinstance(value, str) or not value for value in required_modules)
            ):
                raise CommandRejectedError("institution_project_mechanics_invalid")
            place_record = next(
                (row for row in places if isinstance(row, Mapping) and row.get("id") == place_ref),
                None,
            )
            modules = place_record.get("mechanical_modules") if isinstance(place_record, Mapping) else None
            for required_module in required_modules:
                if not isinstance(modules, Mapping) or not isinstance(modules.get(required_module), Mapping):
                    raise CommandRejectedError("institution_project_facility_prerequisite_missing")
            stock_path, stock, stock_owner_ref = self._stock_record(stock_ref)
            if not self._inventory_holder_authorized(command.actor_id, stock_owner_ref):
                raise CommandRejectedError("institution_project_stock_not_authorized")
            normalized_costs: Dict[str, int] = {}
            for item_ref, quantity in sorted(costs.items()):
                if not isinstance(item_ref, str) or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                    raise CommandRejectedError("institution_project_mechanics_invalid")
                container, key = self._stock_item_key(stock, item_ref)
                available = container.get(key)
                if isinstance(available, bool) or not isinstance(available, int) or available < quantity:
                    raise CommandRejectedError("institution_project_resources_insufficient")
                normalized_costs[item_ref] = quantity
            for item_ref, quantity in normalized_costs.items():
                container, key = self._stock_item_key(stock, item_ref)
                container[key] -= quantity
                resource_consequences.append(f"stock:{stock_ref}:{item_ref}:-{quantity}")

            _economy_record, finance = self._economy_world()
            funding_holder_ref = self._funding_holder_for(institution_ref, finance=finance)
            try:
                project_inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_REGISTRY_PATH))
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("institution_project_funding_invalid") from exc
            holders = project_inventory.get("holders") if isinstance(project_inventory, dict) else None
            funding = holders.get(funding_holder_ref) if isinstance(holders, dict) else None
            if not isinstance(holders, dict) or not isinstance(funding, dict):
                raise CommandRejectedError("institution_project_funding_invalid")
            contractors = holders.setdefault("economy.contractors", {})
            if not isinstance(contractors, dict):
                raise CommandRejectedError("institution_project_funding_invalid")
            balance = funding.get("currency.ryo", 0)
            contractor_balance = contractors.get("currency.ryo", 0)
            if (
                isinstance(balance, bool) or not isinstance(balance, int) or balance < currency_cost_ryo
                or isinstance(contractor_balance, bool) or not isinstance(contractor_balance, int) or contractor_balance < 0
            ):
                raise CommandRejectedError("institution_project_funding_insufficient")
            funding["currency.ryo"] = balance - currency_cost_ryo
            contractors["currency.ryo"] = contractor_balance + currency_cost_ryo
            resource_consequences.append(
                f"currency:{funding_holder_ref}->economy.contractors:{currency_cost_ryo}ryo"
            )

            project_ref = f"project.institution.{command.digest[:24]}"
            if any(isinstance(row, Mapping) and row.get("id") == project_ref for row in projects):
                raise CommandRejectedError("institution_project_conflict")
            next_due = current_time.add_seconds(24 * 60 * 60)
            project = {
                "id": project_ref,
                "kind": "institution_project",
                "project_type": project_type,
                "status": "active",
                "subject_ref": place_ref,
                "institution_ref": institution_ref,
                "authority_ref": command.actor_id,
                "stock_ref": stock_ref,
                "funding_holder_ref": funding_holder_ref,
                "currency_cost_ryo": currency_cost_ryo,
                "module_kind": module_kind,
                "opened_at": str(current_time),
                "completed_at": None,
                "next_due_at": str(next_due),
                "last_advanced_at": str(current_time),
                "required_work_units": required_work,
                "progress_units": 0,
                "work_units_per_active_hour": work_rate,
                "resource_costs": normalized_costs,
                "result": None,
            }
            projects.append(project)

        else:
            project_ref = _stable_id(command.payload["project_ref"], "institution_project_ref_invalid", prefix="project.institution.")
            matches = [row for row in projects if isinstance(row, dict) and row.get("id") == project_ref]
            if len(matches) != 1:
                raise CommandRejectedError("institution_project_unresolved")
            project = matches[0]
            if project.get("kind") != "institution_project" or project.get("institution_ref") != institution_ref:
                raise CommandRejectedError("institution_project_unresolved")
            if project.get("status") != "active":
                raise CommandRejectedError("institution_project_not_active")
            project_type = project.get("project_type")
            project_rule = project_types.get(project_type) if isinstance(project_type, str) else None
            if not isinstance(project_rule, Mapping):
                raise CommandRejectedError("institution_project_mechanics_invalid")
            if command.payload["project_type"] is not None or command.payload["place_ref"] is not None or command.payload["stock_ref"] is not None:
                raise CommandRejectedError("institution_project_fields_invalid")

            if action == "cancel":
                if command.payload["target_time"] is not None or command.payload["active_hours"] is not None:
                    raise CommandRejectedError("institution_project_fields_invalid")
                project["status"] = "cancelled"
                project["completed_at"] = None
                project["next_due_at"] = None
                project["result"] = summary.strip()
            else:
                try:
                    target_time = CampaignTime.parse(command.payload["target_time"])
                    active_hours = Decimal(str(command.payload["active_hours"]))
                except Exception as exc:
                    raise CommandRejectedError("institution_project_time_invalid") from exc
                if target_time <= current_time:
                    raise CommandRejectedError("institution_project_time_invalid")
                elapsed_hours = Decimal(
                    int((_campaign_datetime(target_time) - _campaign_datetime(current_time)).total_seconds())
                ) / Decimal(3600)
                if not active_hours.is_finite() or active_hours <= 0 or active_hours > elapsed_hours:
                    raise CommandRejectedError("institution_project_time_invalid")
                rate = project.get("work_units_per_active_hour")
                progress = project.get("progress_units")
                required_work = project.get("required_work_units")
                if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (rate, progress, required_work)) or rate <= 0 or required_work <= 0:
                    raise CommandRejectedError("institution_project_state_invalid")
                added = int((active_hours * Decimal(rate)).to_integral_value(rounding=ROUND_FLOOR))
                if added <= 0:
                    raise CommandRejectedError("institution_project_work_too_small")
                base = self._time_spanning_base(command, meta, current_time, target_time=target_time)
                if base.result.get("interrupted") or CampaignTime.parse(base.result["world_time"]) != target_time:
                    raise CommandRejectedError("time_boundary_requires_domain_settlement")
                world_time = target_time
                project["progress_units"] = min(required_work, progress + added)
                project["last_advanced_at"] = str(target_time)
                if project["progress_units"] >= required_work:
                    try:
                        routes = copy.deepcopy(self.repository.read_json(_ROUTES_PATH))
                    except (FileNotFoundError, ValueError) as exc:
                        raise CommandRejectedError("institution_project_place_invalid") from exc
                    payload = routes.get("payload") if isinstance(routes, dict) else None
                    places = payload.get("places") if isinstance(payload, dict) else None
                    place_ref = project.get("subject_ref")
                    matches_places = [row for row in places or [] if isinstance(row, dict) and row.get("id") == place_ref]
                    if len(matches_places) != 1:
                        raise CommandRejectedError("institution_project_place_invalid")
                    place = matches_places[0]
                    modules = place.setdefault("mechanical_modules", {})
                    if not isinstance(modules, dict):
                        raise CommandRejectedError("institution_project_place_invalid")
                    module_kind = project.get("module_kind")
                    defaults = project_rule.get("create_defaults")
                    effects = project_rule.get("effects")
                    caps = project_rule.get("caps")
                    if not isinstance(module_kind, str) or not isinstance(defaults, Mapping) or not isinstance(effects, Mapping) or not isinstance(caps, Mapping):
                        raise CommandRejectedError("institution_project_mechanics_invalid")
                    module = modules.get(module_kind)
                    if module is None:
                        module = copy.deepcopy(dict(defaults))
                        modules[module_kind] = module
                    if not isinstance(module, dict):
                        raise CommandRejectedError("institution_project_place_invalid")
                    for field_name, delta in effects.items():
                        cap = caps.get(field_name)
                        current_value = module.get(field_name, defaults.get(field_name))
                        if (
                            not isinstance(field_name, str)
                            or isinstance(delta, bool) or not isinstance(delta, int)
                            or isinstance(cap, bool) or not isinstance(cap, int)
                            or isinstance(current_value, bool) or not isinstance(current_value, int)
                        ):
                            raise CommandRejectedError("institution_project_mechanics_invalid")
                        module[field_name] = min(cap, current_value + delta)
                    completed_module = copy.deepcopy(module)
                    project["status"] = "completed"
                    project["completed_at"] = str(target_time)
                    project["next_due_at"] = None
                    project["result"] = summary.strip()
                else:
                    project["next_due_at"] = str(target_time.add_seconds(24 * 60 * 60))

        world_events = self._world_events_after(base)
        consequence_refs = [project_ref, f"project_status:{project.get('status')}", *resource_consequences]
        if completed_module is not None:
            consequence_refs.append(f"facility_module_changed:{project.get('subject_ref')}:{project.get('module_kind')}")
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="institution_project_changed",
            at=world_time,
            actor_refs=(command.actor_id,),
            affected_owner_refs=tuple(
                x for x in (
                    _DOMAIN_REGISTRY_PATH, _ROUTES_PATH if routes is not None else None, stock_path,
                    _INVENTORY_REGISTRY_PATH if project_inventory is not None else None,
                ) if isinstance(x, str)
            ),
            material_consequence_refs=tuple(consequence_refs),
            classification=visibility,
            audience_refs=(institution_ref,),
            reducer_ref="shinobi_runtime.commands.institution_project_resolution",
        )
        writes = dict(base.writes) if base is not None else {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=world_time))
        }
        writes[_DOMAIN_REGISTRY_PATH] = _json_bytes(registry)
        if routes is not None and completed_module is not None:
            writes[_ROUTES_PATH] = _json_bytes(routes)
        if stock_path is not None and stock is not None:
            writes[stock_path] = _json_bytes(stock)
        if project_inventory is not None:
            writes[_INVENTORY_REGISTRY_PATH] = _json_bytes(project_inventory)
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("institution project write set changed after planning")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=world_time)
            staged_projects = overlay.read_json(_DOMAIN_REGISTRY_PATH).get("projects", [])
            staged = [row for row in staged_projects if isinstance(row, Mapping) and row.get("id") == project_ref]
            if len(staged) != 1 or staged[0].get("status") != project.get("status") or staged[0].get("progress_units", 0) != project.get("progress_units", 0):
                raise ValueError("institution project after-image mismatch")
            if project_inventory is not None:
                staged_inventory = overlay.read_json(_INVENTORY_REGISTRY_PATH)
                staged_holders = staged_inventory.get("holders", {})
                staged_funding = staged_holders.get(funding_holder_ref, {}) if isinstance(staged_holders, Mapping) else {}
                staged_contractors = staged_holders.get("economy.contractors", {}) if isinstance(staged_holders, Mapping) else {}
                expected_funding = project_inventory.get("holders", {}).get(funding_holder_ref, {}).get("currency.ryo")
                expected_contractors = project_inventory.get("holders", {}).get("economy.contractors", {}).get("currency.ryo")
                if staged_funding.get("currency.ryo") != expected_funding or staged_contractors.get("currency.ryo") != expected_contractors:
                    raise ValueError("institution project funding transfer missing")
                before_inventory = self.repository.read_json(_INVENTORY_REGISTRY_PATH)
                before_holders = before_inventory.get("holders", {}) if isinstance(before_inventory, Mapping) else {}
                before_total = sum(
                    record.get("currency.ryo", 0)
                    for record in before_holders.values()
                    if isinstance(record, Mapping)
                    and isinstance(record.get("currency.ryo", 0), int)
                    and not isinstance(record.get("currency.ryo", 0), bool)
                )
                after_total = sum(
                    record.get("currency.ryo", 0)
                    for record in staged_holders.values()
                    if isinstance(record, Mapping)
                    and isinstance(record.get("currency.ryo", 0), int)
                    and not isinstance(record.get("currency.ryo", 0), bool)
                )
                if before_total != after_total:
                    raise ValueError("institution project currency conservation failed")
            if completed_module is not None:
                route_record = overlay.read_json(_ROUTES_PATH)
                places = route_record.get("payload", {}).get("places", [])
                found = [row for row in places if isinstance(row, Mapping) and row.get("id") == project.get("subject_ref")]
                if len(found) != 1 or found[0].get("mechanical_modules", {}).get(project.get("module_kind")) != completed_module:
                    raise ValueError("institution project facility effect missing")

        return _BuiltPlan(
            code="institution_project_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "project_ref": project_ref,
                "action": action,
                "status": project.get("status"),
                "progress_units": project.get("progress_units", 0),
                "required_work_units": project.get("required_work_units"),
                "funding_holder_ref": project.get("funding_holder_ref"),
                "currency_cost_ryo": project.get("currency_cost_ryo"),
                "completed_module": completed_module,
                "world_time": str(world_time),
                "semantic_event_id": event_id,
            },
            validator=validate,
        )
