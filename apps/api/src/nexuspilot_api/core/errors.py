"""Application-level errors converted to HTTP responses at the FastAPI boundary."""


class ApplicationError(Exception):
    """Base class for expected use-case failures that are safe to expose."""


class ResourceNotFoundError(ApplicationError):
    """Report a requested application resource that does not exist."""

    def __init__(self, resource_name: str) -> None:
        """Create a stable not-found message for the named resource type."""

        super().__init__(f"{resource_name} not found")


class ResourceConflictError(ApplicationError):
    """Report a uniqueness or concurrent-state conflict."""


class InvalidCursorError(ApplicationError):
    """Report a malformed, expired, or signature-invalid pagination cursor."""

    def __init__(self) -> None:
        """Create a stable error without exposing cursor decoding details."""

        super().__init__("Invalid pagination cursor")


class InvalidRequestError(ApplicationError):
    """Report a semantically invalid query or action request."""
