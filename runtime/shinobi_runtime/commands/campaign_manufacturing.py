"""Conserved institutional workshop manufacturing and standing production.

Workshop capacity is not finished equipment. This extension registers a bounded
manufacturing command for standing schedules and settles those schedules through
ordinary campaign time advancement. Every completed batch requires a registered
production line, elapsed institutional labor, conserved procurement currency,
and a stock owner that already tracks the finished item.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping, Optional

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.campaign_intake_profiles import CampaignCommandPlanner as _Base
from shinobi_runtime.commands.core import (
    _BuiltPlan, _campaign_datetime, _declared_payload, _json_bytes, _stable_id,
)
from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.commands.paths import (
    DOMAIN_REGISTRY_PATH,
    INVENTORY_REGISTRY_PATH,
    INSTITUTION_PROJECT_MECHANICS_PATH,
    ROUTES_PATH,
)
from shinobi_runtime.commands.specs import COMMAND_SPECS, CommandSpec
from shinobi_runtime.sim.events import CampaignTime
from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.tx.manifest import TransactionManifest

_WEEK_SECONDS = 7 * 24 * 60 * 60


def _install_manufacturing_command_spec() -> None:
    COMMAND_SPECS.setdefault(
        "institution_manufacturing_resolution",
        CommandSpec(
            ("action", "institution_ref", "summary", "visibility"),
            ("schedule_ref", "recipe_ref", "place_ref", "stock_ref", "research_project_ref"),
            "Create or cancel one conserved standing institutional manufacturing schedule, enforcing approved research when a recipe requires it.",
        ),
    )


_install_manufacturing_command_spec()


def _plan_json(plan: Optional[_BuiltPlan], repository: Any, path: str) -> Any:
    raw = plan.writes.get(path) if plan is not None else None
    if raw is None:
        return copy.deepcopy(repository.read_json(path))
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise CommandRejectedError("institution_manufacturing_state_invalid") from exc


class _BasePlanOverlay:
    """Present a composed final overlay as the original nested base plan."""

    def __init__(self, overlay: Any, base: _BuiltPlan):
        self._overlay = overlay
        self._base = base
        self.changed_paths = tuple(sorted(base.writes))

    def read_json(self, path: str) -> Any:
        raw = self._base.writes.get(path)
        if raw is None:
            return self._overlay.read_json(path)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError("base manufacturing overlay decode failed") from exc

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


def _summary_and_visibility(payload: Mapping[str, Any]) -> tuple[str, str]:
    summary = payload.get("summary")
    visibility = payload.get("visibility")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise CommandRejectedError("institution_manufacturing_summary_invalid")
    if visibility not in ("public", "restricted", "secret"):
        raise CommandRejectedError("institution_manufacturing_visibility_invalid")
    return summary.strip(), visibility


class CampaignCommandPlanner(_Base):
    """Production planner with conserved standing workshop output."""

    COMMAND_TYPES = frozenset(COMMAND_SPECS)

    def _manufacturing_mechanics(self) -> tuple[Mapping[str, Any], Mapping[str, Any], int]:
        try:
            mechanics = self.repository.read_json(INSTITUTION_PROJECT_MECHANICS_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_manufacturing_mechanics_invalid") from exc
        recipes = mechanics.get("manufacturing_recipes") if isinstance(mechanics, Mapping) else None
        schedule = mechanics.get("manufacturing_schedule") if isinstance(mechanics, Mapping) else None
        weekly_hours = schedule.get("standing_weekly_active_hours") if isinstance(schedule, Mapping) else None
        max_hours = schedule.get("max_weekly_active_hours") if isinstance(schedule, Mapping) else None
        if (
            not isinstance(recipes, Mapping)
            or not recipes
            or isinstance(weekly_hours, bool)
            or not isinstance(weekly_hours, int)
            or isinstance(max_hours, bool)
            or not isinstance(max_hours, int)
            or not 0 < weekly_hours <= max_hours <= 48
        ):
            raise CommandRejectedError("institution_manufacturing_mechanics_invalid")
        return mechanics, recipes, weekly_hours

    @staticmethod
    def _manufacturing_recipe(recipes: Mapping[str, Any], recipe_ref: object) -> tuple[str, Mapping[str, Any]]:
        ref = _stable_id(recipe_ref, "institution_manufacturing_recipe_invalid", prefix="manufacturing.")
        recipe = recipes.get(ref)
        if not isinstance(recipe, Mapping):
            raise CommandRejectedError("institution_manufacturing_recipe_invalid")
        output = recipe.get("output_item_ref")
        output_quantity = recipe.get("output_quantity_per_batch")
        module_kind = recipe.get("required_module_kind")
        line_field = recipe.get("production_line_field")
        hours = recipe.get("active_hours_per_batch")
        cost = recipe.get("procurement_cost_ryo_per_batch")
        scope = recipe.get("authority_scope_ref")
        if (
            not isinstance(output, str)
            or not output
            or isinstance(output_quantity, bool)
            or not isinstance(output_quantity, int)
            or output_quantity <= 0
            or not isinstance(module_kind, str)
            or not module_kind
            or not isinstance(line_field, str)
            or not line_field
            or isinstance(hours, bool)
            or not isinstance(hours, int)
            or hours <= 0
            or isinstance(cost, bool)
            or not isinstance(cost, int)
            or cost <= 0
            or not isinstance(scope, str)
            or not scope
        ):
            raise CommandRejectedError("institution_manufacturing_mechanics_invalid")
        return ref, recipe

    def _manufacturing_facility_lines(
        self,
        *,
        place_ref: str,
        recipe: Mapping[str, Any],
        base: Optional[_BuiltPlan] = None,
    ) -> int:
        try:
            routes = _plan_json(base, self.repository, ROUTES_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_manufacturing_place_invalid") from exc
        payload = routes.get("payload") if isinstance(routes, Mapping) else None
        places = payload.get("places") if isinstance(payload, Mapping) else None
        matches = [row for row in places or [] if isinstance(row, Mapping) and row.get("id") == place_ref]
        if len(matches) != 1:
            raise CommandRejectedError("institution_manufacturing_place_invalid")
        modules = matches[0].get("mechanical_modules")
        module = modules.get(recipe.get("required_module_kind")) if isinstance(modules, Mapping) else None
        line_field = recipe.get("production_line_field")
        lines = module.get(line_field) if isinstance(module, Mapping) and isinstance(line_field, str) else None
        if isinstance(lines, bool) or not isinstance(lines, int) or lines <= 0:
            raise CommandRejectedError("institution_manufacturing_facility_unavailable")
        return lines

    def _institution_manufacturing_resolution(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        _declared_payload(command.payload, command.command_type)
        action = command.payload.get("action")
        if action not in ("schedule", "cancel"):
            raise CommandRejectedError("institution_manufacturing_action_invalid")
        institution_ref = _stable_id(
            command.payload.get("institution_ref"),
            "institution_manufacturing_institution_invalid",
        )
        summary, visibility = _summary_and_visibility(command.payload)
        _mechanics, recipes, _weekly_hours = self._manufacturing_mechanics()
        try:
            registry = copy.deepcopy(self.repository.read_json(DOMAIN_REGISTRY_PATH))
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_manufacturing_registry_invalid") from exc
        projects = registry.get("projects") if isinstance(registry, dict) else None
        if not isinstance(projects, list):
            raise CommandRejectedError("institution_manufacturing_registry_invalid")

        if action == "schedule":
            if command.payload.get("schedule_ref") is not None:
                raise CommandRejectedError("institution_manufacturing_fields_invalid")
            recipe_ref, recipe = self._manufacturing_recipe(recipes, command.payload.get("recipe_ref"))
            research_raw = command.payload.get("research_project_ref")
            research_project_ref = None if research_raw is None else _stable_id(
                research_raw, "institution_manufacturing_research_invalid", prefix="research."
            )
            if recipe.get("requires_research_approval") is True or research_project_ref is not None:
                if research_project_ref is None:
                    raise CommandRejectedError("institution_manufacturing_research_required")
                try:
                    research = self.repository.read_json("state/reg/research.json")
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("institution_manufacturing_research_invalid") from exc
                project = research.get("projects", {}).get(research_project_ref) if isinstance(research, Mapping) else None
                if (
                    not isinstance(project, Mapping)
                    or project.get("institution_ref") != institution_ref
                    or project.get("status") != "succeeded"
                    or project.get("prototype_status") != "approved"
                    or project.get("candidate_kind") != "manufacturing_recipe"
                    or project.get("candidate_ref") != recipe_ref
                ):
                    raise CommandRejectedError("institution_manufacturing_research_not_approved")
            place_ref = _stable_id(
                command.payload.get("place_ref"),
                "institution_manufacturing_place_invalid",
                prefix="place.",
            )
            stock_ref = _stable_id(
                command.payload.get("stock_ref"),
                "institution_manufacturing_stock_invalid",
                prefix="stock.",
            )
            self._require_growth_scope(
                command=command,
                institution_ref=institution_ref,
                scope_ref=str(recipe["authority_scope_ref"]),
            )
            self._manufacturing_facility_lines(place_ref=place_ref, recipe=recipe)
            stock_path, stock, stock_owner = self._stock_record(stock_ref)
            if stock_owner != institution_ref and not self._inventory_holder_authorized(command.actor_id, stock_owner):
                raise CommandRejectedError("institution_manufacturing_stock_not_authorized")
            self._stock_item_key(stock, str(recipe["output_item_ref"]))
            _economy, finance = self._economy_world()
            funding_holder_ref = self._funding_holder_for(institution_ref, finance=finance)
            try:
                inventory = self.repository.read_json(INVENTORY_REGISTRY_PATH)
            except (FileNotFoundError, ValueError) as exc:
                raise CommandRejectedError("institution_manufacturing_funding_invalid") from exc
            holders = inventory.get("holders") if isinstance(inventory, Mapping) else None
            if not isinstance(holders, Mapping) or not isinstance(holders.get(funding_holder_ref), Mapping):
                raise CommandRejectedError("institution_manufacturing_funding_invalid")
            if any(
                isinstance(row, Mapping)
                and row.get("kind") == "institution_manufacturing_schedule"
                and row.get("status") == "active"
                and row.get("institution_ref") == institution_ref
                and row.get("project_type") == recipe_ref
                and row.get("subject_ref") == place_ref
                and row.get("stock_ref") == stock_ref
                for row in projects
            ):
                raise CommandRejectedError("institution_manufacturing_duplicate_active")
            schedule_ref = f"project.manufacturing.{command.digest[:24]}"
            projects.append({
                "id": schedule_ref,
                "kind": "institution_manufacturing_schedule",
                "status": "active",
                "subject_ref": place_ref,
                "authority_ref": command.actor_id,
                "opened_at": str(current_time),
                "completed_at": None,
                "next_due_at": str(current_time.add_seconds(_WEEK_SECONDS)),
                "result": summary,
                "project_type": recipe_ref,
                "institution_ref": institution_ref,
                "stock_ref": stock_ref,
                "research_project_ref": research_project_ref,
                "module_kind": recipe["required_module_kind"],
                "last_advanced_at": str(current_time),
                # Exact partition-invariant work representation: elapsed seconds
                # are multiplied by weekly active hours and production lines;
                # one batch costs batch-hours * seconds-per-week units.
                "required_work_units": int(recipe["active_hours_per_batch"]) * _WEEK_SECONDS,
                "progress_units": 0,
                "work_units_per_active_hour": _WEEK_SECONDS,
                "resource_costs": {},
                "funding_holder_ref": funding_holder_ref,
                "currency_cost_ryo": int(recipe["procurement_cost_ryo_per_batch"]),
            })
        else:
            if any(command.payload.get(key) is not None for key in ("recipe_ref", "place_ref", "stock_ref", "research_project_ref")):
                raise CommandRejectedError("institution_manufacturing_fields_invalid")
            schedule_ref = _stable_id(
                command.payload.get("schedule_ref"),
                "institution_manufacturing_schedule_invalid",
                prefix="project.manufacturing.",
            )
            matches = [row for row in projects if isinstance(row, dict) and row.get("id") == schedule_ref]
            if len(matches) != 1:
                raise CommandRejectedError("institution_manufacturing_schedule_unresolved")
            schedule = matches[0]
            if (
                schedule.get("kind") != "institution_manufacturing_schedule"
                or schedule.get("status") != "active"
                or schedule.get("institution_ref") != institution_ref
            ):
                raise CommandRejectedError("institution_manufacturing_schedule_unresolved")
            _recipe_ref, recipe = self._manufacturing_recipe(recipes, schedule.get("project_type"))
            self._require_growth_scope(
                command=command,
                institution_ref=institution_ref,
                scope_ref=str(recipe["authority_scope_ref"]),
            )
            schedule["status"] = "cancelled"
            schedule["completed_at"] = str(current_time)
            schedule["next_due_at"] = None
            schedule["result"] = summary

        world_events = self._world_events()
        event_id = self._append_semantic_event(
            world_events,
            command=command,
            kind="institution_manufacturing_schedule_changed",
            at=current_time,
            host_refs=(institution_ref,),
            actor_refs=(command.actor_id,),
            affected_owner_refs=(DOMAIN_REGISTRY_PATH,),
            material_consequence_refs=(f"schedule:{schedule_ref}", f"action:{action}"),
            classification=visibility,
            audience_refs=(command.actor_id,),
            reducer_ref="shinobi_runtime.commands.institution_manufacturing_resolution",
        )
        writes: Dict[str, bytes] = {
            self.meta_path: _json_bytes(self._meta_after(meta, command, world_time=current_time)),
            DOMAIN_REGISTRY_PATH: _json_bytes(registry),
        }
        writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("institution manufacturing write set changed after planning")
            self._assert_meta(
                overlay,
                manifest,
                meta_path=self.meta_path,
                command=command,
                world_time=current_time,
            )
            if overlay.read_json(DOMAIN_REGISTRY_PATH) != registry:
                raise ValueError("institution manufacturing registry after-image mismatch")

        return _BuiltPlan(
            code="institution_manufacturing_resolution_ready",
            affected_refs=expected,
            writes=writes,
            result={
                "command_type": command.command_type,
                "action": action,
                "institution_ref": institution_ref,
                "schedule_ref": schedule_ref,
                "semantic_event_id": event_id,
            },
            validator=validate,
        )

    def _advance_time(
        self,
        command: CommandEnvelope,
        meta: Mapping[str, Any],
        current_time: CampaignTime,
    ) -> _BuiltPlan:
        base = super()._advance_time(command, meta, current_time)
        try:
            through = CampaignTime.parse(base.result.get("world_time"))
        except (TypeError, ValueError) as exc:
            raise CommandRejectedError("institution_manufacturing_time_invalid") from exc
        _mechanics, recipes, weekly_hours = self._manufacturing_mechanics()
        try:
            registry = _plan_json(base, self.repository, DOMAIN_REGISTRY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("institution_manufacturing_registry_invalid") from exc
        projects = registry.get("projects") if isinstance(registry, dict) else None
        if not isinstance(projects, list):
            raise CommandRejectedError("institution_manufacturing_registry_invalid")
        active = [
            row for row in projects
            if isinstance(row, dict)
            and row.get("kind") == "institution_manufacturing_schedule"
            and row.get("status") == "active"
        ]
        if not active:
            return base

        inventory: Optional[Dict[str, Any]] = None
        holders: Optional[Dict[str, Any]] = None
        stock_cache: Dict[str, tuple[str, Dict[str, Any]]] = {}
        settlements: list[Mapping[str, Any]] = []
        material: list[str] = []
        touched = False

        for schedule in sorted(active, key=lambda row: str(row.get("id"))):
            try:
                last = CampaignTime.parse(schedule.get("last_advanced_at"))
            except (TypeError, ValueError) as exc:
                raise CommandRejectedError("institution_manufacturing_schedule_invalid") from exc
            if through <= last:
                continue
            recipe_ref, recipe = self._manufacturing_recipe(recipes, schedule.get("project_type"))
            place_ref = schedule.get("subject_ref")
            institution_ref = schedule.get("institution_ref")
            authority_ref = schedule.get("authority_ref")
            stock_ref = schedule.get("stock_ref")
            funding_holder_ref = schedule.get("funding_holder_ref")
            batch_cost = schedule.get("currency_cost_ryo")
            required_work = schedule.get("required_work_units")
            progress = schedule.get("progress_units")
            if (
                not isinstance(place_ref, str)
                or not isinstance(institution_ref, str)
                or not isinstance(authority_ref, str)
                or not isinstance(stock_ref, str)
                or not isinstance(funding_holder_ref, str)
                or isinstance(batch_cost, bool)
                or not isinstance(batch_cost, int)
                or batch_cost <= 0
                or isinstance(required_work, bool)
                or not isinstance(required_work, int)
                or required_work <= 0
                or isinstance(progress, bool)
                or not isinstance(progress, int)
                or progress < 0
            ):
                raise CommandRejectedError("institution_manufacturing_schedule_invalid")
            try:
                lines = self._manufacturing_facility_lines(
                    place_ref=place_ref,
                    recipe=recipe,
                    base=base,
                )
            except CommandRejectedError as exc:
                if exc.code != "institution_manufacturing_facility_unavailable":
                    raise
                schedule["status"] = "failed"
                schedule["completed_at"] = str(through)
                schedule["next_due_at"] = None
                schedule["last_advanced_at"] = str(through)
                schedule["result"] = "Standing manufacturing stopped because its registered production line is unavailable."
                settlements.append({
                    "schedule_ref": schedule.get("id"),
                    "recipe_ref": recipe_ref,
                    "status": "failed_facility_unavailable",
                    "batches": 0,
                })
                material.append(f"manufacturing_failed:{schedule.get('id')}:facility")
                touched = True
                continue

            elapsed_seconds = int(
                (_campaign_datetime(through) - _campaign_datetime(last)).total_seconds()
            )
            if elapsed_seconds <= 0:
                continue
            total_work = progress + elapsed_seconds * weekly_hours * lines
            capacity_batches = total_work // required_work

            if inventory is None:
                loaded = _plan_json(base, self.repository, INVENTORY_REGISTRY_PATH)
                inventory = copy.deepcopy(loaded) if isinstance(loaded, dict) else None
                holders = inventory.get("holders") if isinstance(inventory, dict) else None
                if not isinstance(holders, dict):
                    raise CommandRejectedError("institution_manufacturing_funding_invalid")
            funding = holders.get(funding_holder_ref) if isinstance(holders, dict) else None
            contractors = holders.setdefault("economy.contractors", {}) if isinstance(holders, dict) else None
            if not isinstance(funding, dict) or not isinstance(contractors, dict):
                raise CommandRejectedError("institution_manufacturing_funding_invalid")
            balance = funding.get("currency.ryo", 0)
            contractor_balance = contractors.get("currency.ryo", 0)
            if (
                isinstance(balance, bool)
                or not isinstance(balance, int)
                or balance < 0
                or isinstance(contractor_balance, bool)
                or not isinstance(contractor_balance, int)
                or contractor_balance < 0
            ):
                raise CommandRejectedError("institution_manufacturing_funding_invalid")
            affordable_batches = balance // batch_cost
            batches = min(capacity_batches, affordable_batches)
            output_quantity = batches * int(recipe["output_quantity_per_batch"])

            if stock_ref not in stock_cache:
                stock_path, repository_stock, stock_owner = self._stock_record(stock_ref)
                if stock_owner != institution_ref and not self._inventory_holder_authorized(authority_ref, stock_owner):
                    raise CommandRejectedError("institution_manufacturing_stock_not_authorized")
                planned_stock = _plan_json(base, self.repository, stock_path)
                stock = copy.deepcopy(planned_stock) if isinstance(planned_stock, dict) else None
                if not isinstance(stock, dict):
                    raise CommandRejectedError("institution_manufacturing_stock_invalid")
                stock_cache[stock_ref] = (stock_path, stock)
            stock_path, stock = stock_cache[stock_ref]
            container, key = self._stock_item_key(stock, str(recipe["output_item_ref"]))
            current_stock = container.get(key)
            if isinstance(current_stock, bool) or not isinstance(current_stock, int) or current_stock < 0:
                raise CommandRejectedError("institution_manufacturing_stock_invalid")

            if batches > 0:
                total_cost = batches * batch_cost
                funding["currency.ryo"] = balance - total_cost
                contractors["currency.ryo"] = contractor_balance + total_cost
                container[key] = current_stock + output_quantity
                material.extend((
                    f"manufactured:{recipe['output_item_ref']}:{output_quantity}",
                    f"procurement:{funding_holder_ref}->economy.contractors:{total_cost}ryo",
                ))

            # Preserve only sub-batch work. Whole unaffordable batches are not
            # banked as future catch-up debt.
            schedule["progress_units"] = total_work % required_work
            schedule["last_advanced_at"] = str(through)
            schedule["next_due_at"] = str(through.add_seconds(_WEEK_SECONDS))
            schedule["result"] = (
                f"Standing manufacturing settled through {through}: {batches} batch(es), "
                f"{output_quantity} {recipe['output_item_ref']}."
            )
            settlements.append({
                "schedule_ref": schedule.get("id"),
                "recipe_ref": recipe_ref,
                "status": "active",
                "batches": batches,
                "output_item_ref": recipe["output_item_ref"],
                "output_quantity": output_quantity,
                "line_count": lines,
            })
            touched = True

        if not touched:
            return base

        world_events = self._world_events_after(base)
        event_id = None
        if material:
            event_id = self._append_semantic_event(
                world_events,
                command=command,
                kind="institution_manufacturing_settled",
                at=through,
                host_refs=tuple(sorted({
                    str(row.get("institution_ref"))
                    for row in active
                    if isinstance(row.get("institution_ref"), str)
                })),
                actor_refs=(),
                affected_owner_refs=tuple(sorted({
                    DOMAIN_REGISTRY_PATH,
                    *(
                        path for path, _stock in stock_cache.values()
                    ),
                    *(() if inventory is None else (INVENTORY_REGISTRY_PATH,)),
                })),
                material_consequence_refs=tuple(material),
                classification="restricted",
                audience_refs=(command.actor_id,),
                reducer_ref="shinobi_runtime.commands.institution_manufacturing_settlement",
            )

        writes: Dict[str, bytes] = dict(base.writes)
        writes[DOMAIN_REGISTRY_PATH] = _json_bytes(registry)
        if inventory is not None:
            writes[INVENTORY_REGISTRY_PATH] = _json_bytes(inventory)
        for stock_path, stock in stock_cache.values():
            writes[stock_path] = _json_bytes(stock)
        if event_id is not None:
            writes.update(self._world_event_writes(world_events))
        writes = self._prune_noop_writes(writes)
        expected = tuple(sorted(writes))
        base_validator = base.validator

        def validate(overlay: StagedOverlay, manifest: TransactionManifest) -> None:
            if overlay.changed_paths != expected:
                raise ValueError("institution manufacturing time write set changed after planning")
            base_validator(_BasePlanOverlay(overlay, base), manifest)
            if overlay.read_json(DOMAIN_REGISTRY_PATH) != registry:
                raise ValueError("standing manufacturing registry after-image mismatch")
            if inventory is not None and overlay.read_json(INVENTORY_REGISTRY_PATH) != inventory:
                raise ValueError("standing manufacturing funding after-image mismatch")
            for stock_path, stock in stock_cache.values():
                if overlay.read_json(stock_path) != stock:
                    raise ValueError("standing manufacturing stock after-image mismatch")

        result = dict(base.result)
        result["institution_manufacturing_settlements"] = [dict(row) for row in settlements]
        result["institution_manufacturing_event_id"] = event_id
        return _BuiltPlan(
            code=base.code,
            affected_refs=expected,
            writes=writes,
            result=result,
            validator=validate,
        )


__all__ = ["CampaignCommandPlanner", "_install_manufacturing_command_spec"]
