from __future__ import annotations

import argparse
import os

from hermes_trading.control_client import ControlClient, control_status_message, qualification_message


def main() -> None:
    parser = argparse.ArgumentParser(description="Call the private Hermes local control API")
    parser.add_argument("command", choices=("status", "qualification"))
    parser.add_argument("--phase", default="validation_ready")
    parser.add_argument("--base-url", default=os.getenv("HERMES_CONTROL_BASE_URL", "http://127.0.0.1:8765"))
    args = parser.parse_args()

    secret = os.getenv("HERMES_CONTROL_HMAC_SECRET", "").strip()
    if not secret:
        raise SystemExit("HERMES_CONTROL_HMAC_SECRET is required")
    client = ControlClient(
        args.base_url,
        secret,
        allowed_hosts=allowed_control_hosts(os.getenv("HERMES_CONTROL_ALLOWED_HOSTS", "")),
    )
    if args.command == "status":
        print(control_status_message(client.status()))
        return
    print(qualification_message(client.qualification(args.phase)))


def allowed_control_hosts(raw: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in raw.replace(";", ",").split(",") if part.strip())


if __name__ == "__main__":
    main()
