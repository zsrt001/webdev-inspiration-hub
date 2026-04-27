"""Application configuration using pydantic-settings."""

import base64
import hashlib
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "AI Wedding Photo API"
    debug: bool = False
    auto_create_tables: bool | None = None
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Database (Supabase/Neon compatible)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_wedding"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    task_execution_mode: str = "auto"  # auto | arq | inline

    # AWS S3 Storage (for Vercel deployment)
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_bucket: str = "ai-wedding-photos"
    aws_region: str = "us-east-1"
    aws_s3_endpoint: str = ""  # Leave empty for AWS, set for MinIO/LocalStack
    aws_s3_public_url_base: str = ""  # Optional public base when upload endpoint is private/local
    storage_provider: str = "local"  # local | s3 | vercel

    # Vercel runtime / Blob Storage
    vercel: str = ""
    vercel_url: str = ""
    vercel_project_production_url: str = ""
    blob_read_write_token: str = ""
    vercel_blob_token: str = ""
    vercel_blob_upload_url_base: str = ""
    vercel_blob_public_url_base: str = ""

    # Primary vision LLM provider
    llm_provider: str = "wenwen"  # jiekou | wenwen
    jiekou_api_key: str = ""
    jiekou_chat_url: str = "https://api.jiekou.ai/v1/chat/completions"
    jiekou_vision_model: str = "gemini-3.1-flash"
    wenwen_api_key: str = ""
    wenwen_chat_api_key: str = ""
    wenwen_vision_api_key: str = ""
    wenwen_api_base_url: str = "https://breakout.wenwen-ai.com/v1"
    wenwen_chat_path: str = "/chat/completions"
    wenwen_models_path: str = "/models"
    wenwen_text_model: str = "deepseek-v3.2"
    wenwen_vision_model: str = "gemini-3.1-pro-preview"
    wenwen_image_model: str = "gemini-3-pro-image-preview"
    wenwen_image_generate_path: str = "/images/generations"
    wenwen_native_image_generate_path_template: str = "/v1beta/models/{model}:generateContent"
    wenwen_task_path_template: str = "/tasks/{task_id}"
    wenwen_image_size_single: str = "4:5"
    wenwen_image_size_couple: str = "3:2"
    wenwen_poll_interval: float = 3.0
    wenwen_poll_timeout: int = 240
    wenwen_max_retries: int = 0
    gatekeeper_allow_without_pillow: bool = False
    qa_allow_without_pillow: bool = False
    qa_require_vision: bool = False
    webhook_base_url: str = ""  # Public URL for webhooks
    frontend_base_url: str = ""  # Public URL for web/join links
    # Hosted checkout / staged commercial validation
    payment_provider: str = "creem"  # creem | manual_review
    manual_payment_display_name: str = "Manual Review Checkout"
    manual_payment_instructions: str = (
        "Submit the order reference to customer support after payment. Credits are issued after review."
    )
    manual_payment_contact: str = ""
    support_contact_email: str = ""
    support_contact_url: str = ""
    refund_policy_url: str = ""
    creem_api_key: str = ""
    creem_webhook_secret: str = ""
    creem_api_base_url: str = "https://api.creem.io"
    creem_product_pack_50: str = ""
    creem_product_pack_120: str = ""
    creem_product_pack_300: str = ""
    subscription_billing_enabled: bool = False
    creem_subscription_starter_product_id: str = ""
    creem_subscription_creator_product_id: str = ""
    creem_subscription_studio_product_id: str = ""

    # Generation engine
    generation_engine: str = "wenwen"  # comfyui | wenwen

    # Security
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    admin_token: str = ""  # If set, protects admin-only endpoints via X-Admin-Token header.
    phone_crypto_key: str = ""  # Optional dedicated key for lead phone encryption.
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_auth_timeout: float = 5.0
    rate_limit_enabled: bool = False
    rate_limit_default_requests: int = 240
    rate_limit_default_window_seconds: int = 60
    rate_limit_sensitive_requests: int = 40
    rate_limit_sensitive_window_seconds: int = 60
    rate_limit_exempt_paths: str = "/health,/health/ready,/api/v1/ops/readiness,/api/v1/ops/public_config"

    # ComfyUI
    comfy_provider: str = "local"  # local | cloud
    comfyui_base_url: str = "http://127.0.0.1:8188"
    comfy_cloud_base_url: str = "https://cloud.comfy.org"
    comfy_cloud_api_key: str = ""
    comfy_cloud_workflow_path: str = "workflows/comfyui_cloud_base_minimal.json"
    comfy_cloud_couple_workflow_path: str = "workflows/comfyui_cloud_couple_minimal.json"
    comfy_cloud_node_map: str = "{\"prompt\":{\"id\":\"2\",\"key\":\"text\"},\"negative_prompt\":{\"id\":\"3\",\"key\":\"text\"},\"init_image_name\":{\"id\":\"4\",\"key\":\"image\"}}"
    comfyui_workflow_path: str = "workflows/comfyui_base.json"
    comfyui_inpaint_workflow_path: str = "workflows/comfyui_couple_inpaint.json"
    comfyui_live_portrait_workflow_path: str = "workflows/comfyui_live_portrait.json"
    comfyui_node_map: str = "{\"prompt\":{\"id\":\"2\",\"key\":\"text\"},\"negative_prompt\":{\"id\":\"3\",\"key\":\"text\"},\"scene_image_url\":{\"id\":\"12\",\"key\":\"image\",\"upload\":true,\"type\":\"input\",\"allow_empty\":true},\"clothing_image_url\":{\"id\":\"13\",\"key\":\"image\",\"upload\":true,\"type\":\"input\",\"allow_empty\":true},\"face_image_url\":{\"id\":\"14\",\"key\":\"image\",\"upload\":true,\"type\":\"input\",\"allow_empty\":true},\"face_image_url_2\":{\"id\":\"34\",\"key\":\"image\",\"upload\":true,\"type\":\"input\",\"allow_empty\":true},\"pose_image_url\":{\"id\":\"22\",\"key\":\"image\",\"upload\":true,\"type\":\"input\",\"allow_empty\":true},\"depth_image_url\":{\"id\":\"23\",\"key\":\"image\",\"upload\":true,\"type\":\"input\",\"allow_empty\":true},\"normal_image_url\":{\"id\":\"24\",\"key\":\"image\",\"upload\":true,\"type\":\"input\",\"allow_empty\":true},\"scene_ip_weight\":{\"id\":\"19\",\"key\":\"weight\"},\"clothing_ip_weight\":{\"id\":\"20\",\"key\":\"weight\"},\"face_ip_weight\":{\"id\":\"21\",\"key\":\"weight\"},\"face2_ip_weight\":{\"id\":\"35\",\"key\":\"weight\"},\"pose_cn_weight\":{\"id\":\"28\",\"key\":\"strength\"},\"depth_cn_weight\":{\"id\":\"29\",\"key\":\"strength\"},\"normal_cn_weight\":{\"id\":\"30\",\"key\":\"strength\"},\"pose_cn_start\":{\"id\":\"28\",\"key\":\"start_percent\"},\"pose_cn_end\":{\"id\":\"28\",\"key\":\"end_percent\"},\"depth_cn_start\":{\"id\":\"29\",\"key\":\"start_percent\"},\"depth_cn_end\":{\"id\":\"29\",\"key\":\"end_percent\"},\"normal_cn_start\":{\"id\":\"30\",\"key\":\"start_percent\"},\"normal_cn_end\":{\"id\":\"30\",\"key\":\"end_percent\"}}"
    comfyui_live_portrait_node_map: str = "{\"image_url\":{\"id\":\"1\",\"key\":\"image\",\"upload\":true,\"type\":\"input\"},\"seconds\":{\"id\":\"2\",\"key\":\"seconds\"}}"
    comfyui_poll_interval: float = 1.5
    comfyui_poll_timeout: int = 300
    comfyui_max_retries: int = 1
    comfyui_require_storage_delivery: bool = True
    cleanup_source_images_on_complete: bool = False
    cleanup_cron_token: str = ""
    cron_secret: str = ""

    # Live Portrait / Remote Join
    live_portrait_enabled: bool = False
    remote_join_enabled: bool = True
    allow_memory_fallback: bool = False

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug_flag(cls, value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "debug", "dev", "development"}:
                return True
            if normalized in {"0", "false", "no", "off", "release", "prod", "production"}:
                return False
        return value

    @property
    def should_auto_create_tables(self) -> bool:
        """Whether startup may mutate database schema for local/dev convenience."""
        if self.auto_create_tables is not None:
            return bool(self.auto_create_tables)
        return bool(self.debug)

    @property
    def effective_cleanup_cron_token(self) -> str:
        """Token accepted by the cleanup endpoint.

        Vercel Cron sends `Authorization: Bearer $CRON_SECRET` when `CRON_SECRET`
        is configured. `CLEANUP_CRON_TOKEN` is kept as an app-specific alias so
        local/manual cron jobs do not need to depend on Vercel naming.
        """
        return (self.cleanup_cron_token or self.cron_secret or "").strip()

    @property
    def rate_limit_exempt_path_list(self) -> list[str]:
        return [item.strip() for item in (self.rate_limit_exempt_paths or "").split(",") if item.strip()]

    @property
    def s3_public_url_base(self) -> str:
        """Get base URL for public S3 objects."""
        if self.aws_s3_public_url_base:
            return self.aws_s3_public_url_base.rstrip("/")
        if self.aws_s3_endpoint:
            return f"{self.aws_s3_endpoint}/{self.aws_s3_bucket}"
        return f"https://{self.aws_s3_bucket}.s3.{self.aws_region}.amazonaws.com"

    @property
    def cors_origins(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        origins = [item.strip() for item in raw.split(",") if item.strip()] if raw else []
        frontend_origin = self.effective_frontend_base_url
        if frontend_origin and frontend_origin not in origins:
            origins.append(frontend_origin)
        return origins

    @property
    def phone_fernet_key(self) -> str:
        source = (self.phone_crypto_key or self.secret_key or "").encode("utf-8")
        digest = hashlib.sha256(source).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8")

    @property
    def comfy_mode(self) -> str:
        provider = (self.comfy_provider or "").strip().lower()
        if provider in {"cloud", "comfy_cloud", "comfycloud"}:
            return "cloud"
        if provider in {"local", "self_hosted", "self-hosted"}:
            return "local"
        if self.comfy_cloud_api_key:
            return "cloud"
        return "local"

    @property
    def using_comfy_cloud(self) -> bool:
        return self.comfy_mode == "cloud"

    @property
    def using_wenwen_generation(self) -> bool:
        return (self.generation_engine or "").strip().lower() == "wenwen"

    @property
    def is_vercel_runtime(self) -> bool:
        return str(self.vercel or "").strip().lower() in {"1", "true", "yes"}

    @staticmethod
    def _normalize_public_base_url(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if not raw.startswith(("http://", "https://")):
            raw = f"https://{raw.lstrip('/')}"
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def vercel_public_base_url(self) -> str:
        for candidate in (self.vercel_project_production_url, self.vercel_url):
            normalized = self._normalize_public_base_url(candidate)
            if normalized:
                return normalized
        return ""

    @property
    def effective_frontend_base_url(self) -> str:
        explicit = self._normalize_public_base_url(self.frontend_base_url)
        if explicit:
            return explicit
        if self.is_vercel_runtime:
            derived = self.vercel_public_base_url
            if derived:
                return derived
        return "http://localhost:3000"

    @property
    def effective_webhook_base_url(self) -> str:
        explicit = self._normalize_public_base_url(self.webhook_base_url)
        if explicit:
            return explicit
        if self.is_vercel_runtime:
            derived = self.vercel_public_base_url
            if derived:
                return derived
        return "http://localhost:8001"

    @property
    def vercel_blob_token_effective(self) -> str:
        return (self.vercel_blob_token or self.blob_read_write_token or "").strip()

    @property
    def generation_execution_mode(self) -> str:
        mode = (self.task_execution_mode or "").strip().lower()
        if mode in {"inline", "sync", "direct"}:
            return "inline"
        if mode in {"arq", "queue", "redis"}:
            return "arq"
        if self.is_vercel_runtime:
            return "inline"
        return "arq"

    @property
    def using_inline_generation_execution(self) -> bool:
        return self.generation_execution_mode == "inline"

    @property
    def using_background_queue(self) -> bool:
        return self.generation_execution_mode == "arq"

    @property
    def payment_mode(self) -> str:
        provider = (self.payment_provider or "").strip().lower()
        if provider in {"manual", "manual_review", "manual-review", "offline"}:
            return "manual_review"
        return "creem"

    @property
    def using_manual_review_payments(self) -> bool:
        return self.payment_mode == "manual_review"

    @property
    def wenwen_text_api_key_effective(self) -> str:
        return (self.wenwen_chat_api_key or self.wenwen_api_key or "").strip()

    @property
    def wenwen_vision_api_key_effective(self) -> str:
        return (self.wenwen_vision_api_key or self.wenwen_api_key or "").strip()

    @property
    def comfy_api_base_url(self) -> str:
        if self.using_comfy_cloud:
            return f"{self.comfy_cloud_base_url.rstrip('/')}/api"
        return self.comfyui_base_url.rstrip("/")

    @property
    def comfy_public_base_url(self) -> str:
        if self.using_comfy_cloud:
            return self.comfy_cloud_base_url.rstrip("/")
        return self.comfyui_base_url.rstrip("/")

    @property
    def comfy_auth_headers(self) -> dict[str, str]:
        if self.using_comfy_cloud and self.comfy_cloud_api_key:
            return {"X-API-Key": self.comfy_cloud_api_key}
        return {}

    @property
    def generation_provider_name(self) -> str:
        if self.using_wenwen_generation:
            return "wenwen"
        if self.using_comfy_cloud:
            return "comfy_cloud"
        return "comfyui"

    @property
    def generation_poll_timeout(self) -> int:
        if self.using_wenwen_generation:
            return int(self.wenwen_poll_timeout)
        return int(self.comfyui_poll_timeout)

    @property
    def generation_max_retries(self) -> int:
        if self.using_wenwen_generation:
            return int(self.wenwen_max_retries)
        return int(self.comfyui_max_retries)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
