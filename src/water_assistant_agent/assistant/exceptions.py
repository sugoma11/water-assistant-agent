"""Service-level exceptions mapped to HTTP responses by the API layer.

Ported from core-agent ``exceptions.py`` — dialect-agnostic, so the hierarchy
is unchanged. See ``exception_handlers.register_exception_handlers``.
"""

from http import HTTPStatus


class ServiceError(Exception):
    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(self, detail: str = "Internal server error") -> None:
        self.detail = detail
        super().__init__(detail)


class NotFoundError(ServiceError):
    status_code: int = HTTPStatus.NOT_FOUND

    def __init__(self, detail: str = "Resource not found") -> None:
        super().__init__(detail)


class BadRequestError(ServiceError):
    status_code: int = HTTPStatus.BAD_REQUEST

    def __init__(self, detail: str = "Bad request") -> None:
        super().__init__(detail)
