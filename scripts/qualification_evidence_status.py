from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_trading.persistence.repository import StateRepository
from hermes_trading.qualification_evidence import EVIDENCE_KEYS, QualificationEvidenceCollector


def main() -> None:
    parser = argparse.ArgumentParser(description="Print Hermes qualification evidence status")
    parser.add_argument("--state-dir", default="state")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    database_path = state_dir / "trading.db"
    repository = StateRepository.open_read_only(database_path) if database_path.exists() else None
    try:
        checks = QualificationEvidenceCollector(state_dir, repository=repository).collect()
    finally:
        if repository is not None:
            repository.close()

    for key in EVIDENCE_KEYS:
        check = checks[key]
        print(f"{key}={check.status.value} observed={check.observed} reason={check.reason}")


if __name__ == "__main__":
    main()
