from __future__ import annotations

import math
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

    returns = [float(trade.get("realized_pnl_pct", 0.0)) for trade in trades]
    equity = [float(trade.get("equity", 100.0)) for trade in trades]
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
