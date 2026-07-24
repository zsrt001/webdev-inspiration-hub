"""Fixed-shape, redacted API errors with request correlation IDs."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_DEFAULT_ERRORS: dict[int, tuple[str, str]] = {
    400: ("bad_request", "Invalid request. Please check your inputs."),
    401: ("unauthorized", "Please sign in and try again."),
    402: ("payment_required", "Insufficient credits."),
    403: ("forbidden", "You do not have permission to do this."),
    404: ("not_found", "The requested resource was not found."),
    409: ("conflict", "This action conflicts with the current state."),
    422: ("validation_failed", "Some required information is missing or invalid."),
    429: ("rate_limited", "Too many requests. Please wait a moment."),
    500: ("internal_server_error", "Service is temporarily unavailable."),
    503: ("service_unavailable", "Service is temporarily unavailable."),
}
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]{0,79}$")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]+")
_GRANT_TOKEN_PATH_PATTERN = re.compile(
    r"(/api/v1/media/grants/)[A-Za-z0-9_-]{20,128}"
)
_EVOLINK_CALLBACK_TOKEN_PATH_PATTERN = re.compile(
    r"(/api/v1/provider-callbacks/evolink/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/)[0-9a-fA-F]{64}"
)


def redact_sensitive_path(value: object) -> str:
    """Remove bearer-like path credentials before any application log sink."""

    redacted = _GRANT_TOKEN_PATH_PATTERN.sub(r"\1[REDACTED]", str(value or ""))
    return _EVOLINK_CALLBACK_TOKEN_PATH_PATTERN.sub(r"\1[REDACTED]", redacted)


class SensitivePathLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_path(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_sensitive_path(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_sensitive_path(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def install_sensitive_path_log_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, SensitivePathLogFilter) for item in access_logger.filters):
        access_logger.addFilter(SensitivePathLogFilter())


def redact_sentry_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Redact grant path credentials from Sentry request and breadcrumb URLs."""

    request = event.get("request")
    if isinstance(request, dict) and isinstance(request.get("url"), str):
        request["url"] = redact_sensitive_path(request["url"])
    breadcrumbs = event.get("breadcrumbs")
    values = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else None
    if isinstance(values, list):
        for breadcrumb in values:
            if not isinstance(breadcrumb, dict):
                continue
            data = breadcrumb.get("data")
            if isinstance(data, dict):
                for key in ("url", "path"):
                    if isinstance(data.get(key), str):
                        data[key] = redact_sensitive_path(data[key])
    return event


def get_request_id(request: Request) -> str:
    existing = str(getattr(request.state, "request_id", "") or "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(existing):
        return existing
    supplied = str(request.headers.get("x-request-id") or "").strip()
    request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    return request_id


async def request_id_middleware(request: Request, call_next):
    request_id = get_request_id(request)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _defaults(status_code: int) -> tuple[str, str]:
    if status_code in _DEFAULT_ERRORS:
        return _DEFAULT_ERRORS[status_code]
    if status_code >= 500:
        return _DEFAULT_ERRORS[500]
    if status_code >= 400:
        return _DEFAULT_ERRORS[400]
    return ("request_failed", "Request failed.")


def _clean_code(value: object, fallback: str) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if _CODE_PATTERN.fullmatch(candidate) else fallback


def _clean_message(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    candidate = _CONTROL_PATTERN.sub(" ", value).strip()
    return candidate[:300] if candidate else fallback


def _clean_field_errors(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        field = _CONTROL_PATTERN.sub(" ", str(item.get("field") or "")).strip()[:160]
        code = _clean_code(item.get("code"), "invalid")
        message = _clean_message(item.get("message"), "Invalid value.")
        if field:
            result.append({"field": field, "code": code, "message": message})
    return result


def normalize_error_detail(detail: Any, status_code: int, request_id: str) -> dict[str, Any]:
    """Return only the public five-field contract; arbitrary detail never passes through."""

    default_code, default_message = _defaults(status_code)
    structured = detail if isinstance(detail, dict) else {}
    code = _clean_code(structured.get("code") or structured.get("error"), default_code)
    message = _clean_message(structured.get("message"), default_message)
    retryable = bool(structured.get("retryable", status_code in _RETRYABLE_STATUS))
    return {
        "code": code,
        "message": message,
        "request_id": request_id,
        "retryable": retryable,
        "field_errors": _clean_field_errors(structured.get("field_errors")),
    }


def error_response(*, request: Request, status_code: int, detail: Any) -> JSONResponse:
    request_id = get_request_id(request)
    return JSONResponse(
        status_code=status_code,
        content=normalize_error_detail(detail, status_code, request_id),
        headers={"X-Request-ID": request_id},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    response = error_response(request=request, status_code=int(exc.status_code), detail=exc.detail)
    for name in ("retry-after", "www-authenticate"):
        value = (exc.headers or {}).get(name)
        if value:
            response.headers[name] = value
    return response


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    field_errors = []
    for item in exc.errors()[:50]:
        location = [str(part) for part in item.get("loc", ()) if str(part) not in {"body", "query", "path"}]
        field_errors.append(
            {
                "field": ".".join(location) or "request",
                "code": str(item.get("type") or "invalid").replace(".", "_")[:80],
                "message": str(item.get("msg") or "Invalid value."),
            }
        )
    return error_response(
        request=request,
        status_code=422,
        detail={"code": "validation_failed", "field_errors": field_errors},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = get_request_id(request)
    logger.exception(
        "Unhandled API error request_id=%s exception_type=%s",
        request_id,
        type(exc).__name__,
    )
    return error_response(
        request=request,
        status_code=500,
        detail={"code": "internal_server_error"},
    )
