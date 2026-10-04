"""Verify deployment secret generation and rendered network isolation with synthetic files."""

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "deployment_initialize", ROOT / "deployments/production/initialize.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def read_environment(file):
    """Read generated synthetic environment values without executing shell expansion."""
    return dict(line.split("=", 1) for line in file.read_text().splitlines())


def test_generated_config_is_private_separate_and_not_overwritten(tmp_path):
    """Generate usable separate credentials, canonicalize HTTPS and preserve existing config."""
    directory = tmp_path / "config"
    MODULE.initialize(directory, "https://CHAT.example.test:443", tmp_path / "tls", "test")
    api = read_environment(directory / "api.env")
    web = read_environment(directory / "web.env")
    assert api["NEXUSPILOT_AUTHENTICATION_ALLOWED_ORIGINS"] == "https://chat.example.test"
    assert api["NEXUSPILOT_API_KEY"] == web["NEXUSPILOT_API_KEY"]
    assert set(web) == {"NEXUSPILOT_API_KEY", "NEXUSPILOT_API_URL"}
    for file in directory.iterdir():
        assert file.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        MODULE.initialize(directory, "https://chat.example.test", tmp_path / "tls", "test")
    assert read_environment(directory / "api.env") == api


@pytest.mark.parametrize(
    "origin",
    [
        "http://chat.example.test",
        "https://user:pass@chat.example.test",
        "https://chat.example.test/path",
        "https://chat.example.test:bad",
        "https://chat.example.test;bad",
    ],
)
def test_initializer_rejects_unsafe_origin_before_writing(tmp_path, origin):
    """Reject config-injection and invalid origins before any credential files are created."""
    with pytest.raises(ValueError):
        MODULE.initialize(tmp_path / "config", origin, tmp_path / "tls", "test")
    assert not (tmp_path / "config").exists()


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="Docker Compose required for rendered config"
)
def test_production_compose_only_publishes_ingress(tmp_path):
    """Check the approved pinned storage image, isolation, secrets and explicit migration."""
    directory = tmp_path / "config"
    MODULE.initialize(directory, "https://chat.example.test", tmp_path / "tls", "test")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"DEPLOY_CONFIG_DIR", "RELEASE_TAG", "TLS_CERTS_DIR", "NEXUS_HOST", "PUBLIC_ORIGIN"}
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(directory / ".env"),
            "-f",
            str(ROOT / "deployments/compose/docker-compose.production.yml"),
            "--profile",
            "operations",
            "config",
            "--format",
            "json",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "Compose failed to parse generated configuration"
    services = json.loads(result.stdout)["services"]
    mysql_probe = services["mysql"]["healthcheck"]["test"][1]
    assert "--protocol=TCP" in mysql_probe
    assert "SELECT 1" in mysql_probe
    assert services["minio"]["image"].startswith(
        "coollabsio/minio:RELEASE.2025-10-15T17-29-55Z@sha256:"
    )
    assert len(services["minio"]["image"].split("@sha256:")[1]) == 64
    assert services["minio"]["healthcheck"]["test"] == [
        "CMD",
        "/bin/sh",
        "/etc/nexus/minio-healthcheck.sh",
    ]
    assert any(
        mount["target"] == "/etc/nexus/minio-healthcheck.sh" and mount["read_only"]
        for mount in services["minio"]["volumes"]
    )
    assert [name for name, config in services.items() if config.get("ports")] == ["nginx"]
    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert "alembic" not in services["api"]["command"]
    assert (
        services["api"]["environment"]["NEXUSPILOT_API_KEY"]
        == services["web"]["environment"]["NEXUSPILOT_API_KEY"]
    )
    assert (
        services["api"]["environment"]["NEXUSPILOT_DATABASE_URL"]
        != services["migrate"]["environment"]["NEXUSPILOT_DATABASE_URL"]
    )


def test_mysql_initializer_does_not_change_parent_shell_options():
    """Source the init hook like MySQL does and preserve the entrypoint's shell options."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            'mysql() { :; }; before="$-"; source "$1"; test "$before" = "$-"',
            "test-init",
            str(ROOT / "deployments/production/mysql-app-user.sh"),
        ],
        env={**os.environ, "MYSQL_APP_PASSWORD": "a" * 64, "MYSQL_ROOT_PASSWORD": "test-only"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "Sourced init hook changed parent shell options"
