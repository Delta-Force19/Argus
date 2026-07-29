from unicodedata import category


def is_probably_readable_text(value: str | None) -> bool:
    """Reject empty or binary-like text without making language assumptions."""

    if value is None:
        return False
    normalized = value.strip()
    if not normalized:
        return False

    suspicious_count = sum(
        character == "\ufffd"
        or (
            category(character) in {"Cc", "Cs"}
            and character not in "\n\r\t"
        )
        for character in normalized
    )
    allowed_suspicious_count = max(2, len(normalized) // 200)
    return suspicious_count <= allowed_suspicious_count
