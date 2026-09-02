"""Immutable idempotency receipts stored outside mutable campaign owners."""

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from shinobi_runtime.commands.envelope import CommandEnvelope
from shinobi_runtime.tx.canonical import (
    canonical_json_bytes,
    canonical_sha256,
    freeze_json,
    thaw_json,
)
from shinobi_runtime.tx.errors import IdempotencyConflictError


@dataclass(frozen=True)
class IdempotencyReceipt:
    request_id: str
    request_digest: str
    transaction_id: str
    campaign_id: str
    committed_revision: int
    committed_at: str
    result: Mapping[str, Any]
    command: Optional[Mapping[str, Any]] = None

    SCHEMA = "shinobi.idempotency-receipt"
    VERSION = 1

    def __post_init__(self) -> None:
        for field in (
            "request_id",
            "request_digest",
            "transaction_id",
            "campaign_id",
            "committed_at",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError("%s must be a non-empty string" % field)
        if len(self.request_digest) != 64:
            raise ValueError("request_digest must be a SHA-256 digest")
        try:
            int(self.request_digest, 16)
        except ValueError as exc:
            raise ValueError("request_digest must be hexadecimal") from exc
        if isinstance(self.committed_revision, bool) or not isinstance(
            self.committed_revision, int
        ):
            raise TypeError("committed_revision must be an integer")
        if self.committed_revision < 0:
            raise ValueError("committed_revision must be non-negative")
        if not isinstance(self.result, Mapping):
            raise TypeError("receipt result must be an object")
        object.__setattr__(self, "result", freeze_json(self.result))

        if self.command is not None:
            if not isinstance(self.command, Mapping):
                raise TypeError("receipt command must be an object")
            command_record = thaw_json(freeze_json(self.command))
            try:
                envelope = CommandEnvelope.from_record(command_record)
            except (TypeError, ValueError) as exc:
                raise ValueError("receipt command is invalid") from exc
            if envelope.to_record() != command_record:
                raise ValueError("receipt command is not canonical")
            if envelope.request_id != self.request_id:
                raise ValueError("receipt command request identity mismatch")
            if envelope.campaign_id != self.campaign_id:
                raise ValueError("receipt command campaign identity mismatch")
            if envelope.digest != self.request_digest:
                raise ValueError("receipt command digest mismatch")
            object.__setattr__(self, "command", freeze_json(command_record))

    @classmethod
    def for_command(
        cls,
        command: CommandEnvelope,
        transaction_id: str,
        committed_revision: int,
        committed_at: str,
        result: Mapping[str, Any],
    ) -> "IdempotencyReceipt":
        return cls(
            request_id=command.request_id,
            request_digest=command.digest,
            transaction_id=transaction_id,
            campaign_id=command.campaign_id,
            committed_revision=committed_revision,
            committed_at=committed_at,
            result=result,
            command=command.to_record(),
        )

    def to_record(self) -> Mapping[str, Any]:
        record: dict[str, Any] = {
            "schema": self.SCHEMA,
            "version": self.VERSION,
            "request_id": self.request_id,
            "request_digest": self.request_digest,
            "transaction_id": self.transaction_id,
            "campaign_id": self.campaign_id,
            "committed_revision": self.committed_revision,
            "committed_at": self.committed_at,
            "result": thaw_json(self.result),
        }
        if self.command is not None:
            record["command"] = thaw_json(self.command)
        return record

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "IdempotencyReceipt":
        if record.get("schema") != cls.SCHEMA or record.get("version") != cls.VERSION:
            raise ValueError("unsupported idempotency receipt")
        return cls(
            request_id=record.get("request_id"),
            request_digest=record.get("request_digest"),
            transaction_id=record.get("transaction_id"),
            campaign_id=record.get("campaign_id"),
            committed_revision=record.get("committed_revision"),
            committed_at=record.get("committed_at"),
            result=record.get("result"),
            command=record.get("command"),
        )


class ReceiptStore:
    """Insert-once receipts plus a durable campaign high-water index.

    Exact retry lookup remains request-ID addressed.  The high-water index is
    runtime-private metadata used only to decide whether an expensive lifetime
    integrity scan is necessary after an unusual campaign rollback/repair.
    Normal execution never enumerates historical receipts.
    """

    INDEX_SCHEMA = "shinobi.idempotency-receipt-index"
    INDEX_VERSION = 1
    INDEX_NAME = "_campaign-max-revision.json"

    def __init__(self, directory: object) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.directory / self.INDEX_NAME

    @staticmethod
    def _is_receipt_path(path: Path) -> bool:
        stem = path.stem
        return (
            path.suffix == ".json"
            and len(stem) == 64
            and all(ch in "0123456789abcdef" for ch in stem)
        )

    def _receipt_paths(self) -> Iterable[Path]:
        for path in self.directory.glob("*.json"):
            if self._is_receipt_path(path):
                yield path

    def _read_index(self) -> Optional[dict[str, Any]]:
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("corrupt idempotency receipt index") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != self.INDEX_SCHEMA
            or raw.get("version") != self.INDEX_VERSION
            or not isinstance(raw.get("campaigns"), dict)
        ):
            raise ValueError("invalid idempotency receipt index")
        campaigns = raw["campaigns"]
        for campaign_id, revision in campaigns.items():
            if (
                not isinstance(campaign_id, str)
                or not campaign_id
                or isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 0
            ):
                raise ValueError("invalid idempotency receipt index")
        return raw

    def _write_index(self, campaigns: Mapping[str, int]) -> None:
        record = {
            "schema": self.INDEX_SCHEMA,
            "version": self.INDEX_VERSION,
            "campaigns": dict(sorted(campaigns.items())),
        }
        content = canonical_json_bytes(record)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".%s." % self.index_path.name,
            suffix=".tmp",
            dir=str(self.directory),
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(self.index_path))
            directory_fd = os.open(str(self.directory), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def rebuild_index(self) -> Mapping[str, int]:
        campaigns: dict[str, int] = {}
        for path in self._receipt_paths():
            receipt = self._read(path)
            prior = campaigns.get(receipt.campaign_id, -1)
            if receipt.committed_revision > prior:
                campaigns[receipt.campaign_id] = receipt.committed_revision
        self._write_index(campaigns)
        return dict(campaigns)

    def _ensure_index(self) -> dict[str, Any]:
        index = self._read_index()
        if index is None:
            campaigns = self.rebuild_index()
            return {
                "schema": self.INDEX_SCHEMA,
                "version": self.INDEX_VERSION,
                "campaigns": dict(campaigns),
            }
        return index

    def campaign_max_revision(self, campaign_id: str) -> Optional[int]:
        if not isinstance(campaign_id, str) or not campaign_id:
            raise ValueError("campaign_id must be non-empty")
        index = self._ensure_index()
        value = index["campaigns"].get(campaign_id)
        return value if isinstance(value, int) else None

    def get_campaign_revision(
        self, campaign_id: str, revision: int
    ) -> Optional[IdempotencyReceipt]:
        """Return the unique receipt for one exact campaign revision.

        This deliberately scans immutable receipt metadata only for explicit
        transition-recovery reads.  Normal gameplay execution remains request-ID
        addressed and never enumerates history.  A duplicate revision fails
        closed because choosing one receipt would make transition chronology
        ambiguous.
        """

        if not isinstance(campaign_id, str) or not campaign_id:
            raise ValueError("campaign_id must be non-empty")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("revision must be a non-negative integer")
        match: Optional[IdempotencyReceipt] = None
        for path in self._receipt_paths():
            receipt = self._read(path)
            if receipt.campaign_id != campaign_id or receipt.committed_revision != revision:
                continue
            if match is not None:
                raise ValueError("multiple receipts claim one campaign revision")
            match = receipt
        return match

    def _note_campaign_revision(self, receipt: IdempotencyReceipt) -> None:
        index = self._ensure_index()
        campaigns = dict(index["campaigns"])
        prior = campaigns.get(receipt.campaign_id, -1)
        if receipt.committed_revision <= prior:
            return
        campaigns[receipt.campaign_id] = receipt.committed_revision
        self._write_index(campaigns)

    def iter_campaign_receipts_above(
        self, campaign_id: str, revision: int
    ) -> Iterable[IdempotencyReceipt]:
        """Exhaustively inspect only the exceptional rollback/repair case."""

        for path in self._receipt_paths():
            receipt = self._read(path)
            if (
                receipt.campaign_id == campaign_id
                and receipt.committed_revision > revision
            ):
                yield receipt

    def _path(self, request_id: str) -> Path:
        name = canonical_sha256({"request_id": request_id})
        return self.directory / (name + ".json")

    def _read(self, path: Path) -> IdempotencyReceipt:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("corrupt idempotency receipt: %s" % path) from exc
        if not isinstance(record, dict):
            raise ValueError("idempotency receipt must be an object")
        return IdempotencyReceipt.from_record(record)

    def get(self, request_id: str) -> Optional[IdempotencyReceipt]:
        path = self._path(request_id)
        try:
            return self._read(path)
        except FileNotFoundError:
            return None

    def lookup(self, command: CommandEnvelope) -> Optional[IdempotencyReceipt]:
        receipt = self.get(command.request_id)
        if receipt is None:
            return None
        if receipt.request_id != command.request_id:
            raise IdempotencyConflictError("receipt request identity mismatch")
        if receipt.request_digest != command.digest:
            raise IdempotencyConflictError(
                "request ID was already committed with different command bytes"
            )
        return receipt

    def put(self, receipt: IdempotencyReceipt) -> IdempotencyReceipt:
        path = self._path(receipt.request_id)
        content = canonical_json_bytes(receipt.to_record())
        path.parent.mkdir(parents=True, exist_ok=True)

        # Resolve an existing immutable receipt before advancing the high-water
        # index.  The index is deliberately written *before* linking a new
        # receipt: an overestimate after a crash is safe and forces the rare
        # rollback integrity path, while an underestimate could hide a future
        # receipt after campaign repair.
        try:
            existing = self._read(path)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if canonical_json_bytes(existing.to_record()) != content:
                raise IdempotencyConflictError(
                    "request ID already has a different committed receipt"
                )
            self._note_campaign_revision(existing)
            return existing

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".%s." % path.name,
            suffix=".tmp",
            dir=str(path.parent),
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

            self._note_campaign_revision(receipt)
            try:
                os.link(str(temporary), str(path))
            except FileExistsError:
                existing = self._read(path)
                if canonical_json_bytes(existing.to_record()) != content:
                    raise IdempotencyConflictError(
                        "request ID already has a different committed receipt"
                    )
                self._note_campaign_revision(existing)
                return existing
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return receipt
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
