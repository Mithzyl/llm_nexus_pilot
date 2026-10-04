"""Create new mode-0600 deployment environment files; never overwrite existing config.

python3 deployments/production/initialize.py --origin https://chat.example.com \
  --certs /absolute/certificate-directory --release release-tag
Certificate directory must contain fullchain.pem and privkey.pem before Nginx starts.
Use --directory for a server-private location. No secrets are printed.
"""

import argparse
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit


def write_environment(path: Path, entries: dict[str, str]) -> None:
    """Create a private environment file exclusively; generated values use a safe single-line alphabet."""
    if any(
        "\n" in value or "\r" in value or "$" in value for value in entries.values()
    ):
        raise ValueError(
            "Environment values must not contain expansion or newline characters"
        )
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as output:
        for key, value in entries.items():
            output.write(f"{key}={value}\n")


def initialize(directory: Path, origin: str, certs: Path, release: str) -> None:
    """Generate separate app/migration/storage keys and immutable first-boot credentials."""
    parsed = urlsplit(origin)
    port = parsed.port
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[a-zA-Z0-9.-]+", parsed.hostname)
        or not re.fullmatch(r"[a-zA-Z0-9_.-]+", release)
    ):
        raise ValueError(
            "Use an HTTPS origin without path/credentials and a simple release tag"
        )
    if not certs.is_absolute():
        raise ValueError("Certificate directory must be absolute")
    origin = f"https://{parsed.hostname}" + (f":{port}" if port and port != 443 else "")
    if directory.exists():
        raise FileExistsError(
            "Configuration directory already exists; refusing to replace it"
        )
    directory.mkdir(mode=0o700, parents=True)
    (
        service,
        internal,
        cursor,
        continuation,
        app_db,
        migration_db,
        root_db,
        root_s3,
        app_s3,
    ) = (secrets.token_hex(32) for _ in range(9))
    api = {
        "NEXUSPILOT_ENVIRONMENT": "production",
        "NEXUSPILOT_API_KEY": service,
        "NEXUSPILOT_INTERNAL_API_KEY": internal,
        "NEXUSPILOT_CURSOR_SIGNING_KEY": cursor,
        "NEXUSPILOT_PROVIDER_CONTINUATION_ENCRYPTION_KEY": continuation,
        "NEXUSPILOT_AUTHENTICATION_ENABLED": "true",
        "NEXUSPILOT_AUTHENTICATION_COOKIE_SECURE": "true",
        "NEXUSPILOT_AUTHENTICATION_ALLOWED_ORIGINS": origin,
        "NEXUSPILOT_DATABASE_URL": f"mysql+aiomysql://nexuspilot_app:{app_db}@mysql:3306/nexuspilot",
        "NEXUSPILOT_MINIO_ENDPOINT": "minio:9000",
        "NEXUSPILOT_MINIO_ACCESS_KEY": "nexuspilot-files",
        "NEXUSPILOT_MINIO_SECRET_KEY": app_s3,
        "NEXUSPILOT_MINIO_BUCKET": "nexuspilot-artifacts",
        "NEXUSPILOT_MINIO_SECURE": "false",
        "NEXUSPILOT_DEEPSEEK_API_KEY": "",
    }
    write_environment(directory / "api.env", api)
    write_environment(
        directory / "migration.env",
        {
            **api,
            "NEXUSPILOT_DATABASE_URL": f"mysql+aiomysql://nexuspilot_migrator:{migration_db}@mysql:3306/nexuspilot",
        },
    )
    write_environment(
        directory / "web.env",
        {"NEXUSPILOT_API_URL": "http://api:8000", "NEXUSPILOT_API_KEY": service},
    )
    write_environment(
        directory / "mysql.env",
        {
            "MYSQL_DATABASE": "nexuspilot",
            "MYSQL_ROOT_HOST": "localhost",
            "MYSQL_USER": "nexuspilot_migrator",
            "MYSQL_PASSWORD": migration_db,
            "MYSQL_ROOT_PASSWORD": root_db,
            "MYSQL_APP_PASSWORD": app_db,
        },
    )
    write_environment(
        directory / "minio.env",
        {"MINIO_ROOT_USER": "nexus-storage-admin", "MINIO_ROOT_PASSWORD": root_s3},
    )
    write_environment(
        directory / "storage-init.env",
        {
            "MINIO_ENDPOINT": "minio:9000",
            "MINIO_ROOT_USER": "nexus-storage-admin",
            "MINIO_ROOT_PASSWORD": root_s3,
            "MINIO_APP_USER": "nexuspilot-files",
            "MINIO_APP_PASSWORD": app_s3,
            "MINIO_APP_BUCKET": "nexuspilot-artifacts",
        },
    )
    write_environment(
        directory / ".env",
        {
            "DEPLOY_CONFIG_DIR": str(directory.resolve()),
            "NEXUS_HOST": parsed.hostname,
            "PUBLIC_ORIGIN": origin,
            "RELEASE_TAG": release,
            "TLS_CERTS_DIR": str(certs),
            "BIND_ADDRESS": "0.0.0.0",
            "HTTP_PORT": "80",
            "HTTPS_PORT": str(port or 443),
        },
    )


def main():
    """Parse non-secret deployment coordinates and write a new configuration directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--certs", required=True, type=Path)
    parser.add_argument("--release", required=True)
    parser.add_argument(
        "--directory", type=Path, default=Path(__file__).parent / "runtime"
    )
    args = parser.parse_args()
    initialize(args.directory, args.origin, args.certs, args.release)
    print(
        f"Private configuration created in {args.directory}. Add Provider credentials to api.env."
    )


if __name__ == "__main__":
    main()
