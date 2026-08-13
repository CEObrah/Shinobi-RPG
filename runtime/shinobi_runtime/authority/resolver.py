"""Exact template, blank-owner, and system-contract resolution.

This module follows the maintenance boundary in ``RUNTIME.md``.  It never
infers shape from an existing owner or neighboring file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple


class AuthorityError(RuntimeError):
    pass


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuthorityError(f"{name} must be non-empty text")
    return value


@dataclass(frozen=True)
class StructuralAuthority:
    target_schema: str
    template_path: str
    template: Mapping[str, Any]
    blank_path: str
    blank: Mapping[str, Any]
    contract_path: str
    contract: Mapping[str, Any]
    source_schema_path: Optional[str]
    source_schema: Optional[Mapping[str, Any]]

    @property
    def validators(self) -> Tuple[str, ...]:
        validators = self.contract.get("validators", [])
        return tuple(validators)


class StructuralResolver:
    def __init__(self, repository_root: object) -> None:
        self.root = Path(repository_root).resolve()
        if not self.root.is_dir():
            raise AuthorityError("repository root does not exist")

    def _path(self, relative: str) -> Path:
        value = _text(relative, "registered path")
        path = (self.root / value).resolve(strict=False)
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise AuthorityError(f"registered path escapes repository: {value}") from exc
        return path

    def _json(self, relative: str) -> Mapping[str, Any]:
        path = self._path(relative)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AuthorityError(f"registered authority is missing: {relative}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthorityError(f"registered authority is invalid JSON: {relative}") from exc
        if not isinstance(value, dict):
            raise AuthorityError(f"registered authority must be an object: {relative}")
        return value

    def resolve_template(self, target_schema: str) -> Tuple[str, Mapping[str, Any]]:
        target_schema = _text(target_schema, "target schema")
        index = self._json("runtime/contracts/template-index.json")
        shards = index.get("shards")
        if not isinstance(shards, dict):
            raise AuthorityError("template index has no shard map")
        shard_path = shards.get(target_schema[0].lower())
        if not isinstance(shard_path, str):
            raise AuthorityError(f"no template shard registered for {target_schema}")
        shard = self._json(shard_path)
        entries = shard.get("templates")
        if not isinstance(entries, dict):
            raise AuthorityError(f"template shard has no templates: {shard_path}")
        entry = entries.get(target_schema)
        if not isinstance(entry, dict):
            raise AuthorityError(f"no exact structural template for {target_schema}")
        template_path = _text(entry.get("path"), "template path")
        template = self._json(template_path)
        if template.get("target_schema") != target_schema:
            raise AuthorityError(f"template target mismatch for {target_schema}")
        return template_path, template

    def resolve_blank(self, target_schema: str) -> Tuple[str, Mapping[str, Any]]:
        index = self._json("runtime/contracts/blank-owner-index.json")
        owners = index.get("owners")
        if not isinstance(owners, dict):
            raise AuthorityError("blank owner index has no owner map")
        blank_path = owners.get(target_schema)
        if not isinstance(blank_path, str):
            raise AuthorityError(f"no blank owner registered for {target_schema}")
        return blank_path, self._json(blank_path)

    def resolve_contract(self, system_id: str) -> Tuple[str, Mapping[str, Any]]:
        system_id = _text(system_id, "system id")
        index = self._json("runtime/contracts/system-contract-index.json")
        systems = index.get("systems")
        if not isinstance(systems, dict):
            raise AuthorityError("system contract index has no systems map")
        contract_path = systems.get(system_id)
        if not isinstance(contract_path, str):
            raise AuthorityError(f"no exact system contract for {system_id}")
        contract = self._json(contract_path)
        if contract.get("system_id") != system_id:
            raise AuthorityError(f"system contract identity mismatch for {system_id}")
        return contract_path, contract

    def resolve(self, target_schema: str, system_id: str) -> StructuralAuthority:
        template_path, template = self.resolve_template(target_schema)
        blank_path, blank = self.resolve_blank(target_schema)
        contract_path, contract = self.resolve_contract(system_id)
        owner_templates = contract.get("owner_templates")
        if not isinstance(owner_templates, list) or target_schema not in owner_templates:
            raise AuthorityError(
                f"system {system_id} does not authorize owner template {target_schema}"
            )
        validators = contract.get("validators")
        if not isinstance(validators, list) or not validators:
            raise AuthorityError(f"system {system_id} has no validator stack")
        if any(not isinstance(path, str) or not path for path in validators):
            raise AuthorityError(f"system {system_id} has invalid validator paths")

        source_path = template.get("source_schema")
        source = None
        if source_path is not None:
            if not isinstance(source_path, str):
                raise AuthorityError("template source_schema must be text or null")
            source = self._json(source_path)
        return StructuralAuthority(
            target_schema=target_schema,
            template_path=template_path,
            template=template,
            blank_path=blank_path,
            blank=blank,
            contract_path=contract_path,
            contract=contract,
            source_schema_path=source_path,
            source_schema=source,
        )
