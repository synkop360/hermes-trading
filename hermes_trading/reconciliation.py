from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from hermes_trading.domain import OrderRecord, OrderStatus, Position, PositionStatus, json_value
from hermes_trading.exchange import ExchangeGateway, ExchangeOrder
from hermes_trading.identifiers import reconciled_entry_position_id
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import StateRepository

if TYPE_CHECKING:
    from hermes_trading.execution import ExecutionService


IssueSeverity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class ReconciliationIssue:
    code: str
    severity: IssueSeverity
    message: str
    blocking: bool = True
    client_order_id: str | None = None
    exchange_order_id: str | None = None


@dataclass(frozen=True)
class ReconciliationReport:
    safe_for_new_entries: bool
    issues: tuple[ReconciliationIssue, ...]
    balances: dict[str, Decimal]
    next_reconciliation_seconds: int


class ReconciliationService:
    """Fail-closed comparison of persisted intent state and one exchange cycle."""

    def __init__(
        self,
        repository: StateRepository,
        *,
        projector: ArtifactProjector | None = None,
        normal_cadence_seconds: int = 60,
        fast_cadence_seconds: int = 5,
    ) -> None:
        if fast_cadence_seconds <= 0 or normal_cadence_seconds < fast_cadence_seconds:
            raise ValueError("reconciliation cadences must be positive and ordered")
        self.repository = repository
        self.projector = projector or _default_projector(repository)
        self.normal_cadence_seconds = normal_cadence_seconds
        self.fast_cadence_seconds = fast_cadence_seconds

    async def run(self, gateway: ExchangeGateway) -> ReconciliationReport:
        remote_orders = await gateway.fetch_orders()
        balances = await gateway.fetch_balances()
        issues: list[ReconciliationIssue] = []
        intents = {intent.client_order_id: intent for intent in self.repository.order_intents()}
        local_orders = {
            order.client_order_id: order
            for order in (self.repository.order_by_client_id(intent_id) for intent_id in intents)
            if order is not None
        }
        remote_by_client_id: dict[str, list[ExchangeOrder]] = {}
        for remote in remote_orders:
            if remote.client_order_id is not None:
                remote_by_client_id.setdefault(remote.client_order_id, []).append(remote)

        for remote in remote_orders:
            if remote.client_order_id not in intents:
                issues.append(
                    ReconciliationIssue(
                        "unknown_remote_order",
                        "critical",
                        "Remote order has no matching persisted client order id.",
                        exchange_order_id=remote.exchange_order_id,
                    )
                )

        for client_order_id, matching_orders in remote_by_client_id.items():
            if len(matching_orders) > 1:
                issues.append(
                    ReconciliationIssue(
                        "duplicate_remote_client_id",
                        "critical",
                        "Multiple remote orders share one persisted client order id.",
                        client_order_id=client_order_id,
                    )
                )

        for client_order_id, intent in intents.items():
            matching_orders = remote_by_client_id.get(client_order_id, [])
            local = local_orders.get(client_order_id)
            if len(matching_orders) > 1:
                continue
            remote = matching_orders[0] if matching_orders else None
            if remote is None:
                if local is None or local.status not in _TERMINAL_STATUSES:
                    issues.append(
                        ReconciliationIssue(
                            "missing_remote_order",
                            "critical",
                            "Persisted order intent was not found at the exchange.",
                            client_order_id=client_order_id,
                        )
                    )
                elif local.side == "buy" and local.status is OrderStatus.FILLED:
                    position_id = intent.position_id or reconciled_entry_position_id(intent.client_order_id)
                    if self.repository.position_by_id(position_id) is None:
                        issues.append(ReconciliationIssue("missing_expected_position", "critical", "A terminal buy has no persisted local position.", client_order_id=client_order_id))
                continue
            comparison_issues = _order_mismatches(
                intent.asset, intent.side, intent.quantity, local, remote
            )
            issues.extend(comparison_issues)
            self._persist_remote_order(
                intent,
                remote,
                reconstruct_position=not any(issue.blocking for issue in comparison_issues),
            )
            active_protective_stop = (
                intent.order_type == "stop-loss"
                and remote.side == "sell"
                and remote.order_type == "stop-loss"
                and remote.asset == intent.asset
                and remote.amount == intent.quantity
                and remote.trigger_price == intent.stop_price
                and _canonical_status(remote.status)
                in {OrderStatus.SUBMITTED, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}
            )
            if (
                _canonical_status(remote.status) not in _TERMINAL_STATUSES
                and not active_protective_stop
            ):
                issues.append(
                    ReconciliationIssue(
                        "transient_remote_order",
                        "warning",
                        "Remote order remains pending or partially filled; a follow-up reconciliation is required.",
                        client_order_id=client_order_id,
                        exchange_order_id=remote.exchange_order_id,
                    )
                )

        issues.extend(_balance_issues(balances, self.repository.active_positions(), remote_orders))
        self._persist_issues(issues)
        issues.extend(self._persistent_blockers(issues))
        safe = not any(issue.blocking for issue in issues)
        if safe:
            self.repository.mark_reconciliation_complete()
        report = ReconciliationReport(
            safe_for_new_entries=safe,
            issues=tuple(issues),
            balances=balances,
            next_reconciliation_seconds=self._next_cadence(issues),
        )
        self._project(report)
        return report

    async def reconcile_pending_order(
        self, gateway: ExchangeGateway, client_order_id: str
    ) -> ReconciliationReport:
        if self.repository.intent_by_client_id(client_order_id) is None:
            raise ValueError(f"unknown client order id {client_order_id!r}")
        return await self.run(gateway)

    async def verify_protective_stops(
        self, gateway: ExchangeGateway, execution: "ExecutionService"
    ) -> ReconciliationReport:
        """Fail closed at startup when an open position lacks its active stop.

        This separate pre-entry check is deliberately not wired into the worker
        yet.  It is callable by the future live startup path and only operates
        through the exchange gateway supplied by that path.
        """
        positions = self.repository.active_positions()
        try:
            remote_orders = await gateway.fetch_orders()
        except Exception as error:
            reason = f"protective_stop_lookup_failed:{type(error).__name__}"
            now = datetime.now(UTC)
            self.repository.set_risk_state(
                kill_switch=True, reason=reason, updated_at=now
            )
            self.repository.open_incident(
                "protective-stop:startup", reason, severity="critical", opened_at=now
            )
            issues = [
                ReconciliationIssue(
                    "protective_stop_lookup_failed",
                    "critical",
                    "Protective stops could not be verified at the exchange.",
                )
            ]
            for position in positions:
                await self._emergency_close_or_record(
                    execution, position, gateway, reason
                )
            report = ReconciliationReport(
                safe_for_new_entries=False,
                issues=tuple(issues),
                balances={},
                next_reconciliation_seconds=self.fast_cadence_seconds,
            )
            self._project(report)
            return report
        issues: list[ReconciliationIssue] = []
        remote_by_client_id = {
            order.client_order_id: order
            for order in remote_orders
            if order.client_order_id is not None
        }
        for position in positions:
            if self._has_matching_protective_stop(position, remote_by_client_id):
                continue
            issues.append(
                ReconciliationIssue(
                    "missing_protective_stop",
                    "critical",
                    f"Open {position.asset} position has no active protective stop.",
                )
            )
            await self._emergency_close_or_record(
                execution,
                position,
                gateway,
                "startup_missing_protective_stop",
            )
        self._persist_issues(issues)
        issues.extend(self._persistent_blockers(issues))
        report = ReconciliationReport(
            safe_for_new_entries=not any(issue.blocking for issue in issues),
            issues=tuple(issues),
            balances={},
            next_reconciliation_seconds=(
                self.fast_cadence_seconds if issues else self.normal_cadence_seconds
            ),
        )
        self._project(report)
        return report

    async def _emergency_close_or_record(
        self,
        execution: "ExecutionService",
        position: Position,
        gateway: ExchangeGateway,
        reason: str,
    ) -> None:
        try:
            await execution.emergency_close(position, gateway, reason=reason)
        except Exception as error:
            self.repository.open_incident(
                f"protective-stop:{position.position_id}",
                f"{reason}:emergency_close_failed:{type(error).__name__}",
                severity="critical",
            )

    def _has_matching_protective_stop(
        self, position: Position, remote_by_client_id: dict[str, ExchangeOrder]
    ) -> bool:
        for intent in self.repository.order_intents():
            if (
                intent.position_id != position.position_id
                or intent.side != "sell"
                or intent.order_type != "stop-loss"
                or intent.quantity != position.quantity
            ):
                continue
            remote = remote_by_client_id.get(intent.client_order_id)
            if remote is None:
                continue
            if (
                remote.asset == position.asset
                and remote.side == "sell"
                and remote.order_type == "stop-loss"
                and remote.amount == position.quantity
                and remote.trigger_price == intent.stop_price
                and _canonical_status(remote.status)
                in {OrderStatus.SUBMITTED, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}
            ):
                return True
        return False

    def _persist_remote_order(
        self, intent, remote: ExchangeOrder, *, reconstruct_position: bool
    ) -> None:
        previous_order = self.repository.order_by_client_id(intent.client_order_id)
        previous_filled = previous_order.filled_quantity if previous_order is not None else Decimal("0")
        previous_fees = previous_order.fees if previous_order is not None else Decimal("0")
        filled_delta = remote.filled - previous_filled
        fee_delta = remote.fee - previous_fees
        status = _canonical_status(remote.status)
        self.repository.upsert_order(
            OrderRecord(
                order_id=f"kraken:{remote.exchange_order_id}",
                client_order_id=intent.client_order_id,
                exchange_order_id=remote.exchange_order_id,
                asset=remote.asset,
                side=remote.side,
                order_type=remote.order_type,
                status=status,
                requested_quantity=remote.amount,
                filled_quantity=remote.filled,
                average_fill_price=remote.average_price,
                fees=remote.fee,
                created_at=remote.created_at,
                updated_at=remote.updated_at,
            )
        )
        if (
            reconstruct_position
            and remote.filled > 0
            and remote.side == "buy"
            and remote.average_price is not None
        ):
            position_id = intent.position_id or reconciled_entry_position_id(intent.client_order_id)
            if filled_delta > 0 or self.repository.position_by_id(position_id) is None:
                self.repository.save_position(
                    Position(
                        position_id=position_id,
                        asset=remote.asset,
                        status=PositionStatus.OPEN,
                        quantity=remote.filled,
                        entry_price=remote.average_price,
                        stop_price=intent.stop_price or remote.average_price,
                        strategy_version=self.repository.decision_strategy_version(intent.decision_id),
                        opened_at=remote.updated_at,
                        fees=remote.fee,
                    )
                )
        if reconstruct_position and filled_delta > 0 and remote.side == "sell" and intent.position_id is not None and remote.average_price is not None:
            previous = self.repository.position_by_id(intent.position_id)
            if previous is not None and previous.status in {PositionStatus.OPENING, PositionStatus.OPEN, PositionStatus.CLOSING}:
                quantity = min(previous.quantity, filled_delta)
                remaining = previous.quantity - quantity
                exit_fee = fee_delta
                entry_order = self.repository.entry_order_for_position(intent.position_id)
                entry_fee = entry_order.fees if entry_order is not None else previous.fees
                entry_quantity = entry_order.filled_quantity if entry_order is not None else previous.quantity
                remaining_entry_fee = entry_fee * (entry_quantity - previous_filled) / entry_quantity
                allocated_entry_fee = remaining_entry_fee * quantity / previous.quantity
                realized = previous.realized_pnl + quantity * (remote.average_price - previous.entry_price) - allocated_entry_fee - exit_fee
                total_fees = entry_fee + self.repository.other_exit_fees(intent.position_id, intent.client_order_id) + remote.fee
                self.repository.save_position(Position(
                    previous.position_id, previous.asset,
                    PositionStatus.CLOSED if remaining == 0 else PositionStatus.OPEN,
                    remaining if remaining > 0 else entry_quantity,
                    previous.entry_price, previous.stop_price, previous.strategy_version,
                    previous.opened_at, remote.updated_at if remaining == 0 else None,
                    realized, total_fees,
                    take_profit_price=previous.take_profit_price,
                ))

    def _persist_issues(self, issues: list[ReconciliationIssue]) -> None:
        active_ids: set[str] = set()
        for issue in issues:
            identifier = issue.client_order_id or issue.exchange_order_id or "portfolio"
            incident_id = f"reconciliation:{issue.code}:{identifier}"
            active_ids.add(incident_id)
            self.repository.open_incident(incident_id, issue.message, severity=issue.severity)
        for row in self.repository.export_canonical_tables()["incidents"]:
            incident_id = str(row["incident_id"])
            if (
                incident_id.startswith("reconciliation:")
                and _may_auto_resolve(incident_id)
                and incident_id not in active_ids
            ):
                self.repository.resolve_incident(incident_id)

    def _persistent_blockers(
        self, current_issues: list[ReconciliationIssue]
    ) -> list[ReconciliationIssue]:
        current_ids = {
            f"reconciliation:{issue.code}:{issue.client_order_id or issue.exchange_order_id or 'portfolio'}"
            for issue in current_issues
        }
        blockers: list[ReconciliationIssue] = []
        for row in self.repository.export_canonical_tables()["incidents"]:
            incident_id = str(row["incident_id"])
            if (
                row["status"] == "open"
                and incident_id.startswith("reconciliation:")
                and incident_id not in current_ids
            ):
                blockers.append(
                    ReconciliationIssue(
                        "unresolved_reconciliation_incident",
                        "critical",
                        f"Earlier reconciliation incident requires review: {row['reason']}",
                    )
                )
        return blockers

    def _next_cadence(self, issues: list[ReconciliationIssue]) -> int:
        transient = [issue for issue in issues if issue.code in {"transient_remote_order", "missing_remote_order", "duplicate_remote_client_id", "unknown_remote_order", "unknown_remote_order_status"}]
        if not transient:
            return self.normal_cadence_seconds
        revisions = []
        for issue in transient:
            identifier = issue.client_order_id or issue.exchange_order_id or "portfolio"
            incident_id = f"reconciliation:{issue.code}:{identifier}"
            row = next(
                (row for row in self.repository.export_canonical_tables()["incidents"] if row["incident_id"] == incident_id),
                None,
            )
            revisions.append(int(row["revision"]) if row is not None else 1)
        return min(self.normal_cadence_seconds, self.fast_cadence_seconds * (2 ** (max(revisions) - 1)))

    def _project(self, report: ReconciliationReport) -> None:
        if self.projector is None:
            return
        payload = json_value(
            {
                "safe_for_new_entries": report.safe_for_new_entries,
                "issues": report.issues,
                "balances": report.balances,
                "next_reconciliation_seconds": report.next_reconciliation_seconds,
            }
        )
        self.projector.append_event("reconciliation.jsonl", payload)
        status = self.repository.export_system_status()
        status["reconciliation"] = payload
        self.projector.write_snapshot("system_status.json", status)


