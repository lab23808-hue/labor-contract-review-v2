class DomainError(Exception):
    pass


class ValidationError(DomainError):
    pass


class ContractNotFoundError(DomainError):
    pass


class InvalidStateError(DomainError):
    pass
