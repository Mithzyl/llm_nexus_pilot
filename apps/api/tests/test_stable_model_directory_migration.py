"""Focused migration checks for stable model-directory backfill behavior."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext


def test_stable_model_directory_migration_deduplicates_capability_history() -> None:
    """Verify distinct supported catalog identities become one stable row each."""

    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    catalog_versions = sa.Table(
        "llm_model_catalog_versions",
        metadata,
        sa.Column("catalog_version_id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            catalog_versions.insert(),
            [
                {
                    "catalog_version_id": "catalog-1",
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                },
                {
                    "catalog_version_id": "catalog-2",
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                },
                {
                    "catalog_version_id": "catalog-3",
                    "provider": "unknown-provider",
                    "model": "historical-only",
                },
            ],
        )
        migration_context = MigrationContext.configure(connection)
        migration_path = (
            Path(__file__).parents[1]
            / "migrations"
            / "versions"
            / "20260827_0016_stable_model_directory.py"
        )
        specification = importlib.util.spec_from_file_location(
            "stable_model_directory_migration", migration_path
        )
        assert specification is not None and specification.loader is not None
        migration_module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(migration_module)
        with Operations.context(migration_context):
            migration_module.upgrade()

        registered_rows = connection.execute(
            sa.text(
                "SELECT provider, model_identifier, display_name "
                "FROM llm_models ORDER BY provider, model_identifier"
            )
        ).all()
        stored_provider = connection.execute(
            sa.text("SELECT provider FROM llm_models")
        ).scalar_one()

    assert registered_rows == [("deepseek", "deepseek-v4-flash", "deepseek-v4-flash")]
    assert stored_provider == "deepseek"
