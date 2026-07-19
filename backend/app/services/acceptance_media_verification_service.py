"""Protected read-after-delete verification for one Production acceptance account."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account_tombstone import AccountTombstone
from app.models.media_asset import MediaAsset, MediaAssetStatus
from app.services.storage import PrivateObjectStore, storage_service


class AcceptanceMediaVerificationError(RuntimeError):
    """The account or private object store did not prove physical deletion."""


@dataclass(frozen=True)
class AcceptanceMediaAbsence:
    verified_asset_count: int
    facts_sha256: str
    closed_at: datetime


async def verify_acceptance_media_absence(
    db: AsyncSession,
    *,
    user_id: UUID,
    object_store: PrivateObjectStore = storage_service,
) -> AcceptanceMediaAbsence:
    """Require every closed-account asset to be unreadable in the private store."""

    tombstone_result = await db.execute(
        select(AccountTombstone).where(AccountTombstone.user_id == user_id)
    )
    tombstone = tombstone_result.scalar_one_or_none()
    if tombstone is None or tombstone.media_cleanup_pending is not False:
        raise AcceptanceMediaVerificationError(
            "account media cleanup is not durably complete"
        )

    assets_result = await db.execute(
        select(MediaAsset)
        .where(MediaAsset.owner_user_id == user_id)
        .order_by(MediaAsset.id)
    )
    assets = list(assets_result.scalars().all())
    if not assets:
        raise AcceptanceMediaVerificationError(
            "acceptance account has no private media facts"
        )

    fact_lines: list[str] = []
    for asset in assets:
        if (
            MediaAssetStatus(asset.status) != MediaAssetStatus.DELETED
            or asset.read_revoked_at is None
            or asset.deleted_at is None
            or asset.deleted_at < tombstone.closed_at
        ):
            raise AcceptanceMediaVerificationError(
                "acceptance media database state is not fully deleted"
            )
        try:
            content = await asyncio.to_thread(
                object_store.read_private,
                asset.object_key,
            )
        except FileNotFoundError:
            content = None
        except Exception as exc:
            raise AcceptanceMediaVerificationError(
                "private object absence could not be verified"
            ) from exc
        if content is not None:
            raise AcceptanceMediaVerificationError(
                "private object remained readable after account cleanup"
            )
        fact_lines.append(
            ":".join(
                (
                    str(asset.id),
                    str(asset.sha256),
                    asset.deleted_at.isoformat(),
                    "NOT_FOUND",
                )
            )
        )

    return AcceptanceMediaAbsence(
        verified_asset_count=len(assets),
        facts_sha256=hashlib.sha256(
            "\n".join(fact_lines).encode("utf-8")
        ).hexdigest(),
        closed_at=tombstone.closed_at,
    )
