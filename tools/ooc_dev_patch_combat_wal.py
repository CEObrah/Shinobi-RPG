from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def replace_once(rel: str, old: str, new: str) -> None:
    text = read(rel)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{rel}: expected one exact replacement, found {count}")
    write(rel, text.replace(old, new, 1))


# 1. Rally must read the authoritative martial Command skill, not a nonexistent
# professional Command field.
replace_once(
    "runtime/shinobi_runtime/martial_world/exact_combat.py",
    '''    leader_prof=leader.get("professional_skills",{}) if isinstance(leader.get("professional_skills"),Mapping) else {}
    ally_prof=ally.get("professional_skills",{}) if isinstance(ally.get("professional_skills"),Mapping) else {}
    leader_attrs=_attrs(leader); ally_attrs=_attrs(ally)
    leadership=(
        max(0,int(leader_prof.get("command",0)))*4
        + max(0,int(leader_attrs.get("willpower",0)))*2
        + max(0,int(leader_attrs.get("intelligence",0)))
        + max(0,int(ally_attrs.get("willpower",0)))
        + max(0,int(ally_prof.get("command",0)))
    )''',
    '''    leader_skills=_skills(leader); ally_skills=_skills(ally)
    leader_attrs=_attrs(leader); ally_attrs=_attrs(ally)
    leadership=(
        max(0,int(leader_skills.get("command",0)))*4
        + max(0,int(leader_attrs.get("willpower",0)))*2
        + max(0,int(leader_attrs.get("intelligence",0)))
        + max(0,int(ally_attrs.get("willpower",0)))
        + max(0,int(ally_skills.get("command",0)))
    )''',
)

# 2. A decisive lethal doctrine should finish an immediately vulnerable body
# before preferring a farther runner merely because that runner is disengaging.
replace_once(
    "runtime/shinobi_runtime/martial_world/exact_combat.py",
    '''        return (
            -pressure.get(ref, 0),
            pursuit_rank,
            finish_rank,
            planar_distance_mm(actor_position, position) if position else 10**12,
            ref,
        )''',
    '''        distance_rank=planar_distance_mm(actor_position, position) if position else 10**12
        if finishing=="commit_decisively":
            return (-pressure.get(ref,0),finish_rank,distance_rank,pursuit_rank,ref)
        return (-pressure.get(ref,0),pursuit_rank,finish_rank,distance_rank,ref)''',
)

# 3. One semantic rally declaration gets one contested rally attempt. The
# continuing combat span may preserve lethal pursuit, but must not reroll the
# same leadership order every exchange.
replace_once(
    "runtime/shinobi_runtime/commands/jianghu_extended.py",
    "            player_rally_allies=bool(rally_allies),",
    "            player_rally_allies=bool(rally_allies and exchanges==0),",
)

# 4. Focused OOC DEV audit can inspect durable WAL provenance without exposing
# raw state images or mutating campaign truth.
ooc_source = '''\"\"\"Bounded read-only Jianghu runtime audit.\"\"\"
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
'''
write("runtime/shinobi_runtime/api/ooc.py", ooc_source)

