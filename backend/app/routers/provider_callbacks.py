"""Provider callbacks that remain available while commercial capabilities are OFF."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.evolink_callback_service import (
    EvolinkCallbackError,
    bind_evolink_callback_task,
)
from app.services.evolink_service import EvolinkProviderError, parse_evolink_task_fact
from app.services.generation_executor_service import reconcile_generation_job


router = APIRouter(include_in_schema=False)
_MAX_CALLBACK_BYTES = 65_536


@router.post(
    "/provider-callbacks/evolink/{attempt_id}/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def evolink_task_callback(
    attempt_id: uuid.UUID,
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    raw = await request.body()
    if not raw or len(raw) > _MAX_CALLBACK_BYTES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("evolink_callback_payload_invalid")
        body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        task_id = body.get("id") or body.get("task_id")
        if not isinstance(task_id, str):
            raise ValueError("evolink_callback_task_id_missing")
        fact = parse_evolink_task_fact(task_id.strip(), payload)
        result = await bind_evolink_callback_task(
            db,
            attempt_id=attempt_id,
            token=token,
            fact=fact,
        )
        await db.commit()
        await reconcile_generation_job(result.job_id)
    except EvolinkCallbackError as exc:
        if exc.code == "evolink_callback_not_found":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc
        if exc.code == "evolink_callback_not_terminal":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc
    except (EvolinkProviderError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
