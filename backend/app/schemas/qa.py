"""Strict, versioned contracts for mandatory delivery QA."""

from __future__ import annotations

from typing import Annotated, Literal
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    model_validator,
)

from app.models.qa_verdict import QaDecision


QA_SCHEMA_VERSION = "vowpic.qa.v1"
QA_CHECKER_VERSION = "vowpic-checker.v1"
QA_MODEL_VERSION = "vowpic-vision.v1"

ReasonCode = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_.-]*$",
    ),
]
StrictScore = Annotated[
    float,
    Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False),
]


class QaCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    passed: StrictBool
    score: StrictScore
    reason_codes: list[ReasonCode] = Field(max_length=16)

    @model_validator(mode="after")
    def enforce_reason_consistency(self) -> "QaCheck":
        if self.passed and self.reason_codes:
            raise ValueError("passing QA check cannot contain failure reasons")
        if not self.passed and not self.reason_codes:
            raise ValueError("failed QA check requires at least one reason code")
        return self


class MandatoryQaChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    technical: QaCheck
    identity: QaCheck
    subject: QaCheck
    safety: QaCheck
    style: QaCheck
    composition: QaCheck
    exposure: QaCheck
    watermark: QaCheck


class StrictQaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[QA_SCHEMA_VERSION]
    candidate_asset_id: uuid.UUID
    source_asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=2)
    is_couple: StrictBool


class StrictQaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[QA_SCHEMA_VERSION]
    checker_version: Literal[QA_CHECKER_VERSION]
    model_version: Literal[QA_MODEL_VERSION]
    passed: StrictBool
    reason_codes: list[ReasonCode] = Field(max_length=32)
    checks: MandatoryQaChecks

    @model_validator(mode="after")
    def enforce_complete_verdict(self) -> "StrictQaResponse":
        check_values = tuple(self.checks.__dict__.values())
        all_passed = all(check.passed for check in check_values)
        if self.passed is not all_passed:
            raise ValueError("top-level QA verdict must match every mandatory check")
        if self.passed and self.reason_codes:
            raise ValueError("passing QA verdict cannot contain failure reasons")
        if not self.passed and not self.reason_codes:
            raise ValueError("failed QA verdict requires at least one reason code")
        return self


class QaScores(BaseModel):
    """Normalized mandatory scores stored with an immutable verdict."""

    model_config = ConfigDict(extra="forbid", strict=True)

    technical: StrictScore
    identity: StrictScore
    subject: StrictScore
    safety: StrictScore
    style: StrictScore
    composition: StrictScore
    exposure: StrictScore
    watermark: StrictScore


class StrictQaVerdict(BaseModel):
    """Internal fail-closed decision contract derived from one QA response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: QaDecision
    required_checks: MandatoryQaChecks
    reasons: tuple[ReasonCode, ...] = Field(max_length=32)
    scores: QaScores

    @model_validator(mode="after")
    def enforce_decision_consistency(self) -> "StrictQaVerdict":
        checks = tuple(self.required_checks.__dict__.values())
        all_passed = all(check.passed for check in checks)
        if self.decision is QaDecision.PASS:
            if not all_passed or self.reasons:
                raise ValueError("PASS requires every mandatory check and no reasons")
        elif all_passed or not self.reasons:
            raise ValueError("REPAIR or REJECT requires failed checks and reasons")
        return self

    @classmethod
    def from_response(
        cls,
        response: StrictQaResponse,
        *,
        decision: QaDecision,
    ) -> "StrictQaVerdict":
        normalized_decision = QaDecision(decision)
        if response.passed is not (normalized_decision is QaDecision.PASS):
            raise ValueError("QA response and decision disagree")
        return cls(
            decision=normalized_decision,
            required_checks=response.checks,
            reasons=tuple(response.reason_codes),
            scores=QaScores(
                technical=response.checks.technical.score,
                identity=response.checks.identity.score,
                subject=response.checks.subject.score,
                safety=response.checks.safety.score,
                style=response.checks.style.score,
                composition=response.checks.composition.score,
                exposure=response.checks.exposure.score,
                watermark=response.checks.watermark.score,
            ),
        )


def failed_qa_response(reason_code: ReasonCode) -> StrictQaResponse:
    """Build a complete typed failure; dependency errors never omit checks."""

    failed_check = {
        "passed": False,
        "score": 0.0,
        "reason_codes": [reason_code],
    }
    return StrictQaResponse.model_validate(
        {
            "schema_version": QA_SCHEMA_VERSION,
            "checker_version": QA_CHECKER_VERSION,
            "model_version": QA_MODEL_VERSION,
            "passed": False,
            "reason_codes": [reason_code],
            "checks": {
                "technical": dict(failed_check),
                "identity": dict(failed_check),
                "subject": dict(failed_check),
                "safety": dict(failed_check),
                "style": dict(failed_check),
                "composition": dict(failed_check),
                "exposure": dict(failed_check),
                "watermark": dict(failed_check),
            },
        }
    )