# 5. Extend forward repair with a provenance-checked WAL revision-chain mode so
# a severed Git root cannot make an already-corrupt release snapshot the oldest
# recoverable state. Callers still choose only a starting world revision, never
# paths, commits, or replacement values.
repair_path = "runtime/shinobi_runtime/api/repair.py"
repair = read(repair_path)
repair = repair.replace("import json\n", "import base64\nimport json\n", 1)
repair = repair.replace("    TransactionError,\n)", "    TransactionError,\n    WalError,\n)", 1)
repair = repair.replace("_MAX_REPAIR_CHAIN = 32\n", "_MAX_REPAIR_CHAIN = 32\n_MAX_WAL_REPAIR_CHAIN = 256\n", 1)
repair = repair.replace(
    '''@dataclass(frozen=True)
class _RepairPlan:''',
    '''@dataclass(frozen=True)
class _RepairRequest:
    transaction_ids: tuple[str, ...] = ()
    wal_revision_start: Optional[int] = None


@dataclass(frozen=True)
class _RepairPlan:''',
    1,
)
require_pattern = re.compile(
    r"    def _require_base\(self, command: CommandEnvelope, \*, require_revision: bool = True\).*?\n    def _require_fresh_deployment",
    re.S,
)
require_replacement = '''    def _require_base(self, command: CommandEnvelope, *, require_revision: bool = True) -> _RepairRequest:
        if command.mode != REPAIR_MODE or command.command_type != REPAIR_COMMAND_TYPE:
            raise OperationError(403, "repair_mode_required")
        if command.actor_id not in self.operations.allowed_actor_ids:
            raise OperationError(403, "actor_not_allowed")

        keys = set(command.payload)
        request: _RepairRequest
        if keys == {"damaged_wal_revision_start"}:
            raw_start = command.payload.get("damaged_wal_revision_start")
            if (
                isinstance(raw_start, bool)
                or not isinstance(raw_start, int)
                or raw_start < 1
                or raw_start > command.expected_revision
                or command.expected_revision - raw_start + 1 > _MAX_WAL_REPAIR_CHAIN
            ):
                raise OperationError(422, "repair_payload_invalid")
            request = _RepairRequest(wal_revision_start=raw_start)
        else:
            if keys == {"damaged_transaction_id"}:
                raw_ids: Any = [command.payload.get("damaged_transaction_id")]
            elif keys == {"damaged_transaction_ids"}:
                raw_ids = command.payload.get("damaged_transaction_ids")
                if not isinstance(raw_ids, (list, tuple)) or not 1 <= len(raw_ids) <= _MAX_REPAIR_CHAIN:
                    raise OperationError(422, "repair_payload_invalid")
            else:
                raise OperationError(422, "repair_payload_invalid")

            transaction_ids: list[str] = []
            seen: set[str] = set()
            for raw in raw_ids:
                if (
                    not isinstance(raw, str)
                    or not raw.startswith("tx.")
                    or len(raw) > 160
                    or raw in seen
                ):
                    raise OperationError(422, "repair_payload_invalid")
                seen.add(raw)
                transaction_ids.append(raw)
            if not transaction_ids:
                raise OperationError(422, "repair_payload_invalid")
            request = _RepairRequest(transaction_ids=tuple(transaction_ids))

        try:
            self.repository.require_campaign(command.campaign_id, _META_PATH)
            if require_revision:
                self.repository.require_revision(command.expected_revision, _META_PATH)
        except StaleRevisionError as exc:
            raise OperationError(409, "stale_revision") from exc
        except (TypeError, ValueError) as exc:
            raise OperationError(409, "repair_campaign_mismatch") from exc
        return request

    def _require_fresh_deployment'''
repair, count = require_pattern.subn(require_replacement, repair, count=1)
if count != 1:
    raise RuntimeError(f"repair _require_base replacement count={count}")

