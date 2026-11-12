from __future__ import annotations


_KRAKEN_POSITION_PREFIX = "kraken:"


def reconciled_entry_position_id(client_order_id: str) -> str:
    if not isinstance(client_order_id, str) or not client_order_id.strip():
        raise ValueError("client order id must be a non-empty string")
    return f"{_KRAKEN_POSITION_PREFIX}{client_order_id}"


def entry_client_order_id(position_id: str) -> str | None:
    if not isinstance(position_id, str) or not position_id.startswith(_KRAKEN_POSITION_PREFIX):
        return None
    client_order_id = position_id[len(_KRAKEN_POSITION_PREFIX):]
    return client_order_id or None


__all__ = ["entry_client_order_id", "reconciled_entry_position_id"]
