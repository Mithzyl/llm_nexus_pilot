"""Opt-in test backing services; never load developer database or storage settings.

NEXUSPILOT_TEST_AUTH_MYSQL_URL must target the explicitly named authz_audit
database on loopback. Each test creates and drops its own UUID-suffixed database.
Optional NEXUSPILOT_TEST_AUTH_MINIO_* settings select an isolated MinIO instance.
"""

import uuid
from contextlib import asynccontextmanager

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nexuspilot_api.models import Base


@asynccontextmanager
async def authorization_mysql_factory(url):
    """Create an owned ephemeral database, exercise actual MySQL, and drop only that database."""
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost"} or parsed.database != "authz_audit":
        raise ValueError("Authorization test database must be loopback/authz_audit")
    name = f"authz_audit_{uuid.uuid4().hex}"
    admin = create_async_engine(parsed, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(parsed.set(database=name))
    try:
        async with admin.connect() as connection:
            await connection.exec_driver_sql(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.exec_driver_sql(f"DROP DATABASE IF EXISTS `{name}`")
        await admin.dispose()
