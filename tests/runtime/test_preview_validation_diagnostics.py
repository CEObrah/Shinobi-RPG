from shinobi_runtime.api.preview_validation import _schema_validation_error_code


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
