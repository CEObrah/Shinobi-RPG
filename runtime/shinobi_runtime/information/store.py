"""Deterministic storage for claims, deliveries, and holder information routes.

The root information file is only a bounded routing/count projection. Exact
claims, exact deliveries, complete holder claim membership, and complete
holder delivery participation live in stable hash shards. This keeps lookup
cost tied to the exact causal object rather than lifetime campaign volume.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Mapping, MutableMapping, Optional

INFORMATION_INDEX_PATH = "state/reg/information-deliveries.json"
_RECENT_LIMIT = 64


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_ref(value: object, prefix: str, code: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) > 240:
        raise ValueError(code)
    return value


class InformationStore:
    """Targeted read/write facade over sharded information authorities.

    ``staged`` may be a transaction's shared record_writes map. Only paths
    actually mutated by this facade are returned by ``encoded_writes`` and
    ``affected_paths`` so unrelated staged records are never treated as
    information authority.
    """

    def __init__(self, repository: Any, staged: Optional[MutableMapping[str, Dict[str, Any]]] = None) -> None:
        self.repository = repository
        self.staged: MutableMapping[str, Dict[str, Any]] = staged if staged is not None else {}
        self._touched: set[str] = set()

    @staticmethod
    def claim_shard_path(claim_id: str) -> str:
        _require_ref(claim_id, "claim.", "information_claim_invalid")
        digest = _digest(claim_id)
        return f"state/reg/information/claims/{digest[:2]}/{digest[2:4]}.json"

    @staticmethod
    def delivery_shard_path(delivery_id: str) -> str:
        _require_ref(delivery_id, "delivery.", "information_delivery_invalid")
        digest = _digest(delivery_id)
        return f"state/reg/information/deliveries/{digest[:2]}/{digest[2:4]}.json"

    @staticmethod
    def holder_hash(holder_ref: str) -> str:
        if not isinstance(holder_ref, str) or not holder_ref or len(holder_ref) > 240:
            raise ValueError("information_holder_invalid")
        return _digest(holder_ref)[:20]

    @classmethod
    def knowledge_index_path(cls, holder_ref: str) -> str:
        return f"state/reg/information/knowledge/{cls.holder_hash(holder_ref)}/index.json"

    @classmethod
    def knowledge_shard_path(cls, holder_ref: str, claim_id: str) -> str:
        _require_ref(claim_id, "claim.", "information_claim_invalid")
        bucket = _digest(claim_id)[:2]
        return f"state/reg/information/knowledge/{cls.holder_hash(holder_ref)}/{bucket}.json"

    @classmethod
    def delivery_membership_shard_path(cls, holder_ref: str, delivery_id: str) -> str:
        _require_ref(delivery_id, "delivery.", "information_delivery_invalid")
        bucket = _digest(delivery_id)[:2]
        return f"state/reg/information/knowledge/{cls.holder_hash(holder_ref)}/delivery-{bucket}.json"

    @classmethod
    def subject_shard_path(cls, holder_ref: str, subject_ref: str) -> str:
        if not isinstance(subject_ref, str) or not subject_ref:
            raise ValueError("information_subject_invalid")
        bucket = _digest(subject_ref)[:2]
        return f"state/reg/information/knowledge/{cls.holder_hash(holder_ref)}/subject-{bucket}.json"

    def _read_optional(self, path: str) -> Optional[Dict[str, Any]]:
        staged = self.staged.get(path)
        if staged is not None:
            if not isinstance(staged, dict):
                raise ValueError("information_shard_invalid")
            return staged
        raw = self.repository.read_optional_bytes(path)
        if raw is None:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("information_shard_invalid") from exc
        if not isinstance(value, dict):
            raise ValueError("information_shard_invalid")
        return value

    def _mutable(self, path: str, factory: Any) -> tuple[Dict[str, Any], bool]:
        staged = self.staged.get(path)
        if staged is not None:
            if not isinstance(staged, dict):
                raise ValueError("information_shard_invalid")
            self._touched.add(path)
            return staged, False
        loaded = self._read_optional(path)
        if loaded is None:
            value = factory()
            created = True
        else:
            value = copy.deepcopy(loaded)
            created = False
        self.staged[path] = value
        self._touched.add(path)
        return value, created

    def projection(self, *, mutable: bool = False) -> Dict[str, Any]:
        def blank() -> Dict[str, Any]:
            return {
                "schema": "information-routing-index",
                "owner_id": "registry.information",
                "owner_type": "information_routing_index",
                "claim_count": 0,
                "delivery_count": 0,
                "knowledge_holder_count": 0,
                "recent_claim_refs": [],
                "recent_delivery_refs": [],
            }
        if mutable:
            value, _ = self._mutable(INFORMATION_INDEX_PATH, blank)
        else:
            value = self._read_optional(INFORMATION_INDEX_PATH) or blank()
        if value.get("schema") != "information-routing-index":
            raise ValueError("information_index_invalid")
        for field in ("claim_count", "delivery_count", "knowledge_holder_count"):
            raw = value.get(field)
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise ValueError("information_index_invalid")
        for field in ("recent_claim_refs", "recent_delivery_refs"):
            refs = value.get(field)
            if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                raise ValueError("information_index_invalid")
        return value

    @staticmethod
    def _remember(refs: list[str], ref: str) -> None:
        if ref in refs:
            refs.remove(ref)
        refs.append(ref)
        if len(refs) > _RECENT_LIMIT:
            del refs[:-_RECENT_LIMIT]

    def claim(self, claim_id: str) -> Optional[Mapping[str, Any]]:
        path = self.claim_shard_path(claim_id)
        shard = self._read_optional(path)
        if shard is None:
            return None
        if shard.get("schema") != "information-claim-shard" or not isinstance(shard.get("claims"), dict):
            raise ValueError("information_claim_shard_invalid")
        row = shard["claims"].get(claim_id)
        return row if isinstance(row, Mapping) else None

    def add_claim(self, record: Mapping[str, Any]) -> bool:
        claim_id = _require_ref(record.get("claim_id"), "claim.", "information_claim_invalid")
        path = self.claim_shard_path(claim_id)
        digest = _digest(claim_id)
        def blank() -> Dict[str, Any]:
            return {"schema": "information-claim-shard", "bucket": digest[:4], "claims": {}}
        shard, _ = self._mutable(path, blank)
        if shard.get("schema") != "information-claim-shard" or shard.get("bucket") != digest[:4] or not isinstance(shard.get("claims"), dict):
            raise ValueError("information_claim_shard_invalid")
        existing = shard["claims"].get(claim_id)
        if existing is not None:
            if existing != dict(record):
                raise ValueError("information_claim_conflict")
            return False
        shard["claims"][claim_id] = copy.deepcopy(dict(record))
        projection = self.projection(mutable=True)
        projection["claim_count"] += 1
        self._remember(projection["recent_claim_refs"], claim_id)
        return True

    def delivery(self, delivery_id: str) -> Optional[Mapping[str, Any]]:
        path = self.delivery_shard_path(delivery_id)
        shard = self._read_optional(path)
        if shard is None:
            return None
        if shard.get("schema") != "information-delivery-shard" or not isinstance(shard.get("deliveries"), dict):
            raise ValueError("information_delivery_shard_invalid")
        row = shard["deliveries"].get(delivery_id)
        return row if isinstance(row, Mapping) else None

    def add_delivery(self, record: Mapping[str, Any]) -> bool:
        delivery_id = _require_ref(record.get("delivery_id"), "delivery.", "information_delivery_invalid")
        path = self.delivery_shard_path(delivery_id)
        digest = _digest(delivery_id)
        def blank() -> Dict[str, Any]:
            return {"schema": "information-delivery-shard", "bucket": digest[:4], "deliveries": {}}
        shard, _ = self._mutable(path, blank)
        if shard.get("schema") != "information-delivery-shard" or shard.get("bucket") != digest[:4] or not isinstance(shard.get("deliveries"), dict):
            raise ValueError("information_delivery_shard_invalid")
        existing = shard["deliveries"].get(delivery_id)
        if existing is not None:
            if existing != dict(record):
                raise ValueError("information_delivery_conflict")
            return False
        shard["deliveries"][delivery_id] = copy.deepcopy(dict(record))
        projection = self.projection(mutable=True)
        projection["delivery_count"] += 1
        self._remember(projection["recent_delivery_refs"], delivery_id)
        for holder_ref in (record.get("sender_ref"), record.get("recipient_ref")):
            if isinstance(holder_ref, str) and holder_ref:
                self._record_delivery_participation(holder_ref, delivery_id)
        return True

    def _knowledge_index(self, holder_ref: str, *, mutable: bool = False) -> Optional[Dict[str, Any]]:
        holder_hash = self.holder_hash(holder_ref)
        path = self.knowledge_index_path(holder_ref)
        def blank() -> Dict[str, Any]:
            return {
                "schema": "information-knowledge-index",
                "holder_ref": holder_ref,
                "holder_hash": holder_hash,
                "claim_count": 0,
                "delivery_count": 0,
                "nonempty_buckets": [],
                "nonempty_delivery_buckets": [],
                "recent_claim_refs": [],
                "recent_delivery_refs": [],
            }
        if mutable:
            index, created = self._mutable(path, blank)
            if created:
                self.projection(mutable=True)["knowledge_holder_count"] += 1
        else:
            index = self._read_optional(path)
            if index is None:
                return None
        if (
            index.get("schema") != "information-knowledge-index"
            or index.get("holder_ref") != holder_ref
            or index.get("holder_hash") != holder_hash
            or isinstance(index.get("claim_count"), bool)
            or not isinstance(index.get("claim_count"), int)
            or isinstance(index.get("delivery_count"), bool)
            or not isinstance(index.get("delivery_count"), int)
            or not isinstance(index.get("nonempty_buckets"), list)
            or not isinstance(index.get("nonempty_delivery_buckets"), list)
            or not isinstance(index.get("recent_claim_refs"), list)
            or not isinstance(index.get("recent_delivery_refs"), list)
        ):
            raise ValueError("information_knowledge_index_invalid")
        return index

    def grant(self, holder_ref: str, claim_id: str) -> bool:
        _require_ref(claim_id, "claim.", "information_claim_invalid")
        if self.claim(claim_id) is None:
            raise ValueError("information_claim_not_found")
        index = self._knowledge_index(holder_ref, mutable=True)
        assert index is not None
        bucket = _digest(claim_id)[:2]
        path = self.knowledge_shard_path(holder_ref, claim_id)
        holder_hash = self.holder_hash(holder_ref)
        def blank() -> Dict[str, Any]:
            return {
                "schema": "information-knowledge-shard",
                "holder_ref": holder_ref,
                "holder_hash": holder_hash,
                "bucket": bucket,
                "claim_refs": [],
            }
        shard, _ = self._mutable(path, blank)
        refs = shard.get("claim_refs")
        if (
            shard.get("schema") != "information-knowledge-shard"
            or shard.get("holder_ref") != holder_ref
            or shard.get("holder_hash") != holder_hash
            or shard.get("bucket") != bucket
            or not isinstance(refs, list)
        ):
            raise ValueError("information_knowledge_shard_invalid")
        if claim_id in refs:
            self._remember(index["recent_claim_refs"], claim_id)
            self._record_subject_claim(holder_ref, claim_id)
            return False
        refs.append(claim_id)
        refs.sort()
        index["claim_count"] += 1
        if bucket not in index["nonempty_buckets"]:
            index["nonempty_buckets"].append(bucket)
            index["nonempty_buckets"].sort()
        self._remember(index["recent_claim_refs"], claim_id)
        self._record_subject_claim(holder_ref, claim_id)
        return True

    def _record_subject_claim(self, holder_ref: str, claim_id: str) -> None:
        claim = self.claim(claim_id)
        subject_ref = claim.get("subject_ref") if isinstance(claim, Mapping) else None
        if not isinstance(subject_ref, str) or not subject_ref:
            return
        holder_hash = self.holder_hash(holder_ref)
        bucket = _digest(subject_ref)[:2]
        path = self.subject_shard_path(holder_ref, subject_ref)
        def blank() -> Dict[str, Any]:
            return {
                "schema": "information-knowledge-subject-shard",
                "holder_ref": holder_ref,
                "holder_hash": holder_hash,
                "bucket": bucket,
                "subjects": {},
            }
        shard, _ = self._mutable(path, blank)
        subjects = shard.get("subjects")
        if (
            shard.get("schema") != "information-knowledge-subject-shard"
            or shard.get("holder_ref") != holder_ref
            or shard.get("holder_hash") != holder_hash
            or shard.get("bucket") != bucket
            or not isinstance(subjects, dict)
        ):
            raise ValueError("information_knowledge_subject_shard_invalid")
        refs = subjects.setdefault(subject_ref, [])
        if not isinstance(refs, list):
            raise ValueError("information_knowledge_subject_shard_invalid")
        self._remember(refs, claim_id)

    def _record_delivery_participation(self, holder_ref: str, delivery_id: str) -> bool:
        index = self._knowledge_index(holder_ref, mutable=True)
        assert index is not None
        bucket = _digest(delivery_id)[:2]
        path = self.delivery_membership_shard_path(holder_ref, delivery_id)
        holder_hash = self.holder_hash(holder_ref)
        def blank() -> Dict[str, Any]:
            return {
                "schema": "information-delivery-membership-shard",
                "holder_ref": holder_ref,
                "holder_hash": holder_hash,
                "bucket": bucket,
                "delivery_refs": [],
            }
        shard, _ = self._mutable(path, blank)
        refs = shard.get("delivery_refs")
        if (
            shard.get("schema") != "information-delivery-membership-shard"
            or shard.get("holder_ref") != holder_ref
            or shard.get("holder_hash") != holder_hash
            or shard.get("bucket") != bucket
            or not isinstance(refs, list)
        ):
            raise ValueError("information_delivery_membership_shard_invalid")
        if delivery_id in refs:
            self._remember(index["recent_delivery_refs"], delivery_id)
            return False
        refs.append(delivery_id)
        refs.sort()
        index["delivery_count"] += 1
        if bucket not in index["nonempty_delivery_buckets"]:
            index["nonempty_delivery_buckets"].append(bucket)
            index["nonempty_delivery_buckets"].sort()
        self._remember(index["recent_delivery_refs"], delivery_id)
        return True

    def holder_knows(self, holder_ref: str, claim_id: str) -> bool:
        index = self._knowledge_index(holder_ref)
        if index is None:
            return False
        path = self.knowledge_shard_path(holder_ref, claim_id)
        shard = self._read_optional(path)
        if shard is None:
            return False
        refs = shard.get("claim_refs")
        return isinstance(refs, list) and claim_id in refs

    def holder_recent_claim_refs(self, holder_ref: str, *, limit: int = 64) -> list[str]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 or limit > 64:
            raise ValueError("information_query_limit_invalid")
        index = self._knowledge_index(holder_ref)
        if index is None:
            return []
        return list(index["recent_claim_refs"][-limit:])

    def holder_recent_delivery_refs(self, holder_ref: str, *, limit: int = 64) -> list[str]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 or limit > 64:
            raise ValueError("information_query_limit_invalid")
        index = self._knowledge_index(holder_ref)
        if index is None:
            return []
        return list(index["recent_delivery_refs"][-limit:])

    def holder_subject_claim_refs(
        self, holder_ref: str, subject_ref: str, *, limit: int = 64
    ) -> list[str]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 or limit > 64:
            raise ValueError("information_query_limit_invalid")
        path = self.subject_shard_path(holder_ref, subject_ref)
        shard = self._read_optional(path)
        if shard is None:
            # Upgrade-safe bounded fallback. A release migration materializes
            # subject shards; older campaigns never fall back to lifetime scans.
            refs = []
            for claim_id in self.holder_recent_claim_refs(holder_ref, limit=64):
                claim = self.claim(claim_id)
                if isinstance(claim, Mapping) and claim.get("subject_ref") == subject_ref:
                    refs.append(claim_id)
            return refs[-limit:]
        holder_hash = self.holder_hash(holder_ref)
        bucket = _digest(subject_ref)[:2]
        subjects = shard.get("subjects")
        if (
            shard.get("schema") != "information-knowledge-subject-shard"
            or shard.get("holder_ref") != holder_ref
            or shard.get("holder_hash") != holder_hash
            or shard.get("bucket") != bucket
            or not isinstance(subjects, Mapping)
        ):
            raise ValueError("information_knowledge_subject_shard_invalid")
        refs = subjects.get(subject_ref, [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise ValueError("information_knowledge_subject_shard_invalid")
        return list(refs[-limit:])

    def holder_claim_refs(self, holder_ref: str) -> list[str]:
        index = self._knowledge_index(holder_ref)
        if index is None:
            return []
        refs: set[str] = set()
        holder_hash = self.holder_hash(holder_ref)
        for bucket in index["nonempty_buckets"]:
            if not isinstance(bucket, str) or len(bucket) != 2:
                raise ValueError("information_knowledge_index_invalid")
            path = f"state/reg/information/knowledge/{holder_hash}/{bucket}.json"
            shard = self._read_optional(path)
            if shard is None:
                raise ValueError("information_knowledge_shard_missing")
            values = shard.get("claim_refs")
            if not isinstance(values, list):
                raise ValueError("information_knowledge_shard_invalid")
            refs.update(value for value in values if isinstance(value, str))
        if len(refs) != index["claim_count"]:
            raise ValueError("information_knowledge_count_mismatch")
        return sorted(refs)

    def holder_delivery_refs(self, holder_ref: str) -> list[str]:
        index = self._knowledge_index(holder_ref)
        if index is None:
            return []
        refs: set[str] = set()
        holder_hash = self.holder_hash(holder_ref)
        for bucket in index["nonempty_delivery_buckets"]:
            if not isinstance(bucket, str) or len(bucket) != 2:
                raise ValueError("information_knowledge_index_invalid")
            path = f"state/reg/information/knowledge/{holder_hash}/delivery-{bucket}.json"
            shard = self._read_optional(path)
            if shard is None:
                raise ValueError("information_delivery_membership_shard_missing")
            values = shard.get("delivery_refs")
            if not isinstance(values, list):
                raise ValueError("information_delivery_membership_shard_invalid")
            refs.update(value for value in values if isinstance(value, str))
        if len(refs) != index["delivery_count"]:
            raise ValueError("information_delivery_membership_count_mismatch")
        return sorted(refs)

    def holder_summary(self, holder_ref: str) -> Mapping[str, Any]:
        """Return bounded routing metadata for one holder without scanning shards."""
        index = self._knowledge_index(holder_ref)
        if index is None:
            return {
                "holder_ref": holder_ref,
                "claim_count": 0,
                "delivery_count": 0,
                "recent_claim_refs": [],
                "recent_delivery_refs": [],
            }
        return {
            "holder_ref": holder_ref,
            "claim_count": index["claim_count"],
            "delivery_count": index["delivery_count"],
            "recent_claim_refs": list(index["recent_claim_refs"]),
            "recent_delivery_refs": list(index["recent_delivery_refs"]),
        }

    def claims_for_holder(self, holder_ref: str) -> Dict[str, Mapping[str, Any]]:
        out: Dict[str, Mapping[str, Any]] = {}
        for claim_id in self.holder_claim_refs(holder_ref):
            row = self.claim(claim_id)
            if row is None:
                raise ValueError("information_claim_missing")
            out[claim_id] = row
        return out

    def encoded_writes(self) -> Dict[str, bytes]:
        return {
            path: (json.dumps(self.staged[path], ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            for path in sorted(self._touched)
        }

    @property
    def affected_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._touched))
