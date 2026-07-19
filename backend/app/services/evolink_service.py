"""Strict, single-submit Evolink image-generation adapter."""

from __future__ import annotations

from enum import StrEnum
import hashlib
import hmac
import re
from typing import Any
from urllib.parse import urlsplit
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import get_settings


settings = get_settings()


class EvolinkProviderError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool, acceptance_possible: bool = False):
        self.code = code
        self.retryable = retryable
        self.acceptance_possible = acceptance_possible
        super().__init__(code)


class EvolinkTaskState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def _validated_https_url(value: object) -> str:
    normalized = str(value or "").strip()
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("evolink_url_invalid") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("evolink_url_must_be_https")
    return normalized


class EvolinkGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=1900)
    image_urls: tuple[str, ...] = Field(min_length=1, max_length=4)
    size: str = Field(min_length=1, max_length=16)
    quality: str = Field(min_length=1, max_length=16)
    model_params: dict[str, bool]

    @model_validator(mode="after")
    def _strict_payload(self) -> "EvolinkGenerationRequest":
        if tuple(_validated_https_url(item) for item in self.image_urls) != self.image_urls:
            raise ValueError("evolink_image_url_not_normalized")
        if self.model_params != {"web_search": False}:
            raise ValueError("evolink_model_params_invalid")
        return self

    def provider_payload(self, *, callback_url: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": self.prompt,
            "image_urls": list(self.image_urls),
            "size": self.size,
            "quality": self.quality,
            "model_params": dict(self.model_params),
        }
        if callback_url is not None:
            payload["callback_url"] = _validated_https_url(callback_url)
        return payload


class EvolinkSubmitFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    cost_minor_units: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _cost_pair(self) -> "EvolinkSubmitFact":
        if (self.cost_minor_units is None) != (self.currency is None):
            raise ValueError("evolink_cost_pair_incomplete")
        return self


class EvolinkTaskFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    task_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    state: EvolinkTaskState
    output_urls: tuple[str, ...] = ()
    failure_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _terminal_shape(self) -> "EvolinkTaskFact":
        urls = tuple(_validated_https_url(item) for item in self.output_urls)
        if urls != self.output_urls or len(set(urls)) != len(urls) or len(urls) > 4:
            raise ValueError("evolink_task_outputs_invalid")
        if self.state is EvolinkTaskState.SUCCEEDED and not urls:
            raise ValueError("evolink_task_success_without_output")
        if self.state is not EvolinkTaskState.SUCCEEDED and urls:
            raise ValueError("evolink_task_non_success_with_output")
        if self.state in {EvolinkTaskState.FAILED, EvolinkTaskState.CANCELLED}:
            if not self.failure_code:
                raise ValueError("evolink_task_failure_code_missing")
        elif self.failure_code is not None:
            raise ValueError("evolink_task_unexpected_failure_code")
        return self


def _object_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise EvolinkProviderError("evolink_response_not_object", retryable=False)
    nested = payload.get("data")
    return nested if isinstance(nested, dict) else payload


