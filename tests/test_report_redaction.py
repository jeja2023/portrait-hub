from tools.report_redaction import redact_for_report, safe_report_repr


def test_redact_for_report_masks_nested_sensitive_fields() -> None:
    payload = {
        "status": "failed",
        "api_key": "secret-key",
        "nested": {"token": "secret-token", "safe": "visible"},
        "embedding": [1.0, 2.0],
    }

    assert redact_for_report(payload) == {
        "status": "failed",
        "api_key": "<redacted>",
        "nested": {"token": "<redacted>", "safe": "visible"},
        "embedding": "<redacted>",
    }


def test_redact_for_report_masks_secret_like_text() -> None:
    text = 'request failed Authorization: Bearer bearer-secret token=plain-secret "api_key":"json-secret"'

    redacted = redact_for_report(text)

    assert "bearer-secret" not in redacted
    assert "plain-secret" not in redacted
    assert "json-secret" not in redacted
    assert "<redacted>" in redacted


def test_safe_report_repr_uses_path_sensitive_redaction() -> None:
    assert safe_report_repr("actual-secret", "$.metadata.token") == "'<redacted>'"
    assert safe_report_repr("visible", "$.metadata.note") == "'visible'"
