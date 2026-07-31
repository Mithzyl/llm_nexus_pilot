"""User persistence queries isolated from HTTP and transaction boundaries."""

from dataclasses import dataclass
from datetime import UTC

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.pagination import CursorPosition
from nexuspilot_api.models import User


@dataclass(frozen=True)
class UserPageData:
    """Return one repository page and the position used for the next page."""

    items: list[User]
    next_position: CursorPosition | None


class UserRepository:
    """Read and stage User entities without committing the surrounding transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind repository operations to one request-scoped SQLAlchemy session."""

        self.session = session

    async def get(self, user_id: str) -> User | None:
        """Return a user by stable identifier, or None when absent."""

        return await self.session.get(User, user_id)

    def add(self, user: User) -> None:
        """Stage a new user in the current transaction without committing it."""

        self.session.add(user)

    async def list_page(
        self,
        *,
        is_active: bool | None,
        position: CursorPosition | None,
        limit: int,
    ) -> UserPageData:
        """Return users ordered by creation time and identifier with cursor pagination."""

        statement = select(User)
        if is_active is not None:
            statement = statement.where(User.is_active == is_active)
        if position is not None:
            cursor_time = position.created_at.astimezone(UTC).replace(tzinfo=None)
            statement = statement.where(
                or_(
                    User.created_at > cursor_time,
                    and_(
                        User.created_at == cursor_time,
                        User.user_id > position.identifier,
                    ),
                )
            )
        statement = statement.order_by(User.created_at, User.user_id).limit(limit + 1)
        users = list((await self.session.scalars(statement)).all())
        has_more = len(users) > limit
        items = users[:limit]
        next_position = None
        if has_more and items:
            last = items[-1]
            next_position = CursorPosition(
                created_at=last.created_at,
                identifier=last.user_id,
            )
        return UserPageData(items=items, next_position=next_position)
