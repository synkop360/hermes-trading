from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import json
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from hermes_trading.domain import (
    Decision,
    Fill,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    Position,
    PositionStatus,
    json_value,
)
from hermes_trading.identifiers import entry_client_order_id
from hermes_trading.providers import ProviderAttempt
from hermes_trading.persistence.migrations import (
    V1_COLUMNS,
    UnsupportedSchemaError,
    migrate,
)


class DuplicateIntentError(ValueError):
    """Raised when an order intent repeats a client order identifier."""


class ActivePositionExists(ValueError):
    """Raised when an asset already has an opening, open, or closing position."""


class RelatedRecordNotFoundError(ValueError):
    """Raised when a write references a missing canonical parent record."""


@dataclass(frozen=True)
class ProjectionItem:
    """One committed canonical outcome awaiting its human-readable projection."""

    item_id: str
    decision_id: str


@dataclass(frozen=True)
class RiskState:
    """The durable, process-independent portfolio risk switch."""

    kill_switch: bool
    reason: str | None
    revision: int
    updated_at: str | None


@dataclass(frozen=True)
class RiskDecisionRecord:
    risk_decision_id: str
    allowed: bool
    reason_code: str
    notional: Decimal | None
    risk_state_revision: int
    decided_at: str
    projected_at: str | None


