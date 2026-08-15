from __future__ import annotations

from shinobi_runtime.api.ooc import _bounded_files
from shinobi_runtime.tx.receipts import ReceiptStore


def test_receipt_audit_filter_excludes_high_water_index(tmp_path) -> None:
    (tmp_path / ReceiptStore.INDEX_NAME).write_text("{}", encoding="utf-8")
    receipt_path = tmp_path / (("a" * 64) + ".json")
    receipt_path.write_text("{}", encoding="utf-8")

    files, truncated = _bounded_files(tmp_path, 8, receipt_only=True)

    assert files == (receipt_path,)
    assert truncated is False
