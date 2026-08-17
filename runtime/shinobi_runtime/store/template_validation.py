"""Registered structural-template validation for staged campaign owners."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

from shinobi_runtime.store.overlay import StagedOverlay
from shinobi_runtime.store.repository import RepositoryStore


_TEMPLATE_REASON_PREFIXES = (
    ("registered structural template has invalid requirements", "invalid_template_requirements"),
    ("staged owner is missing required structural key:", "missing_required_key"),
    ("staged owner violates structural type at", "structural_type"),
    ("staged owner has no object contract at", "missing_object_contract"),
    ("registered closed-object contract is invalid", "invalid_object_contract"),
    ("staged owner has unregistered keys at", "unregistered_keys"),
    ("registered object contract has invalid mode", "invalid_object_contract"),
    ("staged owner has no array contract at", "missing_array_contract"),
    ("registered array contract is invalid", "invalid_array_contract"),
    ("registered array item types are invalid", "invalid_array_item_types"),
    ("staged owner violates array item type at", "array_item_type"),
)


class TemplateValidationError(ValueError):
    """Structural validation failure with bounded code-owned diagnostics.

    ``detail`` remains the internal developer-facing error message. ``schema_id``
    and ``reason`` are deliberately limited to routing identity and a static
    structural classifier so callers can expose useful diagnostics without
    leaking staged paths, owner IDs, pointers, keys, or values.
    """

    def __init__(
        self,
        detail: str,
        *,
        schema_id: str | None,
        reason: str,
    ) -> None:
        super().__init__(detail)
        self.schema_id = schema_id
        self.reason = reason


def _template_reason(exc: BaseException) -> str:
    detail = str(exc)
    for prefix, reason in _TEMPLATE_REASON_PREFIXES:
        if detail.startswith(prefix):
            return reason
    return "unknown"


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_allowed(actual: str, allowed: object) -> bool:
    if not isinstance(allowed, list) or not allowed:
        return True
    return actual in allowed or (actual == "integer" and "number" in allowed)


def _child_path(parent: str, key: str) -> str:
    return parent + "/" + key if parent else "/" + key


class RegisteredTemplateValidator:
    """Enforce the repository's exact cold structural templates at runtime.

    JSON Schema and domain validators remain independent layers. Templates own
    closed/open object shape, pointer-specific types, and array item shape;
    enforcing them here prevents an otherwise schema-compatible transaction
    from silently introducing an unregistered owner field.
    """

    def __init__(
        self,
        repository: RepositoryStore,
        *,
        index_path: str = "runtime/contracts/template-index.json",
    ) -> None:
        index = repository.read_json(index_path)
        shards = index.get("shards") if isinstance(index, Mapping) else None
        if not isinstance(shards, Mapping) or not shards:
            raise ValueError("structural template index is missing or invalid")

        templates: Dict[str, Mapping[str, Any]] = {}
        scopes: Dict[str, str] = {}
        for prefix, shard_path in sorted(shards.items()):
            if (
                not isinstance(prefix, str)
                or len(prefix) != 1
                or not isinstance(shard_path, str)
                or not shard_path
            ):
                raise ValueError("structural template shard map is invalid")
            shard = repository.read_json(shard_path)
            entries = shard.get("templates") if isinstance(shard, Mapping) else None
            if not isinstance(entries, Mapping):
                raise ValueError(f"structural template shard is invalid: {shard_path}")
            for schema_id, entry in sorted(entries.items()):
                if (
                    not isinstance(schema_id, str)
                    or not schema_id
                    or schema_id in templates
                    or not isinstance(entry, Mapping)
                ):
                    raise ValueError("structural template entry is invalid")
                template_path = entry.get("path")
                scope = entry.get("scope")
                if (
                    not isinstance(template_path, str)
                    or not template_path
                    or not isinstance(scope, str)
                    or not scope
                ):
                    raise ValueError(
                        f"structural template entry is incomplete: {schema_id}"
                    )
                template = repository.read_json(template_path)
                if (
                    not isinstance(template, Mapping)
                    or template.get("schema") != "file-template"
                    or template.get("target_schema") != schema_id
                    or template.get("scope") != scope
                    or template.get("unknown_key_policy") != "reject"
                    or not isinstance(template.get("object_contracts"), Mapping)
                    or not isinstance(template.get("type_contracts"), Mapping)
                    or not isinstance(template.get("array_contracts"), Mapping)
                ):
                    raise ValueError(
                        f"registered structural template is invalid: {schema_id}"
                    )
                templates[schema_id] = template
                scopes[schema_id] = scope
        self.templates = templates
        self.scopes = scopes

    @classmethod
    def optional(
        cls, repository: RepositoryStore
    ) -> Optional["RegisteredTemplateValidator"]:
        if repository.read_optional_bytes("runtime/contracts/template-index.json") is None:
            return None
        return cls(repository)

    @staticmethod
    def _validate_document(
        value: Mapping[str, Any],
        template: Mapping[str, Any],
        *,
        label: str,
    ) -> None:
        required = template.get("required_top_level_keys", [])
        if not isinstance(required, list) or any(
            not isinstance(key, str) or not key for key in required
        ):
            raise ValueError("registered structural template has invalid requirements")
        for key in required:
            if key not in value:
                raise ValueError(f"staged owner is missing required structural key: {key}")

        object_contracts = template["object_contracts"]
        type_contracts = template["type_contracts"]
        array_contracts = template["array_contracts"]
        stack = [(value, "")]
        while stack:
            current, pointer = stack.pop()
            actual_type = _json_type(current)
            if not _type_allowed(actual_type, type_contracts.get(pointer, [])):
                raise ValueError(
                    f"staged owner violates structural type at {pointer or '/'}"
                )
            if isinstance(current, dict):
                contract = object_contracts.get(pointer)
                if not isinstance(contract, Mapping):
                    raise ValueError(
                        f"staged owner has no object contract at {pointer or '/'}"
                    )
                mode = contract.get("mode")
                if mode == "closed":
                    allowed_keys = contract.get("allowed_keys")
                    if not isinstance(allowed_keys, list) or any(
                        not isinstance(key, str) for key in allowed_keys
                    ):
                        raise ValueError("registered closed-object contract is invalid")
                    extra = sorted(set(current) - set(allowed_keys))
                    if extra:
                        raise ValueError(
                            f"staged owner has unregistered keys at "
                            f"{pointer or '/'}: {extra}"
                        )
                    stack.extend(
                        (child, _child_path(pointer, key))
                        for key, child in current.items()
                    )
                elif mode == "open_map":
                    wildcard = _child_path(pointer, "*")
                    stack.extend((child, wildcard) for child in current.values())
                else:
                    raise ValueError("registered object contract has invalid mode")
            elif isinstance(current, list):
                contract = array_contracts.get(pointer)
                if contract is None:
                    if current:
                        raise ValueError(
                            f"staged owner has no array contract at {pointer or '/'}"
                        )
                    continue
                if not isinstance(contract, Mapping):
                    raise ValueError("registered array contract is invalid")
                item_types = contract.get("item_types")
                if not isinstance(item_types, list) or not item_types:
                    raise ValueError("registered array item types are invalid")
                wildcard = _child_path(pointer, "*")
                for child in current:
                    if not _type_allowed(_json_type(child), item_types):
                        raise ValueError(
                            f"staged owner violates array item type at "
                            f"{pointer or '/'}"
                        )
                    stack.append((child, wildcard))

    def validate_overlay(
        self,
        overlay: StagedOverlay,
        changed_paths: Iterable[str],
    ) -> None:
        for path in sorted(set(changed_paths)):
            if not path.endswith(".json") or overlay.read_optional_bytes(path) is None:
                continue
            value = overlay.read_json(path)
            if not isinstance(value, Mapping):
                if path.startswith("state/"):
                    raise TemplateValidationError(
                        "staged state JSON must be an owner object",
                        schema_id=None,
                        reason="owner_not_object",
                    )
                continue
            schema_id = value.get("schema")
            if not isinstance(schema_id, str):
                if path.startswith("state/"):
                    raise TemplateValidationError(
                        "staged state JSON has no structural template ID",
                        schema_id=None,
                        reason="missing_template_id",
                    )
                continue
            template = self.templates.get(schema_id)
            if template is None:
                if path.startswith("state/"):
                    raise TemplateValidationError(
                        f"staged state owner has no structural template: {schema_id}",
                        schema_id=schema_id,
                        reason="missing_template",
                    )
                continue
            if path.startswith("state/") and self.scopes[schema_id] != "mutable_state":
                raise TemplateValidationError(
                    "staged state owner uses a non-mutable template",
                    schema_id=schema_id,
                    reason="non_mutable_template",
                )
            try:
                self._validate_document(value, template, label=path)
            except ValueError as exc:
                raise TemplateValidationError(
                    str(exc),
                    schema_id=schema_id,
                    reason=_template_reason(exc),
                ) from exc


__all__ = ["RegisteredTemplateValidator", "TemplateValidationError"]