wal_method = r'''
    @staticmethod
    def _decode_wal_image(value: Any) -> Optional[bytes]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise OperationError(409, "repair_wal_provenance_invalid")
        try:
            return base64.b64decode(value.encode("ascii"), validate=True)
        except (ValueError, UnicodeEncodeError) as exc:
            raise OperationError(409, "repair_wal_provenance_invalid") from exc

    def _build_wal_chain(self, command: CommandEnvelope, start_revision: int) -> _RepairPlan:
        try:
            records = self.coordinator.wal.records(("committed",))
        except (OSError, WalError) as exc:
            raise OperationError(409, "repair_wal_provenance_invalid") from exc

        by_revision: dict[int, Mapping[str, Any]] = {}
        for record in records:
            manifest = record.get("manifest", {}) if isinstance(record, Mapping) else {}
            if not isinstance(manifest, Mapping) or manifest.get("campaign_id") != command.campaign_id:
                continue
            target_revision = manifest.get("target_revision")
            if isinstance(target_revision, bool) or not isinstance(target_revision, int):
                continue
            if not start_revision <= target_revision <= command.expected_revision:
                continue
            if target_revision in by_revision:
                raise OperationError(409, "repair_wal_provenance_invalid")
            by_revision[target_revision] = record

        expected_revisions = list(range(start_revision, command.expected_revision + 1))
        if sorted(by_revision) != expected_revisions:
            raise OperationError(409, "repair_wal_provenance_incomplete")
        ordered = [by_revision[revision] for revision in expected_revisions]

        histories: dict[str, list[Mapping[str, Any]]] = {}
        transaction_ids: list[str] = []
        for revision, record in zip(expected_revisions, ordered):
            manifest = record.get("manifest", {})
            transaction_id = record.get("transaction_id")
            if (
                manifest.get("base_revision") != revision - 1
                or manifest.get("target_revision") != revision
                or manifest.get("mode") not in {"gameplay", "autonomous", "repair"}
                or not isinstance(transaction_id, str)
                or manifest.get("transaction_id") != transaction_id
            ):
                raise OperationError(409, "repair_wal_provenance_invalid")
            entries = record.get("entries")
            if not isinstance(entries, list) or not entries:
                raise OperationError(409, "repair_wal_provenance_invalid")
            seen_paths: set[str] = set()
            for entry in entries:
                if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
                    raise OperationError(409, "repair_wal_provenance_invalid")
                path = str(entry["path"])
                if not path.startswith("state/") or path in seen_paths:
                    raise OperationError(409, "repair_wal_provenance_invalid")
                seen_paths.add(path)
                history = histories.setdefault(path, [])
                if history and history[-1].get("after_sha256") != entry.get("before_sha256"):
                    raise OperationError(409, "repair_wal_provenance_invalid")
                history.append(entry)
            if _META_PATH not in seen_paths:
                raise OperationError(409, "repair_wal_provenance_invalid")
            transaction_ids.append(transaction_id)

        meta_history = histories.get(_META_PATH, [])
        if len(meta_history) != len(expected_revisions):
            raise OperationError(409, "repair_wal_provenance_invalid")
        baseline_meta_raw = self._decode_wal_image(meta_history[0].get("before_b64"))
        if baseline_meta_raw is None:
            raise OperationError(409, "repair_wal_provenance_invalid")
        try:
            baseline_meta = json.loads(baseline_meta_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OperationError(409, "repair_wal_provenance_invalid") from exc
        if (
            not isinstance(baseline_meta, dict)
            or baseline_meta.get("campaign_id") != command.campaign_id
            or baseline_meta.get("revision") != start_revision - 1
        ):
            raise OperationError(409, "repair_wal_provenance_invalid")

        writes: dict[str, Optional[bytes]] = {}
        for path, history in sorted(histories.items()):
            if self.repository.digest(path) != history[-1].get("after_sha256"):
                raise OperationError(409, "repair_wal_base_changed")
            desired = self._decode_wal_image(history[0].get("before_b64"))
            if path == _META_PATH:
                repaired_meta = dict(baseline_meta)
                repaired_meta["revision"] = command.expected_revision + 1
                desired = (json.dumps(repaired_meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if self.repository.read_optional_bytes(path) != desired:
                writes[path] = desired
        if _META_PATH not in writes:
            raise OperationError(409, "repair_wal_provenance_invalid")

        result = {
            "repair_kind": "forward_wal_revision_chain_repair",
            "restored_state_revision": start_revision - 1,
            "committed_revision": command.expected_revision + 1,
            "restored_world_time": baseline_meta.get("time"),
            "damaged_revision_start": start_revision,
            "damaged_revision_end": command.expected_revision,
            "damaged_transaction_ids": transaction_ids,
            "repaired_path_count": len(writes),
            "provenance_source": "committed_wal_before_images",
        }
        return _RepairPlan(
            transaction_id="tx.repair." + command.digest,
            created_at=command.submitted_at,
            writes=writes,
            result=result,
            affected_refs=tuple(sorted(writes)),
        )

'''
needle = "    def _build(self, command: CommandEnvelope) -> _RepairPlan:\n"
if repair.count(needle) != 1:
    raise RuntimeError("repair _build insertion point not unique")
repair = repair.replace(needle, wal_method + needle, 1)
replace_build = '''    def _build(self, command: CommandEnvelope) -> _RepairPlan:
        damaged_transaction_ids = self._require_base(command)
        self._require_fresh_deployment()
        git = self.coordinator.git
'''
replacement_build = '''    def _build(self, command: CommandEnvelope) -> _RepairPlan:
        repair_request = self._require_base(command)
        self._require_fresh_deployment()
        if repair_request.wal_revision_start is not None:
            return self._build_wal_chain(command, repair_request.wal_revision_start)
        damaged_transaction_ids = repair_request.transaction_ids
        git = self.coordinator.git
'''
if repair.count(replace_build) != 1:
    raise RuntimeError("repair _build prologue not unique")