_TERMINAL_STATUSES = frozenset(
    {OrderStatus.VALIDATED, OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED}
)


def _canonical_status(status: object) -> OrderStatus:
    if isinstance(status, OrderStatus):
        return status
    if status == "pending":
        return OrderStatus.SUBMITTED
    if status == "cancelled":
        return OrderStatus.CANCELED
    return OrderStatus.ERROR


def _order_mismatches(asset: str, side: str, quantity: Decimal, local: OrderRecord | None, remote: ExchangeOrder) -> list[ReconciliationIssue]:
    if remote.status == "unknown":
        return [ReconciliationIssue("unknown_remote_order_status", "critical", "Remote order status is unknown.", client_order_id=remote.client_order_id, exchange_order_id=remote.exchange_order_id)]
    mismatched = remote.asset != asset or remote.side != side or remote.amount != quantity
    if local is not None:
        if local.exchange_order_id is not None and local.exchange_order_id != remote.exchange_order_id:
            mismatched = True
        if local.status in _TERMINAL_STATUSES and _canonical_status(remote.status) != local.status:
            mismatched = True
        if local.status in _TERMINAL_STATUSES and remote.status in _TERMINAL_STATUSES:
            mismatched = mismatched or any(
                (
                    local.filled_quantity != remote.filled,
                    local.fees != remote.fee,
                    local.average_fill_price != remote.average_price,
                )
            )
    if not mismatched:
        return []
    return [ReconciliationIssue("remote_order_mismatch", "critical", "Remote order identity, side, or quantity differs from persisted intent.", client_order_id=remote.client_order_id, exchange_order_id=remote.exchange_order_id)]


