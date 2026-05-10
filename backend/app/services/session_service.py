import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.redis_client import get_redis
from app.services.template_service import get_template_by_id

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


class SessionData(BaseModel):
    session_id: str
    status: str
    template_id: str
    host_image_url: Optional[str] = None
    guest_image_url: Optional[str] = None
    created_at: str
    expires_at: str


class SessionService:
    SESSION_TTL_MINUTES = 30
    _db_table_ready = False

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

    async def _ensure_db_table(self) -> None:
        if self.__class__._db_table_ready:
            return

        async with async_session_maker() as db:
            await db.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {DB_TABLE_NAME} (
                        session_id VARCHAR(16) PRIMARY KEY,
                        payload TEXT NOT NULL,
                        order_id VARCHAR(64),
                        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )
                    """
                )
            )
            await db.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_{DB_TABLE_NAME}_order_id ON {DB_TABLE_NAME} (order_id)")
            )
            await db.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_{DB_TABLE_NAME}_expires_at ON {DB_TABLE_NAME} (expires_at)")
            )
            await db.commit()

        self.__class__._db_table_ready = True

    async def _save_session_db(self, session_id: str, session: dict) -> None:
        await self._ensure_db_table()
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
        await self._ensure_db_table()
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
        await self._ensure_db_table()
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

    async def create_session(self, template_id: str, host_image_url: Optional[str] = None) -> dict:
        session_id = str(uuid.uuid4())[:8].upper()
        now = datetime.now()
        expires_at = now + timedelta(minutes=self.SESSION_TTL_MINUTES)

        session_data = {
            "status": SessionStatus.WAITING,
            "template_id": template_id,
            "host_image_url": host_image_url,
            "guest_image_url": None,
            "order_id": None,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

        await self._save_session(session_id, session_data)

        base_url = settings.effective_frontend_base_url.rstrip("/")
        join_url = f"{base_url}/pages/join/landing?session={session_id}"

        return {
            "session_id": session_id,
            "join_url": join_url,
            "qr_code_url": "",
            "expires_in_minutes": self.SESSION_TTL_MINUTES,
        }

    async def get_share_meta(self, session_id: str) -> Optional[dict]:
        session = await self.get_session(session_id)
        if not session or session.get("status") == SessionStatus.EXPIRED:
            return None

        template = get_template_by_id(session.get("template_id") or "")
        template_title = template.title if template else "Couple Session"

        base_backend = settings.effective_webhook_base_url.rstrip("/")
        image_url: str | None = None
        if template and template.image_url:
            image_url = template.image_url
            if not image_url.startswith("http"):
                image_url = f"{base_backend}{image_url}"

        base_frontend = settings.effective_frontend_base_url.rstrip("/")
        join_url = f"{base_frontend}/pages/join/landing?session={session_id}"
        mp_path = f"/pages/join/landing?session={session_id}"

        if template and template.category == "vintage":
            title = f"金婚重塑邀请 · {template_title}"
            description = "上传一张自拍，和 Ta 一起完成一组高质感纪念婚纱照。"
        else:
            title = f"邀请你合拍婚纱照 · {template_title}"
            description = "上传你的自拍，完成合拍并解锁同款成片。"

        return {
            "session_id": session_id,
            "join_url": join_url,
            "mp_path": mp_path,
            "title": title,
            "description": description,
            "image_url": image_url,
        }

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

    async def get_status(self, session_id: str) -> dict:
        session = await self.get_session(session_id)
        if not session:
            return {"exists": False, "status": "not_found"}

        return {
            "exists": True,
            "status": session["status"],
            "host_ready": session["host_image_url"] is not None,
            "guest_ready": session["guest_image_url"] is not None,
            "order_id": session.get("order_id"),
            "template_id": session["template_id"],
        }

    async def bind_order(self, session_id: str, order_id: str) -> bool:
        session = await self.get_session(session_id)
        if not session or session["status"] == SessionStatus.EXPIRED:
            return False

        session["order_id"] = order_id
        await self._save_session(session_id, session)

        if self._prefer_memory_store():
            order_sessions[order_id] = session_id
            return True

        try:
            redis = await get_redis()
            await redis.setex(self._order_key(order_id), self._remaining_ttl_seconds(session), session_id)
        except Exception as exc:
            logger.warning("remote_join_order_redis_mapping_failed: %s", exc)
            if self._allow_memory_fallback():
                order_sessions[order_id] = session_id
        return True

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

    async def upload_host_image(self, session_id: str, image_url: str) -> bool:
        session = await self.get_session(session_id)
        if not session or session["status"] == SessionStatus.EXPIRED:
            return False

        session["host_image_url"] = image_url
        self._check_ready(session)
        await self._save_session(session_id, session)
        return True

    async def upload_guest_image(self, session_id: str, image_url: str) -> bool:
        session = await self.get_session(session_id)
        if not session or session["status"] == SessionStatus.EXPIRED:
            return False

        session["status"] = SessionStatus.UPLOADING
        session["guest_image_url"] = image_url
        self._check_ready(session)
        await self._save_session(session_id, session)
        return True

    def _check_ready(self, session: dict):
        if session.get("host_image_url") and session.get("guest_image_url"):
            session["status"] = SessionStatus.READY

    async def get_images(self, session_id: str) -> Optional[dict]:
        session = await self.get_session(session_id)
        if not session or session["status"] != SessionStatus.READY:
            return None

        return {
            "host_image_url": session["host_image_url"],
            "guest_image_url": session["guest_image_url"],
            "template_id": session["template_id"],
        }

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
