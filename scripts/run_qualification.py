from __future__ import annotations

import argparse
from pathlib import Path

from hermes_trading.qualification import QualificationPhase, QualificationService
from hermes_trading.qualification_runner import QualificationRunner
from hermes_trading.run import ensure_state_files
from hermes_trading.persistence.repository import StateRepository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--phase", default="validation_ready", choices=[phase.value for phase in QualificationPhase])
    args = parser.parse_args()
    state_dir = Path(args.state_dir)
    ensure_state_files(state_dir)
    repository = StateRepository.open(state_dir / "trading.db")
    try:
        report = QualificationRunner(repository, state_dir, QualificationService()).run_from_state(QualificationPhase(args.phase))
        print(report.markdown)
    finally:
        repository.close()


if __name__ == "__main__":
    main()
