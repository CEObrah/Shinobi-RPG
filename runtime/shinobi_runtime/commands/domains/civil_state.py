"""Legal, diplomatic, and civil-governance semantic commands.

These reducers deliberately compose existing authorities instead of duplicating
people, money, custody, forces, places, or populations.  Military occupation is
only a prerequisite/basis for government; diplomacy requires explicit party
consent; bounty money moves through conserved inventory currency.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Optional, Sequence

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.specs import COMMAND_SPECS
from shinobi_runtime.commands.core import _BuiltPlan, _OwnerResolutionCache, _json_bytes, _stable_id
from shinobi_runtime.commands.paths import (
    CONFLICT_REGISTRY_PATH as _CONFLICT_REGISTRY_PATH,
    POPULATION_REGISTRY_PATH as _POPULATION_REGISTRY_PATH,
    ROUTES_PATH as _ROUTES_PATH,
)
from shinobi_runtime.reducers import PopulationPool, PopulationTransfer, apply_transfer, neutral_proportional_selection
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_LEGAL_PATH = "state/reg/legal-cases.json"
_DIPLOMACY_PATH = "state/reg/diplomacy.json"
_GOVERNANCE_PATH = "state/reg/governance.json"
_INVENTORY_PATH = "state/inventory/registry.json"
_GOVERNANCE_MECHANICS_PATH = "game/data/mechanics/governance.json"


class CivilStateCommandsMixin:
    def _civil_authority(self, actor_ref: str, owner_ref: str) -> str:
        owner_ref = _stable_id(owner_ref, "civil_authority_owner_invalid")
        try:
            self._resolve_covered_owner(owner_ref, cache=_OwnerResolutionCache())
        except CommandRejectedError as exc:
            raise CommandRejectedError("civil_authority_owner_invalid") from exc
        decision = self._domain_authority(cache=_OwnerResolutionCache()).owner_leadership(
            holder_ref=actor_ref, owner_ref=owner_ref
        )
        if not decision.allowed:
            raise CommandRejectedError("civil_authority_denied")
        return decision.basis

    @staticmethod
    def _bounded_text(value: Any, code: str, *, max_len: int = 1000) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > max_len:
            raise CommandRejectedError(code)
        return value.strip()

    @staticmethod
    def _visibility(value: Any) -> str:
        if value not in ("public", "restricted", "secret"):
            raise CommandRejectedError("civil_visibility_invalid")
        return str(value)

    def _event_exists(self, event_ref: str) -> bool:
        return self._world_event_by_id(event_ref) is not None

    def _legal_case_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        action = command.payload.get("action")
        case_ref = _stable_id(command.payload.get("case_ref"), "legal_case_ref_invalid", prefix="case.")
        try:
            registry = copy.deepcopy(self.repository.read_json(_LEGAL_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("legal_case_registry_invalid") from exc
        cases = registry.get("cases") if isinstance(registry, dict) else None
        if not isinstance(cases, dict):
            raise CommandRejectedError("legal_case_registry_invalid")
        inventory = None
        authority_basis = ""
        result: Dict[str, Any] = {"command_type": command.command_type, "action": action, "case_ref": case_ref}

        if action == "open":
            if case_ref in cases:
                raise CommandRejectedError("legal_case_exists")
            issuer_ref = _stable_id(command.payload.get("issuer_ref"), "legal_case_issuer_invalid")
            authority_basis = self._civil_authority(command.actor_id, issuer_ref)
            subject_ref = _stable_id(command.payload.get("subject_ref"), "legal_case_subject_invalid")
            try:
                self._resolve_covered_owner(subject_ref, cache=_OwnerResolutionCache())
            except CommandRejectedError as exc:
                raise CommandRejectedError("legal_case_subject_invalid") from exc
            case_kind = self._bounded_text(command.payload.get("case_kind"), "legal_case_kind_invalid", max_len=80)
            summary = self._bounded_text(command.payload.get("summary"), "legal_case_summary_invalid")
            visibility = self._visibility(command.payload.get("visibility"))
            offense_refs = command.payload.get("offense_refs")
            if not isinstance(offense_refs, Sequence) or isinstance(offense_refs, (str, bytes, bytearray)) or any(not isinstance(x, str) or not x for x in offense_refs) or len(set(offense_refs)) != len(offense_refs):
                raise CommandRejectedError("legal_case_offense_refs_invalid")
            cases[case_ref] = {
                "id": case_ref, "case_kind": case_kind, "issuer_ref": issuer_ref, "subject_ref": subject_ref,
                "requester_ref": issuer_ref, "status": "open", "opened_at": str(current_time), "updated_at": str(current_time),
                "summary": summary, "visibility": visibility, "offense_refs": sorted(offense_refs), "evidence_refs": [],
                "warrant": {"status": "none", "authority_ref": None, "issued_at": None},
                "bounty": {"status": "none", "payer_ref": None, "payer_holder_ref": None, "escrow_holder_ref": None, "amount_ryo": 0, "hunter_refs": [], "posted_at": None, "verified_evidence_refs": [], "settled_at": None},
                "custody_ref": None, "disposition": None,
            }
        else:
            case = cases.get(case_ref)
            if not isinstance(case, dict):
                raise CommandRejectedError("legal_case_not_found")
            issuer_ref = str(case.get("issuer_ref") or "")
            if action == "post_bounty":
                payer_authority_ref = _stable_id(command.payload.get("payer_ref"), "legal_bounty_payer_invalid")
                authority_basis = self._civil_authority(command.actor_id, payer_authority_ref)
            elif action == "cancel_bounty" and isinstance(case.get("bounty"), Mapping) and isinstance(case["bounty"].get("payer_ref"), str):
                authority_basis = self._civil_authority(command.actor_id, str(case["bounty"]["payer_ref"]))
            else:
                authority_basis = self._civil_authority(command.actor_id, issuer_ref)
            if case.get("status") in ("resolved", "dismissed") and action not in ("record_custody",):
                raise CommandRejectedError("legal_case_closed")
            if action in ("add_evidence", "verify_bounty"):
                evidence_ref = _stable_id(command.payload.get("evidence_ref"), "legal_case_evidence_invalid", prefix="event.")
                if not self._event_exists(evidence_ref):
                    raise CommandRejectedError("legal_case_evidence_unresolved")
                if evidence_ref not in case["evidence_refs"]:
                    case["evidence_refs"].append(evidence_ref); case["evidence_refs"].sort()
                if action == "verify_bounty":
                    bounty = case["bounty"]
                    if bounty.get("status") not in ("posted", "claimed"):
                        raise CommandRejectedError("legal_bounty_not_posted")
                    if evidence_ref not in bounty["verified_evidence_refs"]:
                        bounty["verified_evidence_refs"].append(evidence_ref); bounty["verified_evidence_refs"].sort()
                    bounty["status"] = "claimed"
                result["evidence_ref"] = evidence_ref
            elif action == "issue_warrant":
                if not case["evidence_refs"]:
                    raise CommandRejectedError("legal_warrant_requires_evidence")
                authority_ref = _stable_id(command.payload.get("authority_ref"), "legal_warrant_authority_invalid")
                if authority_ref != issuer_ref:
                    self._civil_authority(command.actor_id, authority_ref)
                case["warrant"] = {"status": "active", "authority_ref": authority_ref, "issued_at": str(current_time)}
                case["status"] = "warranted"
            elif action == "post_bounty":
                payer_ref = _stable_id(command.payload.get("payer_ref"), "legal_bounty_payer_invalid")
                contractual = (
                    case.get("case_kind") == "contractual_bounty_target"
                    and bool(case.get("evidence_refs"))
                    and case.get("requester_ref") == payer_ref
                )
                if case["warrant"].get("status") != "active" and not contractual:
                    raise CommandRejectedError("legal_bounty_requires_warrant_or_verified_contract")
                amount = command.payload.get("amount_ryo")
                if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                    raise CommandRejectedError("legal_bounty_amount_invalid")
                payer_holder_ref = self._funding_holder_for(payer_ref)
                escrow_ref = f"escrow.legal.{case_ref.removeprefix('case.')}"
                try:
                    inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_PATH))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("inventory_registry_invalid") from exc
                holders = inventory.get("holders") if isinstance(inventory, dict) else None
                if not isinstance(holders, dict):
                    raise CommandRejectedError("inventory_registry_invalid")
                payer = holders.get(payer_holder_ref)
                if not isinstance(payer, dict):
                    raise CommandRejectedError("legal_bounty_payer_account_missing")
                balance = payer.get("currency.ryo", 0)
                if isinstance(balance, bool) or not isinstance(balance, int) or balance < amount:
                    raise CommandRejectedError("legal_bounty_funds_insufficient")
                escrow = holders.setdefault(escrow_ref, {})
                escrow_balance = escrow.get("currency.ryo", 0)
                if isinstance(escrow_balance, bool) or not isinstance(escrow_balance, int) or escrow_balance != 0:
                    raise CommandRejectedError("legal_bounty_escrow_not_empty")
                payer["currency.ryo"] = balance - amount
                if payer["currency.ryo"] == 0: payer.pop("currency.ryo")
                escrow["currency.ryo"] = amount
                case["bounty"] = {"status":"posted","payer_ref":payer_ref,"payer_holder_ref":payer_holder_ref,"escrow_holder_ref":escrow_ref,"amount_ryo":amount,"hunter_refs":[],"posted_at":str(current_time),"verified_evidence_refs":[],"settled_at":None}
                case["status"] = "bounty_posted"
                result.update({"amount_ryo": amount, "payer_holder_ref": payer_holder_ref, "escrow_holder_ref": escrow_ref})
            elif action == "assign_hunter":
                if case["bounty"].get("status") not in ("posted", "claimed"):
                    raise CommandRejectedError("legal_bounty_not_posted")
                hunter_ref = _stable_id(command.payload.get("hunter_ref"), "legal_bounty_hunter_invalid")
                try: self._resolve_covered_owner(hunter_ref, cache=_OwnerResolutionCache())
                except CommandRejectedError as exc: raise CommandRejectedError("legal_bounty_hunter_invalid") from exc
                if hunter_ref not in case["bounty"]["hunter_refs"]:
                    case["bounty"]["hunter_refs"].append(hunter_ref); case["bounty"]["hunter_refs"].sort()
                result["hunter_ref"] = hunter_ref
            elif action == "settle_bounty":
                bounty = case["bounty"]
                hunter_ref = _stable_id(command.payload.get("hunter_ref"), "legal_bounty_hunter_invalid")
                if bounty.get("status") != "claimed" or hunter_ref not in bounty.get("hunter_refs", []) or not bounty.get("verified_evidence_refs"):
                    raise CommandRejectedError("legal_bounty_claim_unverified")
                try: inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_PATH))
                except (FileNotFoundError, ValueError) as exc: raise CommandRejectedError("inventory_registry_invalid") from exc
                holders = inventory.get("holders") if isinstance(inventory, dict) else None
                escrow_ref, amount = bounty.get("escrow_holder_ref"), bounty.get("amount_ryo")
                if not isinstance(holders, dict) or not isinstance(escrow_ref, str) or isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                    raise CommandRejectedError("legal_bounty_escrow_invalid")
                escrow = holders.get(escrow_ref); hunter = holders.setdefault(hunter_ref,{})
                if not isinstance(escrow, dict) or escrow.get("currency.ryo") != amount:
                    raise CommandRejectedError("legal_bounty_escrow_invalid")
                hunter_balance = hunter.get("currency.ryo",0)
                if isinstance(hunter_balance,bool) or not isinstance(hunter_balance,int): raise CommandRejectedError("inventory_currency_account_invalid")
                escrow.pop("currency.ryo",None); hunter["currency.ryo"] = hunter_balance + amount
                bounty["status"]="paid"; bounty["settled_at"]=str(current_time); case["status"]="resolved"; case["disposition"]="bounty_paid_after_verified_evidence"
                result.update({"hunter_ref": hunter_ref, "amount_ryo": amount})
            elif action == "cancel_bounty":
                bounty=case["bounty"]
                if bounty.get("status") != "posted": raise CommandRejectedError("legal_bounty_not_cancellable")
                try: inventory=copy.deepcopy(self.repository.read_json(_INVENTORY_PATH))
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("inventory_registry_invalid") from exc
                holders=inventory.get("holders") if isinstance(inventory,dict) else None; escrow_ref=bounty.get("escrow_holder_ref"); payer_holder_ref=bounty.get("payer_holder_ref"); amount=bounty.get("amount_ryo")
                if not isinstance(holders,dict) or not isinstance(escrow_ref,str) or not isinstance(payer_holder_ref,str) or not isinstance(amount,int): raise CommandRejectedError("legal_bounty_escrow_invalid")
                escrow=holders.get(escrow_ref); payer=holders.setdefault(payer_holder_ref,{})
                if not isinstance(escrow,dict) or escrow.get("currency.ryo")!=amount: raise CommandRejectedError("legal_bounty_escrow_invalid")
                escrow.pop("currency.ryo",None); payer["currency.ryo"]=int(payer.get("currency.ryo",0))+amount; bounty["status"]="cancelled"; bounty["settled_at"]=str(current_time); case["status"]="warranted"
            elif action == "record_custody":
                custody_ref = _stable_id(command.payload.get("custody_ref"), "legal_custody_ref_invalid", prefix="custody.")
                try: custody = self.repository.read_json("state/reg/custody.json")
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("custody_registry_invalid") from exc
                row = custody.get("records",{}).get(custody_ref) if isinstance(custody,Mapping) else None
                if not isinstance(row,Mapping) or row.get("subject_ref") != case.get("subject_ref"):
                    raise CommandRejectedError("legal_custody_subject_mismatch")
                case["custody_ref"] = custody_ref
                if row.get("status") == "detained" and case["warrant"].get("status") == "active": case["warrant"]["status"] = "served"
            elif action in ("resolve", "dismiss"):
                disposition = self._bounded_text(command.payload.get("disposition"), "legal_disposition_invalid")
                if action == "resolve" and not case["evidence_refs"]:
                    raise CommandRejectedError("legal_resolution_requires_evidence")
                if case["bounty"].get("status") in ("posted","claimed"):
                    raise CommandRejectedError("legal_case_has_unsettled_bounty")
                case["status"] = "resolved" if action == "resolve" else "dismissed"; case["disposition"] = disposition
            else:
                raise CommandRejectedError("legal_case_action_invalid")
            case["updated_at"] = str(current_time)

        world_events=self._world_events(); event_id=self._append_semantic_event(world_events,command=command,kind=f"legal_case_{action}",at=current_time,host_refs=(cases[case_ref]["issuer_ref"],case_ref),actor_refs=(command.actor_id,),causal_refs=tuple(cases[case_ref]["evidence_refs"]),affected_owner_refs=tuple(x for x in (_LEGAL_PATH,_INVENTORY_PATH if inventory is not None else None) if x),material_consequence_refs=(case_ref,),classification=cases[case_ref]["visibility"],audience_refs=(command.actor_id,),reducer_ref="shinobi_runtime.commands.domains.civil_state.legal_case_resolution")
        writes={self.meta_path:_json_bytes(self._meta_after(meta,command,world_time=current_time)),_LEGAL_PATH:_json_bytes(registry),**self._world_event_writes(world_events)}
        if inventory is not None: writes[_INVENTORY_PATH]=_json_bytes(inventory)
        writes=self._prune_noop_writes(writes); expected=tuple(sorted(writes)); result.update({"authority_basis":authority_basis,"status":cases[case_ref]["status"],"semantic_event_id":event_id})
        def validate(overlay:StagedOverlay,manifest:TransactionManifest)->None:
            if overlay.changed_paths!=expected: raise ValueError("legal case write set changed")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=current_time)
            if case_ref not in overlay.read_json(_LEGAL_PATH).get("cases",{}): raise ValueError("legal case not persisted")
        return _BuiltPlan(code="legal_case_resolution_ready",affected_refs=expected,writes=writes,result=result,validator=validate)

    def _diplomacy_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        action = command.payload.get("action")
        agreement_ref = _stable_id(command.payload.get("agreement_ref"), "diplomacy_agreement_invalid", prefix="agreement.")
        try:
            registry = copy.deepcopy(self.repository.read_json(_DIPLOMACY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("diplomacy_registry_invalid") from exc
        agreements = registry.get("agreements") if isinstance(registry, dict) else None
        if not isinstance(agreements, dict):
            raise CommandRejectedError("diplomacy_registry_invalid")
        representing_ref = _stable_id(command.payload.get("representing_ref"), "diplomacy_representing_invalid")
        authority_basis = self._civil_authority(command.actor_id, representing_ref)
        visibility = self._visibility(command.payload.get("visibility") or "restricted")
        inventory: Optional[Dict[str, Any]] = None
        settlement_refs: list[str] = []

        def normalize_provisions(agreement_type: str, party_refs: Sequence[str], raw: Any) -> Dict[str, Any]:
            if raw is None:
                raw = {}
            if not isinstance(raw, Mapping):
                raise CommandRejectedError("diplomacy_provisions_invalid")
            allowed = {"grantor_ref", "grantee_ref", "place_refs", "route_refs", "payer_ref", "payee_ref", "amount_ryo", "patron_ref", "client_ref", "guarantor_ref", "protected_ref", "tariff_multiplier_milli"}
            if set(raw) - allowed:
                raise CommandRejectedError("diplomacy_provisions_invalid")
            provisions: Dict[str, Any] = {}
            parties = set(party_refs)
            if agreement_type == "military_access":
                grantor = raw.get("grantor_ref")
                grantee = raw.get("grantee_ref")
                places = raw.get("place_refs")
                routes = raw.get("route_refs", [])
                if (
                    not isinstance(grantor, str) or grantor not in parties
                    or not isinstance(grantee, str) or grantee not in parties or grantee == grantor
                    or not isinstance(places, Sequence) or isinstance(places, (str, bytes, bytearray)) or not places
                    or any(not isinstance(ref, str) or not ref.startswith("place.") for ref in places)
                    or not isinstance(routes, Sequence) or isinstance(routes, (str, bytes, bytearray))
                    or any(not isinstance(ref, str) or not ref.startswith("route_") for ref in routes)
                ):
                    raise CommandRejectedError("diplomacy_military_access_provisions_invalid")
                graph = self._location_graph()
                if any(graph.place(ref) is None for ref in places):
                    raise CommandRejectedError("diplomacy_military_access_place_invalid")
                known_routes = {row.get("id") for row in graph.routes if isinstance(row, Mapping)}
                if any(ref not in known_routes for ref in routes):
                    raise CommandRejectedError("diplomacy_military_access_route_invalid")
                provisions = {
                    "grantor_ref": grantor,
                    "grantee_ref": grantee,
                    "place_refs": sorted(set(places)),
                    "route_refs": sorted(set(routes)),
                }
            elif agreement_type == "tribute":
                if raw:
                    payer = raw.get("payer_ref")
                    payee = raw.get("payee_ref")
                    amount = raw.get("amount_ryo")
                    if (
                        not isinstance(payer, str) or payer not in parties
                        or not isinstance(payee, str) or payee not in parties or payee == payer
                        or isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0
                    ):
                        raise CommandRejectedError("diplomacy_tribute_provisions_invalid")
                    # Resolve both funding paths at proposal time so unanimous consent
                    # cannot later point at fictitious or caller-invented accounts.
                    self._funding_holder_for(payer)
                    self._funding_holder_for(payee)
                    provisions = {"payer_ref": payer, "payee_ref": payee, "amount_ryo": amount}
            elif agreement_type == "trade":
                multiplier = raw.get("tariff_multiplier_milli", 750)
                places = raw.get("place_refs", [])
                routes = raw.get("route_refs", [])
                if (
                    isinstance(multiplier, bool) or not isinstance(multiplier, int) or not 0 <= multiplier <= 1000
                    or not isinstance(places, Sequence) or isinstance(places, (str, bytes, bytearray))
                    or any(not isinstance(ref, str) or not ref.startswith("place.") for ref in places)
                    or not isinstance(routes, Sequence) or isinstance(routes, (str, bytes, bytearray))
                    or any(not isinstance(ref, str) or not ref.startswith("route_") for ref in routes)
                ):
                    raise CommandRejectedError("diplomacy_trade_provisions_invalid")
                graph = self._location_graph()
                if any(graph.place(ref) is None for ref in places):
                    raise CommandRejectedError("diplomacy_trade_place_invalid")
                known_routes = {row.get("id") for row in graph.routes if isinstance(row, Mapping)}
                if any(ref not in known_routes for ref in routes):
                    raise CommandRejectedError("diplomacy_trade_route_invalid")
                provisions = {
                    "tariff_multiplier_milli": multiplier,
                    "place_refs": sorted(set(places)),
                    "route_refs": sorted(set(routes)),
                }
            elif agreement_type == "client_state":
                patron = raw.get("patron_ref")
                client = raw.get("client_ref")
                if (
                    not isinstance(patron, str) or patron not in parties
                    or not isinstance(client, str) or client not in parties or client == patron
                ):
                    raise CommandRejectedError("diplomacy_client_state_provisions_invalid")
                provisions = {"patron_ref": patron, "client_ref": client}
            elif agreement_type == "guarantee":
                guarantor = raw.get("guarantor_ref")
                protected = raw.get("protected_ref")
                if (
                    not isinstance(guarantor, str) or guarantor not in parties
                    or not isinstance(protected, str) or protected not in parties or protected == guarantor
                ):
                    raise CommandRejectedError("diplomacy_guarantee_provisions_invalid")
                provisions = {"guarantor_ref": guarantor, "protected_ref": protected}
            elif agreement_type == "border":
                places = raw.get("place_refs", [])
                routes = raw.get("route_refs", [])
                if (
                    not isinstance(places, Sequence) or isinstance(places, (str, bytes, bytearray))
                    or any(not isinstance(ref, str) or not ref.startswith("place.") for ref in places)
                    or not isinstance(routes, Sequence) or isinstance(routes, (str, bytes, bytearray))
                    or any(not isinstance(ref, str) or not ref.startswith("route_") for ref in routes)
                    or (not places and not routes)
                ):
                    raise CommandRejectedError("diplomacy_border_provisions_invalid")
                graph = self._location_graph()
                if any(graph.place(ref) is None for ref in places):
                    raise CommandRejectedError("diplomacy_border_place_invalid")
                known_routes = {row.get("id") for row in graph.routes if isinstance(row, Mapping)}
                if any(ref not in known_routes for ref in routes):
                    raise CommandRejectedError("diplomacy_border_route_invalid")
                provisions = {"place_refs": sorted(set(places)), "route_refs": sorted(set(routes))}
            elif raw:
                raise CommandRejectedError("diplomacy_provisions_not_supported")
            return provisions

        if action == "propose":
            if agreement_ref in agreements:
                raise CommandRejectedError("diplomacy_agreement_exists")
            party_refs = command.payload.get("party_refs")
            if (
                not isinstance(party_refs, Sequence) or isinstance(party_refs, (str, bytes, bytearray))
                or not 2 <= len(party_refs) <= 16
                or any(not isinstance(x, str) or not x for x in party_refs)
                or len(set(party_refs)) != len(party_refs)
                or representing_ref not in party_refs
            ):
                raise CommandRejectedError("diplomacy_parties_invalid")
            for ref in party_refs:
                try:
                    self._resolve_covered_owner(ref, cache=_OwnerResolutionCache())
                except CommandRejectedError as exc:
                    raise CommandRejectedError("diplomacy_party_unresolved") from exc
            atype = command.payload.get("agreement_type")
            if atype not in (
                "nonaggression", "alliance", "trade", "tribute", "client_state", "border",
                "guarantee", "recognition", "ceasefire_framework", "migration", "military_access",
            ):
                raise CommandRejectedError("diplomacy_type_invalid")
            terms = command.payload.get("terms")
            if (
                not isinstance(terms, Sequence) or isinstance(terms, (str, bytes, bytearray)) or not terms or len(terms) > 32
                or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in terms)
            ):
                raise CommandRejectedError("diplomacy_terms_invalid")
            normalized_parties = sorted(party_refs)
            provisions = normalize_provisions(str(atype), normalized_parties, command.payload.get("provisions"))
            agreements[agreement_ref] = {
                "id": agreement_ref, "agreement_type": atype, "party_refs": normalized_parties,
                "status": "proposed", "proposed_by": representing_ref, "consent_refs": [representing_ref],
                "rejection_refs": [], "terms": list(dict.fromkeys(x.strip() for x in terms)),
                "opened_at": str(current_time), "effective_at": None, "ended_at": None,
                "evidence_refs": [], "visibility": visibility, "provisions": provisions,
                "settlement_count": 0, "last_settled_at": None,
            }
        else:
            row = agreements.get(agreement_ref)
            if not isinstance(row, dict):
                raise CommandRejectedError("diplomacy_agreement_not_found")
            visibility = row["visibility"]
            if representing_ref not in row["party_refs"]:
                raise CommandRejectedError("diplomacy_party_not_in_agreement")
            if action == "accept":
                if row["status"] != "proposed":
                    raise CommandRejectedError("diplomacy_agreement_not_proposed")
                if representing_ref not in row["consent_refs"]:
                    row["consent_refs"].append(representing_ref); row["consent_refs"].sort()
                if set(row["consent_refs"]) == set(row["party_refs"]):
                    row["status"] = "active"; row["effective_at"] = str(current_time)
            elif action == "reject":
                if row["status"] != "proposed":
                    raise CommandRejectedError("diplomacy_agreement_not_proposed")
                if representing_ref not in row["rejection_refs"]:
                    row["rejection_refs"].append(representing_ref); row["rejection_refs"].sort()
                row["status"] = "rejected"; row["ended_at"] = str(current_time)
            elif action == "terminate":
                if row["status"] != "active":
                    raise CommandRejectedError("diplomacy_agreement_not_active")
                row["status"] = "ended"; row["ended_at"] = str(current_time)
            elif action == "record_incident":
                evidence_ref = _stable_id(command.payload.get("evidence_ref"), "diplomacy_evidence_invalid", prefix="event.")
                if not self._event_exists(evidence_ref):
                    raise CommandRejectedError("diplomacy_evidence_unresolved")
                summary = self._bounded_text(command.payload.get("summary"), "diplomacy_incident_summary_invalid")
                incident_kind = self._bounded_text(command.payload.get("incident_kind"), "diplomacy_incident_kind_invalid", max_len=80)
                incident_id = f"incident.{command.digest[:20]}"
                registry["incidents"].append({
                    "id": incident_id, "at": str(current_time), "party_refs": list(row["party_refs"]),
                    "kind": incident_kind, "evidence_ref": evidence_ref, "summary": summary, "visibility": visibility,
                })
                if evidence_ref not in row["evidence_refs"]:
                    row["evidence_refs"].append(evidence_ref); row["evidence_refs"].sort()
            elif action == "settle_tribute":
                if row.get("status") != "active" or row.get("agreement_type") != "tribute":
                    raise CommandRejectedError("diplomacy_tribute_not_active")
                provisions = row.get("provisions")
                if not isinstance(provisions, Mapping):
                    raise CommandRejectedError("diplomacy_tribute_provisions_invalid")
                payer = provisions.get("payer_ref"); payee = provisions.get("payee_ref"); amount = provisions.get("amount_ryo")
                if (not isinstance(payer, str) or not isinstance(payee, str) or isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0):
                    raise CommandRejectedError("diplomacy_tribute_provisions_invalid")
                if representing_ref != payer:
                    raise CommandRejectedError("diplomacy_tribute_payer_authority_required")
                payer_holder = self._funding_holder_for(payer)
                payee_holder = self._funding_holder_for(payee)
                try:
                    inventory = copy.deepcopy(self.repository.read_json(_INVENTORY_PATH))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("inventory_registry_invalid") from exc
                holders = inventory.get("holders") if isinstance(inventory, dict) else None
                payer_account = holders.get(payer_holder) if isinstance(holders, dict) else None
                payee_account = holders.get(payee_holder) if isinstance(holders, dict) else None
                if not isinstance(payer_account, dict) or not isinstance(payee_account, dict):
                    raise CommandRejectedError("diplomacy_tribute_account_invalid")
                payer_balance = payer_account.get("currency.ryo", 0); payee_balance = payee_account.get("currency.ryo", 0)
                if (
                    isinstance(payer_balance, bool) or not isinstance(payer_balance, int) or payer_balance < amount
                    or isinstance(payee_balance, bool) or not isinstance(payee_balance, int) or payee_balance < 0
                ):
                    raise CommandRejectedError("diplomacy_tribute_funds_insufficient")
                payer_account["currency.ryo"] = payer_balance - amount
                payee_account["currency.ryo"] = payee_balance + amount
                row["settlement_count"] = int(row.get("settlement_count", 0)) + 1
                row["last_settled_at"] = str(current_time)
                settlement_refs.extend((f"tribute:{payer_holder}->{payee_holder}:{amount}ryo", f"tribute_count:{row['settlement_count']}"))
            else:
                raise CommandRejectedError("diplomacy_action_invalid")

        row = agreements[agreement_ref]
        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events, command=command, kind=f"diplomacy_{action}", at=current_time,
            host_refs=tuple(row["party_refs"]), actor_refs=(command.actor_id,), causal_refs=tuple(row["evidence_refs"]),
            affected_owner_refs=tuple(x for x in (_DIPLOMACY_PATH, _INVENTORY_PATH if inventory is not None else None) if x),
            material_consequence_refs=(agreement_ref, f"status:{row['status']}", *settlement_refs),
            classification=row["visibility"], audience_refs=tuple(row["party_refs"] if row["visibility"] == "public" else (command.actor_id,)),
            reducer_ref="shinobi_runtime.commands.domains.civil_state.diplomacy_resolution",
        )
        writes = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            _DIPLOMACY_PATH: _json_bytes(registry),
            **self._world_event_writes(world_events),
        }
        if inventory is not None:
            writes[_INVENTORY_PATH] = _json_bytes(inventory)
        writes = self._prune_noop_writes(writes); expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("diplomacy write set changed")
            self._assert_meta(overlay, manifest, meta_path=self.meta_path, command=command, world_time=current_time)
            staged = overlay.read_json(_DIPLOMACY_PATH)["agreements"][agreement_ref]
            if staged["status"] == "active" and set(staged["consent_refs"]) != set(staged["party_refs"]):
                raise ValueError("agreement active without unanimous persisted consent")
            if inventory is not None and overlay.read_json(_INVENTORY_PATH) != inventory:
                raise ValueError("diplomatic settlement inventory after-image mismatch")

        return _BuiltPlan(
            code="diplomacy_resolution_ready", affected_refs=expected, writes=writes,
            result={
                "command_type": command.command_type, "action": action, "agreement_ref": agreement_ref,
                "status": row["status"], "consent_refs": list(row["consent_refs"]),
                "provisions": copy.deepcopy(row.get("provisions", {})), "settlement_refs": settlement_refs,
                "authority_basis": authority_basis, "semantic_event_id": event_id,
            }, validator=validate,
        )

    @staticmethod
    def _pool_view(pool_id: str, record: Mapping[str, Any]) -> PopulationPool:
        profile=record.get("profile"); dims=profile.get("dimension_counts") if isinstance(profile,Mapping) else None; count=record.get("count")
        if isinstance(count,bool) or not isinstance(count,int) or not isinstance(dims,Mapping): raise CommandRejectedError("governance_population_invalid")
        try: return PopulationPool(pool_id,count,dims)
        except (TypeError,ValueError) as exc: raise CommandRejectedError("governance_population_invalid") from exc

    def _occupation_basis(self, place_ref: str, sovereign_ref: str) -> Optional[str]:
        try: registry=self.repository.read_json(_CONFLICT_REGISTRY_PATH)
        except (FileNotFoundError,ValueError): return None
        for conflict in (registry.get("records",{}) if isinstance(registry,Mapping) else {}).values():
            if not isinstance(conflict,Mapping): continue
            for front in conflict.get("fronts",[]):
                if not isinstance(front,Mapping): continue
                for occ in front.get("occupations",[]):
                    if isinstance(occ,Mapping) and occ.get("place_ref")==place_ref and occ.get("controller_ref")==sovereign_ref:
                        ev=occ.get("evidence_event_ref"); return ev if isinstance(ev,str) else str(conflict.get("id") or "occupation")
        return None

    def _governance_garrison_force(
        self,
        *,
        actor_ref: str,
        force_ref: str,
        sovereign_ref: str,
        administration_ref: str,
        presence_place_ref: str,
    ) -> Mapping[str, Any]:
        """Resolve a lawful garrison without turning a remote force into local presence.

        Governance owns the civil assignment only.  Force ownership and operational
        location remain authoritative in their existing force/formation owners.
        """
        cache = _OwnerResolutionCache()
        try:
            _path, _digest, force = self._resolve_covered_owner_view(force_ref, cache=cache)
        except CommandRejectedError as exc:
            raise CommandRejectedError("governance_garrison_invalid") from exc
        if force.get("schema") != "force" or force.get("id") != force_ref:
            raise CommandRejectedError("governance_garrison_invalid")
        force_owner = force.get("owner_ref")
        if not isinstance(force_owner, str) or not force_owner:
            raise CommandRejectedError("governance_garrison_authority_invalid")
        if force_owner not in (sovereign_ref, administration_ref):
            self._civil_authority(actor_ref, force_owner)

        present = force.get("mobilization_anchor_ref") == presence_place_ref
        registry_ref = force.get("formation_registry_ref")
        if not present and isinstance(registry_ref, str):
            try:
                formation_registry = self.repository.read_json(registry_ref)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("governance_garrison_invalid") from exc
            formations = formation_registry.get("formations") if isinstance(formation_registry, Mapping) else None
            present = isinstance(formations, list) and any(
                isinstance(item, Mapping)
                and item.get("location_ref") == presence_place_ref
                and int(item.get("personnel_total", 0) or 0) > 0
                for item in formations
            )
        if not present:
            raise CommandRejectedError("governance_garrison_not_present")
        return force

    def _governance_resolution(self, command: CommandEnvelope, meta: Mapping[str, Any], current_time: CampaignTime) -> _BuiltPlan:
        action=command.payload.get("action"); jurisdiction_ref=_stable_id(command.payload.get("jurisdiction_ref"),"governance_jurisdiction_invalid",prefix="jurisdiction.")
        try: registry=copy.deepcopy(self.repository.read_json(_GOVERNANCE_PATH))
        except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("governance_registry_invalid") from exc
        jurisdictions=registry.get("jurisdictions") if isinstance(registry,dict) else None
        if not isinstance(jurisdictions,dict): raise CommandRejectedError("governance_registry_invalid")
        world=None; population=None; inventory=None; authority_basis=""; basis_refs=[]
        if action in ("establish_administration","found_settlement"):
            if jurisdiction_ref in jurisdictions: raise CommandRejectedError("governance_jurisdiction_exists")
            sovereign_ref=_stable_id(command.payload.get("sovereign_ref"),"governance_sovereign_invalid")
            administration_ref=_stable_id(command.payload.get("administration_ref"),"governance_administration_invalid")
            authority_basis=self._civil_authority(command.actor_id,sovereign_ref)
            # Administration must either be the sovereign itself or an owner the actor also leads.
            if administration_ref!=sovereign_ref: self._civil_authority(command.actor_id,administration_ref)
            visibility=self._visibility(command.payload.get("visibility")); garrison=command.payload.get("garrison_force_ref")
            if garrison is not None:
                garrison=_stable_id(garrison,"governance_garrison_invalid",prefix="force.")
            if action=="establish_administration":
                place_ref=_stable_id(command.payload.get("place_ref"),"governance_place_invalid",prefix="place.")
                graph=self._location_graph(); place=graph.place(place_ref)
                if not isinstance(place,Mapping): raise CommandRejectedError("governance_place_invalid")
                occupation=self._occupation_basis(place_ref,sovereign_ref)
                existing_authority=place.get("authority_ref")
                if occupation is None and existing_authority not in (sovereign_ref,administration_ref): raise CommandRejectedError("governance_control_basis_required")
                if occupation: basis_refs.append(occupation)
                if garrison is not None:
                    self._governance_garrison_force(
                        actor_ref=command.actor_id,
                        force_ref=garrison,
                        sovereign_ref=sovereign_ref,
                        administration_ref=administration_ref,
                        presence_place_ref=place_ref,
                    )
                jurisdictions[jurisdiction_ref]={"id":jurisdiction_ref,"place_ref":place_ref,"sovereign_ref":sovereign_ref,"administration_ref":administration_ref,"status":"military_administration","established_at":str(current_time),"updated_at":str(current_time),"population_pool_ref":None,"treasury_holder_ref":None,"recruitment_rights":False,"tax_milli":0,"integration_milli":50 if occupation else 250,"resistance_milli":500 if occupation else 100,"garrison_force_ref":garrison,"recognition_agreement_refs":[],"parent_jurisdiction_ref":None,"basis_refs":sorted(set(basis_refs)),"visibility":visibility}
            else:
                place_ref=_stable_id(command.payload.get("place_ref"),"governance_place_invalid",prefix="place."); base_place_ref=_stable_id(command.payload.get("base_place_ref"),"governance_base_place_invalid",prefix="place.")
                settlement_name=self._bounded_text(command.payload.get("settlement_name"),"governance_settlement_name_invalid",max_len=120)
                parent_ref=command.payload.get("parent_jurisdiction_ref")
                if parent_ref is not None:
                    parent_ref=_stable_id(parent_ref,"governance_parent_invalid",prefix="jurisdiction."); parent=jurisdictions.get(parent_ref)
                    if not isinstance(parent,Mapping) or parent.get("sovereign_ref")!=sovereign_ref: raise CommandRejectedError("governance_parent_invalid")
                    basis_refs.append(parent_ref)
                graph=self._location_graph(); base_place=graph.place(base_place_ref)
                if not isinstance(base_place,Mapping): raise CommandRejectedError("governance_base_place_invalid")
                if parent_ref is None:
                    occupation=self._occupation_basis(base_place_ref,sovereign_ref)
                    if occupation is None and base_place.get("authority_ref") not in (sovereign_ref,administration_ref): raise CommandRejectedError("governance_control_basis_required")
                    if occupation: basis_refs.append(occupation)
                if garrison is not None:
                    # A founding garrison must already be present at the controlled
                    # base/stronghold from which the new settlement is established.
                    self._governance_garrison_force(
                        actor_ref=command.actor_id,
                        force_ref=garrison,
                        sovereign_ref=sovereign_ref,
                        administration_ref=administration_ref,
                        presence_place_ref=base_place_ref,
                    )
                source_pool_id=_stable_id(command.payload.get("source_population_pool_id"),"governance_population_source_invalid",prefix="pool."); resident_pool_id=_stable_id(command.payload.get("resident_pool_id"),"governance_resident_pool_invalid",prefix="pool.")
                count=command.payload.get("initial_population")
                if isinstance(count,bool) or not isinstance(count,int) or count<=0: raise CommandRejectedError("governance_population_count_invalid")
                try: population=copy.deepcopy(self.repository.read_json(_POPULATION_REGISTRY_PATH))
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("governance_population_invalid") from exc
                pools=population.get("pools") if isinstance(population,dict) else None; source_record=pools.get(source_pool_id) if isinstance(pools,dict) else None
                if not isinstance(source_record,dict) or resident_pool_id in pools: raise CommandRejectedError("governance_population_invalid")
                if source_record.get("linked_force_ref") is not None: raise CommandRejectedError("governance_settlement_requires_civilian_source")
                source_owner=source_record.get("owner_ref")
                migration_ref = None
                source_authorized = True
                try:
                    self._civil_authority(command.actor_id, str(source_owner))
                except CommandRejectedError:
                    source_authorized = False
                if not source_authorized:
                    migration_ref = command.payload.get("migration_agreement_ref")
                    if migration_ref is None:
                        raise CommandRejectedError("governance_population_transfer_authority_required")
                    migration_ref = _stable_id(migration_ref,"governance_migration_agreement_invalid",prefix="agreement.")
                    try:
                        diplomacy = self.repository.read_json(_DIPLOMACY_PATH)
                    except (FileNotFoundError,ValueError) as exc:
                        raise CommandRejectedError("diplomacy_registry_invalid") from exc
                    agreement = diplomacy.get("agreements",{}).get(migration_ref) if isinstance(diplomacy,Mapping) else None
                    parties = agreement.get("party_refs",[]) if isinstance(agreement,Mapping) else []
                    if (
                        not isinstance(agreement,Mapping)
                        or agreement.get("status") != "active"
                        or agreement.get("agreement_type") != "migration"
                        or sovereign_ref not in parties
                        or source_owner not in parties
                    ):
                        raise CommandRejectedError("governance_migration_agreement_invalid")
                    basis_refs.append(migration_ref)
                rep=source_record.get("representation")
                if not isinstance(rep,dict) or not isinstance(rep.get("anonymous_count"),int) or rep["anonymous_count"]<count: raise CommandRejectedError("governance_population_insufficient")
                source=self._pool_view(source_pool_id,source_record); selected=neutral_proportional_selection(source,count)
                zero_dims={dim:{cat:0 for cat in cats} for dim,cats in source.dimensions.items()}; destination=PopulationPool(resident_pool_id,0,zero_dims); transfer=PopulationTransfer(f"settlement.{command.digest[:20]}",source_pool_id,resident_pool_id,count,selected,"neutral_proportional"); source_after,destination_after=apply_transfer(source,destination,transfer)
                source_record["count"]=source_after.total; source_record["profile"]["dimension_counts"]={k:dict(v) for k,v in source_after.dimensions.items()}; source_record["profile"]["category_counts"]={str(source_record.get("category","population")):source_after.total}; source_record["last_changed_at"]=str(current_time); source_record["representation"]["anonymous_count"]-=count
                numeric={}
                for key,row in (source_record.get("profile",{}).get("numeric_distributions",{}) or {}).items():
                    if isinstance(row,Mapping):
                        source_nr=dict(row); source_nr["count"]=source_after.total; source_record["profile"]["numeric_distributions"][key]=source_nr
                        nr=dict(row); nr["count"]=count; numeric[key]=nr
                pools[resident_pool_id]={"owner_ref":administration_ref,"category":"settlement_resident","count":destination_after.total,"status":"active","provenance":f"governance_settlement_transfer:{source_pool_id}","profile":{"numeric_distributions":numeric,"category_counts":{"settlement_resident":count},"dimension_counts":{k:dict(v) for k,v in destination_after.dimensions.items()},"tags":["aggregate_population","governed_settlement"]},"last_changed_at":str(current_time),"representation":{"anonymous_count":count,"rostered_count":0,"rostered_person_refs":[]}}
                population.setdefault("transfers",[]).append({"id":transfer.transfer_id,"at":str(current_time),"source_pool_id":source_pool_id,"destination_ref":resident_pool_id,"requested_count":count,"accepted":count,"rejected":0,"authority_ref":command.actor_id,"authority_basis":authority_basis,"policy_ref":None,"method":"governance_settlement_transfer","accepted_profile":{"numeric_distributions":numeric,"category_counts":{"settlement_resident":count},"dimension_counts":{k:dict(v) for k,v in selected.items()},"tags":["settlement_founding"]},"materialized_person_ids":[],"source_removed":count,"destination_added":count,"selection_note":"Existing civilians moved into the new governed settlement; no population was created."})
                treasury_ref=_stable_id(command.payload.get("treasury_holder_ref"),"governance_treasury_invalid")
                try: inventory=copy.deepcopy(self.repository.read_json(_INVENTORY_PATH))
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("inventory_registry_invalid") from exc
                holders=inventory.get("holders") if isinstance(inventory,dict) else None
                if not isinstance(holders,dict): raise CommandRejectedError("inventory_registry_invalid")
                holders.setdefault(treasury_ref,{})
                try: world=copy.deepcopy(self.repository.read_json(_ROUTES_PATH))
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("world_location_registry_invalid") from exc
                places=world.get("payload",{}).get("places") if isinstance(world,dict) else None
                if not isinstance(places,list) or any(isinstance(x,Mapping) and x.get("id")==place_ref for x in places): raise CommandRejectedError("governance_place_conflict")
                anchor=graph.anchor(base_place_ref)
                places.append({"id":place_ref,"name":settlement_name,"country_id":base_place.get("country_id"),"kind":"settlement_outpost","status":"extant","authority_ref":administration_ref,"timeline_status":"current","knowledge_classification":visibility,"provenance":f"governance_founding:{jurisdiction_ref}","route_anchor_ref":anchor})
                places.sort(key=lambda r:str(r.get("id")))
                jurisdictions[jurisdiction_ref]={"id":jurisdiction_ref,"place_ref":place_ref,"sovereign_ref":sovereign_ref,"administration_ref":administration_ref,"status":"outpost","established_at":str(current_time),"updated_at":str(current_time),"population_pool_ref":resident_pool_id,"treasury_holder_ref":treasury_ref,"recruitment_rights":False,"tax_milli":0,"integration_milli":200,"resistance_milli":100,"garrison_force_ref":garrison,"recognition_agreement_refs":[],"parent_jurisdiction_ref":parent_ref,"basis_refs":sorted(set(basis_refs)),"civil_economy":{"last_settled_at":None,"private_economy_holder_ref":None,"migration_source_pool_ref":source_pool_id,"migration_agreement_ref":migration_ref,"workforce_count":0,"resident_count":count,"net_migration_count":0,"gross_activity_ryo":0,"consumption_ryo":0,"surplus_ryo":0,"shortage_ryo":0,"food_security_milli":0,"service_capacity_milli":0,"local_market_milli":0,"attractiveness_milli":0,"infrastructure_pressure_milli":0,"integration_delta_milli":0,"resistance_delta_milli":0,"development_priority":None,"development_need_milli":0,"food_support_milli":0,"infrastructure_capacity_milli":0,"service_investment_milli":0,"civic_investment_ryo":0,"civic_investment_priority":None,"tax_due_ryo":0,"tax_paid_ryo":0,"tax_arrears_ryo":0},"visibility":visibility}
        else:
            row=jurisdictions.get(jurisdiction_ref)
            if not isinstance(row,dict): raise CommandRejectedError("governance_jurisdiction_not_found")
            authority_basis=self._civil_authority(command.actor_id,str(row.get("sovereign_ref"))); visibility=row["visibility"]
            if action=="set_policy":
                recruitment=command.payload.get("recruitment_rights"); tax=command.payload.get("tax_milli")
                if not isinstance(recruitment,bool) or isinstance(tax,bool) or not isinstance(tax,int) or not 0<=tax<=1000: raise CommandRejectedError("governance_policy_invalid")
                if recruitment and row.get("population_pool_ref") is None: raise CommandRejectedError("governance_recruitment_requires_population")
                if tax > 0 and not isinstance(row.get("treasury_holder_ref"), str): raise CommandRejectedError("governance_tax_requires_treasury")
                row["recruitment_rights"]=recruitment; row["tax_milli"]=tax
            elif action=="set_garrison":
                garrison=_stable_id(command.payload.get("garrison_force_ref"),"governance_garrison_invalid",prefix="force.")
                self._governance_garrison_force(
                    actor_ref=command.actor_id,
                    force_ref=garrison,
                    sovereign_ref=str(row.get("sovereign_ref")),
                    administration_ref=str(row.get("administration_ref")),
                    presence_place_ref=str(row.get("place_ref")),
                )
                row["garrison_force_ref"]=garrison
            elif action=="integrate":
                evidence_ref=_stable_id(command.payload.get("evidence_ref"),"governance_evidence_invalid",prefix="event.")
                evidence=self._world_event_by_id(evidence_ref)
                if not isinstance(evidence,Mapping): raise CommandRejectedError("governance_evidence_unresolved")
                if evidence_ref in row.get("basis_refs",[]): raise CommandRejectedError("governance_evidence_already_applied")
                evidence_hosts=set(x for x in evidence.get("host_refs",[]) if isinstance(x,str))
                evidence_actors=set(x for x in evidence.get("actor_refs",[]) if isinstance(x,str))
                evidence_places=set(x for x in evidence.get("place_refs",[]) if isinstance(x,str))
                if (
                    jurisdiction_ref not in evidence_hosts
                    and row.get("sovereign_ref") not in evidence_hosts | evidence_actors
                    and row.get("administration_ref") not in evidence_hosts | evidence_actors
                    and row.get("place_ref") not in evidence_places
                ):
                    raise CommandRejectedError("governance_evidence_irrelevant")
                delta=command.payload.get("delta_milli")
                if isinstance(delta,bool) or not isinstance(delta,int) or not -200<=delta<=200 or delta==0: raise CommandRejectedError("governance_integration_delta_invalid")
                row["integration_milli"]=max(0,min(1000,int(row["integration_milli"])+delta)); row["resistance_milli"]=max(0,min(1000,int(row["resistance_milli"])-delta));
                if evidence_ref not in row["basis_refs"]: row["basis_refs"].append(evidence_ref); row["basis_refs"].sort()
            elif action=="upgrade":
                target=command.payload.get("target_status")
                order=["outpost","settlement","village","hidden_village"]
                if row.get("status") not in order or target not in order or order.index(target)!=order.index(row["status"])+1: raise CommandRejectedError("governance_upgrade_invalid")
                try: rules=self.repository.read_json(_GOVERNANCE_MECHANICS_PATH)
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("governance_mechanics_invalid") from exc
                req=rules.get("status_requirements",{}).get(target) if isinstance(rules,Mapping) else None
                pool_ref=row.get("population_pool_ref")
                try: pop=self.repository.read_json(_POPULATION_REGISTRY_PATH)
                except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("governance_population_invalid") from exc
                pool=pop.get("pools",{}).get(pool_ref) if isinstance(pop,Mapping) else None; population_count=pool.get("count") if isinstance(pool,Mapping) else 0
                if not isinstance(req,Mapping) or not isinstance(population_count,int) or population_count<int(req.get("min_population",0)): raise CommandRejectedError("governance_upgrade_requirements_unmet")
                if req.get("requires_garrison") and not row.get("garrison_force_ref"): raise CommandRejectedError("governance_upgrade_requires_garrison")
                if req.get("requires_recruitment_rights") and row.get("recruitment_rights") is not True: raise CommandRejectedError("governance_upgrade_requires_recruitment_rights")
                if int(row.get("integration_milli",0))<int(req.get("min_integration_milli",0)): raise CommandRejectedError("governance_upgrade_requires_integration")
                recognition=command.payload.get("recognition_agreement_ref")
                if recognition is not None:
                    recognition=_stable_id(recognition,"governance_recognition_invalid",prefix="agreement.")
                    try: dipl=self.repository.read_json(_DIPLOMACY_PATH)
                    except (FileNotFoundError,ValueError) as exc: raise CommandRejectedError("diplomacy_registry_invalid") from exc
                    agreement=dipl.get("agreements",{}).get(recognition) if isinstance(dipl,Mapping) else None
                    if not isinstance(agreement,Mapping) or agreement.get("status")!="active" or agreement.get("agreement_type")!="recognition" or row.get("sovereign_ref") not in agreement.get("party_refs",[]): raise CommandRejectedError("governance_recognition_invalid")
                    if recognition not in row["recognition_agreement_refs"]: row["recognition_agreement_refs"].append(recognition); row["recognition_agreement_refs"].sort()
                row["status"]=target
            else: raise CommandRejectedError("governance_action_invalid")
            row["updated_at"]=str(current_time)
        row=jurisdictions[jurisdiction_ref]
        world_events=self._world_events(); event_id=self._append_semantic_event(world_events,command=command,kind=f"governance_{action}",at=current_time,host_refs=(row["sovereign_ref"],jurisdiction_ref),actor_refs=(command.actor_id,),place_refs=(row["place_ref"],),causal_refs=tuple(row["basis_refs"]),affected_owner_refs=tuple(x for x in (_GOVERNANCE_PATH,_ROUTES_PATH if world is not None else None,_POPULATION_REGISTRY_PATH if population is not None else None,_INVENTORY_PATH if inventory is not None else None) if x),material_consequence_refs=(jurisdiction_ref,f"status:{row['status']}"),classification=row["visibility"],audience_refs=(command.actor_id,),reducer_ref="shinobi_runtime.commands.domains.civil_state.governance_resolution")
        writes={self.meta_path:_json_bytes(self._meta_after(meta,command,world_time=current_time)),_GOVERNANCE_PATH:_json_bytes(registry),**self._world_event_writes(world_events)}
        if world is not None:writes[_ROUTES_PATH]=_json_bytes(world)
        if population is not None:writes[_POPULATION_REGISTRY_PATH]=_json_bytes(population)
        if inventory is not None:writes[_INVENTORY_PATH]=_json_bytes(inventory)
        writes=self._prune_noop_writes(writes); expected=tuple(sorted(writes))
        def validate(overlay:StagedOverlay,manifest:TransactionManifest)->None:
            if overlay.changed_paths!=expected: raise ValueError("governance write set changed")
            self._assert_meta(overlay,manifest,meta_path=self.meta_path,command=command,world_time=current_time)
            staged=overlay.read_json(_GOVERNANCE_PATH)["jurisdictions"][jurisdiction_ref]
            if action=="found_settlement":
                pool=overlay.read_json(_POPULATION_REGISTRY_PATH)["pools"][staged["population_pool_ref"]]
                if pool["count"]<=0: raise ValueError("founded settlement has no conserved residents")
                if not any(p.get("id")==staged["place_ref"] for p in overlay.read_json(_ROUTES_PATH)["payload"]["places"]): raise ValueError("founded settlement place missing")
        return _BuiltPlan(code="governance_resolution_ready",affected_refs=expected,writes=writes,result={"command_type":command.command_type,"action":action,"jurisdiction_ref":jurisdiction_ref,"place_ref":row["place_ref"],"status":row["status"],"population_pool_ref":row["population_pool_ref"],"recruitment_rights":row["recruitment_rights"],"authority_basis":authority_basis,"semantic_event_id":event_id},validator=validate)
