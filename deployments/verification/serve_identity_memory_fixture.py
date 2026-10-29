"""Local browser acceptance with an isolated MySQL DB, MinIO, SMTP and fixed models.

Run from the repository root with apps/api/.venv/bin/python and PYTHONPATH set to
apps/api/src:packages/models/src. Requires the existing compose-mysql-1 and
compose-minio-1 containers. Binds API 18011 and Web 3117 only on loopback.
Creates a uniquely named database and runs real migrations, never modifying the
normal development database. All model adapters are fixed; no paid HTTP calls.
The synthetic email inbox is a private temporary file, not a public endpoint.
Ctrl-C stops only these processes. Verification database/objects are retained;
no automatic deletion is enabled. Printed paths/IDs contain no credentials.
"""

import asyncio
import json
import os
from pathlib import Path
import secrets
import socketserver
import subprocess
import sys
import tempfile
import threading
from email import policy
from email.parser import BytesParser
from urllib.parse import quote

from cryptography.fernet import Fernet
from product_memory_check import container_environment


def configure():
    """Read only local container credentials, create private fixture state and configure services."""
    root = Path(__file__).resolve().parents[2]
    mysql = container_environment("compose-mysql-1")
    minio = container_environment("compose-minio-1")
    directory = Path(tempfile.mkdtemp(prefix="nexus-identity-acceptance-"))
    name = "identity_acceptance_" + secrets.token_hex(6)
    api_key = secrets.token_urlsafe(32)
    os.environ.update(
        NEXUSPILOT_DATABASE_URL="mysql+aiomysql://root:"
        + quote(mysql["MYSQL_ROOT_PASSWORD"], safe="")
        + "@127.0.0.1:3306/"
        + name,
        NEXUSPILOT_API_KEY=api_key,
        NEXUSPILOT_INTERNAL_API_KEY=secrets.token_urlsafe(32),
        NEXUSPILOT_AUTHENTICATION_ENABLED="true",
        NEXUSPILOT_AUTHENTICATION_COOKIE_SECURE="false",
        NEXUSPILOT_AUTHENTICATION_ALLOWED_ORIGINS="http://127.0.0.1:3117",
        NEXUSPILOT_REGISTRATION_ENABLED="true",
        NEXUSPILOT_REGISTRATION_VERIFICATION_KEY=Fernet.generate_key().decode(),
        NEXUSPILOT_MEMORY_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        NEXUSPILOT_REGISTRATION_SMTP_HOST="127.0.0.1",
        NEXUSPILOT_REGISTRATION_SMTP_PORT="18012",
        NEXUSPILOT_REGISTRATION_SMTP_SENDER="fixture@example.test",
        NEXUSPILOT_REGISTRATION_SMTP_USERNAME="",
        NEXUSPILOT_REGISTRATION_SMTP_STARTTLS="false",
        NEXUSPILOT_MINIO_ENDPOINT="127.0.0.1:9000",
        NEXUSPILOT_MINIO_ACCESS_KEY=minio["MINIO_ROOT_USER"],
        NEXUSPILOT_MINIO_SECRET_KEY=minio["MINIO_ROOT_PASSWORD"],
        NEXUSPILOT_MINIO_BUCKET="authorization-audit",
        NEXUSPILOT_MINIO_SECURE="false",
        NEXUSPILOT_APP_TOOLS_JSON="{}",
        NEXUSPILOT_MCP_CONNECTIONS_JSON="{}",
        NEXUSPILOT_WORKFLOW_EXECUTION_BACKEND="inline",
    )
    return root, directory, name, api_key


def mailbox_handler(directory):
    """Make a bounded synthetic SMTP inbox whose code is never printed or publicly served."""

    class Mailbox(socketserver.StreamRequestHandler):
        """Accept only local SMTP fixture delivery, without relaying or authentication."""

        def handle(self):
            """Read one small message and save only its synthetic verification code privately."""
            self.connection.settimeout(5)
            self.wfile.write(b"220 localhost fixture\r\n")
            while line := self.rfile.readline(4096):
                command = line.split(b" ", 1)[0].strip().upper()
                if command in {b"EHLO", b"HELO", b"MAIL", b"RCPT"}:
                    self.wfile.write(b"250 OK\r\n")
                elif command == b"DATA":
                    self.wfile.write(b"354 Send\r\n")
                    body = bytearray()
                    while chunk := self.rfile.readline(4096):
                        if chunk == b".\r\n":
                            break
                        body.extend(chunk)
                        if len(body) > 16384:
                            return
                    message = BytesParser(policy=policy.default).parsebytes(body)
                    code = message.get_content().splitlines()[1]
                    path = directory / "inbox.json"
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w") as inbox:
                        json.dump({"email": str(message["To"]), "code": code}, inbox)
                    self.wfile.write(b"250 Accepted\r\n")
                elif command == b"QUIT":
                    self.wfile.write(b"221 Bye\r\n")
                    return
                else:
                    self.wfile.write(b"500 Unsupported\r\n")

    return Mailbox


async def prepare_database(name):
    """Create only this generated fixture database, without copying existing user records."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    url = make_url(os.environ["NEXUSPILOT_DATABASE_URL"])
    engine = create_async_engine(
        url.set(database="mysql"), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            await connection.exec_driver_sql(
                f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4"
            )
    finally:
        await engine.dispose()


async def seed_model():
    """Register a synthetic model; the actual adapter is replaced before serving requests."""
    from nexuspilot_api.infrastructure.database import database_session_factory, engine
    from nexuspilot_api.models import LlmRegisteredModel, LlmModelCatalogVersion
    from nexuspilot_api.models.base import utc_now
    from nexuspilot_models.contracts import ProviderName

    async with database_session_factory() as db:
        db.add(
            LlmRegisteredModel(
                provider=ProviderName.OPENAI,
                model_identifier="memory-fixture",
                display_name="固定模型（不调用供应商）",
                reasoning_supported_efforts_json=["medium"],
                reasoning_supports_disable=True,
                reasoning_default_enabled=True,
                reasoning_default_effort="medium",
                reasoning_effort_mapping_json={"medium": "medium"},
                reasoning_profile_version="fixture.v1",
            )
        )
        db.add(
            LlmModelCatalogVersion(
                provider="openai",
                model="memory-fixture",
                catalog_version="identity-memory-fixture",
                source="fixture",
                confirmed_at=utc_now(),
                context_window=65536,
                supports_streaming=True,
                tokenizer_name="utf8_bytes_upper_bound",
                tokenizer_version="v1",
                capabilities_json={},
            )
        )
        await db.commit()
    await engine.dispose()


def main():
    """Own the isolated API/Web/mail lifecycle while retaining test data for inspection."""
    root, directory, name, api_key = configure()
    asyncio.run(prepare_database(name))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root / "apps/api",
        check=True,
    )
    asyncio.run(seed_model())
    from serve_memory_fixture import app, fixture_lifespan
    import uvicorn

    app.router.lifespan_context = fixture_lifespan
    server = socketserver.TCPServer(("127.0.0.1", 18012), mailbox_handler(directory))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {key: value for key, value in os.environ.items() if not key.startswith("NEXUSPILOT_")}
    env.update(NEXUSPILOT_API_URL="http://127.0.0.1:18011", NEXUSPILOT_API_KEY=api_key)
    web = subprocess.Popen(
        ["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", "3117"],
        cwd=root / "apps/web",
        env=env,
    )
    print(f"Fixture database={name}; private inbox directory={directory}", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=18011, access_log=False)
    finally:
        web.terminate()
        web.wait(timeout=10)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
