from __future__ import annotations

import sqlite3
from collections.abc import Sequence


class UnsupportedSchemaError(RuntimeError):
    """Raised when a database records migrations this program cannot read."""


Migration = tuple[int, Sequence[str]]


MIGRATIONS: tuple[Migration, ...] = (
    (
        1,
        (
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS candles_processed (
                candle_id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                close_timestamp TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                UNIQUE(asset, timeframe, close_timestamp)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL,
                candle_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                reference_price TEXT NOT NULL,
                decided_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_intents (
                client_order_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                position_id TEXT,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                quantity TEXT NOT NULL,
                reference_price TEXT NOT NULL,
                stop_price TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                client_order_id TEXT NOT NULL,
                exchange_order_id TEXT,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_quantity TEXT NOT NULL,
                filled_quantity TEXT NOT NULL,
                average_fill_price TEXT,
                fees TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                fee TEXT NOT NULL,
                filled_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS positions (
                position_id TEXT PRIMARY KEY,
                asset TEXT NOT NULL,
                status TEXT NOT NULL,
                quantity TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                stop_price TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                realized_pnl TEXT NOT NULL,
                fees TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS risk_state (
                risk_state_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS strategy_versions (
                strategy_version_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                revision INTEGER NOT NULL,
                source_event_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS provider_attempts (
                provider_attempt_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                attempted_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS cost_events (
                cost_event_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                revision INTEGER NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS qualification_runs (
                qualification_run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS qualification_checks (
                qualification_check_id TEXT PRIMARY KEY,
                qualification_run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                checked_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_request_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                requested_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS control_commands (
                control_command_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT,
                revision INTEGER NOT NULL,
                requested_at TEXT NOT NULL,
                completed_at TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                severity TEXT NOT NULL,
                revision INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_position_per_asset
            ON positions(asset)
            WHERE status IN ('opening', 'open', 'closing')
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_client_order_id
            ON order_intents(client_order_id)
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS unique_processed_candle
            ON candles_processed(asset, timeframe, close_timestamp)
            """,
        ),
    ),
)


def migrate(connection: sqlite3.Connection, applied_at: str) -> None:
    """Apply every known migration once, rejecting databases from newer code."""
    known_versions = {version for version, _ in MIGRATIONS}

    with connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied_versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if not applied_versions.issubset(known_versions):
            raise UnsupportedSchemaError(
                f"unsupported schema migration versions: {sorted(applied_versions - known_versions)}"
            )

        for version, statements in MIGRATIONS:
            if version in applied_versions:
                continue
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, applied_at),
            )
