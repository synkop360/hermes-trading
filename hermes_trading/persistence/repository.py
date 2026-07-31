from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sqlite3

from hermes_trading.domain import (
    Decision,
    Fill,
    OrderIntent,
    OrderRecord,
    OrderStatus,
    Position,
    PositionStatus,
)
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


class StateRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, path: str | Path) -> StateRepository:
        connection = sqlite3.connect(Path(path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return cls(connection)

    @classmethod
    def open_read_only(cls, path: str | Path) -> StateRepository:
        database_uri = Path(path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return cls(connection)

    def close(self) -> None:
        self._connection.close()

    def migrate(self) -> None:
        migrate(self._connection, self._utc_now())
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
        """Return the exact canonical records belonging to one projection item."""
        decision = self._connection.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (item.decision_id,)
        ).fetchone()
        if decision is None:
            raise RelatedRecordNotFoundError(f"projection decision {item.decision_id}")
        orders = list(self._connection.execute(
            """SELECT orders.* FROM orders
            JOIN order_intents ON order_intents.client_order_id = orders.client_order_id
            WHERE order_intents.decision_id = ? ORDER BY orders.order_id""",
            (item.decision_id,),
        ))
        fills = list(self._connection.execute(
            """SELECT order_fills.* FROM order_fills
            JOIN orders ON orders.order_id = order_fills.order_id
            JOIN order_intents ON order_intents.client_order_id = orders.client_order_id
            WHERE order_intents.decision_id = ? ORDER BY order_fills.fill_id""",
            (item.decision_id,),
        ))
        positions = list(self._connection.execute(
            """SELECT DISTINCT positions.* FROM positions
            JOIN order_intents ON order_intents.position_id = positions.position_id
            WHERE order_intents.decision_id = ? ORDER BY positions.position_id""",
            (item.decision_id,),
        ))
        return {
            "decision": dict(decision),
            "orders": tuple(dict(row) for row in orders),
            "fills": tuple(dict(row) for row in fills),
            "positions": tuple(dict(row) for row in positions),
            "closed_positions": tuple(dict(row) for row in positions if row["status"] == "closed"),
        }

    @staticmethod
    def _projection_item_id(decision_id: str) -> str:
        return f"projection:item:{decision_id}"

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


__all__ = [
    "ActivePositionExists",
    "DuplicateIntentError",
    "RelatedRecordNotFoundError",
    "StateRepository",
    "UnsupportedSchemaError",
]
