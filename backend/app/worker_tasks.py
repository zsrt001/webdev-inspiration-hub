"""ARQ worker tasks."""

from __future__ import annotations

import uuid
import logging
from typing import Any

from sqlalchemy import select

from app.core.database import async_session_maker
from app.models.order import Order, OrderStatus
from app.models.live_portrait_job import LivePortraitJob, LivePortraitStatus
from app.services.generation_service import generation_service
from app.services.session_service import session_service

logger = logging.getLogger(__name__)


async def run_order_generation(order_id: str) -> None:
    """Fetch an order from DB and run generation."""
    try:
        order_uuid = uuid.UUID(str(order_id))
    except Exception:
        logger.error(f"[worker] invalid order_id: {order_id}")
        return

    remote_join = False
    async with async_session_maker() as db:
        result = await db.execute(select(Order).where(Order.id == order_uuid))
        order = result.scalar_one_or_none()
        if not order:
            logger.error(f"[worker] order not found: {order_uuid}")
            return

        if order.status in (OrderStatus.COMPLETED,):
            return

        template_id = order.template_id or ""
        source = order.source_image_urls if isinstance(order.source_image_urls, dict) else {}
        user_images = source.get("images") if isinstance(source, dict) else None
        if not template_id or not isinstance(user_images, list) or not user_images:
            order.status = OrderStatus.CREATED
            order.error_message = "Invalid order payload"
            await db.commit()
            return

        params = order.generation_params if isinstance(order.generation_params, dict) else {}
        remote_join = bool(params.get("remote_join"))
        identity_reference_pack = params.get("identity_reference_pack") or source.get("identity_reference_pack")
        if not isinstance(identity_reference_pack, dict):
            identity_reference_pack = None

        # Prefer "effective" params produced by Director Mode cascade.
        scene_image_url = params.get("effective_scene_image_url") or params.get("scene_image_url")
        clothing_image_url = params.get("effective_clothing_image_url") or params.get("clothing_image_url")

        # Control image overrides (optional)
        pose_image_url = params.get("pose_image_url")
        depth_image_url = params.get("depth_image_url")
        normal_image_url = params.get("normal_image_url")

        await db.commit()

    if remote_join:
        try:
            session_id = await session_service.get_session_id_for_order(str(order_uuid))
            if session_id:
                await session_service.mark_processing(session_id)
        except Exception as e:
            logger.warning(f"[worker] failed to mark session processing: {e}")

    await generation_service.generate_photo(
        order_id=str(order_uuid),
        template_id=template_id,
        user_images=[str(u) for u in user_images if u],
        subject_count=params.get("subject_count"),
        couple_flow=params.get("couple_flow"),
        prompt_override=params.get("prompt_override"),
        global_style_text=params.get("effective_global_style_text") or params.get("global_style_text"),
        scene_text=params.get("effective_scene_text"),
        outfit_text=params.get("effective_outfit_text"),
        identity_reference_pack=identity_reference_pack,
        scene_image_url=scene_image_url,
        clothing_image_url=clothing_image_url,
        pose_image_url=pose_image_url,
        depth_image_url=depth_image_url,
        normal_image_url=normal_image_url,
        scene_ip_weight=params.get("scene_ip_weight"),
        clothing_ip_weight=params.get("clothing_ip_weight"),
        face_ip_weight=params.get("face_ip_weight"),
        pose_cn_weight=params.get("pose_cn_weight"),
        depth_cn_weight=params.get("depth_cn_weight"),
        normal_cn_weight=params.get("normal_cn_weight"),
        pose_cn_start=params.get("pose_cn_start"),
        pose_cn_end=params.get("pose_cn_end"),
        depth_cn_start=params.get("depth_cn_start"),
        depth_cn_end=params.get("depth_cn_end"),
        normal_cn_start=params.get("normal_cn_start"),
        normal_cn_end=params.get("normal_cn_end"),
    )

    if remote_join:
        try:
            async with async_session_maker() as db:
                result = await db.execute(select(Order).where(Order.id == order_uuid))
                refreshed = result.scalar_one_or_none()
                if refreshed and refreshed.status == OrderStatus.COMPLETED:
                    session_id = await session_service.get_session_id_for_order(str(order_uuid))
                    if session_id:
                        await session_service.mark_completed(session_id)
        except Exception as e:
            logger.warning(f"[worker] failed to mark session completed: {e}")


async def generate_order(ctx: dict[str, Any], order_id: str) -> None:
    """ARQ worker entrypoint for order generation."""
    _ = ctx
    await run_order_generation(order_id)


async def run_live_portrait_generation(job_id: str) -> None:
    """Generate a short Live Portrait video from an image."""
    try:
        job_uuid = uuid.UUID(str(job_id))
    except Exception:
        logger.error(f"[worker] invalid live_portrait job_id: {job_id}")
        return

    async with async_session_maker() as db:
        result = await db.execute(select(LivePortraitJob).where(LivePortraitJob.id == job_uuid))
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"[worker] live_portrait job not found: {job_uuid}")
            return

        if job.status in (LivePortraitStatus.COMPLETED,):
            return

        job.status = LivePortraitStatus.GENERATING
        await db.commit()

        image_url = job.source_image_url
        seconds = int(job.seconds or 5)

    await generation_service.generate_live_portrait(
        job_id=str(job_uuid),
        image_url=image_url,
        seconds=seconds,
    )


async def generate_live_portrait(ctx: dict[str, Any], job_id: str) -> None:
    """ARQ worker entrypoint for Live Portrait generation."""
    _ = ctx
    await run_live_portrait_generation(job_id)
