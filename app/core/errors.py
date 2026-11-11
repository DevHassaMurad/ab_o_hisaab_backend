"""Domain errors rendered as RFC 7807 problem JSON."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status = 400
    title = "Bad request"

    def __init__(self, code: str, detail: str = "", status: int | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.extra: dict = {}
        self.detail = detail or code
        if status is not None:
            self.status = status


class NotFound(DomainError):
    status = 404
    title = "Not found"


class Conflict(DomainError):
    status = 409
    title = "Conflict"


class Forbidden(DomainError):
    status = 403
    title = "Forbidden"


class Unauthorized(DomainError):
    status = 401
    title = "Unauthorized"


class Unprocessable(DomainError):
    status = 422
    title = "Unprocessable entity"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"type": "about:blank", "title": exc.title, "status": exc.status, "detail": exc.detail,
                     "code": exc.code, **exc.extra},
            media_type="application/problem+json",
        )
