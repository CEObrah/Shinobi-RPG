"""Discover executable training model IDs through the command contract."""
from __future__ import annotations

from functools import wraps
from typing import Any, Mapping

from shinobi_runtime.api.operations import OperationError

_INSTALLED = False
_MODELS_PATH = "game/rules/training/models.json"


def _training_model_guidance(operations: Any) -> Mapping[str, Any]:
    try:
        registry = operations.repository.read_json(_MODELS_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise OperationError(503, "training_model_discovery_invalid") from exc
    models = registry.get("models") if isinstance(registry, Mapping) else None
    if (
        not isinstance(registry, Mapping)
        or registry.get("schema") != "training-model-registry"
        or not isinstance(models, Mapping)
        or len(models) > 32
    ):
        raise OperationError(503, "training_model_discovery_invalid")
    projected: dict[str, dict[str, Any]] = {}
    for model_ref, model in sorted(models.items()):
        if (
            not isinstance(model_ref, str)
            or not model_ref.startswith("training.")
            or not isinstance(model, Mapping)
        ):
            raise OperationError(503, "training_model_discovery_invalid")
        context_kind = model.get("context_kind")
        requires_instructor = model.get("requires_instructor")
        if (
            context_kind not in ("individual", "exact_team", "cohort")
            or not isinstance(requires_instructor, bool)
        ):
            raise OperationError(503, "training_model_discovery_invalid")
        projected[model_ref] = {
            "context_kind": context_kind,
            "requires_instructor": requires_instructor,
        }
    return {
        "model_ref": {
            "allowed_values": list(projected),
            "models": projected,
            "rule": "Use one listed model_ref exactly; do not invent model IDs.",
        },
        "self_directed": {
            "model_ref": "training.self_directed",
            "context_ref": None,
            "instructor_ref": None,
            "rule": "Use for the player character's separate private individual practice when no instructor or exact-team session owns the activity.",
        },
        "context_ref": {
            "rule": "Required only when the selected model declares an exact_team or cohort context; individual training must omit it."
        },
        "instructor_ref": {
            "rule": "Required only when the selected model declares requires_instructor=true; otherwise omit it."
        },
    }


def install_player_training_model_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from shinobi_runtime.api import campaign_stable_operations as module

    cls = module.RouteAwareCampaignOperations
    original = cls.command_contract
    if getattr(original, "_training_model_projection", False):
        _INSTALLED = True
        return

    @wraps(original)
    def command_contract(self: Any, command_type: str) -> Mapping[str, Any]:
        result = dict(original(self, command_type))
        if command_type != "training_resolution":
            return result
        guidance = result.get("input_guidance")
        updated = dict(guidance) if isinstance(guidance, Mapping) else {}
        updated["training_models"] = _training_model_guidance(self)
        result["input_guidance"] = updated
        return result

    command_contract._training_model_projection = True  # type: ignore[attr-defined]
    cls.command_contract = command_contract
    _INSTALLED = True


__all__ = ["install_player_training_model_projection"]