class StateRepository:
    def __init__(self, connection: sqlite3.Connection, database_path: Path | None = None) -> None:
        self._connection = connection
        self._database_path = database_path

    @classmethod
    def open(cls, path: str | Path) -> StateRepository:
        connection = sqlite3.connect(Path(path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return cls(connection, Path(path))

    @classmethod
    def open_read_only(cls, path: str | Path) -> StateRepository:
        database_uri = Path(path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return cls(connection, Path(path))

    @property
    def database_path(self) -> Path | None:
        return self._database_path

    def close(self) -> None:
        self._connection.close()

    def migrate(self) -> None:
        migrate(self._connection, self._utc_now())
        self._backfill_legacy_projection_payloads()
        self._upgrade_legacy_projection_marker()

    def journal_mode(self) -> str:
        return str(self._connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def schema_version(self) -> int:
        row = self._connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0

    def integrity_check(self) -> str:
        return str(self._connection.execute("PRAGMA integrity_check").fetchone()[0])

    def table_names(self) -> set[str]:
        return {
            row[0]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def record_decision(self, decision: Decision) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO decisions (
                    decision_id, asset, action, reason, candle_id, strategy_version,
                    reference_price, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.asset,
                    decision.action,
                    decision.reason,
                    decision.candle_id,
                    decision.strategy_version,
                    str(decision.reference_price),
                    decision.decided_at,
                ),
            )

    def create_order_intent(self, intent: OrderIntent) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO order_intents (
                        client_order_id, decision_id, position_id, asset, side, order_type,
                        quantity, reference_price, stop_price, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.client_order_id,
                        intent.decision_id,
                        intent.position_id,
                        intent.asset,
                        intent.side,
                        intent.order_type,
                        str(intent.quantity),
                        str(intent.reference_price),
                        self._decimal_text(intent.stop_price),
                        intent.created_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            if "order_intents.client_order_id" in str(error):
                raise DuplicateIntentError(intent.client_order_id) from error
            self._raise_related_record_not_found(error, "order intent")
            raise

    def intent_by_client_id(self, client_order_id: str) -> OrderIntent | None:
        row = self._connection.execute(
            "SELECT * FROM order_intents WHERE client_order_id = ?", (client_order_id,)
        ).fetchone()
        return self._intent_from_row(row) if row is not None else None

    def order_by_client_id(self, client_order_id: str) -> OrderRecord | None:
        row = self._connection.execute(
            "SELECT * FROM orders WHERE client_order_id = ? ORDER BY updated_at DESC LIMIT 1",
            (client_order_id,),
        ).fetchone()
        return self._order_from_row(row) if row is not None else None

    def decision_strategy_version(self, decision_id: str) -> str:
        row = self._connection.execute(
            "SELECT strategy_version FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
        if row is None:
            raise RelatedRecordNotFoundError("decision is missing")
        return str(row["strategy_version"])

    def order_intents(self) -> tuple[OrderIntent, ...]:
        rows = self._connection.execute(
            "SELECT * FROM order_intents ORDER BY created_at, client_order_id"
        )
        return tuple(self._intent_from_row(row) for row in rows)

    def active_positions(self) -> tuple[Position, ...]:
        rows = self._connection.execute(
            """
            SELECT position_id, asset, status, quantity, entry_price, stop_price,
                   strategy_version, opened_at, closed_at, realized_pnl, fees
            FROM positions
            WHERE status IN ('opening', 'open', 'closing')
            ORDER BY opened_at, position_id
            """
        )
        return tuple(self._position_from_row(row) for row in rows)

    def closed_positions(self) -> tuple[Position, ...]:
        rows = self._connection.execute(
            """
            SELECT position_id, asset, status, quantity, entry_price, stop_price,
                   strategy_version, opened_at, closed_at, realized_pnl, fees
            FROM positions
            WHERE status = 'closed' AND closed_at IS NOT NULL
            ORDER BY closed_at, position_id
            """
        )
        return tuple(self._position_from_row(row) for row in rows)

    def position_by_id(self, position_id: str) -> Position | None:
        row = self._connection.execute("SELECT * FROM positions WHERE position_id = ?", (position_id,)).fetchone()
        return self._position_from_row(row) if row is not None else None

    def entry_order_for_position(self, position_id: str) -> OrderRecord | None:
        derived_client_order_id = entry_client_order_id(position_id)
        row = self._connection.execute(
            """SELECT o.* FROM orders o JOIN order_intents i ON i.client_order_id = o.client_order_id
            WHERE i.side = 'buy'
              AND (i.position_id = ? OR (i.position_id IS NULL AND i.client_order_id = ?))
            ORDER BY CASE WHEN i.position_id = ? THEN 0 ELSE 1 END, o.updated_at
            LIMIT 1""",
            (position_id, derived_client_order_id, position_id),
        ).fetchone()
        return self._order_from_row(row) if row is not None else None

    def other_exit_fees(self, position_id: str, client_order_id: str) -> Decimal:
        rows = self._connection.execute(
            """SELECT o.fees FROM orders o
            JOIN order_intents i ON i.client_order_id = o.client_order_id
            WHERE i.position_id = ? AND i.side = 'sell' AND i.client_order_id != ?""",
            (position_id, client_order_id),
        )
        return sum((Decimal(row["fees"]) for row in rows), Decimal("0"))

    def persist_execution_intent(self, decision: Decision, intent: OrderIntent) -> bool:
        """Commit an execution decision and its idempotency key together.

        ``False`` means the client order identifier was already committed.  A
        caller must reconcile that intent rather than submit a second order.
        """
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self._connection.execute(
                "SELECT 1 FROM order_intents WHERE client_order_id = ?",
                (intent.client_order_id,),
            ).fetchone()
            if existing is not None:
                self._connection.rollback()
                return False
            self._connection.execute(
                """
                INSERT OR IGNORE INTO decisions (
                    decision_id, asset, action, reason, candle_id, strategy_version,
                    reference_price, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.asset,
                    decision.action,
                    decision.reason,
                    decision.candle_id,
                    decision.strategy_version,
                    str(decision.reference_price),
                    decision.decided_at,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO order_intents (
                    client_order_id, decision_id, position_id, asset, side, order_type,
                    quantity, reference_price, stop_price, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.client_order_id,
                    intent.decision_id,
                    intent.position_id,
                    intent.asset,
                    intent.side,
                    intent.order_type,
                    str(intent.quantity),
                    str(intent.reference_price),
                    self._decimal_text(intent.stop_price),
                    intent.created_at,
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return True

    def upsert_order(self, order: OrderRecord) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO orders (
                        order_id, client_order_id, exchange_order_id, asset, side, order_type,
                        status, requested_quantity, filled_quantity, average_fill_price, fees,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        client_order_id = excluded.client_order_id,
                        exchange_order_id = excluded.exchange_order_id,
                        asset = excluded.asset,
                        side = excluded.side,
                        order_type = excluded.order_type,
                        status = excluded.status,
                        requested_quantity = excluded.requested_quantity,
                        filled_quantity = excluded.filled_quantity,
                        average_fill_price = excluded.average_fill_price,
                        fees = excluded.fees,
                        updated_at = excluded.updated_at
                    """,
                    self._order_values(order),
                )
        except sqlite3.IntegrityError as error:
            self._raise_related_record_not_found(error, "order")
            raise

    def record_fill(self, fill: Fill) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO order_fills (
                        fill_id, order_id, asset, side, quantity, price, fee, filled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fill.fill_id,
                        fill.order_id,
                        fill.asset,
                        fill.side,
                        str(fill.quantity),
                        str(fill.price),
                        str(fill.fee),
                        fill.filled_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            self._raise_related_record_not_found(error, "fill")
            raise

    def save_position(self, position: Position) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO positions (
                        position_id, asset, status, quantity, entry_price, stop_price,
                        strategy_version, opened_at, closed_at, realized_pnl, fees
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(position_id) DO UPDATE SET
                        asset = excluded.asset,
                        status = excluded.status,
                        quantity = excluded.quantity,
                        entry_price = excluded.entry_price,
                        stop_price = excluded.stop_price,
                        strategy_version = excluded.strategy_version,
                        opened_at = excluded.opened_at,
                        closed_at = excluded.closed_at,
                        realized_pnl = excluded.realized_pnl,
                        fees = excluded.fees
                    """,
                    self._position_values(position),
                )
        except sqlite3.IntegrityError as error:
            if "one_active_position_per_asset" in str(error) or "positions.asset" in str(error):
                raise ActivePositionExists(position.asset) from error
            raise

    def open_position(self, asset: str) -> Position | None:
        row = self._connection.execute(
            """
            SELECT position_id, asset, status, quantity, entry_price, stop_price,
                   strategy_version, opened_at, closed_at, realized_pnl, fees
            FROM positions
            WHERE asset = ? AND status IN ('opening', 'open', 'closing')
            ORDER BY opened_at DESC
            LIMIT 1
            """,
            (asset,),
        ).fetchone()
        return self._position_from_row(row) if row is not None else None

    def export_open_positions(self) -> list[dict[str, str | None]]:
        rows = self._connection.execute(
            """
            SELECT position_id, asset, status, quantity, entry_price, stop_price,
                   strategy_version, opened_at, closed_at, realized_pnl, fees
            FROM positions
            WHERE status IN ('opening', 'open', 'closing')
            ORDER BY opened_at, position_id
            """
        )
        return [dict(row) for row in rows]

    def export_open_orders(self) -> list[dict[str, str | None]]:
        rows = self._connection.execute(
            """
            SELECT order_id, client_order_id, exchange_order_id, asset, side, order_type,
                   status, requested_quantity, filled_quantity, average_fill_price, fees,
                   created_at, updated_at
            FROM orders
            WHERE status IN ('intent', 'validated', 'submitted', 'open', 'partially_filled')
            ORDER BY updated_at, order_id
            """
        )
        return [dict(row) for row in rows]

    def export_system_status(self) -> dict[str, int | str]:
        return {
            "integrity": self.integrity_check(),
            "journal_mode": self.journal_mode(),
            "schema_version": self.schema_version(),
        }

    def risk_state(self) -> RiskState:
        row = self._connection.execute(
            """SELECT status, reason, revision, updated_at FROM risk_state
            WHERE risk_state_id = 'portfolio'"""
        ).fetchone()
        if row is None:
            return RiskState(False, None, 0, None)
        return RiskState(
            kill_switch=row["status"] == "halted",
            reason=row["reason"],
            revision=int(row["revision"]),
            updated_at=row["updated_at"],
        )

    def set_risk_state(
        self,
        *,
        kill_switch: bool,
        reason: str | None,
        updated_at: datetime | None = None,
    ) -> RiskState:
        timestamp = (updated_at or datetime.now(UTC)).isoformat()
        status = "halted" if kill_switch else "active"
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO risk_state (risk_state_id, status, reason, revision, updated_at)
                VALUES ('portfolio', ?, ?, 1, ?)
                ON CONFLICT(risk_state_id) DO UPDATE SET
                    status = excluded.status,
                    reason = excluded.reason,
                    revision = risk_state.revision + 1,
                    updated_at = excluded.updated_at
                """,
                (status, reason, timestamp),
            )
        return self.risk_state()

    def open_incident(
        self,
        incident_id: str,
        reason: str,
        *,
        severity: str = "warning",
        opened_at: datetime | None = None,
    ) -> None:
        timestamp = (opened_at or datetime.now(UTC)).isoformat()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, status, reason, severity, revision, opened_at, resolved_at
                ) VALUES (?, 'open', ?, ?, 1, ?, NULL)
                ON CONFLICT(incident_id) DO UPDATE SET
                    status = 'open', reason = excluded.reason, severity = excluded.severity,
                    revision = incidents.revision + 1, resolved_at = NULL
                """,
                (incident_id, reason, severity, timestamp),
            )

    def resolve_incident(self, incident_id: str, *, resolved_at: datetime | None = None) -> None:
        timestamp = (resolved_at or datetime.now(UTC)).isoformat()
        with self._connection:
            self._connection.execute(
                """UPDATE incidents
                SET status = 'resolved', reason = 'resolved', resolved_at = ?
                WHERE incident_id = ? AND status != 'resolved'""",
                (timestamp, incident_id),
            )

    def has_unresolved_incidents(self) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM incidents WHERE status = 'open' LIMIT 1"
        ).fetchone()
        return row is not None

    def mark_reconciliation_complete(self, completed_at: datetime | None = None) -> None:
        timestamp = (completed_at or datetime.now(UTC)).isoformat()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, status, reason, severity, revision, opened_at, resolved_at
                ) VALUES ('reconciliation:latest', 'resolved', 'completed', 'info', 1, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    status = 'resolved', reason = 'completed', severity = 'info',
                    revision = incidents.revision + 1, opened_at = excluded.opened_at,
                    resolved_at = excluded.resolved_at
                """,
                (timestamp, timestamp),
            )

    def has_fresh_reconciliation(
        self,
        *,
        since: datetime,
        now: datetime,
        max_age_seconds: int,
    ) -> bool:
        row = self._connection.execute(
            """SELECT resolved_at FROM incidents
            WHERE incident_id = 'reconciliation:latest' AND status = 'resolved'"""
        ).fetchone()
        if row is None or row["resolved_at"] is None:
            return False
        completed_at = datetime.fromisoformat(row["resolved_at"])
        if completed_at.tzinfo is None:
            return False
        completed_at = completed_at.astimezone(UTC)
        utc_now = now.astimezone(UTC)
        return (
            completed_at >= since.astimezone(UTC)
            and completed_at <= utc_now
            and (utc_now - completed_at).total_seconds() <= max_age_seconds
        )

    def record_risk_decision(
        self,
        *,
        risk_decision_id: str,
        allowed: bool,
        reason_code: str,
        notional: Decimal | None,
        decided_at: datetime,
        activate_kill_switch: bool = False,
    ) -> RiskDecisionRecord:
        """Commit a decision and any automatic portfolio halt together."""
        timestamp = decided_at.astimezone(UTC).isoformat()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if activate_kill_switch:
                self._connection.execute(
                    """
                    INSERT INTO risk_state (risk_state_id, status, reason, revision, updated_at)
                    VALUES ('portfolio', 'halted', ?, 1, ?)
                    ON CONFLICT(risk_state_id) DO UPDATE SET
                        status = 'halted', reason = excluded.reason,
                        revision = risk_state.revision + 1, updated_at = excluded.updated_at
                    """,
                    (reason_code, timestamp),
                )
            state = self._connection.execute(
                "SELECT revision FROM risk_state WHERE risk_state_id = 'portfolio'"
            ).fetchone()
            revision = int(state["revision"]) if state is not None else 0
            self._connection.execute(
                """
                INSERT INTO risk_decisions (
                    risk_decision_id, allowed, reason_code, notional,
                    risk_state_revision, decided_at, projected_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    risk_decision_id,
                    int(allowed),
                    reason_code,
                    self._decimal_text(notional),
                    revision,
                    timestamp,
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        record = self.risk_decision(risk_decision_id)
        if record is None:
            raise RuntimeError("risk decision commit returned no record")
        return record

    def risk_decision(self, risk_decision_id: str) -> RiskDecisionRecord | None:
        row = self._connection.execute(
            "SELECT * FROM risk_decisions WHERE risk_decision_id = ?",
            (risk_decision_id,),
        ).fetchone()
        return self._risk_decision_from_row(row) if row is not None else None

    def risk_decisions(self, *, pending_only: bool = False) -> list[RiskDecisionRecord]:
        query = "SELECT * FROM risk_decisions"
        if pending_only:
            query += " WHERE projected_at IS NULL"
        query += " ORDER BY decided_at, risk_decision_id"
        return [self._risk_decision_from_row(row) for row in self._connection.execute(query)]

    def pending_risk_decision_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM risk_decisions WHERE projected_at IS NULL"
        ).fetchone()
        return int(row[0])

    def acknowledge_risk_decision_projection(
        self, risk_decision_id: str, projected_at: datetime | None = None
    ) -> None:
        timestamp = (projected_at or datetime.now(UTC)).isoformat()
        with self._connection:
            self._connection.execute(
                """UPDATE risk_decisions SET projected_at = ?
                WHERE risk_decision_id = ? AND projected_at IS NULL""",
                (timestamp, risk_decision_id),
            )

    def export_canonical_tables(self) -> dict[str, list[dict[str, object]]]:
        return {
            table_name: [
                dict(row)
                for row in self._connection.execute(
                    f'SELECT * FROM "{table_name}" ORDER BY 1'
                )
            ]
            for table_name in V1_COLUMNS
        }

    def mark_candle_processed(self, asset: str, timeframe: str, close_timestamp: str) -> None:
        candle_id = f"{asset}:{timeframe}:{close_timestamp}"
        with self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO candles_processed (
                    candle_id, asset, timeframe, close_timestamp, processed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (candle_id, asset, timeframe, close_timestamp, self._utc_now()),
            )

    def persist_candle_outcome(
        self,
        asset: str,
        timeframe: str,
        close_timestamp: str,
        decision: Decision,
        *,
        intents: list[OrderIntent] | tuple[OrderIntent, ...] = (),
        orders: list[OrderRecord] | tuple[OrderRecord, ...] = (),
        fills: list[Fill] | tuple[Fill, ...] = (),
        positions: list[Position] | tuple[Position, ...] = (),
    ) -> bool:
        """Atomically persist a candle's canonical decision and financial outcome.

        The return value is false when another worker already committed this candle.
        Callers must project JSONL or snapshots only after a true return.
        """
        candle_id = f"{asset}:{timeframe}:{close_timestamp}"
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            duplicate = self._connection.execute(
                "SELECT 1 FROM candles_processed WHERE asset = ? AND timeframe = ? AND close_timestamp = ?",
                (asset, timeframe, close_timestamp),
            ).fetchone()
            if duplicate is not None:
                self._connection.rollback()
                return False
            self._connection.execute(
                """
                INSERT INTO decisions (
                    decision_id, asset, action, reason, candle_id, strategy_version,
                    reference_price, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id, decision.asset, decision.action, decision.reason,
                    decision.candle_id, decision.strategy_version, str(decision.reference_price),
                    decision.decided_at,
                ),
            )
            for intent in intents:
                self._connection.execute(
                    """
                    INSERT INTO order_intents (
                        client_order_id, decision_id, position_id, asset, side, order_type,
                        quantity, reference_price, stop_price, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent.client_order_id, intent.decision_id, intent.position_id,
                        intent.asset, intent.side, intent.order_type, str(intent.quantity),
                        str(intent.reference_price), self._decimal_text(intent.stop_price), intent.created_at,
                    ),
                )
            for order in orders:
                self._connection.execute(
                    """
                    INSERT INTO orders (
                        order_id, client_order_id, exchange_order_id, asset, side, order_type,
                        status, requested_quantity, filled_quantity, average_fill_price, fees,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        status = excluded.status, filled_quantity = excluded.filled_quantity,
                        average_fill_price = excluded.average_fill_price, fees = excluded.fees,
                        updated_at = excluded.updated_at
                    """,
                    self._order_values(order),
                )
            for fill in fills:
                self._connection.execute(
                    """INSERT INTO order_fills (fill_id, order_id, asset, side, quantity, price, fee, filled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (fill.fill_id, fill.order_id, fill.asset, fill.side, str(fill.quantity), str(fill.price), str(fill.fee), fill.filled_at),
                )
            for position in positions:
                self._connection.execute(
                    """
                    INSERT INTO positions (position_id, asset, status, quantity, entry_price, stop_price,
                        strategy_version, opened_at, closed_at, realized_pnl, fees)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(position_id) DO UPDATE SET
                        status = excluded.status, closed_at = excluded.closed_at,
                        realized_pnl = excluded.realized_pnl, fees = excluded.fees
                    """,
                    self._position_values(position),
                )
            self._connection.execute(
                """INSERT INTO projection_payloads (decision_id, payload, created_at)
                VALUES (?, ?, ?)""",
                (
                    decision.decision_id,
                    self._projection_payload(decision, intents, orders, fills, positions),
                    self._utc_now(),
                ),
            )
            self._connection.execute(
                """INSERT INTO candles_processed (candle_id, asset, timeframe, close_timestamp, processed_at)
                VALUES (?, ?, ?, ?, ?)""",
                (candle_id, asset, timeframe, close_timestamp, self._utc_now()),
            )
            projection_item_id = self._projection_item_id(decision.decision_id)
            self._connection.execute(
                """
                INSERT INTO incidents (incident_id, status, reason, severity, revision, opened_at, resolved_at)
                VALUES (?, 'open', ?, 'warning', 1, ?, NULL)
                """,
                (
                    projection_item_id,
                    f"canonical_commit_pending_projection:{decision.decision_id}",
                    self._utc_now(),
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return True

    def projection_pending(self) -> bool:
        return self.has_pending_projection_items()

    def has_pending_projection_items(self) -> bool:
        """Cheap existence check used after a bounded outbox recovery batch."""
        row = self._connection.execute(
            """SELECT 1 FROM incidents
            WHERE incident_id LIKE 'projection:item:%' AND status = 'open' LIMIT 1"""
        ).fetchone()
        return row is not None

    def pending_projection_items(self, limit: int = 8) -> tuple[ProjectionItem, ...]:
        if limit <= 0:
            raise ValueError("projection recovery limit must be positive")
        rows = self._connection.execute(
            """SELECT incident_id FROM incidents
            WHERE incident_id LIKE 'projection:item:%' AND status = 'open'
            ORDER BY opened_at, incident_id LIMIT ?""",
            (limit,),
        )
        prefix = "projection:item:"
        return tuple(
            ProjectionItem(row["incident_id"], row["incident_id"][len(prefix):])
            for row in rows
        )

    def acknowledge_projection_item(self, item_id: str) -> bool:
        """Resolve only this outbox item after its own artifacts are durable."""
        if not item_id.startswith("projection:item:"):
            raise ValueError("invalid projection item id")
        with self._connection:
            cursor = self._connection.execute(
                """UPDATE incidents SET status = 'resolved', reason = 'resolved', resolved_at = ?
                WHERE incident_id = ? AND status = 'open'""",
                (self._utc_now(), item_id),
            )
        return cursor.rowcount == 1

    def pending_projection_item(self, item_id: str) -> ProjectionItem | None:
        if not item_id.startswith("projection:item:"):
            raise ValueError("invalid projection item id")
        row = self._connection.execute(
            """SELECT incident_id FROM incidents
            WHERE incident_id = ? AND status = 'open'""",
            (item_id,),
        ).fetchone()
        if row is None:
            return None
        prefix = "projection:item:"
        return ProjectionItem(row["incident_id"], row["incident_id"][len(prefix):])

    def projection_outcome(self, item: ProjectionItem) -> dict[str, object]:
        """Return the immutable payload committed with one projection item."""
        row = self._connection.execute(
            "SELECT payload FROM projection_payloads WHERE decision_id = ?",
            (item.decision_id,),
        ).fetchone()
        if row is None:
            raise RelatedRecordNotFoundError(f"projection payload {item.decision_id}")
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            raise RelatedRecordNotFoundError(f"invalid projection payload {item.decision_id}")
        return payload

    @staticmethod
    def _projection_payload(
        decision: Decision,
        intents: list[OrderIntent] | tuple[OrderIntent, ...],
        orders: list[OrderRecord] | tuple[OrderRecord, ...],
        fills: list[Fill] | tuple[Fill, ...],
        positions: list[Position] | tuple[Position, ...],
    ) -> str:
        normalized_positions = json_value(positions)
        payload = {
            "decision": json_value(decision),
            "intents": json_value(intents),
            "orders": json_value(orders),
            "fills": json_value(fills),
            "positions": normalized_positions,
            "closed_positions": [
                position for position in normalized_positions if position["status"] == "closed"
            ],
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _projection_item_id(decision_id: str) -> str:
        return f"projection:item:{decision_id}"

    def _backfill_legacy_projection_payloads(self) -> None:
        """Freeze a one-time payload for outcomes written before migration v2.

        New outcomes never reconstruct their projection from mutable tables. A
        v1 database has no historical transition payload, so this migration
        records its current canonical representation once before any recovery.
        """
        decision_ids = [
            row["decision_id"]
            for row in self._connection.execute(
                """SELECT decision_id FROM decisions
                WHERE decision_id NOT IN (SELECT decision_id FROM projection_payloads)
                ORDER BY decision_id"""
            )
        ]
        if not decision_ids:
            return
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for decision_id in decision_ids:
                decision = self._connection.execute(
                    "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
                ).fetchone()
                intents = list(self._connection.execute(
                    "SELECT * FROM order_intents WHERE decision_id = ? ORDER BY client_order_id",
                    (decision_id,),
                ))
                orders = list(self._connection.execute(
                    """SELECT orders.* FROM orders
                    JOIN order_intents ON order_intents.client_order_id = orders.client_order_id
                    WHERE order_intents.decision_id = ? ORDER BY orders.order_id""",
                    (decision_id,),
                ))
                fills = list(self._connection.execute(
                    """SELECT order_fills.* FROM order_fills
                    JOIN orders ON orders.order_id = order_fills.order_id
                    JOIN order_intents ON order_intents.client_order_id = orders.client_order_id
                    WHERE order_intents.decision_id = ? ORDER BY order_fills.fill_id""",
                    (decision_id,),
                ))
                positions = list(self._connection.execute(
                    """SELECT DISTINCT positions.* FROM positions
                    JOIN order_intents ON order_intents.position_id = positions.position_id
                    WHERE order_intents.decision_id = ? ORDER BY positions.position_id""",
                    (decision_id,),
                ))
                if decision is None:
                    raise RelatedRecordNotFoundError(f"legacy projection decision {decision_id}")
                position_payloads = [dict(row) for row in positions]
                payload = {
                    "decision": dict(decision),
                    "intents": [dict(row) for row in intents],
                    "orders": [dict(row) for row in orders],
                    "fills": [dict(row) for row in fills],
                    "positions": position_payloads,
                    "closed_positions": [
                        position for position in position_payloads if position["status"] == "closed"
                    ],
                }
                self._connection.execute(
                    """INSERT OR IGNORE INTO projection_payloads (decision_id, payload, created_at)
                    VALUES (?, ?, ?)""",
                    (decision_id, json.dumps(payload, separators=(",", ":"), sort_keys=True), self._utc_now()),
                )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def _upgrade_legacy_projection_marker(self) -> None:
        """Turn the former global recovery flag into independently acked items.

        Replaying all canonical decisions is deliberate: JSONL event keys make it
        idempotent, while a legacy marker has no trustworthy ownership boundary.
        """
        legacy = self._connection.execute(
            "SELECT 1 FROM incidents WHERE incident_id = 'projection:pending' AND status = 'open'"
        ).fetchone()
        if legacy is None:
            return
        now = self._utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            for row in self._connection.execute("SELECT decision_id FROM decisions ORDER BY decision_id"):
                decision_id = row["decision_id"]
                self._connection.execute(
                    """INSERT OR IGNORE INTO incidents
                    (incident_id, status, reason, severity, revision, opened_at, resolved_at)
                    VALUES (?, 'open', ?, 'warning', 1, ?, NULL)""",
                    (
                        self._projection_item_id(decision_id),
                        f"canonical_commit_pending_projection:{decision_id}",
                        now,
                    ),
                )
            self._connection.execute(
                """UPDATE incidents SET status = 'resolved', reason = 'superseded_by_projection_outbox',
                resolved_at = ? WHERE incident_id = 'projection:pending' AND status = 'open'""",
                (now,),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def is_candle_processed(self, asset: str, timeframe: str, close_timestamp: str) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM candles_processed
            WHERE asset = ? AND timeframe = ? AND close_timestamp = ?
            """,
            (asset, timeframe, close_timestamp),
        ).fetchone()
        return row is not None

    def record_market_data_failure(self, source: str, reason: str) -> int:
        """Persist a stable incident and return its consecutive failure count."""
        incident_id = f"market-data:{source}"
        now = self._utc_now()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                INSERT INTO incidents (
                    incident_id, status, reason, severity, revision, opened_at, resolved_at
                ) VALUES (?, 'open', ?, 'warning', 1, ?, NULL)
                ON CONFLICT(incident_id) DO UPDATE SET
                    status = 'open',
                    reason = excluded.reason,
                    severity = 'warning',
                    revision = CASE
                        WHEN incidents.status = 'resolved' THEN 1
                        ELSE incidents.revision + 1
                    END,
                    opened_at = CASE
                        WHEN incidents.status = 'resolved' THEN excluded.opened_at
                        ELSE incidents.opened_at
                    END,
                    resolved_at = NULL
                RETURNING revision
                """,
                (incident_id, reason, now),
            ).fetchone()
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        if row is None:
            raise RuntimeError("market data incident increment returned no revision")
        return int(row["revision"])

    def resolve_market_data_incident(self, source: str) -> None:
        """Resolve and reset an incident after the source has recovered."""
        incident_id = f"market-data:{source}"
        with self._connection:
            self._connection.execute(
                """
                UPDATE incidents
                SET status = 'resolved', reason = 'resolved', revision = 0, resolved_at = ?
                WHERE incident_id = ? AND status != 'resolved'
                """,
                (self._utc_now(), incident_id),
            )

    def market_data_failure_count(self, source: str) -> int:
        row = self._connection.execute(
            "SELECT status, revision FROM incidents WHERE incident_id = ?",
            (f"market-data:{source}",),
        ).fetchone()
        if row is None or row["status"] == "resolved":
            return 0
        return int(row["revision"])

    def provider_attempts(self) -> tuple[ProviderAttempt, ...]:
        rows = self._connection.execute(
            "SELECT provider, status, reason, attempted_at FROM provider_attempts ORDER BY attempted_at, provider_attempt_id"
        )
        attempts: list[ProviderAttempt] = []
        for row in rows:
            try:
                details = json.loads(row["reason"] or "{}")
                attempted_at = datetime.fromisoformat(row["attempted_at"]).astimezone(UTC)
                attempts.append(ProviderAttempt(
                    str(row["provider"]), str(details["model"]), str(row["status"]), attempted_at,
                    int(details.get("retry_after_seconds", 0)), details.get("latency_ms"),
                ))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(attempts)

    def record_provider_attempt(self, attempt: ProviderAttempt) -> None:
        details = json.dumps({"model": attempt.model, "retry_after_seconds": attempt.retry_after_seconds, "latency_ms": attempt.latency_ms}, separators=(",", ":"))
        attempt_id = str(uuid5(NAMESPACE_URL, f"hermes-trading:provider:{attempt.provider}:{attempt.model}:{attempt.status}:{attempt.attempted_at.isoformat()}"))
        with self._connection:
            self._connection.execute(
                """INSERT OR REPLACE INTO provider_attempts
                (provider_attempt_id, provider, status, reason, revision, attempted_at)
                VALUES (?, ?, ?, ?, 1, ?)""",
                (attempt_id, attempt.provider, attempt.status, details, attempt.attempted_at.isoformat()),
            )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _decimal_text(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _raise_related_record_not_found(
        error: sqlite3.IntegrityError, record_name: str
    ) -> None:
        if "FOREIGN KEY constraint failed" in str(error):
            raise RelatedRecordNotFoundError(
                f"{record_name} references a missing parent record"
            ) from error

    @staticmethod
    def _order_values(order: OrderRecord) -> tuple[str | None, ...]:
        return (
            order.order_id,
            order.client_order_id,
            order.exchange_order_id,
            order.asset,
            order.side,
            order.order_type,
            order.status.value,
            str(order.requested_quantity),
            str(order.filled_quantity),
            StateRepository._decimal_text(order.average_fill_price),
            str(order.fees),
            order.created_at,
            order.updated_at,
        )

    @staticmethod
    def _position_values(position: Position) -> tuple[str | None, ...]:
        return (
            position.position_id,
            position.asset,
            position.status.value,
            str(position.quantity),
            str(position.entry_price),
            str(position.stop_price),
            position.strategy_version,
            position.opened_at,
            position.closed_at,
            str(position.realized_pnl),
            str(position.fees),
        )

    @staticmethod
    def _position_from_row(row: sqlite3.Row) -> Position:
        return Position(
            position_id=row["position_id"],
            asset=row["asset"],
            status=PositionStatus(row["status"]),
            quantity=Decimal(row["quantity"]),
            entry_price=Decimal(row["entry_price"]),
            stop_price=Decimal(row["stop_price"]),
            strategy_version=row["strategy_version"],
            opened_at=row["opened_at"],
            closed_at=row["closed_at"],
            realized_pnl=Decimal(row["realized_pnl"]),
            fees=Decimal(row["fees"]),
        )

    @staticmethod
    def _order_from_row(row: sqlite3.Row) -> OrderRecord:
        return OrderRecord(
            order_id=row["order_id"],
            client_order_id=row["client_order_id"],
            exchange_order_id=row["exchange_order_id"],
            asset=row["asset"],
            side=row["side"],
            order_type=row["order_type"],
            status=OrderStatus(row["status"]),
            requested_quantity=Decimal(row["requested_quantity"]),
            filled_quantity=Decimal(row["filled_quantity"]),
            average_fill_price=(
                Decimal(row["average_fill_price"])
                if row["average_fill_price"] is not None
                else None
            ),
            fees=Decimal(row["fees"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _intent_from_row(row: sqlite3.Row) -> OrderIntent:
        return OrderIntent(
            client_order_id=row["client_order_id"],
            decision_id=row["decision_id"],
            position_id=row["position_id"],
            asset=row["asset"],
            side=row["side"],
            order_type=row["order_type"],
            quantity=Decimal(row["quantity"]),
            reference_price=Decimal(row["reference_price"]),
            stop_price=Decimal(row["stop_price"]) if row["stop_price"] is not None else None,
            created_at=row["created_at"],
        )

    @staticmethod
    def _risk_decision_from_row(row: sqlite3.Row) -> RiskDecisionRecord:
        return RiskDecisionRecord(
            risk_decision_id=row["risk_decision_id"],
            allowed=bool(row["allowed"]),
            reason_code=row["reason_code"],
            notional=Decimal(row["notional"]) if row["notional"] is not None else None,
            risk_state_revision=int(row["risk_state_revision"]),
            decided_at=row["decided_at"],
            projected_at=row["projected_at"],
        )


__all__ = [
    "ActivePositionExists",
    "DuplicateIntentError",
    "RelatedRecordNotFoundError",
    "RiskState",
    "RiskDecisionRecord",
    "StateRepository",
    "UnsupportedSchemaError",
]
