"""Verify a storage image using real initialization, permissions and persistent bytes.

Run with apps/api/.venv/bin/python deployments/verification/check_storage_image.py
--image IMAGE. Requires Docker, an already downloaded image and API dependencies.
Uses only a newly created container/volume, random credentials and an ephemeral
loopback port. Recreates the container, then deletes its own volume and credentials.
Never mounts existing storage or reads the development API environment file.
"""

import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api/src"))

from minio import Minio  # noqa: E402
from minio.error import S3Error  # noqa: E402

from nexuspilot_api.core.config import Settings  # noqa: E402
from nexuspilot_api.infrastructure.object_storage import (  # noqa: E402
    ObjectStorage,
    ObjectStorageNotFoundError,
)


def docker(*args: str) -> str:
    """Execute a bounded Docker command without exposing credential-bearing diagnostics."""
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=120
    )
    if result.returncode:
        raise RuntimeError(f"Docker {args[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def start_container(name: str, volume: str, image: str, env_file: Path) -> str:
    """Start the isolated server and require the shared production readiness probe to pass."""
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--env-file",
        str(env_file),
        "--publish",
        "127.0.0.1::9000",
        "--volume",
        f"{volume}:/data",
        "--volume",
        f"{ROOT / 'deployments/production/minio-healthcheck.sh'}:/etc/nexus/minio-healthcheck.sh:ro",
        "--memory",
        "1g",
        "--pids-limit",
        "128",
        image,
        "server",
        "/data",
        "--console-address",
        ":9001",
    )
    endpoint = docker("port", name, "9000/tcp")
    for _ in range(30):
        try:
            docker("exec", name, "/bin/sh", "/etc/nexus/minio-healthcheck.sh")
            return endpoint
        except RuntimeError:
            time.sleep(1)
    raise RuntimeError("Storage readiness failed within 30 probes")


def require_denied(action) -> None:
    """Require the actual S3 request to reject the application user's excess permissions."""
    try:
        action()
    except S3Error as exc:
        if exc.code == "AccessDenied":
            return
        raise RuntimeError(f"Expected AccessDenied, received {exc.code}") from None
    raise AssertionError(
        "Application credential unexpectedly allowed forbidden operation"
    )


async def check_bytes(storage: ObjectStorage, *, after_recreate: bool) -> None:
    """Exercise production ObjectStorage methods without involving the database or models."""
    key = "storage-image-probe.txt"
    content = "文件存储兼容性验证\nFILE_CONTEXT_PERSISTED\n".encode()
    uri = storage.uri_for_object(key)
    if not after_recreate:
        stored = await storage.put_bytes(key, content, "text/plain; charset=utf-8")
        assert stored.uri == uri
        assert await storage.verify_object(uri, stored.content_hash)
    opened = await storage.open_object(uri, expected_size_bytes=len(content))
    assert b"".join(opened.chunks) == content
    assert key in await storage.list_objects("storage-image-")
    if after_recreate:
        await storage.delete_object(uri)
        await storage.delete_object(uri)
        try:
            await storage.open_object(uri)
        except ObjectStorageNotFoundError:
            return
        raise AssertionError("Deleted object is still readable")


def verify(image: str) -> dict:
    """Create disposable storage, run initialization twice, check access and recreate it."""
    name = f"nexus-storage-check-{secrets.token_hex(6)}"
    volume = f"{name}-data"
    root_secret, app_secret = secrets.token_hex(32), secrets.token_hex(32)
    bucket = "nexus-storage-check"
    created_volume = False
    report = {"image": image}
    try:
        metadata = json.loads(docker("image", "inspect", image))[0]
        report.update(architecture=metadata["Architecture"], image_id=metadata["Id"])
        with tempfile.TemporaryDirectory(prefix="nexus-storage-check-") as directory:
            env_file = Path(directory) / "minio.env"
            with os.fdopen(
                os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
            ) as output:
                output.write(
                    f"MINIO_ROOT_USER=storage-check-root\nMINIO_ROOT_PASSWORD={root_secret}\n"
                )
            docker("volume", "create", volume)
            created_volume = True
            endpoint = start_container(name, volume, image, env_file)
            environment = {
                **os.environ,
                "MINIO_ENDPOINT": endpoint,
                "MINIO_ROOT_USER": "storage-check-root",
                "MINIO_ROOT_PASSWORD": root_secret,
                "MINIO_APP_USER": "storage-check-app",
                "MINIO_APP_PASSWORD": app_secret,
                "MINIO_APP_BUCKET": bucket,
            }
            for _ in range(2):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "deployments/production/bootstrap_minio.py"),
                    ],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode:
                    raise RuntimeError(
                        "Production bucket/user/policy initialization failed"
                    )
            report["initialization_and_repeat"] = "passed"
            root = Minio(endpoint, "storage-check-root", root_secret, secure=False)
            root.make_bucket("other-private-bucket")
            root.put_object(
                "other-private-bucket", "private.txt", io.BytesIO(b"private"), 7
            )
            settings = Settings(
                _env_file=None,
                environment="development",
                authentication_enabled=False,
                minio_endpoint=endpoint,
                minio_access_key="storage-check-app",
                minio_secret_key=app_secret,
                minio_bucket=bucket,
                minio_secure=False,
            )
            storage = ObjectStorage(settings)
            asyncio.run(check_bytes(storage, after_recreate=False))
            require_denied(lambda: storage.client.make_bucket("forbidden-new-bucket"))
            require_denied(
                lambda: storage.client.stat_object(
                    "other-private-bucket", "private.txt"
                )
            )
            require_denied(
                lambda: storage.client.put_object(
                    "other-private-bucket", "bad.txt", io.BytesIO(b"x"), 1
                )
            )
            assert all(item.name == bucket for item in storage.client.list_buckets())
            try:
                with urllib.request.urlopen(
                    f"http://{endpoint}/{bucket}/storage-image-probe.txt", timeout=5
                ):
                    raise AssertionError("Anonymous read unexpectedly succeeded")
            except urllib.error.HTTPError as exc:
                assert exc.code == 403
            report["object_io_and_permission_denials"] = "passed"
            docker("stop", "--time", "15", name)
            docker("rm", name)
            endpoint = start_container(name, volume, image, env_file)
            storage = ObjectStorage(
                settings.model_copy(update={"minio_endpoint": endpoint})
            )
            asyncio.run(check_bytes(storage, after_recreate=True))
            report["recreation_persistence_and_delete"] = "passed"
    finally:
        if created_volume:
            if docker("ps", "-aq", "--filter", f"name=^{name}$"):
                docker("rm", "-f", name)
            docker("volume", "rm", volume)
    report["temporary_resources_removed"] = True
    return report


def main() -> None:
    """Print safe verification results and fail without printing temporary credentials."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.image), ensure_ascii=False, indent=2))
    except Exception as exc:
        location = traceback.extract_tb(exc.__traceback__)[-1]
        raise SystemExit(
            f"Storage image verification failed ({type(exc).__name__}) "
            f"at {Path(location.filename).name}:{location.lineno} in {location.name}"
        ) from None


if __name__ == "__main__":
    main()
