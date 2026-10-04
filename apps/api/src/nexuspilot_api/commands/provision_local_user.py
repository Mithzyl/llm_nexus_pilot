"""Provision one local-password account in the platform identity database.

Usage:
    python -m nexuspilot_api.commands.provision_local_user \
        --user-id operator --display-name "Operator" --email operator@example.com

The password is read twice from a terminal and never accepted as a command-line argument.
"""

import argparse
import asyncio
import getpass

# Register the complete ORM before identity services import individual model modules.
import nexuspilot_api.models  # noqa: F401
from nexuspilot_api.features.identity.services.authentication_service import (
    provision_local_password_user,
)
from nexuspilot_api.infrastructure.database import database_session_factory, engine


def _parse_arguments() -> argparse.Namespace:
    """Parse non-secret account identity fields from the command line."""

    parser = argparse.ArgumentParser(description="Provision a NexusPilot local-password user")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--email", required=True)
    return parser.parse_args()


async def _provision_user(arguments: argparse.Namespace, password: str) -> None:
    """Create the User and its local login identity in one database transaction."""

    try:
        async with database_session_factory() as database_session:
            await provision_local_password_user(
                database_session,
                user_id=arguments.user_id,
                display_name=arguments.display_name,
                email=arguments.email,
                password=password,
            )
    finally:
        await engine.dispose()


def main() -> None:
    """Prompt for a password and run the asynchronous provisioning transaction."""

    arguments = _parse_arguments()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    asyncio.run(_provision_user(arguments, password))
    print(f"Provisioned local account {arguments.user_id!r} for {arguments.email!r}.")


if __name__ == "__main__":
    main()
