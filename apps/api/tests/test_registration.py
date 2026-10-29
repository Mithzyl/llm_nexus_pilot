"""Verify registration with synthetic mail delivery; no real users or external mail."""

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr


async def test_registration_migration_preserves_existing_users(
    client, test_database_session_factory
):
    """Apply the actual additive migration on a disposable database without changing owners."""
    import importlib.util
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import text
    from test_api import create_test_run

    run = await create_test_run(client)
    path = Path(__file__).parents[1] / "migrations/versions/20261029_0031_registration.py"
    spec = importlib.util.spec_from_file_location("registration_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def cycle(connection):
        """Cycle only the empty challenge table in this disposable fixture database."""
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()

    async with test_database_session_factory() as db:
        await (await db.connection()).run_sync(cycle)
        assert await db.scalar(text("SELECT count(*) FROM registration_challenges")) == 0
        assert (
            await db.scalar(
                text("SELECT count(*) FROM users WHERE user_id=:owner"), {"owner": run["user_id"]}
            )
            == 1
        )


async def test_registration_delivers_code_through_local_smtp(monkeypatch):
    """Use a loopback SMTP server to verify actual envelope and Unicode message delivery."""
    import socketserver
    import threading
    from email import policy
    from email.parser import BytesParser

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.features.identity.services.registration import send_verification_email

    received = []

    class Mailbox(socketserver.StreamRequestHandler):
        """Implement only the SMTP commands needed by this local protocol test."""

        def handle(self):
            """Accept bounded fixture mail bytes and finish cleanly on QUIT."""
            self.connection.settimeout(3)
            self.wfile.write(b"220 localhost test SMTP\r\n")
            sender, recipients = "", []
            while line := self.rfile.readline(4096):
                command = line.split(b" ", 1)[0].strip().upper()
                if command in {b"EHLO", b"HELO"}:
                    self.wfile.write(b"250 localhost\r\n")
                elif command in {b"MAIL", b"RCPT"}:
                    address = line.decode().split("<", 1)[1].split(">", 1)[0]
                    if command == b"MAIL":
                        sender = address
                    else:
                        recipients.append(address)
                    self.wfile.write(b"250 OK\r\n")
                elif command == b"DATA":
                    self.wfile.write(b"354 Send message\r\n")
                    body = bytearray()
                    while chunk := self.rfile.readline(4096):
                        if chunk == b".\r\n":
                            break
                        body.extend(chunk)
                        assert len(body) < 16384
                    received.append((sender, recipients, bytes(body)))
                    self.wfile.write(b"250 Accepted\r\n")
                elif command == b"QUIT":
                    self.wfile.write(b"221 Bye\r\n")
                    return
                else:
                    self.wfile.write(b"500 Unsupported\r\n")

    server = socketserver.TCPServer(("127.0.0.1", 0), Mailbox)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    settings = get_settings()
    monkeypatch.setattr(settings, "registration_smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "registration_smtp_port", server.socket.getsockname()[1])
    monkeypatch.setattr(settings, "registration_smtp_sender", "nexus@example.test")
    monkeypatch.setattr(settings, "registration_smtp_starttls", False)
    thread.start()
    try:
        await send_verification_email("recipient@example.test", "synthetic-email-proof", settings)
        assert len(received) == 1
        sender, recipients, body = received[0]
        assert sender == "nexus@example.test" and recipients == ["recipient@example.test"]
        message = BytesParser(policy=policy.default).parsebytes(body)
        assert message["Subject"] == "NexusPilot 注册验证"
        assert "synthetic-email-proof" in message.get_content()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def test_registration_expiry_lockout_and_request_rate(
    test_database_session_factory, monkeypatch
):
    """Expired or repeatedly incorrect proof is rejected; resending does not create accounts."""
    from datetime import timedelta

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.errors import AuthenticationRateLimitError, InvalidRequestError
    from nexuspilot_api.features.identity.models.registration import RegistrationChallenge
    from nexuspilot_api.features.identity.services import registration
    from nexuspilot_api.models.base import utc_now

    settings = get_settings()
    monkeypatch.setattr(settings, "registration_enabled", True)
    monkeypatch.setattr(
        settings, "registration_verification_key", SecretStr(Fernet.generate_key().decode())
    )
    sent = []

    async def deliver(email, code, settings):
        """Keep the current test proof without exposing it in output."""
        sent.append(code)

    monkeypatch.setattr(registration, "send_verification_email", deliver)
    async with test_database_session_factory() as db:
        await registration.request_registration(db, "expiry@example.test", settings)
        with pytest.raises(AuthenticationRateLimitError):
            await registration.request_registration(db, "expiry@example.test", settings)
        row = await db.get(RegistrationChallenge, "expiry@example.test")
        row.expires_at = utc_now() - timedelta(seconds=1)
        await db.commit()
        with pytest.raises(InvalidRequestError):
            await registration.verify_registration(
                db, row.normalized_email, sent[-1], "Test", "valid-password-123", settings
            )
        await registration.request_registration(db, "locked@example.test", settings)
        for _ in range(5):
            with pytest.raises(InvalidRequestError):
                await registration.verify_registration(
                    db, "locked@example.test", "wrong", "Test", "valid-password-123", settings
                )
        with pytest.raises(InvalidRequestError):
            await registration.verify_registration(
                db, "locked@example.test", sent[-1], "Test", "valid-password-123", settings
            )
        for n in range(3):
            await registration.request_registration(db, f"other{n}@example.test", settings)
        with pytest.raises(AuthenticationRateLimitError):
            await registration.request_registration(db, "sixth@example.test", settings)


async def test_registration_http_origin_verify_and_login(resource_client, monkeypatch):
    """Public enrollment still checks Origin and never authenticates before email verification."""
    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.features.identity.services import registration

    settings = get_settings()
    monkeypatch.setattr(settings, "authentication_enabled", True)
    monkeypatch.setattr(settings, "authentication_allowed_origins", "http://localhost:3000")
    monkeypatch.setattr(settings, "registration_enabled", True)
    monkeypatch.setattr(
        settings, "registration_verification_key", SecretStr(Fernet.generate_key().decode())
    )
    sent = []

    async def deliver(email, code, settings):
        """Capture the fixture email while retaining real HTTP handlers and authentication."""
        sent.append(code)

    monkeypatch.setattr(registration, "send_verification_email", deliver)
    email = "http@example.test"
    headers = {"origin": "http://localhost:3000"}
    denied = await resource_client.post(
        "/api/v1/auth/registration/request",
        json={"email": email},
        headers={"origin": "https://foreign.example"},
    )
    assert denied.status_code == 403 and sent == []
    requested = await resource_client.post(
        "/api/v1/auth/registration/request", json={"email": email}, headers=headers
    )
    assert requested.status_code == 200 and sent[0] not in requested.text
    credentials = {"email": email, "password": "registration-test-password"}
    assert (
        await resource_client.post("/api/v1/auth/login", json=credentials, headers=headers)
    ).status_code == 401
    verified = await resource_client.post(
        "/api/v1/auth/registration/verify",
        json={**credentials, "code": sent[0], "display_name": "Test account"},
        headers=headers,
    )
    assert verified.status_code == 200 and "set-cookie" not in verified.headers
    assert (
        await resource_client.post("/api/v1/auth/login", json=credentials, headers=headers)
    ).status_code == 200


async def test_registration_verifies_once_before_account_creation(
    test_database_session_factory, monkeypatch
):
    """Only possession of the mailed code creates an account; replay cannot create another."""
    from sqlalchemy import select

    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.errors import InvalidRequestError
    from nexuspilot_api.features.identity.services import registration
    from nexuspilot_api.models import User

    settings = get_settings()
    monkeypatch.setattr(settings, "registration_enabled", True)
    monkeypatch.setattr(
        settings, "registration_verification_key", SecretStr(Fernet.generate_key().decode())
    )
    sent = []

    async def deliver(email, code, settings):
        """Capture synthetic mail for explicit verification without an SMTP dependency."""
        sent.append((email, code))

    monkeypatch.setattr(registration, "send_verification_email", deliver)
    async with test_database_session_factory() as db:
        await registration.request_registration(db, "new@example.test", settings)
        assert (await db.scalars(select(User).where(User.display_name == "New user"))).all() == []
        with pytest.raises(InvalidRequestError):
            await registration.verify_registration(
                db, "new@example.test", "wrong", "New user", "test-password-1234", settings
            )
        user_id = await registration.verify_registration(
            db, sent[0][0], sent[0][1], "New user", "test-password-1234", settings
        )
        assert (await db.get(User, user_id)).is_active
        with pytest.raises(InvalidRequestError):
            await registration.verify_registration(
                db, sent[0][0], sent[0][1], "New user", "another-password", settings
            )


async def test_registration_disabled_and_mail_failure_never_activate(
    test_database_session_factory, monkeypatch
):
    """Disabled enrollment and failed mail delivery cannot create a usable challenge or user."""
    from nexuspilot_api.core.config import get_settings
    from nexuspilot_api.core.errors import InvalidRequestError
    from nexuspilot_api.features.identity.services import registration

    settings = get_settings()
    monkeypatch.setattr(settings, "registration_enabled", False)
    async with test_database_session_factory() as db:
        with pytest.raises(InvalidRequestError):
            await registration.request_registration(db, "new@example.test", settings)
    monkeypatch.setattr(settings, "registration_enabled", True)
    monkeypatch.setattr(
        settings, "registration_verification_key", SecretStr(Fernet.generate_key().decode())
    )

    async def fail(email, code, settings):
        """Represent a mail service that cannot accept the message."""
        raise InvalidRequestError("验证邮件暂时无法发送。")

    monkeypatch.setattr(registration, "send_verification_email", fail)
    async with test_database_session_factory() as db:
        with pytest.raises(InvalidRequestError, match="无法发送"):
            await registration.request_registration(db, "new@example.test", settings)
        with pytest.raises(InvalidRequestError):
            await registration.verify_registration(
                db, "new@example.test", "anything", "New user", "test-password-1234", settings
            )
