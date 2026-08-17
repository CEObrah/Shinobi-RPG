from __future__ import annotations

from shinobi_runtime.api.preview_error_diagnostics import (
    _preview_internal_code,
    _stage_from_filename,
)


def test_preview_internal_code_is_bounded_to_runtime_stage_and_error_kind() -> None:
    try:
        raise TypeError("secret mutable detail must not escape")
    except TypeError as exc:
        code = _preview_internal_code(exc)

    assert code == "command_preview_internal__preview_error_diagnostics__type_error"
    assert "secret" not in code
    assert len(code) < 128


def test_stage_from_filename_rejects_non_runtime_frames() -> None:
    assert _stage_from_filename("/tmp/example.py") is None
    assert _stage_from_filename(
        "/srv/runtime/shinobi_runtime/commands/promotion_exam_hosted_lifecycle.py"
    ) == "promotion_exam_hosted_lifecycle"
