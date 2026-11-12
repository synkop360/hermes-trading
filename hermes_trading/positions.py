from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from hermes_trading.execution_policy import initial_stop_price, initial_take_profit_price, trailing_stop_price
from hermes_trading.domain import Decision, Fill, OrderIntent, OrderRecord, OrderStatus, Position, PositionStatus, json_value
from hermes_trading.market_data import MarketSnapshot, CandleIdentity
from hermes_trading.persistence.artifacts import ArtifactProjector
from hermes_trading.persistence.repository import ProjectionItem, StateRepository
from hermes_trading.strategy import StrategySignal, _atr, _decimal_setting, _is_maker_entry, _is_maker_exit, evaluate_strategy
from hermes_trading.venue_constraints import VenueConstraintsError, parse_venue_constraints


@dataclass(frozen=True)
class SimulationSettings:
    quote_notional: Decimal = Decimal("5")
    taker_fee_rate: Decimal = Decimal("0.008")
    maker_fee_rate: Decimal = Decimal("0.004")
    slippage_rate: Decimal = Decimal("0.001")
    minimum_expected_edge_pct: Decimal = Decimal("0.2")
    risk_per_trade_pct: Decimal = Decimal("0.0025")
    max_symbol_exposure_pct: Decimal = Decimal("0.05")
    max_total_exposure_pct: Decimal = Decimal("0.10")
    max_order_quote: Decimal = Decimal("5")
    minimum_order_quote: Decimal = Decimal("0")
    minimum_order_quantity: Decimal = Decimal("0")
    quantity_decimals: int | None = None

    @classmethod
    def from_strategy(cls, strategy: dict[str, Any]) -> SimulationSettings:
        source = strategy.get("simulation", {})
        if not isinstance(source, dict):
            raise ValueError("simulation must be a mapping")
        values = {
            "quote_notional": _decimal_setting(source.get("quote_notional", "5"), "quote_notional"),
            "taker_fee_rate": _decimal_setting(source.get("taker_fee_rate", "0.008"), "taker_fee_rate"),
            "maker_fee_rate": _decimal_setting(source.get("maker_fee_rate", "0.004"), "maker_fee_rate"),
            "slippage_rate": _decimal_setting(source.get("slippage_rate", "0.001"), "slippage_rate"),
            "minimum_expected_edge_pct": _decimal_setting(source.get("minimum_expected_edge_pct", "0.2"), "minimum_expected_edge_pct"),
            "risk_per_trade_pct": _decimal_setting(source.get("risk_per_trade_pct", "0.0025"), "risk_per_trade_pct"),
        }
        for name, default in (("max_symbol_exposure_pct", ".05"), ("max_total_exposure_pct", ".10"), ("max_order_quote", "5"), ("minimum_order_quote", "0"), ("minimum_order_quantity", "0")):
            values[name] = _decimal_setting(source.get(name, default), name)
            if values[name] < 0 or (name.startswith("max_") and values[name] <= 0):
                raise ValueError(f"{name} must be positive (minimums may be zero)")
        if values["max_symbol_exposure_pct"] > 1 or values["max_total_exposure_pct"] > 1:
            raise ValueError("exposure fractions cannot exceed one")
        if values["quote_notional"] <= 0:
            raise ValueError("quote_notional must be greater than zero")
        for name in ("taker_fee_rate", "maker_fee_rate", "slippage_rate", "risk_per_trade_pct"):
            if values[name] < 0 or values[name] >= 1:
                raise ValueError(f"{name} must be in [0, 1)")
        if values["minimum_expected_edge_pct"] < 0:
            raise ValueError("minimum_expected_edge_pct must be non-negative")
        if source.get("quantity_decimals") is not None:
            precision = _decimal_setting(source['quantity_decimals'], 'quantity_decimals')
            if precision != int(precision) or not 0 <= precision <= 18:
                raise ValueError('quantity_decimals must be an integer from zero to 18')
            values['quantity_decimals'] = int(precision)
        return cls(**values)


@dataclass(frozen=True)
class PositionProcessResult:
    processed: bool
    reason: str
    position: Position | None
    closed_positions: tuple[Position, ...] = ()
    decision: Decision | None = None
    orders: tuple[OrderRecord, ...] = ()
    projection_error: str | None = None
    contract_error: str | None = None


