from __future__ import annotations

from hermes_trading.control_server import build_control_server, resolve_control_server_config


def main() -> None:
    config = resolve_control_server_config()
    server, repository = build_control_server(config)
    try:
        host, port = server.server_address[:2]
        print(f"Hermes control API listening on http://{host}:{port}", flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        repository.close()


if __name__ == "__main__":
    main()
