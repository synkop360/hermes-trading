from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification_runner import resolve_starting_wallet_balance


@dataclass(frozen=True)
class PnlGraphPoint:
    timestamp: datetime
    asset: str
    realized_pnl: Decimal
    cumulative_pnl: Decimal
    wallet: Decimal


@dataclass(frozen=True)
class PnlGraphSeries:
    started_at: datetime
    ended_at: datetime
    starting_wallet: Decimal
    points: tuple[PnlGraphPoint, ...]
    symbols: tuple[str, ...]
    symbol_pnl: dict[str, list[Decimal]]


def build_pnl_graph_series(
    repository: StateRepository,
    state_dir: Path,
    *,
    now: datetime,
    env: Mapping[str, str] | None = None,
) -> PnlGraphSeries:
    ended_at = now.astimezone(UTC)
    started_at = _training_started_at(state_dir, ended_at)
    starting_wallet = resolve_starting_wallet_balance(env)
    cumulative_pnl = Decimal("0")
    symbol_totals: OrderedDict[str, Decimal] = OrderedDict()
    symbol_pnl: dict[str, list[Decimal]] = {}
    points: list[PnlGraphPoint] = []

    for position in repository.closed_positions():
        if position.closed_at is None:
            continue
        closed_at = datetime.fromisoformat(position.closed_at).astimezone(UTC)
        if closed_at < started_at or closed_at > ended_at:
            continue
        if position.asset not in symbol_totals:
            symbol_totals[position.asset] = Decimal("0")
            symbol_pnl[position.asset] = [Decimal("0")] * len(points)
        cumulative_pnl += position.realized_pnl
        symbol_totals[position.asset] += position.realized_pnl
        for asset, total in symbol_totals.items():
            symbol_pnl[asset].append(total)
        points.append(
            PnlGraphPoint(
                timestamp=closed_at,
                asset=position.asset,
                realized_pnl=position.realized_pnl,
                cumulative_pnl=cumulative_pnl,
                wallet=starting_wallet + cumulative_pnl,
            )
        )

    return PnlGraphSeries(
        started_at=started_at,
        ended_at=ended_at,
        starting_wallet=starting_wallet,
        points=tuple(points),
        symbols=tuple(symbol_totals),
        symbol_pnl=symbol_pnl,
    )


def render_pnl_graph_png(series: PnlGraphSeries) -> bytes:
    timestamps = [series.started_at, *(point.timestamp for point in series.points)]
    if timestamps[-1] != series.ended_at:
        timestamps.append(series.ended_at)

    wallet = [series.starting_wallet, *(point.wallet for point in series.points)]
    cumulative = [Decimal("0"), *(point.cumulative_pnl for point in series.points)]
    if len(wallet) < len(timestamps):
        wallet.append(wallet[-1])
        cumulative.append(cumulative[-1])

    figure, axis = plt.subplots(figsize=(11, 6), dpi=140)
    axis.plot(timestamps, [_float(value) for value in wallet], label="Wallet", linewidth=2.2)
    axis.plot(timestamps, [_float(value) for value in cumulative], label="Cumulative P&L", linewidth=2.0)
    for symbol in series.symbols:
        values = [Decimal("0"), *series.symbol_pnl[symbol]]
        if len(values) < len(timestamps):
            values.append(values[-1])
        axis.plot(timestamps, [_float(value) for value in values], label=f"{symbol} P&L", linewidth=1.4)

    axis.set_title("Hermes Wallet And Realized P&L")
    axis.set_xlabel("Closed at")
    axis.set_ylabel("USD")
    axis.grid(True, alpha=0.25)
    axis.legend(loc="best")
    figure.autofmt_xdate()
    figure.tight_layout()
    output = BytesIO()
    figure.savefig(output, format="png")
    plt.close(figure)
    return output.getvalue()


def _training_started_at(state_dir: Path, fallback: datetime) -> datetime:
    path = state_dir / "qualification-training-started-at.txt"
    if not path.exists():
        return fallback
    try:
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip()).astimezone(UTC)
    except ValueError:
        return fallback


def _float(value: Decimal) -> float:
    return float(value)


__all__ = [
    "PnlGraphPoint",
    "PnlGraphSeries",
    "build_pnl_graph_series",
    "render_pnl_graph_png",
]