repair = repair.replace(replace_build, replacement_build, 1)
write(repair_path, repair)

# 6. Fix the rally regression fixture so it cannot bless the wrong namespace,
# and add targeted behavioral/source-contract regressions.
rally_test = "tests/current/test_combat_rally_and_approach_budget.py"
replace_once(
    rally_test,
    '''        "martial_skills": {"unarmed": 60, "sword": 0, "spear": 0, "bow": 0, "hidden_weapons": 0},
        "professional_skills": {"command": command},''',
    '''        "martial_skills": {"unarmed": 60, "sword": 0, "spear": 0, "bow": 0, "hidden_weapons": 0, "command": command},
        "professional_skills": {},''',
)
with (ROOT / rally_test).open("a", encoding="utf-8") as handle:
    handle.write(r'''


def test_rally_reads_martial_command_and_ignores_professional_command_shadow():
    low_people = {
        "leader": _person("leader", "a", command=0, will=92, intelligence=100),
        "ally": _person("ally", "a", command=0, will=70, intelligence=60),
    }
    high_people = copy.deepcopy(low_people)
    high_people["leader"]["martial_skills"]["command"] = 55
    high_people["leader"]["professional_skills"]["command"] = 999
    low = exact._rally_withdrawal_attempt(
        leader_ref="leader", ally_ref="ally", people=low_people, withdrawal=_withdrawal("side_collapse")
    )
    high = exact._rally_withdrawal_attempt(
        leader_ref="leader", ally_ref="ally", people=high_people, withdrawal=_withdrawal("side_collapse")
    )
    assert high["leadership_score"] - low["leadership_score"] == 220


def test_one_semantic_rally_command_is_not_rerolled_every_exchange():
    source = (ROOT / "runtime/shinobi_runtime/commands/jianghu_extended.py").read_text(encoding="utf-8")
    assert "player_rally_allies=bool(rally_allies and exchanges==0)" in source


def test_decisive_lethal_targeting_finishes_near_vulnerable_target_before_far_runner(monkeypatch):
    leader = _person("leader", "a", command=55, will=92, intelligence=100)
    vulnerable = _person("vulnerable", "b")
    runner = _person("runner", "b")
    vulnerable["health"]["blood_lost_ml"] = 500
    people = {row["person_id"]: row for row in (leader, vulnerable, runner)}
    combat = exact.initialize_combat(
        combat_ref="kill-efficient-targeting", side_a_refs=["leader"], side_b_refs=["vulnerable", "runner"],
        people=people, zone_ref="test", started_at="SE-0061-01-01T00:00:00",
        objective={"kind": "eliminate", "target_refs": ["vulnerable", "runner"]}, equipment_ledger=_ledger(),
    )
    combat["positions"]["leader"].update(x_mm=0, y_mm=0)
    combat["positions"]["vulnerable"].update(x_mm=2_000, y_mm=0, stance="braced")
    combat["positions"]["runner"].update(x_mm=12_000, y_mm=0, stance="disengaging")
    combat["combatants"]["leader"]["observed_refs"] = ["vulnerable", "runner"]
    monkeypatch.setattr(
        exact,
        "_engagement_doctrine_for",
        lambda _person: {"pursuit_posture": "persistent", "finishing_window": "commit_decisively"},
    )
    assert exact.default_target_for(combat=combat, people=people, actor_ref="leader") == "vulnerable"
''')

