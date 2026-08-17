import pytest

from shinobi_runtime.api.contracts import (
    MAX_PLAN_WRITE_BYTES,
    MAX_PLAN_WRITE_PATHS,
    CommandPlan,
    CommandPreview,
)


def _validate(_overlay, _manifest):
    return None


def _paths(count: int) -> tuple[str, ...]:
    return tuple(f"state/test/world-review/{index:04d}.json" for index in range(count))


def test_world_review_wave_above_legacy_256_is_supported() -> None:
    refs = _paths(300)

    preview = CommandPreview(
        status="ready",
        code="advance_time_ready",
        target_revision=146,
        affected_refs=refs,
    )
    plan = CommandPlan(
        transaction_id="tx.test.world-review-wave",
        created_at="2026-08-16T00:00:00Z",
        writes={path: b"{}\n" for path in refs},
        result={"status": "ready"},
        validator=_validate,
    )

    assert len(preview.affected_refs) == 300
    assert len(plan.writes) == 300


def test_world_review_wave_above_legacy_four_megabytes_is_supported() -> None:
    payload = b"x" * (5 * 1024 * 1024)
    plan = CommandPlan(
        transaction_id="tx.test.world-review-wave-large-bytes",
        created_at="2026-08-17T00:00:00Z",
        writes={"state/test/world-review/large.json": payload},
        result={"status": "ready"},
        validator=_validate,
    )

    assert len(next(iter(plan.writes.values()))) == len(payload)


def test_causal_plan_path_envelope_remains_bounded() -> None:
    refs = _paths(MAX_PLAN_WRITE_PATHS + 1)

    with pytest.raises(ValueError, match="preview affected refs"):
        CommandPreview(
            status="ready",
            code="advance_time_ready",
            target_revision=146,
            affected_refs=refs,
        )

    with pytest.raises(ValueError, match="write-path limit"):
        CommandPlan(
            transaction_id="tx.test.world-review-wave-too-large",
            created_at="2026-08-16T00:00:00Z",
            writes={path: b"{}\n" for path in refs},
            result={"status": "ready"},
            validator=_validate,
        )


def test_causal_plan_byte_envelope_remains_bounded() -> None:
    with pytest.raises(ValueError, match="total write-byte limit"):
        CommandPlan(
            transaction_id="tx.test.world-review-wave-too-many-bytes",
            created_at="2026-08-17T00:00:00Z",
            writes={
                "state/test/world-review/too-large.json": b"x" * (MAX_PLAN_WRITE_BYTES + 1)
            },
            result={"status": "ready"},
            validator=_validate,
        )
