"""Errors that become the `Error` response body from openapi.yaml.

Every non-2xx response is `{code, message, field_errors}`. The frontend shows
`message` to people as-is, so messages here are written for guests and staff,
never for developers.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    status_code = 400
    code = "VALIDATION"

    def __init__(self, message: str, field_errors: dict[str, str] | None = None):
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors or {}


class UnauthorizedError(ApiError):
    status_code = 401
    code = "UNAUTHORIZED"


class ForbiddenError(ApiError):
    status_code = 403
    code = "FORBIDDEN"


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class WaitlistClosedError(ApiError):
    status_code = 409
    code = "WAITLIST_CLOSED"


class EntryClosedError(ApiError):
    status_code = 409
    code = "ENTRY_CLOSED"


class ConflictError(ApiError):
    status_code = 409
    code = "CONFLICT"


class ValidationError(ApiError):
    status_code = 422
    code = "VALIDATION"


def error_body(code: str, message: str, field_errors: dict[str, str] | None = None) -> dict:
    return {"code": code, "message": message, "field_errors": field_errors or {}}


# Messages for fields that are missing entirely, keyed by field name.
REQUIRED_MESSAGES = {
    "guest_name": "Name is required.",
    "mobile_phone": "Mobile phone is required.",
    "party_size": "Party size is required.",
    "name": "Name is required.",
    "address": "Address is required.",
    "phone": "Phone is required.",
    "username": "Username is required.",
    "password": "Password is required.",
    "status": "Status is required.",
    "is_active": "Active status is required.",
}


def _field_errors_from(exc: RequestValidationError) -> dict[str, str]:
    """Turns Pydantic's error list into field -> friendly message."""
    errors: dict[str, str] = {}
    for error in exc.errors():
        loc = [part for part in error.get("loc", ()) if part not in ("body", "path", "query")]
        if not loc or not isinstance(loc[0], str):
            continue
        field = loc[0]
        if field in errors:
            continue
        if error.get("type") == "missing":
            errors[field] = REQUIRED_MESSAGES.get(field, "This field is required.")
        elif error.get("type") == "value_error":
            # Our own validators raise ValueError with a ready-to-show message.
            errors[field] = str(error.get("ctx", {}).get("error", error.get("msg", "")))
        else:
            errors[field] = "Enter a valid value."
    return errors


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.field_errors),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors = _field_errors_from(exc)
        message = "Please fix the highlighted fields." if field_errors else "The request could not be understood."
        return JSONResponse(status_code=422, content=error_body("VALIDATION", message, field_errors))

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(_: Request, __: IntegrityError) -> JSONResponse:
        # Validation and explicit checks catch conflicts first. This is the backstop
        # for a race between two requests, e.g. two admins creating one username.
        return JSONResponse(
            status_code=409,
            content=error_body("CONFLICT", "That change conflicts with existing data. Please refresh and try again."),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Unknown routes and methods: keep the Error shape instead of {"detail": ...}.
        if exc.status_code == 401:
            code, message = "UNAUTHORIZED", "Please log in."
        elif exc.status_code == 403:
            code, message = "FORBIDDEN", "You don't have access to that page."
        else:
            code, message = "NOT_FOUND", "We couldn't find that."
        return JSONResponse(status_code=exc.status_code, content=error_body(code, message))
