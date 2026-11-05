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


class RateLimitExceeded(DomainError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("上传过于频繁，请稍后再试")
        self.retry_after = retry_after


class QueueFullError(DomainError):
    def __init__(self) -> None:
        super().__init__("系统忙")