def _cost_fact(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None
    amount = usage.get("cost_minor_units")
    currency = usage.get("currency")
    if type(amount) is not int or not isinstance(currency, str):
        return None, None
    normalized_currency = currency.strip().upper()
    if amount < 0 or not re.fullmatch(r"[A-Z]{3}", normalized_currency):
        raise EvolinkProviderError("evolink_cost_schema_invalid", retryable=False)
    return amount, normalized_currency


def parse_evolink_submit_fact(payload: Any) -> EvolinkSubmitFact:
    body = _object_payload(payload)
    task_id = body.get("task_id") or body.get("id")
    if not isinstance(task_id, str):
        raise EvolinkProviderError(
            "evolink_submit_task_id_missing",
            retryable=False,
            acceptance_possible=True,
        )
    amount, currency = _cost_fact(body)
    try:
        return EvolinkSubmitFact(
            task_id=task_id.strip(),
            cost_minor_units=amount,
            currency=currency,
        )
    except ValueError as exc:
        raise EvolinkProviderError("evolink_submit_schema_invalid", retryable=False) from exc


_TASK_STATE_MAP = {
    "pending": EvolinkTaskState.PENDING,
    "queued": EvolinkTaskState.PENDING,
    "processing": EvolinkTaskState.RUNNING,
    "running": EvolinkTaskState.RUNNING,
    "completed": EvolinkTaskState.SUCCEEDED,
    "succeeded": EvolinkTaskState.SUCCEEDED,
    "success": EvolinkTaskState.SUCCEEDED,
    "failed": EvolinkTaskState.FAILED,
    "error": EvolinkTaskState.FAILED,
    "cancelled": EvolinkTaskState.CANCELLED,
    "canceled": EvolinkTaskState.CANCELLED,
}


def parse_evolink_task_fact(task_id: str, payload: Any) -> EvolinkTaskFact:
    body = _object_payload(payload)
    raw_status = body.get("status")
    if not isinstance(raw_status, str) or raw_status.strip().lower() not in _TASK_STATE_MAP:
        raise EvolinkProviderError("evolink_task_status_invalid", retryable=False)
    state = _TASK_STATE_MAP[raw_status.strip().lower()]
    outputs: tuple[str, ...] = ()
    if state is EvolinkTaskState.SUCCEEDED:
        raw_outputs = body.get("results") or body.get("output_urls")
        if not isinstance(raw_outputs, list) or not all(isinstance(item, str) for item in raw_outputs):
            raise EvolinkProviderError("evolink_task_outputs_invalid", retryable=False)
        outputs = tuple(raw_outputs)
    failure_code = None
    if state in {EvolinkTaskState.FAILED, EvolinkTaskState.CANCELLED}:
        raw_error = body.get("error")
        nested_code = raw_error.get("code") if isinstance(raw_error, dict) else None
        raw_code = (
            body.get("error_code")
            or nested_code
            or ("provider_cancelled" if state is EvolinkTaskState.CANCELLED else "provider_failed")
        )
        failure_code = re.sub(r"[^a-z0-9_.-]", "_", str(raw_code).strip().lower())[:64]
    try:
        return EvolinkTaskFact(
            task_id=task_id,
            state=state,
            output_urls=outputs,
            failure_code=failure_code,
        )
    except ValueError as exc:
        raise EvolinkProviderError("evolink_task_schema_invalid", retryable=False) from exc


_CALLBACK_DOMAIN = b"vowpic:evolink-callback:v1:"


def build_evolink_callback_token(
    attempt_id: uuid.UUID,
    *,
    secret_key: str | None = None,
) -> str:
    if not isinstance(attempt_id, uuid.UUID):
        raise ValueError("evolink_callback_attempt_id_invalid")
    key = str(settings.secret_key if secret_key is None else secret_key).encode("utf-8")
    if len(key) < 32:
        raise ValueError("evolink_callback_secret_invalid")
    return hmac.new(
        key,
        _CALLBACK_DOMAIN + str(attempt_id).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def verify_evolink_callback_token(
    attempt_id: uuid.UUID,
    token: str,
    *,
    secret_key: str | None = None,
) -> bool:
    candidate = str(token or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        return False
    expected = build_evolink_callback_token(attempt_id, secret_key=secret_key)
    return hmac.compare_digest(candidate, expected)


def build_evolink_callback_url(
    attempt_id: uuid.UUID,
    *,
    base_url: str | None = None,
    secret_key: str | None = None,
) -> str:
    origin = str(
        settings.effective_webhook_base_url if base_url is None else base_url
    ).strip().rstrip("/")
    token = build_evolink_callback_token(attempt_id, secret_key=secret_key)
    return _validated_https_url(
        f"{origin}/api/v1/provider-callbacks/evolink/{attempt_id}/{token}"
    )


class EvolinkService:
    PROVIDER = "evolink"
    PROMPT_CHAR_LIMIT = 1900

    @property
    def provider_name(self) -> str:
        return self.PROVIDER

    @staticmethod
    def _base_url() -> str:
        return str(settings.evolink_api_base_url or "").strip().rstrip("/")

    @classmethod
    def _image_generation_url(cls) -> str:
        return f"{cls._base_url()}/v1/images/generations"

    @classmethod
    def _task_url(cls, task_id: str) -> str:
        return f"{cls._base_url()}/v1/tasks/{task_id}"

    @staticmethod
    def _headers() -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.evolink_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @classmethod
    def compact_prompt(cls, prompt: str, *, subject_count: int) -> str:
        normalized = " ".join(str(prompt or "").split())
        subject_rule = (
            "Exactly two primary wedding subjects; preserve each identity separately; no face merge or role swap."
            if int(subject_count) == 2
            else "Exactly one primary wedding subject; never add a partner, second face, or duplicate body."
        )
        hard_gate = (
            "Identity-preserving image edit using image_urls. "
            f"{subject_rule} Preserve age, face geometry, nose, eyes, skin tone, full wedding wardrobe, "
            "natural hands, upright 3:4 composition, readable face, controlled highlights, no watermark or text. "
        )
        combined = f"{hard_gate}{normalized}"
        if len(combined) <= cls.PROMPT_CHAR_LIMIT:
            return combined
        clipped = combined[: cls.PROMPT_CHAR_LIMIT]
        return clipped.rsplit(" ", 1)[0]

    def validate_runtime_requirements(self, *, force: bool = False) -> None:
        _ = force
        errors: list[str] = []
        if settings.generation_engine != "evolink":
            errors.append("GENERATION_ENGINE must be exactly evolink")
        if not settings.evolink_api_key:
            errors.append("EVOLINK_API_KEY is required")
        try:
            base = urlsplit(self._base_url())
            if base.scheme != "https" or not base.hostname or base.path not in {"", "/"}:
                errors.append("EVOLINK_API_BASE_URL must be an HTTPS origin")
        except ValueError:
            errors.append("EVOLINK_API_BASE_URL must be an HTTPS origin")
        if not settings.evolink_image_model:
            errors.append("EVOLINK_IMAGE_MODEL is required")
        try:
            build_evolink_callback_url(uuid.UUID(int=0))
        except ValueError:
            errors.append("Evolink callback origin and SECRET_KEY must be production-safe")
        if errors:
            raise ValueError("; ".join(errors))

    async def ping_runtime(self) -> tuple[bool, str]:
        self.validate_runtime_requirements(force=True)
        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(f"{self._base_url()}/v1/models", headers=self._headers())
        if response.status_code in {200, 404, 405}:
            return True, f"http_{response.status_code}"
        if response.status_code in {401, 403}:
            raise EvolinkProviderError("evolink_auth_failed", retryable=False)
        raise EvolinkProviderError("evolink_runtime_unavailable", retryable=True)

    async def submit(
        self,
        request: EvolinkGenerationRequest,
        *,
        attempt_id: uuid.UUID,
    ) -> EvolinkSubmitFact:
        self.validate_runtime_requirements()
        callback_url = build_evolink_callback_url(attempt_id)
        timeout = httpx.Timeout(connect=10.0, read=45.0, write=45.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                self._image_generation_url(),
                json=request.provider_payload(callback_url=callback_url),
                headers=self._headers(),
            )
        if response.status_code in {400, 401, 402, 403, 404, 409, 422, 429}:
            raise EvolinkProviderError(
                f"evolink_submit_rejected_{response.status_code}",
                retryable=response.status_code == 429,
                acceptance_possible=False,
            )
        if response.status_code >= 500:
            raise EvolinkProviderError(
                f"evolink_submit_ambiguous_{response.status_code}",
                retryable=False,
                acceptance_possible=True,
            )
        if 300 <= response.status_code < 400:
            raise EvolinkProviderError("evolink_redirect_rejected", retryable=False)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise EvolinkProviderError(
                "evolink_submit_json_invalid",
                retryable=False,
                acceptance_possible=True,
            ) from exc
        return parse_evolink_submit_fact(payload)

    async def get_task(self, task_id: str) -> EvolinkTaskFact:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", str(task_id or "")):
            raise ValueError("evolink_task_id_invalid")
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(self._task_url(task_id), headers=self._headers())
        if response.status_code in {401, 403, 404}:
            raise EvolinkProviderError(
                f"evolink_task_rejected_{response.status_code}", retryable=False
            )
        if response.status_code >= 500 or response.status_code == 429:
            raise EvolinkProviderError("evolink_task_query_transient", retryable=True)
        if 300 <= response.status_code < 400:
            raise EvolinkProviderError("evolink_redirect_rejected", retryable=False)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise EvolinkProviderError("evolink_task_json_invalid", retryable=False) from exc
        return parse_evolink_task_fact(task_id, payload)


evolink_service = EvolinkService()