def _balance_issues(balances: dict[str, Decimal], positions: tuple[Position, ...], remote_orders: tuple[ExchangeOrder, ...]) -> list[ReconciliationIssue]:
    issues: list[ReconciliationIssue] = []
    tolerance = Decimal("0.000000000001")
    expected: dict[str, Decimal] = {}
    for position in positions:
        base, _, quote = position.asset.partition("/")
        if not quote or base not in balances or quote not in balances:
            issues.append(ReconciliationIssue("missing_balance", "critical", f"Balance required to reconcile {position.asset} is unavailable."))
            continue
        expected[base] = expected.get(base, Decimal("0")) + position.quantity
    reserved: dict[str, Decimal] = {}
    for order in remote_orders:
        if order.side == "sell" and _canonical_status(order.status) not in _TERMINAL_STATUSES:
            base = order.asset.partition("/")[0]
            reserved[base] = reserved.get(base, Decimal("0")) + (order.amount - order.filled)
    for base, quantity in expected.items():
        available = balances[base] + reserved.get(base, Decimal("0"))
        if available + tolerance < quantity:
            issues.append(ReconciliationIssue("balance_shortfall", "critical", f"Remote {base} balance is below the reconciled position quantity."))
    expected_bases = set(expected)
    quote_currencies = {"USDT", "USD", "EUR"} | {position.asset.partition("/")[2] for position in positions}
    for currency, quantity in balances.items():
        if currency not in expected_bases and currency not in quote_currencies and quantity > tolerance:
            issues.append(ReconciliationIssue("unexpected_remote_balance", "critical", f"Remote {currency} balance has no reconciled position."))
    return issues


def _may_auto_resolve(incident_id: str) -> bool:
    return incident_id.startswith("reconciliation:transient_remote_order:") or incident_id.startswith(
        "reconciliation:missing_remote_order:"
    )


def _default_projector(repository: StateRepository) -> ArtifactProjector | None:
    path = repository.database_path
    return ArtifactProjector(path.parent) if path is not None else None


__all__ = ["ReconciliationIssue", "ReconciliationReport", "ReconciliationService"]
