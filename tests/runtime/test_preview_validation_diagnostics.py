from shinobi_runtime.api.contracts import CommandRejectedError
from shinobi_runtime.api.preview_validation import (
    _autonomy_drift_fallback,
    _schema_validation_error_code,
    _template_validation_error_code,
)
from shinobi_runtime.store.template_validation import TemplateValidationError


def test_schema_validation_error_code_exposes_only_schema_identity():
    error = ValueError(
        "staged mission-assignment-request-registry schema validation failed: "
        "'secret-value' is not valid under any schema"
    )
    assert _schema_validation_error_code(error) == (
        "preview_schema_validation_failed_mission_assignment_request_registry"
    )


def test_schema_validation_error_code_handles_nested_validation_location():
    error = ValueError(
        "staged mission-runtime schema validation failed at objectives.0.status: "
        "'secret-value' is not one of ['active']"
    )
    code = _schema_validation_error_code(error)
    assert code == "preview_schema_validation_failed_mission_runtime"
    assert "objectives" not in code
    assert "secret" not in code


def test_schema_validation_error_code_does_not_leak_path_or_field_detail():
    error = ValueError(
        "invalid JSON in staged output state/hidden/owner.json: secret-value"
    )
    code = _schema_validation_error_code(error)
    assert code == "preview_schema_validation_failed_invalid_json"
    assert "hidden" not in code
    assert "secret" not in code


def test_schema_validation_error_code_exposes_bounded_unregistered_schema_token():
    error = ValueError("staged JSON uses an unregistered schema: 'mission-offer-runtime'")
    code = _schema_validation_error_code(error)
    assert code == (
        "preview_schema_validation_failed_unregistered_mission_offer_runtime"
    )
    assert "state/" not in code


def test_schema_validation_error_code_handles_legacy_unregistered_schema_format():
    error = ValueError("unregistered top-level schema: mission-offer-runtime")
    assert _schema_validation_error_code(error) == (
        "preview_schema_validation_failed_unregistered_mission_offer_runtime"
    )


def test_schema_validation_error_code_classifies_missing_top_level_schema():
    error = ValueError("staged state JSON requires a registered top-level schema")
    assert _schema_validation_error_code(error) == (
        "preview_schema_validation_failed_missing_top_level_schema"
    )


def test_schema_validation_error_code_falls_back_closed():
    assert _schema_validation_error_code(ValueError("unexpected validation detail")) == (
        "preview_schema_validation_failed"
    )


def test_template_validation_error_code_exposes_only_schema_and_static_reason():
    error = TemplateValidationError(
        "staged owner has unregistered keys at /secret/path: ['hidden-value']",
        schema_id="mission-runtime",
        reason="unregistered_keys",
    )
    code = _template_validation_error_code(error)
    assert code == (
        "preview_template_validation_failed_mission_runtime_unregistered_keys"
    )
    assert "secret" not in code
    assert "hidden" not in code
    assert "state/" not in code


def test_template_validation_error_code_handles_pre_schema_failure():
    error = TemplateValidationError(
        "staged state JSON has no structural template ID",
        schema_id=None,
        reason="missing_template_id",
    )
    assert _template_validation_error_code(error) == (
        "preview_template_validation_failed_missing_template_id"
    )


def test_template_validation_error_code_rejects_unregistered_reason_detail():
    error = TemplateValidationError(
        "secret-value",
        schema_id="test-owner",
        reason="secret-reason-from-state",
    )
    code = _template_validation_error_code(error)
    assert code == "preview_template_validation_failed_test_owner_unknown"
    assert "secret" not in code


def test_template_validation_error_code_falls_back_closed():
    error = ValueError("staged owner has unregistered keys at /secret: ['value']")
    assert _template_validation_error_code(error) == (
        "preview_template_validation_failed"
    )


def _autonomy_drift_error_with_force_frame() -> CommandRejectedError:
    owner_ref = "state/force/secret-owner.json"
    expected_record = {"schema": "force", "hidden": "never exposed"}
    try:
        raise ValueError("autonomous owner after-image differs from plan")
    except ValueError as cause:
        error = CommandRejectedError(
            "advance_time_base_validation_invalid__autonomous_owner_after_image"
        )
        error.__cause__ = cause
        return error


def test_autonomy_drift_fallback_exposes_only_bounded_owner_class():
    error = _autonomy_drift_error_with_force_frame()
    code = _autonomy_drift_fallback(error)
    assert code == "autonomous_owner_after_image_drift__force"
    assert "secret-owner" not in code
    assert "hidden" not in code
    assert "state/" not in code
