"""Run the Beyond the Frame read-only and dynamic-run API."""

from __future__ import annotations

import argparse
import logging
import os

# The thermal detector runs inside the backend process. On Windows/Conda,
# MKL's default Intel threading layer can load a second OpenMP runtime beside
# PyTorch's. Set this before importing the application or model libraries.
os.environ["MKL_THREADING_LAYER"] = "SEQUENTIAL"

from backend.app import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", help="Project root; defaults to this repository or BTF_PROJECT_ROOT")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--auth-username", help="Bootstrap username for the first backend login; defaults to BTF_AUTH_USERNAME")
    parser.add_argument("--auth-password", help="Bootstrap password for the first backend login; defaults to BTF_AUTH_PASSWORD")
    args = parser.parse_args()

    # Built-in HQ credentials are available only for a loopback development
    # server. Network-facing deployments must configure their own credentials.
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    configured_auth = any(
        os.environ.get(name, "").strip()
        for name in (
            "BTF_AUTH_USERNAME", "BTF_USERNAME", "BTF_ADMIN_USERNAME",
            "BTF_AUTH_PASSWORD", "BTF_PASSWORD", "BTF_ADMIN_PASSWORD",
        )
    )
    if args.host.casefold() in local_hosts and not configured_auth and not (args.auth_username or args.auth_password):
        os.environ.setdefault("BTF_ALLOW_DEVELOPMENT_DEFAULTS", "1")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(
        args.project_root,
        args.host,
        args.port,
        auth_username=args.auth_username,
        auth_password=args.auth_password,
        enable_registry_purger=True,
    )
    logging.info("Backend API listening on http://%s:%s/api", *server.server_address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down backend API")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
