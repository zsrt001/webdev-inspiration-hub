#!/usr/bin/env python3
"""Resolve a Preview Provider owner from a real consumed Google binding."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.core.database import normalize_database_url  # noqa: E402
from app.models.acceptance_identity_binding import AcceptanceIdentityBinding  # noqa: E402
from app.models.release_activation import ReleaseActivation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.acceptance_identity_service import compute_subject_hmac  # noqa: E402


REPORT_SCHEMA = "vowpic.preview-provider-owner.v1"
SOURCE_SHA_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def primary_google_subject(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("exactly two protected Google subjects are required")
    subjects: list[str] = []
    for item in payload:
        if isinstance(item, str):
            provider, subject = "google", item
        elif isinstance(item, dict) and set(item) == {"provider", "subject"}:
            provider = str(item["provider"]).strip().lower()
            subject = str(item["subject"])
        else:
            raise ValueError("Google subject records have an invalid schema")
        clean_subject = subject.strip()
        if provider != "google" or not clean_subject:
            raise ValueError("only non-empty Google subjects are allowed")
        subjects.append(clean_subject)
    if len(set(subjects)) != 2:
        raise ValueError("protected Google subjects must be distinct")
    return subjects[0]


def validate_source_coordinates(
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
) -> tuple[str, str, int]:
    clean_sha = source_sha.strip().lower()
    clean_run_id = workflow_run_id.strip()
    if not SOURCE_SHA_RE.fullmatch(clean_sha):
        raise ValueError("Preview source SHA is invalid")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", clean_run_id):
        raise ValueError("Preview workflow run ID is invalid")
    if workflow_attempt < 1:
        raise ValueError("Preview workflow attempt is invalid")
    return clean_sha, clean_run_id, workflow_attempt


async def resolve_owner_user_id(
    db: AsyncSession,
    *,
    source_sha: str,
    workflow_run_id: str,
    workflow_attempt: int,
    subject_hmac: str,
) -> tuple[ReleaseActivation, uuid.UUID]:
    clean_sha, clean_run_id, clean_attempt = validate_source_coordinates(
        source_sha=source_sha,
        workflow_run_id=workflow_run_id,
        workflow_attempt=workflow_attempt,
    )
    activation = await db.scalar(
        select(ReleaseActivation).where(
            ReleaseActivation.environment == "preview",
            ReleaseActivation.kind == "PREVIEW_IDENTITY",
            ReleaseActivation.source_sha == clean_sha,
            ReleaseActivation.workflow_run_id == clean_run_id,
            ReleaseActivation.workflow_attempt == clean_attempt,
            ReleaseActivation.phase == "CLEANED",
        )
    )
    if activation is None:
        raise ValueError("the exact cleaned Preview Identity activation is absent")
    deployment_id = str(activation.api_deployment_id or "").strip()
    if not deployment_id:
        raise ValueError("the cleaned Preview Identity deployment ID is absent")
    binding = await db.scalar(
        select(AcceptanceIdentityBinding).where(
            AcceptanceIdentityBinding.environment == "preview",
            AcceptanceIdentityBinding.deployment_id == deployment_id,
            AcceptanceIdentityBinding.provider == "google",
            AcceptanceIdentityBinding.subject_hmac == subject_hmac,
            AcceptanceIdentityBinding.consumed_at.is_not(None),
            AcceptanceIdentityBinding.consumed_user_id.is_not(None),
            AcceptanceIdentityBinding.revoked_at.is_(None),
        )
    )
    if binding is None or binding.consumed_user_id is None:
        raise ValueError(
            "the primary Google identity was not consumed by the exact Identity activation"
        )
    return activation, binding.consumed_user_id


async def resolve_latest_cleaned_owner_user_id(
    db: AsyncSession,
    *,
    subject_hmac: str,
) -> tuple[ReleaseActivation, uuid.UUID]:
    rows = (
        await db.execute(
            select(ReleaseActivation, AcceptanceIdentityBinding.consumed_user_id)
            .join(
                AcceptanceIdentityBinding,
                AcceptanceIdentityBinding.deployment_id
                == ReleaseActivation.api_deployment_id,
            )
            .join(User, User.id == AcceptanceIdentityBinding.consumed_user_id)
            .where(
                ReleaseActivation.environment == "preview",
                ReleaseActivation.kind == "PREVIEW_IDENTITY",
                ReleaseActivation.phase == "CLEANED",
                AcceptanceIdentityBinding.environment == "preview",
                AcceptanceIdentityBinding.provider == "google",
                AcceptanceIdentityBinding.subject_hmac == subject_hmac,
                AcceptanceIdentityBinding.consumed_at.is_not(None),
                AcceptanceIdentityBinding.consumed_user_id.is_not(None),
                AcceptanceIdentityBinding.revoked_at.is_(None),
                User.status == "active",
            )
            .order_by(
                ReleaseActivation.updated_at.desc(),
                ReleaseActivation.created_at.desc(),
                ReleaseActivation.id.desc(),
            )
            .limit(1)
        )
    ).one_or_none()
    if rows is None or rows[1] is None:
        raise ValueError("no cleaned real Google acceptance binding is available")
    return rows[0], rows[1]


def build_report(
    activation: ReleaseActivation,
    owner_user_id: uuid.UUID,
    *,
    selection_mode: str = "exact",
) -> dict[str, Any]:
    if selection_mode not in {"exact", "latest-cleaned"}:
        raise ValueError("Provider owner selection mode is invalid")
    deployment_id = str(activation.api_deployment_id or "").strip()
    if not deployment_id:
        raise ValueError("Preview Identity deployment ID is absent")
    return {
        "schema": REPORT_SCHEMA,
        "source_sha": activation.source_sha,
        "workflow_run_id": activation.workflow_run_id,
        "workflow_attempt": int(activation.workflow_attempt),
        "identity_activation_id": str(activation.id),
        "identity_deployment_id": deployment_id,
        "owner_user_id_sha256": hashlib.sha256(
            str(owner_user_id).encode("utf-8")
        ).hexdigest(),
        "state": (
            "RESOLVED_FROM_CONSUMED_BINDING"
            if selection_mode == "exact"
            else "RESOLVED_FROM_PRIOR_CLEANED_BINDING"
        ),
    }


def write_job_env(path: Path, *, owner_user_id: uuid.UUID) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"PREVIEW_PROVIDER_OWNER_USER_ID={owner_user_id}\n")


async def _run(args: argparse.Namespace) -> int:
    database_url = os.environ.get(args.database_url_env, "").strip()
    hmac_key = os.environ.get(args.hmac_key_env, "").strip()
    if not database_url:
        raise ValueError("Preview control-reader database URL is required")
    if len(hmac_key) < 32:
        raise ValueError("acceptance identity HMAC key must contain at least 32 characters")
    subject = primary_google_subject(args.subjects_file)
    subject_hmac = compute_subject_hmac(hmac_key, "google", subject)
    normalized_url, connect_args = normalize_database_url(database_url)
    engine = create_async_engine(normalized_url, connect_args=connect_args)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            if args.selection_mode == "exact":
                if not args.source_sha or not args.workflow_run_id or args.workflow_attempt is None:
                    raise ValueError("exact Provider owner selection requires workflow coordinates")
                activation, owner_user_id = await resolve_owner_user_id(
                    db,
                    source_sha=args.source_sha,
                    workflow_run_id=args.workflow_run_id,
                    workflow_attempt=args.workflow_attempt,
                    subject_hmac=subject_hmac,
                )
            else:
                if args.source_sha or args.workflow_run_id or args.workflow_attempt is not None:
                    raise ValueError("latest-cleaned Provider owner selection forbids workflow coordinates")
                activation, owner_user_id = await resolve_latest_cleaned_owner_user_id(
                    db,
                    subject_hmac=subject_hmac,
                )
    finally:
        await engine.dispose()
    report = build_report(
        activation,
        owner_user_id,
        selection_mode=args.selection_mode,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    write_job_env(args.job_env, owner_user_id=owner_user_id)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--selection-mode",
        choices=("exact", "latest-cleaned"),
        default="exact",
    )
    parser.add_argument("--source-sha")
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--workflow-attempt", type=int)
    parser.add_argument("--subjects-file", type=Path, required=True)
    parser.add_argument(
        "--database-url-env",
        default="PREVIEW_CONTROL_READ_DATABASE_URL",
    )
    parser.add_argument(
        "--hmac-key-env",
        default="ACCEPTANCE_IDENTITY_HMAC_KEY",
    )
    parser.add_argument("--job-env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
