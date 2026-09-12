from __future__ import annotations

import shutil
from pathlib import Path

from shinobi_runtime import deployment_freshness as freshness


ROOT = Path(__file__).resolve().parents[2]


def test_loaded_runtime_source_sentinels_match_checkout() -> None:
    assert freshness._loaded_runtime_source_mismatches(ROOT) == ()


def test_loaded_runtime_source_mismatch_is_reported(tmp_path, monkeypatch) -> None:
    package_root = tmp_path / "shinobi_runtime"
    checkout_root = ROOT / "runtime" / "shinobi_runtime"
    for relative in freshness._RUNTIME_SOURCE_SENTINELS:
        source = checkout_root / relative
        target = package_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    operations = package_root / "api" / "operations.py"
    operations.write_bytes(operations.read_bytes() + b"\n# stale-loaded-runtime-test\n")
    monkeypatch.setattr(freshness, "_RUNTIME_PACKAGE_ROOT", package_root)

    assert freshness._loaded_runtime_source_mismatches(ROOT) == (
        "runtime/shinobi_runtime/api/operations.py",
    )