# 7. WAL-backed forward repair tests.
write("tests/current/test_wal_campaign_repair.py", r'''from __future__ import annotations

import base64
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from shinobi_runtime.api.operations import OperationError
from shinobi_runtime.api.repair import CampaignRepairService, REPAIR_COMMAND_TYPE
from shinobi_runtime.commands import CommandEnvelope


def _bytes(value):
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _sha(value):
    return None if value is None else hashlib.sha256(value).hexdigest()


def _b64(value):
    return None if value is None else base64.b64encode(value).decode("ascii")


def _entry(path, before, after):
    return {
        "path": path,
        "before_sha256": _sha(before),
        "after_sha256": _sha(after),
        "before_b64": _b64(before),
        "after_b64": _b64(after),
    }


def _record(campaign_id, base, target, tx, entries):
    return {
        "status": "committed",
        "transaction_id": tx,
        "manifest": {
            "transaction_id": tx,
            "campaign_id": campaign_id,
            "request_id": f"req-{target}",
            "command_digest": "d" * 64,
            "mode": "gameplay",
            "base_revision": base,
            "target_revision": target,
            "created_at": f"2026-08-30T00:00:{target:02d}Z",
            "mutations": [
                {"path": row["path"], "before_sha256": row["before_sha256"], "after_sha256": row["after_sha256"]}
                for row in entries
            ],
        },
        "entries": list(entries),
        "receipt": None,
    }


class _Repository:
    def __init__(self, root: Path, files):
        self.root = root
        self.files = dict(files)

    def read_optional_bytes(self, path):
        return self.files.get(str(path))

    def read_bytes(self, path):
        value = self.read_optional_bytes(path)
        if value is None:
            raise FileNotFoundError(str(path))
        return value

    def read_json(self, path):
        return json.loads(self.read_bytes(path).decode("utf-8"))

    def digest(self, path):
        return _sha(self.read_optional_bytes(path))

    def require_campaign(self, expected, path="state/meta.json"):
        if self.read_json(path).get("campaign_id") != expected:
            raise ValueError("campaign mismatch")
        return expected

    def require_revision(self, expected, path="state/meta.json"):
        from shinobi_runtime.tx.errors import StaleRevisionError
        actual = self.read_json(path).get("revision")
        if actual != expected:
            raise StaleRevisionError(expected, actual)
        return actual


class _Wal:
    def __init__(self, records):
        self._records = tuple(records)

    def records(self, statuses=None):
        return self._records


class _Coordinator:
    def __init__(self, records):
        self.wal = _Wal(records)


class _Operations:
    def __init__(self, repository, records):
        self.repository = repository
        self.coordinator = _Coordinator(records)
        self.allowed_actor_ids = frozenset({"pc.test"})

    def _locked(self):
        return nullcontext()


def _command(expected=144, payload=None):
    return CommandEnvelope(
        campaign_id="test-campaign",
        request_id="repair-wal-test",
        actor_id="pc.test",
        command_type=REPAIR_COMMAND_TYPE,
        expected_revision=expected,
        submitted_at="2026-08-31T00:00:00Z",
        payload={"damaged_wal_revision_start": 143} if payload is None else payload,
        mode="repair",
    )


def _fixture(tmp_path):
    meta142 = _bytes({"schema": "meta", "campaign_id": "test-campaign", "game": "jianghu", "revision": 142, "time": "SE-0061-09-27T21:15:00", "player_id": "pc.test"})
    meta143 = _bytes({"schema": "meta", "campaign_id": "test-campaign", "game": "jianghu", "revision": 143, "time": "SE-0061-09-27T22:58:33", "player_id": "pc.test"})
    meta144 = _bytes({"schema": "meta", "campaign_id": "test-campaign", "game": "jianghu", "revision": 144, "time": "SE-0061-09-27T22:59:22", "player_id": "pc.test"})
    clean = _bytes({"value": "clean"})
    bad1 = _bytes({"value": "bad-143"})
    bad2 = _bytes({"value": "bad-144"})
    records = [
        _record("test-campaign", 142, 143, "tx.gameplay." + "1" * 64, [_entry("state/meta.json", meta142, meta143), _entry("state/example.json", clean, bad1)]),
        _record("test-campaign", 143, 144, "tx.gameplay." + "2" * 64, [_entry("state/meta.json", meta143, meta144), _entry("state/example.json", bad1, bad2)]),
    ]
    repository = _Repository(tmp_path, {"state/meta.json": meta144, "state/example.json": bad2})
    return repository, records, clean


def test_wal_chain_repair_restores_before_severed_git_root_and_advances_forward(tmp_path):
    repository, records, clean = _fixture(tmp_path)
    service = CampaignRepairService(_Operations(repository, records))
    plan = service._build(_command())
    assert plan.result["repair_kind"] == "forward_wal_revision_chain_repair"
    assert plan.result["restored_state_revision"] == 142
    assert plan.result["committed_revision"] == 145
    assert plan.result["restored_world_time"] == "SE-0061-09-27T21:15:00"
    assert plan.writes["state/example.json"] == clean
    repaired_meta = json.loads(plan.writes["state/meta.json"].decode())
    assert repaired_meta["revision"] == 145
    assert repaired_meta["time"] == "SE-0061-09-27T21:15:00"


def test_wal_chain_repair_fails_if_a_world_revision_is_missing(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    service = CampaignRepairService(_Operations(repository, records[1:]))
    with pytest.raises(OperationError) as caught:
        service._build(_command())
    assert caught.value.code == "repair_wal_provenance_incomplete"


def test_wal_chain_repair_fails_on_path_hash_discontinuity(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    records[1]["entries"][1]["before_sha256"] = "f" * 64
    service = CampaignRepairService(_Operations(repository, records))
    with pytest.raises(OperationError) as caught:
        service._build(_command())
    assert caught.value.code == "repair_wal_provenance_invalid"


def test_wal_chain_repair_fails_if_current_state_no_longer_matches_last_after_image(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    repository.files["state/example.json"] = _bytes({"value": "different"})
    service = CampaignRepairService(_Operations(repository, records))
    with pytest.raises(OperationError) as caught:
        service._build(_command())
    assert caught.value.code == "repair_wal_base_changed"


def test_wal_repair_payload_cannot_choose_paths_or_replacement_values(tmp_path):
    repository, records, _ = _fixture(tmp_path)
    service = CampaignRepairService(_Operations(repository, records))
    with pytest.raises(OperationError) as caught:
        service._build(_command(payload={"damaged_wal_revision_start": 143, "path": "state/example.json"}))
    assert caught.value.code == "repair_payload_invalid"
''')

