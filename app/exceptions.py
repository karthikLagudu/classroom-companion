class DomainError(Exception):
    """Base error for expected domain failures."""


class AuthorizationError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class InvalidStateTransition(DomainError):
    pass


class InviteError(DomainError):
    pass


class LLMInterpretationError(DomainError):
    pass


class TelegramDeliveryError(DomainError):
    pass


class ValidationError(DomainError):
    pass


class AmbiguousReferenceError(ValidationError):
    pass
