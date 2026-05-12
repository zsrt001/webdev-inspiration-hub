"""Consistent API error responses and request IDs."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


_DEFAULT_ERRORS: dict[int, tuple[str, str, str]] = {
    400: ("bad_request", "Invalid request. Please check your inputs.", "Check the form and try again."),
    401: ("unauthorized", "Please sign in and try again.", "Sign in again."),
    402: ("payment_required", "Insufficient credits.", "Top up credits and retry."),
    403: ("forbidden", "You do not have permission to do this.", "Use an authorized account."),
    404: ("not_found", "The requested resource was not found.", "Refresh and try again."),
    409: ("conflict", "This action conflicts with the current state.", "Refresh and try again."),
    422: ("validation_failed", "Some required information is missing or invalid.", "Correct the highlighted input."),
    429: ("rate_limited", "Too many requests. Please wait a moment.", "Wait and try again."),
    500: ("internal_server_error", "Service is temporarily unavailable.", "Try again later."),
    503: ("service_unavailable", "Service is temporarily unavailable.", "Try again later."),
}


def get_request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", "")
    if existing:
        return str(existing)
    header_value = (request.headers.get("x-request-id") or "").strip()
    request_id = header_value[:80] if header_value else uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    return request_id


async def request_id_middleware(request: Request, call_next):
    request_id = get_request_id(request)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _defaults(status_code: int) -> tuple[str, str, str]:
    if status_code in _DEFAULT_ERRORS:
        return _DEFAULT_ERRORS[status_code]
    if status_code >= 500:
        return _DEFAULT_ERRORS[500]
    if status_code >= 400:
        return _DEFAULT_ERRORS[400]
    return ("request_failed", "Request failed.", "Try again.")


def _first_advice(detail: dict[str, Any]) -> str:
    advice = detail.get("advice")
    if isinstance(advice, list) and advice:
        return str(advice[0])
    if isinstance(advice, str) and advice.strip():
        return advice.strip()
    return ""


def normalize_error_detail(detail: Any, status_code: int, request_id: str) -> dict[str, Any]:
    default_error, default_message, default_action = _defaults(status_code)

    if isinstance(detail, dict):
        normalized = dict(detail)
        error = str(normalized.get("error") or normalized.get("code") or default_error).strip() or default_error
        message = str(normalized.get("message") or normalized.get("detail") or _first_advice(normalized) or default_message).strip()
        action = str(normalized.get("action") or default_action).strip() or default_action
        normalized.update(
            {
                "error": error,
                "message": message,
                "action": action,
                "request_id": request_id,
            }
        )
        return normalized

    if isinstance(detail, str) and detail.strip():
        message = detail.strip()
    else:
        message = default_message

    return {
        "error": default_error,
        "message": message,
        "action": default_action,
        "request_id": request_id,
    }


def error_response(*, request: Request, status_code: int, detail: Any) -> JSONResponse:
    request_id = get_request_id(request)
    normalized = normalize_error_detail(detail, status_code, request_id)
    headers = {"X-Request-ID": request_id}
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": normalized,
            "error": normalized["error"],
            "message": normalized["message"],
            "action": normalized["action"],
            "request_id": request_id,
        },
        headers=headers,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(request=request, status_code=int(exc.status_code), detail=exc.detail)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        request=request,
        status_code=422,
        detail={
            "error": "validation_failed",
            "message": "Some required information is missing or invalid.",
            "fields": exc.errors(),
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = get_request_id(request)
    logger.exception("Unhandled API error request_id=%s", request_id)
    return error_response(
        request=request,
        status_code=500,
        detail={
            "error": "internal_server_error",
            "message": "Service is temporarily unavailable.",
        },
    )