class PositionEngine:
    def __init__(
        self,
        state: StateRepository,
        projector: ArtifactProjector | None = None,
        *,
        equity: Decimal | None = None,
        entries_allowed: bool = True,
        entry_not_before: Any = None,
        wallet_cash_capital: Decimal | None = None,
        entry_contract_resolver: Any = None,
        force_exit_reason: str | None = None,
        prospective_at: Any = None,
        entry_cost_reserve: Any = None,
        reserve_state_dir: Any = None,
    ) -> None:
        self.state = state
        self.projector = projector
        # Current portfolio equity (e.g. loop.py's compute_virtual_wallet_balance,
        # computed once per cycle) -- when known, _entry sizes trades as a fraction
        # of it (compounding) instead of the fixed SimulationSettings.quote_notional.
        # None preserves the historical flat-notional behavior for callers that
        # don't track a wallet balance (tests, qualification replay).
        self.equity = equity
        self.entries_allowed = entries_allowed
        self.entry_not_before = entry_not_before
        self.wallet_cash_capital = wallet_cash_capital
        self.entry_contract_resolver = entry_contract_resolver
        self.force_exit_reason = force_exit_reason
        self.entry_cost_reserve = entry_cost_reserve
        self.reserve_state_dir = reserve_state_dir
        if prospective_at is not None:
            from hermes_trading.reflection_supervision import _now
            prospective_at = _now(prospective_at)
        self.prospective_at = prospective_at

    def process_candle(
        self,
        snapshot: MarketSnapshot,
        strategy: dict[str, Any],
        *,
        recovery_error: str | None = None,
        recover_pending: bool = True,
        signal_override: StrategySignal | None = None,
    ) -> PositionProcessResult:
        resume_error = self.recover_pending_projections() if recover_pending else recovery_error
        candle = snapshot.candle
        if candle is None:
            return PositionProcessResult(False, snapshot.reason, None, projection_error=resume_error)
        identity = snapshot.candle_identity
        source_identity = identity
        existing = self.state.open_position(snapshot.asset)
        pending = self.state.pending_paper_exit(existing.position_id) if existing is not None and self.prospective_at is not None else None
        entry_candle_reservation = None
        source_processed = self.state.is_candle_processed(identity.asset, identity.timeframe, identity.close_timestamp.isoformat())
        if existing is not None and self.prospective_at is not None and self.force_exit_reason is None:
            from hermes_trading.reflection_supervision import _time
            if source_processed and self.prospective_at==_time(existing.opened_at):
                # The entry transaction already consumed this exact observation.
                return PositionProcessResult(False,'duplicate_candle',existing,projection_error=resume_error)
            # Received quote observations continue within a consumed signal bar.
            # Keep one stable identity across pending/partial state transitions.
            identity = CandleIdentity(snapshot.asset,'paper-position:'+existing.position_id,self.prospective_at)
            entry_candle_reservation = (source_identity.asset,source_identity.timeframe,source_identity.close_timestamp.isoformat())
        if self.state.is_candle_processed(identity.asset, identity.timeframe, identity.close_timestamp.isoformat()):
            return PositionProcessResult(False, "duplicate_candle", self.state.open_position(snapshot.asset), projection_error=resume_error)

        existing = self.state.open_position(snapshot.asset)
        contract_error = None
        if existing is not None and self.entry_contract_resolver is not None:
            from hermes_trading.entry_contracts import EntryContractError
            try:
                strategy = self.entry_contract_resolver(snapshot.asset, existing.strategy_version)
            except EntryContractError as error:
                contract_error = str(error)
                self.entries_allowed = False
                # Explicit paper recovery: no inherited hypothesis or indefinite hold.
                strategy = dict(strategy, version=existing.strategy_version, exit={})
        settings = SimulationSettings.from_strategy(strategy)
        venue = None
        execution_mode = self.prospective_at is not None or self.force_exit_reason is not None
        if execution_mode:
            try:
                venue = parse_venue_constraints(snapshot.venue_rules,asset=snapshot.asset,
                                                at=self.prospective_at or identity.close_timestamp)
            except VenueConstraintsError:
                pass
            if venue is not None:
                # Narrow sizing precision without changing the archived economic
                # contract. Existing full quantities are never rounded away.
                precision = venue.quantity_decimals
                if settings.quantity_decimals is not None:precision=min(precision,settings.quantity_decimals)
                settings=replace(settings,quantity_decimals=precision)
        quote = None
        if self.prospective_at is not None:
            from hermes_trading.prospective_execution import require_taker_contract,execution_quote
            require_taker_contract(strategy)
            if self.prospective_at < candle.close_timestamp:
                raise ValueError('prospective decision precedes its signal candle')
            from hermes_trading.reflection_supervision import _time
            not_before = _time(pending['triggered_at']) if pending is not None and pending.get('triggered_at') else None
            try:quote = execution_quote(snapshot,self.prospective_at,not_before=not_before,risk_event=self.force_exit_reason is not None)
            except (ValueError,TypeError):pass
        evaluation_snapshot = snapshot
        evaluation_position = existing
        if self.prospective_at is not None and existing is not None:
            from hermes_trading.reflection_supervision import _time
            signal_time = self.state.paper_entry_signal_time(existing.position_id)
            evaluation_position = replace(existing,opened_at=signal_time)
            mark = quote[0] if quote is not None else candle.close
            evaluation_snapshot = replace(snapshot,candle=replace(candle,open=mark,high=mark,low=mark,close=mark))
        if self.force_exit_reason is not None:
            from hermes_trading.reflection_supervision import _time
            if existing is None:
                signal = StrategySignal("no_trade", snapshot.asset, "paired_halted", candle.close, identity.candle_id)
            elif identity.close_timestamp < _time(existing.opened_at):
                signal = StrategySignal("no_trade", snapshot.asset, "out_of_order_risk_event", candle.close, identity.candle_id)
            else:
                bid = (snapshot.price or {}).get('bid')
                if bid is None:
                    signal = StrategySignal("no_trade", snapshot.asset, "risk_quote_missing", candle.close, identity.candle_id)
                else:
                    signal = StrategySignal("exit", snapshot.asset, self.force_exit_reason, _decimal_setting(bid,'risk bid'), identity.candle_id)
        elif contract_error is not None:
            signal = StrategySignal("exit", snapshot.asset, "entry_contract_recovery_exit", candle.close, identity.candle_id)
        elif pending is not None:
            signal = StrategySignal('exit',snapshot.asset,pending['reason'],Decimal(pending['reference_price']),identity.candle_id)
            if pending['reason']=='partial_exit' and quote is not None:
                protective=evaluate_strategy(evaluation_snapshot,strategy,evaluation_position)
                if protective.action=='exit' and protective.reason in {'stop_loss','max_bars'}:
                    signal=replace(protective,candle_id=identity.candle_id)
        elif existing is None and not _expected_payoff_covers_costs(strategy, settings, snapshot):
            signal = StrategySignal("no_trade", snapshot.asset, "expected_payoff_below_costs", candle.close, identity.candle_id)
        elif existing is not None and self.prospective_at is not None and quote is None and source_processed and signal_override is None:
            # A consumed historical close cannot trigger a fresh intrabar stop
            # when the current executable quote is unavailable.
            signal = StrategySignal('no_trade',snapshot.asset,'execution_quote_unavailable',candle.close,identity.candle_id)
        elif existing is not None and identity.close_timestamp.isoformat() <= existing.opened_at:
            signal = StrategySignal("no_trade", snapshot.asset, "out_of_order_candle", candle.close, identity.candle_id)
        else:
            # Prospective mode has no resting orders: historical bar extrema
            # cannot establish an executable stop/target event.
            signal = signal_override or evaluate_strategy(evaluation_snapshot, strategy, evaluation_position)
            if signal.action == "enter" and self._exposure_cap_reached(strategy):
                signal = StrategySignal("no_trade", snapshot.asset, "exposure_limit", signal.reference_price, signal.candle_id)
        if entry_candle_reservation is not None:signal=replace(signal,candle_id=identity.candle_id)
        pending_write = None
        resolved_pending = None
        def pending_payload():
            if pending is not None and pending['reason']==signal.reason:return pending
            return {'reason':signal.reason,'reference_price':str(signal.reference_price),
                    'candle_id':pending['candle_id'] if pending is not None else signal.candle_id,
                    'triggered_at':(self.prospective_at or identity.close_timestamp).isoformat()}
        if self.prospective_at is not None and signal.action in {'enter','exit'}:
            if quote is None:
                if signal.action == 'exit' and existing is not None:
                    payload = pending_payload()
                    pending_write = (existing.position_id,payload)
                    signal = StrategySignal('no_trade',snapshot.asset,'execution_quote_pending',signal.reference_price,identity.candle_id)
                else:
                    signal = StrategySignal('no_trade',snapshot.asset,'execution_quote_unavailable',signal.reference_price,identity.candle_id)
            else:
                signal = replace(signal,reference_price=quote[1] if signal.action=='enter' else quote[0],candle_id=identity.candle_id)
        if execution_mode and venue is None and signal.action in {'enter','exit'}:
            if signal.action=='exit' and existing is not None:
                pending_write=(existing.position_id,pending_payload())
                signal=replace(signal,action='no_trade',reason='execution_venue_pending')
            else:
                signal=replace(signal,action='no_trade',reason='execution_venue_unavailable')
        if signal.action == "enter":
            if self.entry_not_before is not None and identity.close_timestamp < self.entry_not_before:
                signal = StrategySignal("no_trade", snapshot.asset, "pre_experiment_candle", signal.reference_price, signal.candle_id)
        if signal.action == "enter":
            if not self.entries_allowed:
                signal = StrategySignal("no_trade", snapshot.asset, "wallet_measurement_incomplete", signal.reference_price, signal.candle_id)
        if signal.action == "enter":
            fill_price = signal.reference_price * (1 + settings.slippage_rate)
            try:
                stop = initial_stop_price(snapshot, strategy, fill_price)
                reserve_args={'snapshot':snapshot} if strategy.get('simulation',{}).get('cost_stress_reserve') is True else {}
                notional = self._entry_notional(strategy, settings, fill_price, stop, **reserve_args)
            except (ValueError,ArithmeticError):
                if strategy.get('simulation',{}).get('cost_stress_reserve',False) is False:raise
                signal = StrategySignal('no_trade',snapshot.asset,'entry_cost_reserve_unavailable',signal.reference_price,signal.candle_id)
                notional=Decimal(0)
            quantity = _round_quantity(notional / fill_price, settings)
            if signal.action=='enter' and (quantity <= 0 or quantity * fill_price < settings.minimum_order_quote or quantity < settings.minimum_order_quantity):
                reason='cost_reserve_entry_rejected' if quantity<=0 and strategy.get('simulation',{}).get('cost_stress_reserve') is True else 'below_exchange_minimum'
                signal = StrategySignal("no_trade", snapshot.asset, reason, signal.reference_price, signal.candle_id)
            if signal.action=='enter' and venue is not None:
                try:venue.validate_order(quantity,fill_price)
                except VenueConstraintsError:
                    signal=replace(signal,action='no_trade',reason='below_current_venue_minimum')
        if signal.reason == "partial_exit" and existing is not None:
            fraction = _decimal_setting(strategy['exit']['partial_close_pct'], 'partial_close_pct')
            leg = _round_quantity(existing.quantity * fraction, settings)
            smaller = min(leg, existing.quantity - leg)
            partial_price = signal.reference_price*(1-settings.slippage_rate) if self.prospective_at is not None else candle.close
            if smaller < settings.minimum_order_quantity or smaller * partial_price < settings.minimum_order_quote:
                if execution_mode:
                    pending_write=(existing.position_id,pending_payload())
                    signal=replace(signal,action='no_trade',reason='execution_minimum_pending')
                else:
                    signal = StrategySignal("hold", snapshot.asset, "partial_below_exchange_minimum", signal.reference_price, signal.candle_id)
            if signal.action=='exit' and venue is not None:
                try:
                    venue.validate_order(leg,partial_price)
                    venue.validate_order(existing.quantity-leg,partial_price)
                except VenueConstraintsError:
                    pending_write=(existing.position_id,pending_payload())
                    signal=replace(signal,action='no_trade',reason='execution_venue_pending')
        if signal.action == 'exit' and signal.reason != 'partial_exit' and existing is not None and (self.prospective_at is not None or self.force_exit_reason is not None):
            executable_price = signal.reference_price*(1-settings.slippage_rate)
            if existing.quantity < settings.minimum_order_quantity or existing.quantity*executable_price < settings.minimum_order_quote:
                pending_write = (existing.position_id,pending_payload())
                signal = replace(signal,action='no_trade',reason='execution_minimum_pending')
            if signal.action=='exit' and venue is not None:
                try:venue.validate_order(existing.quantity,executable_price)
                except VenueConstraintsError:
                    pending_write=(existing.position_id,pending_payload())
                    signal=replace(signal,action='no_trade',reason='execution_venue_pending')
        now = (self.prospective_at or identity.close_timestamp).isoformat()
        decision = Decision(
            decision_id=_identifier("decision", signal.candle_id, signal.action),
            asset=snapshot.asset,
            action=signal.action,
            reason=signal.reason,
            candle_id=signal.candle_id,
            strategy_version=str(strategy.get("version", "01")),
            reference_price=signal.reference_price,
            decided_at=now,
        )
        intents: list[OrderIntent] = []
        orders: list[OrderRecord] = []
        fills: list[Fill] = []
        positions: list[Position] = []
        closed: tuple[Position, ...] = ()
        current = existing
        if signal.action == "enter":
            current, intent, order, fill = self._entry(snapshot, decision, strategy, settings)
            positions.append(current)
            intents.append(intent)
            orders.append(order)
            fills.append(fill)
        elif signal.action == "exit" and existing is not None and signal.reason == "partial_exit":
            closed_leg, remainder, intent, order, fill = self._partial_exit(snapshot, decision, strategy, settings, existing)
            positions.append(closed_leg)
            positions.append(remainder)
            intents.append(intent)
            orders.append(order)
            fills.append(fill)
            closed = (closed_leg,)
            current = remainder
        elif signal.action == "exit" and existing is not None:
            current, intent, order, fill = self._exit(snapshot, decision, strategy, settings, existing, signal)
            positions.append(current)
            intents.append(intent)
            orders.append(order)
            fills.append(fill)
            closed = (current,)
        elif signal.action == "hold" and existing is not None:
            trailed = self._trail_stop(evaluation_snapshot, strategy, existing)
            if trailed is not None:
                current = trailed
                positions.append(current)

        if existing is not None and (signal.action == 'exit' or (pending is not None and signal.reason == 'partial_below_exchange_minimum')):
            resolved_pending = existing.position_id
        committed = self.state.persist_candle_outcome(
            identity.asset,
            identity.timeframe,
            identity.close_timestamp.isoformat(),
            decision,
            intents=intents,
            orders=orders,
            fills=fills,
            positions=positions,
            pending_paper_exit=pending_write,
            resolved_paper_exit=resolved_pending,
            entry_candle_reservation=entry_candle_reservation,
        )
        if not committed:
            return PositionProcessResult(False, "duplicate_candle", self.state.open_position(snapshot.asset), projection_error=resume_error)
        projection_error = self._project_pending_item(decision.decision_id)
        projection_error = resume_error or projection_error
        return PositionProcessResult(True, signal.reason, current, closed, decision, tuple(orders), projection_error, contract_error)

    def recover_pending_projections(self) -> str | None:
        """Retry a bounded batch of durable artifact work before market handling."""
        items = self.state.pending_projection_items()
        if not items:
            return None
        if self.projector is None:
            return "no artifact projector is configured"
        for item in items:
            error = self._project_item(item)
            if error is not None:
                return error
        if self.state.has_pending_projection_items():
            return "projection backlog remains after bounded recovery"
        return None

    def _project_pending_item(self, decision_id: str) -> str | None:
        return self._project_item_id(f"projection:item:{decision_id}")

    def _project_item_id(self, item_id: str) -> str | None:
        item = self.state.pending_projection_item(item_id)
        return self._project_item(item) if item is not None else None

    def _project_item(self, item: ProjectionItem) -> str | None:
        if self.projector is None:
            return "no artifact projector is configured"
        try:
            self.projector.project_pending_item(self.state, item)
            self.state.acknowledge_projection_item(item.item_id)
        except Exception as error:
            return str(error)
        return None

    def _entry(self, snapshot: MarketSnapshot, decision: Decision, strategy: dict[str, Any], settings: SimulationSettings) -> tuple[Position, OrderIntent, OrderRecord, Fill]:
        candle = snapshot.candle
        fill_price = (decision.reference_price if self.prospective_at is not None else candle.close) * (Decimal("1") + settings.slippage_rate)
        _positive_finite(fill_price, "entry fill price")
        stop_price = initial_stop_price(snapshot, strategy, fill_price)
        reserve_args={'snapshot':snapshot} if strategy.get('simulation',{}).get('cost_stress_reserve') is True else {}
        quote_notional = self._entry_notional(strategy, settings, fill_price, stop_price, **reserve_args)
        quantity = _round_quantity(quote_notional / fill_price, settings)
        _positive_finite(quantity, "entry quantity")
        maker = _is_maker_entry(strategy.get("entry"))
        fee = quantity * fill_price * (settings.maker_fee_rate if maker else settings.taker_fee_rate)

        take_profit_price = initial_take_profit_price(snapshot, strategy, fill_price)
        return self._records(
            decision, "buy", quantity, fill_price, fee, stop_price, strategy, PositionStatus.OPEN,
            order_type="limit" if maker else "market", take_profit_price=take_profit_price,
        )

    def _entry_notional(
        self, strategy: dict[str, Any], settings: SimulationSettings, fill_price: Decimal, stop_price: Decimal, *, snapshot=None,
    ) -> Decimal:
        """Trade size in quote currency: compounding (equity-based) when the
        caller supplied a current wallet balance, otherwise the historical flat
        SimulationSettings.quote_notional.

        Mirrors risk.py::RiskEngine.authorize_entry's live-mode formula
        (risk_budget = equity * risk_per_trade_pct * position_size_r, sized by
        stop distance) so position_size_r finally means something in training
        mode too -- previously this simulator always used a fixed notional and
        the live-only risk engine was the sole consumer of that variable
        (reflect.py's SUPPORTED_STRATEGY_VARIABLES docstring called it "inert"
        here). Costs enter the risk denominator; symbol, portfolio and order caps
        bound exposure. Minimum-size checks reject dust without rounding risk up.
        """
        if self.equity is None:
            from hermes_trading.entry_cost_reserve import reserve_enabled
            if reserve_enabled(strategy):return Decimal(0)
            return settings.quote_notional
        stop_fraction = (fill_price - stop_price) / fill_price
        if stop_fraction <= 0:
            from hermes_trading.entry_cost_reserve import reserve_enabled
            if reserve_enabled(strategy):return Decimal(0)
            return settings.quote_notional
        position_size_r = _decimal_setting(strategy.get("position_size_r", "0.5"), "position_size_r")
        risk_budget = self.equity * settings.risk_per_trade_pct * position_size_r
        entry_fee = settings.maker_fee_rate if _is_maker_entry(strategy.get("entry")) else settings.taker_fee_rate
        cost_fraction = entry_fee + settings.taker_fee_rate + 2 * settings.slippage_rate
        notional = risk_budget / (stop_fraction + cost_fraction)
        # Conservatively reserve the book value plus fees of existing positions.
        exposure = sum((p.quantity * p.entry_price for p in self.state.active_positions()), Decimal("0"))
        cash = self.equity - exposure
        if self.wallet_cash_capital is not None:
            # Marked inventory gains affect risk equity, never spendable cash.
            # Read committed positions again so earlier fills in this cycle count.
            cash = self.wallet_cash_capital
            for position in self.state.portfolio_positions():
                row = json_value(position)
                if row['status'] == 'closed':
                    cash += Decimal(row['realized_pnl'])
                elif row['status'] == 'open':
                    cash -= Decimal(row['quantity']) * Decimal(row['entry_price']) + Decimal(row['fees'])
                else:
                    raise ValueError('unsettled positions cannot fund entries')
        notional = min(notional, settings.max_order_quote,
                       self.equity * settings.max_symbol_exposure_pct,
                       max(Decimal("0"), self.equity * settings.max_total_exposure_pct - exposure),
                       max(Decimal("0"), cash) / (1 + entry_fee))
        from hermes_trading.entry_cost_reserve import reserve_enabled,reserved_notional
        if reserve_enabled(strategy):
            import sqlite3
            try:return reserved_notional(self,strategy,settings,snapshot,fill_price,notional)
            except (ValueError,OSError,ArithmeticError,sqlite3.Error,TypeError,KeyError):return Decimal(0)
        return notional

    def _exposure_cap_reached(self, strategy: dict[str, Any]) -> bool:
        """Whether opening a new position right now would exceed
        simulation.max_concurrent_positions (unset/absent = no cap).

        Mirrors risk.py::RiskLimits' live-mode exposure caps, ported to
        training mode: correlated assets (this basket trades in near
        lockstep) held simultaneously compound a single market-wide move
        into several losses at once rather than one, which is what actually
        happened across the basket during the 2026-08-25 drawdown.
        """
        simulation = strategy.get("simulation")
        simulation = simulation if isinstance(simulation, dict) else {}
        cap = simulation.get("max_concurrent_positions")
        if cap is None:
            return False
        cap_value = int(_decimal_setting(cap, "max_concurrent_positions"))
        if cap_value <= 0:
            raise ValueError("max_concurrent_positions must be greater than zero")
        return len(self.state.active_positions()) >= cap_value

    def _trail_stop(self, snapshot: MarketSnapshot, strategy: dict[str, Any], position: Position) -> Position | None:
        """Ratchets an open position's stop up toward the current price by a
        fixed ATR multiple, never down. Returns None (no persisted change)
        when exit.trailing_atr isn't configured or the candidate stop
        wouldn't actually raise the existing one.

        Only fires on "hold" cycles -- stop_loss/take_profit/max_bars/
        explicit_exit checks in evaluate_strategy already ran first and would
        have exited the position this same candle if warranted, so there is
        no ordering conflict with those.
        """
        candidate_stop = trailing_stop_price(snapshot, strategy, position.stop_price)
        if candidate_stop <= position.stop_price:
            return None
        return replace(position, stop_price=candidate_stop)

    def _exit(self, snapshot: MarketSnapshot, decision: Decision, strategy: dict[str, Any], settings: SimulationSettings, position: Position, signal: StrategySignal) -> tuple[Position, OrderIntent, OrderRecord, Fill]:
        candle = snapshot.candle
        if self.prospective_at is not None:
            base_price = decision.reference_price
        elif signal.reason == "stop_loss":
            base_price = min(candle.open, position.stop_price)
        elif signal.reason == "paired_drawdown_stop":
            base_price = signal.reference_price
        elif signal.reason in {"take_profit", "take_profit_atr"}:
            base_price = max(candle.open, signal.reference_price)
        else:
            base_price = candle.close
        fill_price = base_price * (Decimal("1") - settings.slippage_rate)
        exit_rule = strategy.get("exit")
        maker = signal.reason == "explicit_exit" and _is_maker_exit(exit_rule)
        fee = position.quantity * fill_price * (settings.maker_fee_rate if maker else settings.taker_fee_rate)
        _positive_finite(fill_price, "exit fill price")
        _positive_finite(position.quantity, "exit quantity")
        closed, intent, order, fill = self._records(decision, "sell", position.quantity, fill_price, fee, position.stop_price, strategy, PositionStatus.CLOSED, previous=position, order_type="limit" if maker else "market")
        return closed, intent, order, fill

    def _partial_exit(self, snapshot: MarketSnapshot, decision: Decision, strategy: dict[str, Any], settings: SimulationSettings, position: Position) -> tuple[Position, Position, OrderIntent, OrderRecord, Fill]:
        """Splits an explicit_exit trigger into a banked partial close plus an
        open remainder (exit.partial_close_pct) -- see the docstring in
        strategy.py::evaluate_strategy for why. Returns (closed_leg,
        remainder, intent, order, fill): closed_leg is a brand-new,
        immediately CLOSED Position for the realized fraction (its own
        position_id, so wallet-balance/KPI code counts it as a normal
        completed trade); remainder carries the original position_id forward
        OPEN with a reduced quantity and partial_exit_count incremented.
        Entry fees already paid are split proportionally between the two
        legs rather than charged twice or dropped.
        """
        candle = snapshot.candle
        exit_rule = strategy.get("exit")
        partial_close_pct = _decimal_setting(exit_rule["partial_close_pct"], "partial_close_pct")
        fill_price = (decision.reference_price if self.prospective_at is not None else candle.close) * (Decimal("1") - settings.slippage_rate)
        _positive_finite(fill_price, "partial exit fill price")
        partial_quantity = _round_quantity(position.quantity * partial_close_pct, settings)
        _positive_finite(partial_quantity, "partial exit quantity")
        remaining_quantity = position.quantity - partial_quantity
        _positive_finite(remaining_quantity, "partial exit remaining quantity")
        maker = _is_maker_exit(exit_rule)
        exit_fee = partial_quantity * fill_price * (settings.maker_fee_rate if maker else settings.taker_fee_rate)
        entry_fee_share = position.fees * partial_quantity / position.quantity

        marker = f"{decision.candle_id}:sell:partial"
        closed_position_id = _identifier("position", decision.asset, decision.candle_id, "partial")
        client_order_id = _identifier("intent", marker)
        order_id = _identifier("order", marker)
        now = decision.decided_at
        order_type = "limit" if maker else "market"
        intent = OrderIntent(client_order_id, decision.decision_id, decision.asset, "sell", order_type, partial_quantity, fill_price, now, closed_position_id, position.stop_price)
        order = OrderRecord(order_id, client_order_id, decision.asset, "sell", order_type, OrderStatus.FILLED, partial_quantity, partial_quantity, fill_price, exit_fee, now, now)
        fill = Fill(_identifier("fill", marker), order_id, decision.asset, "sell", partial_quantity, fill_price, exit_fee, now)

        realized_pnl = partial_quantity * (fill_price - position.entry_price) - entry_fee_share - exit_fee
        closed_leg = Position(
            closed_position_id, position.asset, PositionStatus.CLOSED, partial_quantity, position.entry_price,
            position.stop_price, position.strategy_version, position.opened_at, now, realized_pnl,
            entry_fee_share + exit_fee, take_profit_price=position.take_profit_price,
        )
        remainder = replace(
            position,
            quantity=remaining_quantity,
            fees=position.fees - entry_fee_share,
            partial_exit_count=position.partial_exit_count + 1,
        )
        return closed_leg, remainder, intent, order, fill

    def _records(self, decision: Decision, side: str, quantity: Decimal, fill_price: Decimal, fee: Decimal, stop_price: Decimal, strategy: dict[str, Any], status: PositionStatus, previous: Position | None = None, *, order_type: str, take_profit_price: Decimal | None = None) -> tuple[Position, OrderIntent, OrderRecord, Fill]:
        marker = f"{decision.candle_id}:{side}"
        position_id = previous.position_id if previous is not None else _identifier("position", decision.asset, decision.candle_id)
        client_order_id = _identifier("intent", marker)
        order_id = _identifier("order", marker)
        now = decision.decided_at
        intent = OrderIntent(client_order_id, decision.decision_id, decision.asset, side, order_type, quantity, fill_price, now, position_id, stop_price)
        order = OrderRecord(order_id, client_order_id, decision.asset, side, order_type, OrderStatus.FILLED, quantity, quantity, fill_price, fee, now, now)
        fill = Fill(_identifier("fill", marker), order_id, decision.asset, side, quantity, fill_price, fee, now)
        if previous is None:
            position = Position(position_id, decision.asset, status, quantity, fill_price, stop_price, decision.strategy_version, now, fees=fee, take_profit_price=take_profit_price)
        else:
            total_fees = previous.fees + fee
            pnl = previous.quantity * (fill_price - previous.entry_price) - total_fees
            position = Position(
                position_id, previous.asset, status, previous.quantity, previous.entry_price, previous.stop_price,
                previous.strategy_version, previous.opened_at, now, pnl, total_fees,
                take_profit_price=previous.take_profit_price,
            )
        return position, intent, order, fill

