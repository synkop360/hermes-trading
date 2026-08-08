from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from statistics import mean, pstdev


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    volatility = pstdev(returns)
    if volatility == 0:
        return 3.0 if mean(returns) > 0 else 0.0
    return (mean(returns) / volatility) * math.sqrt(max(1, len(returns)))


def score(trades: list[dict], goal: dict) -> float:
    """Score closed trades against the operator goal on a bounded -1 to +1 scale."""
    if not trades:
        return -0.2

    returns = [net_return(trade) for trade in trades]
    equity = _equity_curve(trades, returns)
    realized_return = sum(returns)
    drawdown = _max_drawdown(equity)
    sharpe = _sharpe(returns)

    target = float(goal.get("target_return_30d", 0.05))
    max_drawdown = float(goal.get("max_drawdown", 0.08))
    min_sharpe = float(goal.get("min_sharpe", 1.0))

    return_score = _clip(realized_return / target if target else 0.0)
    drawdown_score = _clip(1.0 - (drawdown / max_drawdown if max_drawdown else 2.0))
    sharpe_score = _clip(sharpe / min_sharpe if min_sharpe else 0.0)

    composite = (0.45 * return_score) + (0.35 * drawdown_score) + (0.20 * sharpe_score)
    if drawdown > max_drawdown:
        composite -= 0.6
    return _clip(round(composite, 4))


def net_return(trade: dict) -> float:
    """Resolve a closed trade's net percent return, across both trades.jsonl schemas."""
    try:
        if trade.get("realized_pnl") is not None:
            entry_cost = _resolve_entry_cost(trade)
            if entry_cost is not None and entry_cost > 0:
                return float(Decimal(str(trade["realized_pnl"])) / entry_cost)
    except (InvalidOperation, TypeError, ValueError):
        pass
    return float(trade.get("realized_pnl_pct", 0.0) or 0.0)


def _resolve_entry_cost(trade: dict) -> Decimal | None:
    """Resolve the capital committed to a trade, for percent-return scoring.

    Prefers an explicit ``entry_cost`` (legacy trades.jsonl schema, used by
    trades closed before the 2026-08-01 persistence migration). The current
    ``Position``-derived schema (``positions.py::_records``) never persists
    ``entry_cost`` directly -- only ``entry_price`` and ``quantity`` -- so fall
    back to their product, which is exactly what entry_cost meant.
    """
    try:
        if trade.get("entry_cost") is not None:
            return Decimal(str(trade["entry_cost"]))
        if trade.get("entry_price") is not None and trade.get("quantity") is not None:
            return Decimal(str(trade["entry_price"])) * Decimal(str(trade["quantity"]))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return None


def _equity_curve(trades: list[dict], returns: list[float]) -> list[float]:
    """Resolve a per-trade equity series for drawdown scoring.

    Prefers an explicit ``equity`` field (legacy trades.jsonl schema, and the
    format existing tests construct directly). The current schema does not
    persist a running equity snapshot per trade, so trades missing the field
    fall back to a synthetic curve compounded from each trade's own net
    return, continuing from the same 100.0 base the legacy schema started at
    (or from the last known real ``equity`` value, if the trade list mixes
    both schemas in chronological order).
    """
    curve: list[float] = []
    equity = 100.0
    for trade, trade_return in zip(trades, returns):
        if trade.get("equity") is not None:
            try:
                equity = float(trade["equity"])
            except (TypeError, ValueError):
                equity *= 1.0 + trade_return
        else:
            equity *= 1.0 + trade_return
        curve.append(equity)
    return curve
