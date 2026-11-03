"""Deterministic bootstrap for the eight pilot paper accounts.

Creates the adaptive/control x 50/100/150/200 EUR account cohort under a
fresh ``.test-tmp*`` root. Each account is owned explicitly by the
deterministic worker and the four control strategies are frozen. No
collector, model access or order flow is started: the CLI only supports
``--init-only``.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ARMS = ("adaptive", "control")
WALLETS_EUR = (50, 100, 150, 200)
ASSETS = ["ETH/EUR"]
TIMEFRAME = "1h"
DEFAULT_EXPERIMENT_ID = "pilot-paper"

BASE_STRATEGY = {
    "version": "1",
    "entry": {"indicator": "rsi", "threshold": "30", "direction": "long"},
    "stop_loss_pct": "2",
    "position_size_r": ".5",
    "simulation": {
        "maker_fee_rate": "0",
        "taker_fee_rate": "0",
        "slippage_rate": "0",
        "minimum_expected_edge_pct": "0",
        "max_order_quote": "5",
    },
}


def _validate_root(root: Path) -> Path:
    """A pilot root must be new and live under a .test-tmp* tree."""
    root = Path(root).resolve()
    if root.exists() or root.is_symlink():
        raise ValueError(f"root already exists: {root}")
    if not any(part.startswith(".test-tmp") for part in root.parts):
        raise ValueError("root must be inside a .test-tmp* directory")
    return root


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _strategy_for(arm: str) -> dict:
    strategy = copy.deepcopy(BASE_STRATEGY)
    if arm == "control":
        strategy["research"] = {"frozen": True}
    return strategy


def _reflection_owner(experiment_id: str) -> dict:
    return {
        "schema_version": 1,
        "owner": "deterministic_worker",
        "experiment_id": experiment_id,
        "cadence_closed_trades": 5,
        "one_variable_only": True,
    }


def _wallet_account(experiment_id: str, wallet: int, at: datetime) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "currency": "EUR",
        "starting_balance": str(wallet),
        "activated_at": at.isoformat(),
    }


def _wallet_costs(experiment_id: str, at: datetime) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "currency": "EUR",
        "scope_start": at.isoformat(),
        "as_of": at.isoformat(),
        "totals": {
            name: {"amount": "0", "source": "explicit_bootstrap_zero", "quality": "actual"}
            for name in ("hosting", "ai", "tools", "funding")
        },
    }


def init_pilot_accounts(
    root: Path,
    *,
    now: datetime | None = None,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
) -> dict:
    """Create the eight fresh EUR accounts and return the bootstrap receipt.

    The manifest records the cohort binding but stays non-activated
    (``execution_enabled`` is false and the status is not ``paper_ready``),
    so this bootstrap alone can never feed the collector or the worker.
    """
    root = _validate_root(root)
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("an explicit experiment identity is required")
    at = now or datetime.now(timezone.utc)
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise ValueError("an aware activation time is required")
    at = at.astimezone(timezone.utc)
    root.mkdir(parents=True)
    profiles = []
    accounts = {}
    for arm in ARMS:
        for wallet in WALLETS_EUR:
            name = f"{arm}_{wallet}"
            account_dir = root / name
            account_dir.mkdir()
            _write_yaml(account_dir / "strategy.yaml", _strategy_for(arm))
            _write_yaml(account_dir / "goal.yaml", {"assets": ASSETS})
            _write_json(account_dir / "wallet_account.json", _wallet_account(experiment_id, wallet, at))
            _write_json(account_dir / "wallet_costs.json", _wallet_costs(experiment_id, at))
            _write_json(account_dir / "reflection_owner.json", _reflection_owner(experiment_id))
            digest = hashlib.sha256((account_dir / "strategy.yaml").read_bytes()).hexdigest()
            profiles.append(
                {"arm": arm, "wallet_eur": wallet, "directory": name, "strategy_sha256": digest}
            )
            accounts[name] = {
                "arm": arm,
                "wallet_eur": wallet,
                "directory": name,
                "currency": "EUR",
                "starting_balance": str(wallet),
                "activated_at": at.isoformat(),
                "owner": "deterministic_worker",
                "strategy_sha256": digest,
            }
    manifest = {
        "schema_version": 1,
        "status": "pilot_bootstrap",
        "execution_enabled": False,
        "assets": ASSETS,
        "timeframe": TIMEFRAME,
        "experiment_id": experiment_id,
        "profiles": profiles,
    }
    _write_json(root / "manifest.json", manifest)
    return {
        "schema_version": 1,
        "status": "initialized",
        "experiment_id": experiment_id,
        "activated_at": at.isoformat(),
        "root": str(root),
        "accounts": accounts,
        "collection": "not_started",
        "model_access": "not_configured",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="new pilot root under a .test-tmp* directory")
    parser.add_argument(
        "--init-only",
        action="store_true",
        help="only initialize the eight accounts (the sole supported mode)",
    )
    parser.add_argument("--experiment-id", default=DEFAULT_EXPERIMENT_ID)
    args = parser.parse_args()
    if not args.init_only:
        print("error: only --init-only is supported; no collection or model is available",
              file=sys.stderr, flush=True)
        raise SystemExit(1)
    try:
        receipt = init_pilot_accounts(Path(args.root), experiment_id=args.experiment_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
