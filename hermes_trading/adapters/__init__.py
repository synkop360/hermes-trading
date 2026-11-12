class SchemaError(RuntimeError):
    """Raised when an adapter returns data that does not match its contract."""


def require_schema(payload: dict, expected_version: int) -> dict:
    if payload.get("schema_version") != expected_version:
        raise SchemaError(
            f"adapter schema mismatch: expected {expected_version}, got {payload.get('schema_version')}"
        )
    return payload
