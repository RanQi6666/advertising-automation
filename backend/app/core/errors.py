class AppError(Exception):
    """Base application error."""


class NotFoundError(AppError):
    """Raised when a domain object does not exist."""


class ProviderError(AppError):
    """Raised when an external provider fails."""


class WorkflowError(AppError):
    """Raised when an AI workflow cannot continue."""
