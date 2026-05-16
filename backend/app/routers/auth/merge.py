"""Guest-to-registered account merge — transfers orders and live portrait jobs."""

import hashlib
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)


async def _merge_guest_account(db: AsyncSession, guest_id: str, new_user_id: uuid.UUID) -> None:
    guest_id = (guest_id or "").strip()
    if not guest_id:
        return

    possible_openids: list[str] = []
    if guest_id.startswith("guest_"):
        possible_openids.append(f"visitor_{guest_id}")
    code_seed = f"web_{guest_id}"
    possible_openids.append(f"wx_{hashlib.sha256(code_seed.encode('utf-8')).hexdigest()[:32]}")

    guest_user: User | None = None
    for openid in possible_openids:
        result = await db.execute(select(User).where(User.openid == openid))
        guest_user = result.scalar_one_or_none()
        if guest_user:
            break

    if not guest_user or guest_user.id == new_user_id:
        return
    if guest_user.password or guest_user.email_verified_at:
        logger.warning("Refused merge: guest_id %s points to non-guest account %s", guest_id, guest_user.id)
        return

    guest_user_id = guest_user.id
    from app.models.order import Order
    await db.execute(update(Order).where(Order.user_id == guest_user_id).values(user_id=new_user_id))
    from app.models.live_portrait_job import LivePortraitJob
    await db.execute(update(LivePortraitJob).where(LivePortraitJob.user_id == guest_user_id).values(user_id=new_user_id))
    await db.flush()
    logger.info("Merged guest account %s -> %s", guest_user_id, new_user_id)
