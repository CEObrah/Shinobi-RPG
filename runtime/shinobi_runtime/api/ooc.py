"""Bounded read-only Jianghu runtime audit."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from shinobi_runtime.api.contracts import OocAuditResult
from shinobi_runtime.deployment_freshness import inspect_deployment_freshness
from shinobi_runtime.store import RepositoryStore
from shinobi_runtime.martial_world.civilian_state import civilian_population_total
from shinobi_runtime.martial_world.live_state import _derived_person_routes
from shinobi_runtime.tx import WriteAheadLog
from shinobi_runtime.tx.errors import WalError


def _wal_image(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WalError("WAL image is not encoded text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise WalError("WAL image is invalid") from exc


def _json_image(value: Any) -> Mapping[str, Any] | None:
    raw = _wal_image(value)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WalError("WAL JSON image is invalid") from exc
    return parsed if isinstance(parsed, Mapping) else None


def _meta_time(entry: Mapping[str, Any], side: str) -> str | None:
    image = _json_image(entry.get(f"{side}_b64"))
    value = image.get("time") if image is not None else None
    return str(value) if isinstance(value, str) else None


def _combat_elapsed(entry: Mapping[str, Any], side: str, combat_ref: str) -> int | None:
    image = _json_image(entry.get(f"{side}_b64"))
    combats = image.get("combats", {}) if image is not None else {}
    combat = combats.get(combat_ref, {}) if isinstance(combats, Mapping) else {}
    value = combat.get("elapsed_ms") if isinstance(combat, Mapping) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _combat_ref_from_images(entry: Mapping[str, Any]) -> str | None:
    before = _json_image(entry.get("before_b64")) or {}
    after = _json_image(entry.get("after_b64")) or {}
    before_combats = before.get("combats", {}) if isinstance(before, Mapping) else {}
    after_combats = after.get("combats", {}) if isinstance(after, Mapping) else {}
    refs = sorted(set(before_combats if isinstance(before_combats, Mapping) else {}) | set(after_combats if isinstance(after_combats, Mapping) else {}))
    for ref in refs:
        if not isinstance(ref, str):
            continue
        before_row = before_combats.get(ref, {}) if isinstance(before_combats, Mapping) else {}
        after_row = after_combats.get(ref, {}) if isinstance(after_combats, Mapping) else {}
        if isinstance(before_row, Mapping) and isinstance(after_row, Mapping):
            if before_row.get("elapsed_ms") != after_row.get("elapsed_ms"):
                return ref
        elif before_row != after_row:
            return ref
    return None


class RepositoryOocAudit:
    def __init__(self, repository: RepositoryStore, runtime_root: object = None, **_kw):
        self.repository = repository
        self.runtime_root = None if runtime_root is None else Path(runtime_root)

    def _wal_combat_diagnostics(self, campaign_id: str) -> list[str]:
        if self.runtime_root is None:
            return ["wal_combat_provenance:unavailable reason=runtime_root_missing"]
        try:
            records = WriteAheadLog(self.runtime_root / "wal").records(("committed",))
        except (OSError, WalError):
            return ["wal_combat_provenance:unavailable reason=wal_read_failed"]
        rows: list[tuple[int, str]] = []
        for record in records:
            manifest = record.get("manifest", {}) if isinstance(record, Mapping) else {}
            if not isinstance(manifest, Mapping) or manifest.get("campaign_id") != campaign_id:
                continue
            target_revision = manifest.get("target_revision")
            base_revision = manifest.get("base_revision")
            if (
                isinstance(target_revision, bool)
                or not isinstance(target_revision, int)
                or isinstance(base_revision, bool)
                or not isinstance(base_revision, int)
            ):
                continue
            entries = {
                str(entry.get("path")): entry
                for entry in record.get("entries", [])
                if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
            }
            combat_entry = entries.get("state/martial-world/combats.json")
            if combat_entry is None:
                continue
            receipt = record.get("receipt", {}) if isinstance(record.get("receipt"), Mapping) else {}
            result = receipt.get("result", {}) if isinstance(receipt.get("result"), Mapping) else {}
            combat_ref = result.get("combat_ref") if isinstance(result.get("combat_ref"), str) else None
            if combat_ref is None:
                combat_ref = _combat_ref_from_images(combat_entry)
            if combat_ref is None:
                continue
            meta_entry = entries.get("state/meta.json")
            before_time = _meta_time(meta_entry, "before") if meta_entry is not None else None
            after_time = _meta_time(meta_entry, "after") if meta_entry is not None else None
            before_elapsed = _combat_elapsed(combat_entry, "before", combat_ref)
            after_elapsed = _combat_elapsed(combat_entry, "after", combat_ref)
            tx = str(record.get("transaction_id") or manifest.get("transaction_id") or "")
            request_id = str(manifest.get("request_id") or "")
            exchanges = result.get("exchanges_resolved")
            stop = result.get("scope_stop_reason")
            rows.append((
                target_revision,
                "wal_combat:"
                f"base={base_revision} rev={target_revision} tx={tx} request={request_id} "
                f"time={before_time}->{after_time} elapsed_ms={before_elapsed}->{after_elapsed} "
                f"exchanges={exchanges} stop={stop}",
            ))
        rows.sort(key=lambda item: item[0])
        if not rows:
            return ["wal_combat_provenance:none"]
        return [line for _revision, line in rows[-40:]]

    def __call__(self, focus: Optional[str], observations: Tuple[str, ...]) -> OocAuditResult:
        diagnostics=[]; suggestions=[]
        meta = None
        try:
            meta=self.repository.read_json('state/meta.json')
            diagnostics.append(f"campaign:game={meta.get('game')} revision={meta.get('revision')} time={meta.get('time')}")
            if meta.get('game')!='jianghu': suggestions.append('campaign_game_mismatch')
        except Exception:
            diagnostics.append('campaign:invalid'); suggestions.append('repair_campaign_meta')
        try:
            sch=self.repository.read_json('state/martial-world/scheduler.json')
            diagnostics.append(f"jianghu_scheduler:settled_through={sch.get('settled_through')} classes={len(sch.get('recurring',{}))}")
        except Exception:
            diagnostics.append('jianghu_scheduler:invalid'); suggestions.append('repair_jianghu_scheduler')
        try:
            diagnostics.append(f"jianghu_people:routed={len(_derived_person_routes(self.repository))}")
        except Exception:
            diagnostics.append('jianghu_people:roster_index_invalid'); suggestions.append('repair_jianghu_rosters')
        try:
            civ=self.repository.read_json('state/martial-world/civilian-populations.json')
            diagnostics.append(f"civilian_population:aggregate={civilian_population_total(civ)}")
        except Exception:
            diagnostics.append('civilian_population:invalid'); suggestions.append('repair_civilian_population_authority')
        normalized_focus = str(focus or '').lower()
        if meta is not None and any(token in normalized_focus for token in ('wal provenance','repair provenance','combat provenance')):
            diagnostics.extend(self._wal_combat_diagnostics(str(meta.get('campaign_id') or '')))
        try:
            deployment=inspect_deployment_freshness(self.repository.root)
            diagnostics.append(deployment.diagnostic())
            if not deployment.healthy:
                suggestions.append('redeploy_runtime_source')
        except Exception:
            diagnostics.append('deployment_source:summary status=unverified reason=diagnostic_failed')
            suggestions.append('inspect_runtime_deployment_source')
        return OocAuditResult(tuple(diagnostics[:48]),tuple(suggestions[:48]),None)