# 8. Focused WAL combat provenance audit regression.
write("tests/current/test_ooc_wal_combat_provenance.py", r'''from __future__ import annotations

import base64
import json

import shinobi_runtime.api.ooc as ooc


def _b64(value):
    return base64.b64encode((json.dumps(value) + "\n").encode()).decode("ascii")


class _Repo:
    root = None

    def read_json(self, path):
        if path == "state/meta.json":
            return {"campaign_id": "c", "game": "jianghu", "revision": 143, "time": "T1"}
        if path == "state/martial-world/scheduler.json":
            return {"settled_through": "T1", "recurring": {}}
        if path == "state/martial-world/civilian-populations.json":
            return {"schema": "x", "places": {}}
        raise FileNotFoundError(path)


class _Wal:
    def __init__(self, _path):
        pass

    def records(self, _statuses):
        combat_ref = "combat:test"
        return ({
            "transaction_id": "tx.gameplay.test",
            "manifest": {"campaign_id": "c", "base_revision": 142, "target_revision": 143, "request_id": "combat-test"},
            "entries": [
                {"path": "state/meta.json", "before_b64": _b64({"time": "T0"}), "after_b64": _b64({"time": "T1"})},
                {"path": "state/martial-world/combats.json", "before_b64": _b64({"combats": {combat_ref: {"elapsed_ms": 1000}}}), "after_b64": _b64({"combats": {combat_ref: {"elapsed_ms": 6200000}}})},
            ],
            "receipt": {"result": {"combat_ref": combat_ref, "exchanges_resolved": 160, "scope_stop_reason": "execution_frontier"}},
        },)


def test_focused_ooc_audit_surfaces_bounded_combat_wal_timeline(monkeypatch, tmp_path):
    monkeypatch.setattr(ooc, "WriteAheadLog", _Wal)
    monkeypatch.setattr(ooc, "_derived_person_routes", lambda _repo: {})
    monkeypatch.setattr(ooc, "civilian_population_total", lambda _value: 0)
    monkeypatch.setattr(ooc, "inspect_deployment_freshness", lambda _root: type("D", (), {"healthy": True, "diagnostic": lambda self: "deployment:ok"})())
    result = ooc.RepositoryOocAudit(_Repo(), tmp_path)("combat repair provenance", ())
    joined = "\n".join(result.diagnostics)
    assert "wal_combat:base=142 rev=143" in joined
    assert "elapsed_ms=1000->6200000" in joined
    assert "exchanges=160" in joined
''')

print("OOC DEV combat/WAL patch applied")
