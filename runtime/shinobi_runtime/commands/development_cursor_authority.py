"""Single-authority normalization for exact-character development cursors.

The shared development bank owns exact-character ``resolved_through`` once a
character has a bank entry. Legacy character records may still carry the older
``development.last_settled_at`` field. Any semantic command that changes a
character bank entry must retire that legacy cursor in the same bounded write
set so future progression has one writable clock.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping

from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.commands.core import _OwnerResolutionCache, _json_bytes
from shinobi_runtime.commands.paths import DEVELOPMENT_BANK_PATH


class _DevelopmentCursorValidationView:
    """Expose pre-normalization cursor shape only to legacy equality validators.

    Schema/template validation and persistence still see the true normalized
    after-image.  This adapter exists for older domain validators that captured a
    full owner dict before ``_prune_noop_writes`` retired
    ``development.last_settled_at`` and then compare the staged owner by exact
    equality.  Re-introducing only that deprecated field for the comparison keeps
    those validators meaningful without undoing single cursor authority.
    """

    def __init__(self, planner: Any, overlay: Any, final_writes: Mapping[str, bytes]):
        self._planner = planner
        self._overlay = overlay
        self._final_writes = final_writes

    @property
    def changed_paths(self):
        return self._overlay.changed_paths

    def read_json(self, path: str) -> Any:
        value = self._overlay.read_json(path)
        raw_final = self._final_writes.get(path)
        if raw_final is None or not isinstance(value, dict):
            return value
        try:
            final_record = json.loads(raw_final.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return value
        if not isinstance(final_record, dict) or final_record.get("schema") != "shinobi_character":
            return value
        final_development = final_record.get("development")
        if not isinstance(final_development, dict) or "last_settled_at" in final_development:
            return value
        try:
            before_record = self._planner.repository.read_json(path)
        except (FileNotFoundError, ValueError):
            return value
        before_development = before_record.get("development") if isinstance(before_record, Mapping) else None
        if not isinstance(before_development, Mapping) or "last_settled_at" not in before_development:
            return value
        restored = copy.deepcopy(value)
        development = restored.get("development")
        if isinstance(development, dict):
            development["last_settled_at"] = before_development["last_settled_at"]
        return restored

    def __getattr__(self, name: str) -> Any:
        return getattr(self._overlay, name)


class DevelopmentCursorAuthorityMixin:
    """Normalize legacy character cursors before reducer write pruning."""

    def _development_cursor_validation_view(
        self,
        overlay: Any,
        final_writes: Mapping[str, bytes],
    ) -> Any:
        return _DevelopmentCursorValidationView(self, overlay, final_writes)

    def _prune_noop_writes(self, writes: Mapping[str, bytes]) -> Dict[str, bytes]:
        normalized = dict(writes)
        raw_after = normalized.get(DEVELOPMENT_BANK_PATH)
        if raw_after is None:
            return super()._prune_noop_writes(normalized)

        try:
            after_bank = json.loads(raw_after.decode("utf-8"))
            before_bank = self.repository.read_json(DEVELOPMENT_BANK_PATH)
        except (UnicodeDecodeError, json.JSONDecodeError, FileNotFoundError, ValueError) as exc:
            raise CommandRejectedError("development_bank_invalid") from exc
        after_entries = after_bank.get("entries") if isinstance(after_bank, Mapping) else None
        before_entries = before_bank.get("entries") if isinstance(before_bank, Mapping) else None
        if not isinstance(after_entries, Mapping) or not isinstance(before_entries, Mapping):
            raise CommandRejectedError("development_bank_invalid")

        changed_character_refs = [
            owner_ref
            for owner_ref, after_entry in after_entries.items()
            if isinstance(owner_ref, str)
            and isinstance(after_entry, Mapping)
            and after_entry.get("owner_type") == "character"
            and before_entries.get(owner_ref) != after_entry
        ]

        cache = _OwnerResolutionCache()
        for owner_ref in sorted(changed_character_refs):
            try:
                owner_path, _digest, owner_view = self._resolve_covered_owner_view(
                    owner_ref, cache=cache
                )
            except CommandRejectedError as exc:
                raise CommandRejectedError("development_bank_character_unresolved") from exc
            if not isinstance(owner_view, Mapping) or owner_view.get("schema") != "shinobi_character":
                raise CommandRejectedError("development_bank_character_unresolved")

            raw_character = normalized.get(owner_path)
            if raw_character is None:
                try:
                    character = copy.deepcopy(self.repository.read_json(owner_path))
                except (FileNotFoundError, ValueError) as exc:
                    raise CommandRejectedError("development_bank_character_unresolved") from exc
            else:
                try:
                    character = json.loads(raw_character.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise CommandRejectedError("development_bank_character_unresolved") from exc
            if not isinstance(character, dict) or character.get("owner_id") != owner_ref or character.get("schema") != "shinobi_character":
                raise CommandRejectedError("development_bank_character_unresolved")

            development = character.get("development")
            if isinstance(development, dict) and "last_settled_at" in development:
                development.pop("last_settled_at")
                normalized[owner_path] = _json_bytes(character)

        return super()._prune_noop_writes(normalized)


__all__ = ["DevelopmentCursorAuthorityMixin"]
