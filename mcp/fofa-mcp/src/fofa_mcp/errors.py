class FofaError(Exception):
    """Base class for expected FOFA MCP errors."""

    error_type = "fofa_error"
    retryable = False

    def __init__(self, message: str, *, account: str | None = None):
        super().__init__(message)
        self.message = message
        self.account = account


class ConfigurationError(FofaError):
    error_type = "configuration_error"


class AuthenticationError(FofaError):
    error_type = "authentication_error"


class RateLimitError(FofaError):
    error_type = "rate_limit"
    retryable = True


class QuotaExceededError(FofaError):
    error_type = "quota_exceeded"


class InvalidQueryError(FofaError):
    error_type = "invalid_query"


class InvalidFieldError(FofaError):
    error_type = "invalid_field"


class PaginationError(FofaError):
    error_type = "pagination_error"


class RemoteApiError(FofaError):
    error_type = "remote_api_error"
    retryable = True


class ResponseSchemaError(FofaError):
    error_type = "response_schema_error"


class ResponseParseError(FofaError):
    error_type = "response_parse_error"


class ResponseTooLargeError(FofaError):
    error_type = "response_too_large"


class NetworkError(FofaError):
    error_type = "network_error"
    retryable = True


class PolicyDeniedError(FofaError):
    error_type = "policy_denied"


class DuplicateQueryError(FofaError):
    error_type = "duplicate_query"
