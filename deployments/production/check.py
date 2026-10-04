"""Validate production Compose without printing its expanded secret values.

python3 deployments/production/check.py --env-file /absolute/private/config/.env
Requires Docker Compose, generated environment files and installed TLS certificate
files. This validates configuration, not external reachability or certificate trust.
"""

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path


def check(env_file: Path) -> list[str]:
    """Inspect resolved deployment configuration and return non-secret checks that passed."""
    root = Path(__file__).resolve().parents[2]
    process = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(root / "deployments/compose/docker-compose.production.yml"),
            "--profile",
            "operations",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        raise ValueError(
            "Compose configuration failed; check required private files and variables"
        )
    config = json.loads(process.stdout)
    services = config["services"]
    for name, service in services.items():
        if name != "nginx" and service.get("ports"):
            raise ValueError(f"{name} must not publish a host port")
        if service.get("privileged") or service.get("network_mode") == "host":
            raise ValueError(f"{name} has excessive host access")
    for name in ("api", "web"):
        service = services[name]
        if not service.get("read_only") or "ALL" not in service.get("cap_drop", []):
            raise ValueError(
                f"{name} requires a read-only root and dropped capabilities"
            )
        if any(
            "docker.sock" in volume.get("source", "")
            for volume in service.get("volumes", [])
        ):
            raise ValueError(f"{name} must not mount Docker's socket")
    api = services["api"]["environment"]
    web = services["web"]["environment"]
    if api.get("NEXUSPILOT_ENVIRONMENT") != "production" or any(
        api.get(key) != "true"
        for key in (
            "NEXUSPILOT_AUTHENTICATION_ENABLED",
            "NEXUSPILOT_AUTHENTICATION_COOKIE_SECURE",
        )
    ):
        raise ValueError("Production authentication and secure cookies must be enabled")
    if api.get("NEXUSPILOT_API_KEY") != web.get("NEXUSPILOT_API_KEY"):
        raise ValueError("Web and API deployment keys differ")
    if set(key for key in web if key.startswith("NEXUSPILOT_")) != {
        "NEXUSPILOT_API_URL",
        "NEXUSPILOT_API_KEY",
    }:
        raise ValueError("Web received unnecessary application secrets")
    if not config["networks"]["data"].get("internal") or not config["networks"][
        "application"
    ].get("internal"):
        raise ValueError("Data and application networks must be internal")
    for name in (
        ".env",
        "api.env",
        "migration.env",
        "web.env",
        "mysql.env",
        "minio.env",
        "storage-init.env",
    ):
        file = env_file.parent / name
        info = file.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_uid != os.getuid()
        ):
            raise ValueError(
                f"{name} must be a private regular file owned by the operator"
            )
    tls = next(
        volume["source"]
        for volume in services["nginx"]["volumes"]
        if volume["target"] == "/etc/nginx/tls"
    )
    if not all(
        (Path(tls) / name).is_file() for name in ("fullchain.pem", "privkey.pem")
    ):
        raise ValueError("TLS certificate files are missing")
    return [
        "only ingress publishes ports",
        "application privilege restrictions",
        "production identity configuration",
        "separate Web secrets",
        "internal networks",
        "private config files",
        "TLS files present (trust and expiry require separate verification)",
    ]


def main():
    """Print safe preflight results; never emit resolved Compose JSON or secret-bearing stderr."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    try:
        for result in check(args.env_file.resolve()):
            print(f"PASS: {result}")
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"Deployment preflight failed: {exc}") from None


if __name__ == "__main__":
    main()