def _round_quantity(quantity: Decimal, settings: SimulationSettings) -> Decimal:
    if settings.quantity_decimals is None:
        return quantity
    return quantity.quantize(Decimal(1).scaleb(-settings.quantity_decimals), rounding=ROUND_DOWN)


def _identifier(*parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "hermes-trading:" + ":".join(parts)))


def _positive_finite(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be positive and finite")


def _expected_payoff_covers_costs(
    strategy: dict[str, Any], settings: SimulationSettings, snapshot: MarketSnapshot
) -> bool:
    """Block new entries whose configured exit target cannot clear round-trip costs.

    Covers both percentage take-profits (``take_profit_pct``) and ATR-based ones
    (``take_profit_atr``, converted to a price percentage using the candle close).
    A strategy with neither key configured is not blocked here; other guards are
    responsible for that case.

    The entry leg reflects entry.fee (known at this point); the exit leg stays
    pinned to taker regardless of exit.fee -- which exit reason will actually
    fire (stop_loss/take_profit/max_bars all stay taker; only explicit_exit can
    be maker) isn't known yet, so this deliberately keeps the conservative,
    worst-case exit-cost assumption rather than risk waving through a trade
    that wouldn't actually clear costs if it exits via a taker path.
    """
    exit_rule = strategy.get("exit")
    if not isinstance(exit_rule, dict):
        return True
    entry_fee_rate = settings.maker_fee_rate if _is_maker_entry(strategy.get("entry")) else settings.taker_fee_rate
    round_trip_cost = entry_fee_rate + settings.taker_fee_rate + Decimal("2") * settings.slippage_rate
    edge = settings.minimum_expected_edge_pct / Decimal("100")
    required = round_trip_cost + edge
    if "trailing_atr" in exit_rule and "take_profit_atr" in exit_rule:
        # A trailing strategy has no fixed ATR target. Require a volatility
        # distance that covers costs; this is a filter, not a profit forecast.
        atr = _atr(snapshot)
        if atr is None or snapshot.candle is None or snapshot.candle.close <= 0:
            return False
        distance = atr * _decimal_setting(exit_rule["trailing_atr"], "trailing_atr")
        return distance / snapshot.candle.close >= required
    if "take_profit_pct" in exit_rule:
        take_profit = _decimal_setting(exit_rule["take_profit_pct"], "take_profit_pct") / Decimal("100")
        return take_profit >= required
    if "take_profit_atr" in exit_rule:
        candle = snapshot.candle
        if candle is None or candle.close <= 0:
            return False
        atr = _atr(snapshot)
        if atr is None:
            return False
        take_profit_atr = _decimal_setting(exit_rule["take_profit_atr"], "take_profit_atr")
        take_profit_pct = (atr * take_profit_atr) / candle.close
        return take_profit_pct >= required
    return True


__all__ = ["PositionEngine", "PositionProcessResult", "SimulationSettings"]
