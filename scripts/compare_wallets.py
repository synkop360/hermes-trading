from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

DEFAULT_STARTING_WALLET_BALANCE = Decimal("50")


def _fmt3(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.001")))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def wallet_started_at(state_dir: Path) -> datetime:
    """Mirrors qualification_runner.py::run_from_state's training-window start:
    the qualification-training-started-at.txt marker if one exists (written by
    scripts/reset_qualification.py), otherwise the state dir's own creation
    time -- so a freshly-seeded instance with no marker yet still scopes to
    "trades since it existed" rather than counting nothing as pre-existing."""
    marker = state_dir / "qualification-training-started-at.txt"
    if marker.exists():
        parsed = _parse_timestamp(marker.read_text(encoding="utf-8").strip())
        if parsed:
            return parsed
    return datetime.fromtimestamp(state_dir.stat().st_ctime, tz=timezone.utc)


def summarize(
    state_dir: Path,
    label: str,
    starting_balance: Decimal = DEFAULT_STARTING_WALLET_BALANCE,
) -> dict[str, Any]:
    if not state_dir.exists():
        raise FileNotFoundError(f"state dir does not exist: {state_dir}")

    started_at = wallet_started_at(state_dir)
    trades = _load_jsonl(state_dir / "trades.jsonl")
    scoped = []
    for trade in trades:
        closed_at = _parse_timestamp(trade.get("closed_at"))
        if closed_at is not None and closed_at >= started_at:
            scoped.append(trade)

    realized_pnls = [Decimal(str(trade["realized_pnl"])) for trade in scoped]
    total_pnl = sum(realized_pnls, Decimal("0"))
    wins = sum(1 for pnl in realized_pnls if pnl > 0)
    losses = sum(1 for pnl in realized_pnls if pnl < 0)

    return {
        "label": label,
        "state_dir": str(state_dir),
        "started_at": started_at.isoformat(),
        "starting_balance": starting_balance,
        "wallet_balance": starting_balance + total_pnl,
        "total_realized_pnl": total_pnl,
        "trade_count": len(scoped),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / len(scoped) * 100, 1) if scoped else None,
    }


def format_comparison(*summaries: dict[str, Any]) -> str:
    """Renders a side-by-side table for any number of instances (2 for the
    original baseline/consultant comparison, 3+ once other variants like
    state-trailing-exit are added to the comparison)."""
    def row(label: str, values: list[str]) -> str:
        return f"{label:22}" + "".join(f"{value:>24}" for value in values)

    def win_rate(summary: dict[str, Any]) -> str:
        return f"{summary['win_rate_pct']}%" if summary["win_rate_pct"] is not None else "n/a"

    lines = [
        row("", [summary["label"].upper() for summary in summaries]),
        row("state dir", [summary["state_dir"] for summary in summaries]),
        row("started at", [summary["started_at"][:19] for summary in summaries]),
        row("wallet balance", [_fmt3(summary["wallet_balance"]) for summary in summaries]),
        row("realized P&L", [_fmt3(summary["total_realized_pnl"]) for summary in summaries]),
        row("trades", [str(summary["trade_count"]) for summary in summaries]),
        row("wins/losses", [f"{summary['wins']}/{summary['losses']}" for summary in summaries]),
        row("win rate", [win_rate(summary) for summary in summaries]),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Side-by-side wallet comparison between the Railway baseline (state/, synced "
            "locally via scripts/sync_railway_state.py) and the local market-analysis "
            "'consultant' instance (state-consultant/ by default), each scoped to its own "
            "qualification-training-started-at.txt reset point."
        )
    )
    parser.add_argument("--baseline-state-dir", default="state")
    parser.add_argument("--consultant-state-dir", default="state-consultant")
    parser.add_argument(
        "--trailing-exit-state-dir", default="state-trailing-exit",
        help=(
            "Third instance to compare alongside baseline/consultant (the "
            "exit.trailing_overrides_explicit_exit variant). Included automatically "
            "whenever this directory exists, same as baseline/consultant -- no flag "
            "needed for the normal three-way check. Pass a different path to compare "
            "some other third instance instead, or a path that doesn't exist to fall "
            "back to the original two-column comparison."
        ),
    )
    args = parser.parse_args(argv)

    summaries = [
        summarize(Path(args.baseline_state_dir), "baseline"),
        summarize(Path(args.consultant_state_dir), "consultant"),
    ]
    if args.trailing_exit_state_dir and Path(args.trailing_exit_state_dir).exists():
        summaries.append(summarize(Path(args.trailing_exit_state_dir), "trailing-exit"))
    print(format_comparison(*summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
