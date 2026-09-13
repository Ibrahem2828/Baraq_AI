from app.training.anonymizer import anonymize_value


def test_anonymizer_redacts_common_pii() -> None:
    value = {
        "email": "student@example.com",
        "note": "اتصل على +963 999 123 456 أو أرسل إلى a@b.com",
    }
    cleaned, report = anonymize_value(value)
    assert cleaned["email"] == "[REDACTED]"
    assert "[PHONE_REDACTED]" in cleaned["note"]
    assert "[EMAIL_REDACTED]" in cleaned["note"]
    assert report["phones"] >= 1
    assert report["unverified_free_text"] >= 1


def test_anonymizer_redacts_identity_and_source_fields() -> None:
    cleaned, report = anonymize_value(
        {
            "user_id": "user-private",
            "source_id": "source-private",
            "address": "Damascus",
            "institution": "Private School",
        }
    )
    assert set(cleaned.values()) == {"[REDACTED]"}
    assert report["redacted_sensitive_fields"] == 4
