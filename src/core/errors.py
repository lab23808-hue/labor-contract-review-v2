class DomainError(Exception):
    pass


class ValidationError(DomainError):
    pass


class ContractNotFoundError(DomainError):
    pass


class InvalidStateError(DomainError):
    pass


class ArtifactMissingError(DomainError):
    pass


class CorruptedFileError(DomainError):
    pass


class StorageDeleteError(DomainError):
    pass
