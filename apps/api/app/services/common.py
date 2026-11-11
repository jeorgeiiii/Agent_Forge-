from datetime import datetime, timezone


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC so comparisons work."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ServiceError(Exception):
    """Raised by services; routes translate it to an HTTP error."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFound(ServiceError):
    def __init__(self, message: str = "Not found."):
        super().__init__(message, 404)


class Forbidden(ServiceError):
    def __init__(self, message: str = "You do not have access to this resource."):
        super().__init__(message, 403)


class Conflict(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, 409)
