import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.redis_client import get_redis

settings = get_settings()
logger = logging.getLogger(__name__)

sessions: dict[str, dict] = {}
order_sessions: dict[str, str] = {}

SESSION_KEY_PREFIX = "aiws:session:"
ORDER_SESSION_KEY_PREFIX = "aiws:order_session:"
DB_TABLE_NAME = "remote_join_sessions"


class SessionStatus:
    WAITING = "waiting"
    UPLOADING = "uploading"
    READY = "ready"
    PROCESSING = "processing"
    COMPLETED = "completed"
    EXPIRED = "expired"


class SessionService:
    SESSION_TTL_MINUTES = 30

    @staticmethod
    def _allow_memory_fallback() -> bool:
        return settings.debug or settings.allow_memory_fallback

    @staticmethod
    def _prefer_memory_store() -> bool:
        return settings.debug and settings.allow_memory_fallback and not settings.is_vercel_runtime

    def _key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"

    def _order_key(self, order_id: str) -> str:
        return f"{ORDER_SESSION_KEY_PREFIX}{order_id}"

    def _remaining_ttl_seconds(self, session: dict) -> int:
        try:
            expires_at = datetime.fromisoformat(session["expires_at"])
            ttl = int((expires_at - datetime.now()).total_seconds())
            return max(1, ttl)
        except Exception:
            return self.SESSION_TTL_MINUTES * 60

    async def _save_session_db(self, session_id: str, session: dict) -> None:
        payload = json.dumps(session, ensure_ascii=False)
        expires_at = datetime.fromisoformat(session["expires_at"])

        async with async_session_maker() as db:
            await db.execute(
                text(
                    f"""
                    INSERT INTO {DB_TABLE_NAME} (session_id, payload, order_id, expires_at, updated_at)
                    VALUES (:session_id, :payload, :order_id, :expires_at, CURRENT_TIMESTAMP)
                    ON CONFLICT (session_id) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        order_id = EXCLUDED.order_id,
                        expires_at = EXCLUDED.expires_at,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "session_id": session_id,
                    "payload": payload,
                    "order_id": session.get("order_id"),
                    "expires_at": expires_at,
                },
            )
            await db.commit()

    async def _get_session_db(self, session_id: str) -> Optional[dict]:
        async with async_session_maker() as db:
            result = await db.execute(
                text(f"SELECT payload FROM {DB_TABLE_NAME} WHERE session_id = :session_id LIMIT 1"),
                {"session_id": session_id},
            )
            raw = result.scalar_one_or_none()
        if not raw:
            return None
        return json.loads(raw)

    async def _get_session_id_for_order_db(self, order_id: str) -> str | None:
        async with async_session_maker() as db:
            result = await db.execute(
                text(
                    f"""
                    SELECT session_id
                    FROM {DB_TABLE_NAME}
                    WHERE order_id = :order_id AND expires_at > CURRENT_TIMESTAMP
                    LIMIT 1
                    """
                ),
                {"order_id": order_id},
            )
            value = result.scalar_one_or_none()
        return str(value).strip() if value else None

    async def _save_session(self, session_id: str, session: dict) -> None:
        if self._prefer_memory_store():
            sessions[session_id] = session
            return

        try:
            redis = await get_redis()
            await redis.setex(self._key(session_id), self._remaining_ttl_seconds(session), json.dumps(session))
            try:
                await self._save_session_db(session_id, session)
            except Exception as exc:
                logger.warning("remote_join_db_mirror_failed: %s", exc)
            return
        except Exception as redis_exc:
            try:
                await self._save_session_db(session_id, session)
                return
            except Exception as db_exc:
                if self._allow_memory_fallback():
                    sessions[session_id] = session
                    return
                raise RuntimeError(f"session_store_unavailable: redis={redis_exc}; db={db_exc}") from db_exc

    async def get_session(self, session_id: str) -> Optional[dict]:
        session: dict | None = None

        if self._prefer_memory_store():
            session = sessions.get(session_id)
        else:
            try:
                redis = await get_redis()
                raw = await redis.get(self._key(session_id))
                if raw:
                    session = json.loads(raw)
            except Exception as redis_exc:
                try:
                    session = await self._get_session_db(session_id)
                except Exception as db_exc:
                    if self._allow_memory_fallback():
                        session = sessions.get(session_id)
                    else:
                        raise RuntimeError(f"session_store_unavailable: redis={redis_exc}; db={db_exc}") from db_exc

            if session is None:
                try:
                    session = await self._get_session_db(session_id)
                except Exception as db_exc:
                    if self._allow_memory_fallback():
                        session = sessions.get(session_id)
                    else:
                        raise RuntimeError(f"session_store_unavailable: db={db_exc}") from db_exc

            if session is None and self._allow_memory_fallback():
                session = sessions.get(session_id)

        if not session:
            return None

        expires_at = datetime.fromisoformat(session["expires_at"])
        if datetime.now() > expires_at:
            session["status"] = SessionStatus.EXPIRED
            await self._save_session(session_id, session)
            return session

        return session

    async def get_session_id_for_order(self, order_id: str) -> str | None:
        if not order_id:
            return None

        if self._prefer_memory_store():
            return order_sessions.get(order_id)

        try:
            redis = await get_redis()
            raw = await redis.get(self._order_key(order_id))
            if raw:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", errors="ignore")
                return str(raw).strip() or None
        except Exception as exc:
            logger.warning("remote_join_order_redis_lookup_failed: %s", exc)

        try:
            value = await self._get_session_id_for_order_db(order_id)
            if value:
                return value
        except Exception as db_exc:
            if self._allow_memory_fallback():
                return order_sessions.get(order_id)
            raise RuntimeError(f"session_store_unavailable: db={db_exc}") from db_exc

        if self._allow_memory_fallback():
            return order_sessions.get(order_id)

        return None

    async def mark_processing(self, session_id: str):
        session = await self.get_session(session_id)
        if not session:
            return

        session["status"] = SessionStatus.PROCESSING
        await self._save_session(session_id, session)

    async def mark_completed(self, session_id: str):
        session = await self.get_session(session_id)
        if not session:
            return

        session["status"] = SessionStatus.COMPLETED
        await self._save_session(session_id, session)


session_service = SessionService()
