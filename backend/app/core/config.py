"""Application configuration using pydantic-settings."""

from functools import lru_cache
from pathlib import Path
import re
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import AliasChoices, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE_PATH = Path(__file__).resolve().parents[2] / ".env"
PRODUCTION_ALLOWED_IMAGE_MODELS = (
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
)


def _database_login_and_target(database_url: str) -> tuple[str, tuple[str, str, int, str]]:
    try:
        parsed = urlparse(str(database_url or "").strip())
        login = unquote(parsed.username or "").strip()
        host = (parsed.hostname or "").strip().lower()
        port = parsed.port or 5432
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid PostgreSQL database URL") from exc
    scheme = parsed.scheme.split("+", 1)[0].lower()
    database = unquote((parsed.path or "").lstrip("/")).strip()
    if scheme not in {"postgres", "postgresql"} or not login or not host or not database:
        raise ValueError("database URL must include PostgreSQL login, host, and database")
    if host.startswith("db.") and host.endswith(".supabase.co"):
        project_ref = host.removeprefix("db.").removesuffix(".supabase.co")
        if not project_ref:
            raise ValueError("Supabase direct URL is missing its project reference")
        target = ("supabase", project_ref, 0, database)
    elif "pooler.supabase." in host:
        if "." not in login:
            raise ValueError("Supabase pooler login is missing its project reference")
        project_ref = login.rsplit(".", 1)[1].strip().lower()
        if not project_ref:
            raise ValueError("Supabase pooler login is missing its project reference")
        target = ("supabase", project_ref, 0, database)
    else:
        target = ("postgresql", host, port, database)
    return login, target


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE_PATH),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # Application
    app_name: str = "AI Wedding Photo API"
    debug: bool = False
    auto_create_tables: bool | None = None
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    runtime_environment: Literal["development", "preview", "production"] = Field(
        default="development",
        validation_alias=AliasChoices("RUNTIME_ENVIRONMENT", "VERCEL_ENV"),
    )
    vercel_deployment_id: str = Field(default="", validation_alias="VERCEL_DEPLOYMENT_ID")
    vercel_git_commit_sha: str = Field(
        default="",
        validation_alias=AliasChoices("VERCEL_GIT_COMMIT_SHA", "SOURCE_SHA"),
    )
    runtime_bundle_id: str = ""
    release_role: str = Field(default="", validation_alias="RELEASE_ROLE")
    worker_image_digest: str = ""
    acceptance_identity_hmac_key: str = ""

    # Database (Supabase/Neon compatible)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_wedding"
    control_plane_database_url: str = ""

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
    private_blob_read_write_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "PRIVATE_BLOB_READ_WRITE_TOKEN",
            "VOWPIC_PRIVATE_BLOB_READ_WRITE_TOKEN",
        ),
    )
    upload_max_bytes: int = 10_485_760
    upload_max_files: int = 5
    upload_max_pixels: int = 40_000_000
    upload_requests_per_hour: int = 20
    upload_bytes_per_day: int = 209_715_200
    upload_max_concurrent: int = 2
    upload_intent_ttl_seconds: int = 900
    provider_asset_grant_ttl_seconds: int = 600
    provider_asset_grant_max_reads: int = 3
    external_fetch_max_redirects: int = 2
    external_fetch_connect_timeout_seconds: int = 5
    external_fetch_total_timeout_seconds: int = 30
    external_fetch_max_bytes: int = 10_485_760

    # Primary vision LLM provider
    llm_provider: str = "wenwen"  # jiekou | wenwen
    jiekou_api_key: str = ""
    jiekou_chat_url: str = "https://api.jiekou.ai/v1/chat/completions"
    jiekou_vision_model: str = "gemini-3.1-flash"
    wenwen_chat_api_key: str = ""
    wenwen_vision_api_key: str = ""
    wenwen_api_base_url: str = "https://breakout.wenwen-ai.com/v1"
    wenwen_chat_path: str = "/chat/completions"
    wenwen_text_model: str = "deepseek-v3.2"
    wenwen_vision_model: str = "gemini-3.1-pro-preview"
    generation_allowed_image_models: str = "gemini-3-pro-image-preview,gemini-3.1-flash-image-preview"
    evolink_api_key: str = ""
    evolink_api_base_url: str = "https://api.evolink.ai"
    evolink_image_model: str = "gemini-3.1-flash-image-preview"
    evolink_image_quality: str = "2K"
    evolink_image_size: str = "3:4"
    evolink_poll_interval: float = 5.0
    evolink_poll_timeout: int = 720
    evolink_max_retries: int = 2
    gatekeeper_allow_without_pillow: bool = False
    qa_allow_without_pillow: bool = False
    qa_require_vision: bool = False
    qa_require_identity_vision: bool = True
    qa_require_identity_embedding: bool = True
    qa_identity_similarity_single_threshold: float = 0.55
    qa_identity_similarity_couple_threshold: float = 0.50
    qa_identity_similarity_margin_threshold: float = 0.08
    qa_identity_generated_face_similarity_max: float = 0.82
    qa_identity_embedding_model_name: str = "buffalo_l"
    qa_identity_embedding_det_size: int = 640
    qa_identity_embedding_ctx_id: int = -1
    qa_require_photometric: bool = True
    qa_photometric_face_luma_min: float = 86.0
    qa_photometric_face_luma_max: float = 205.0
    qa_photometric_background_face_delta_max: float = 20.0
    qa_photometric_skin_highlight_ratio_max: float = 0.065
    qa_photometric_dress_clip_ratio_max: float = 0.16
    qa_photometric_flat_face_delta_min: float = 4.0
    qa_photometric_flat_face_contrast_max: float = 13.0
    qa_photometric_harsh_face_delta_max: float = 72.0
    qa_photometric_color_temp_delta_max: float = 46.0
    qa_fail_on_vision_error: bool = False
    qa_vision_error_retry_attempts: int = 3
    qa_vision_timeout_seconds: float = 75.0
    webhook_base_url: str = ""  # Public URL for webhooks
    provider_grant_origin: str = ""  # Exact isolated origin for Provider asset reads
    provider_grant_probe_secret: str = ""  # Authenticates the isolated runtime probe
    frontend_base_url: str = ""  # Public URL for web/join links
    # Hosted checkout / staged commercial validation
    payment_provider: str = "creem"  # creem | manual_review
    manual_payment_display_name: str = "Manual Review Checkout"
    manual_payment_instructions: str = (
        "Submit the order reference to customer support after payment. Credits are issued after review."
    )
    manual_payment_contact: str = ""
    support_contact_email: str = "zst000001@gmail.com"
    support_contact_url: str = ""
    refund_policy_url: str = ""
    creem_api_key: str = ""
    creem_webhook_secret: str = ""
    creem_api_base_url: str = "https://api.creem.io"
    creem_product_pack_50: str = ""
    creem_product_pack_120: str = ""
    creem_product_pack_300: str = ""
    google_auth_enabled: bool = False
    authenticated_upload_enabled: bool = False
    generation_enabled: bool = False
    credit_pack_checkout_enabled: bool = False
    subscription_billing_enabled: bool = False
    private_download_enabled: bool = False
    partner_invite_enabled: bool = False
    creem_subscription_starter_product_id: str = ""
    creem_subscription_creator_product_id: str = ""
    creem_subscription_studio_product_id: str = ""
    supabase_pooler_region: str = "us-east-1"
    supabase_pooler_host: str = ""

    # Generation engine
    generation_engine: str = "evolink"

    # Security
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 days
    admin_token: str = ""  # Backend-only fallback for scripts/internal admin calls. Do not expose in frontend.
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_auth_timeout: float = 5.0
    supabase_exchange_max_token_age_seconds: int = 600
    supabase_clock_skew_seconds: int = 60
    rate_limit_enabled: bool = False
    rate_limit_default_requests: int = 240
    rate_limit_default_window_seconds: int = 60
    rate_limit_sensitive_requests: int = 40
    rate_limit_sensitive_window_seconds: int = 60
    rate_limit_exempt_paths: str = "/health,/health/ready,/api/v1/ops/readiness,/api/v1/ops/public_config"
    new_account_ip_limit_per_hour: int = 8
    new_account_device_limit_per_hour: int = 3
    resend_api_key: str = ""
    trial_welcome_credits: int = Field(default=2, ge=2, le=2)
    trial_daily_generation_limit: int = Field(default=3, ge=3, le=3)
    order_active_user_limit: int = 1
    order_active_window_minutes: int = 45
    trial_preview_max_width: int = 900
    trial_preview_max_height: int = 1125
    trial_watermark_text: str = "AI WEDDING STUDIO PREVIEW"
    postprocess_enabled: bool = True
    postprocess_upscale_factor: int = 2
    postprocess_max_long_edge: int = 2400
    postprocess_jpeg_quality: int = 92
    postprocess_variants: str = "2x3,3x2,3x4,4x5,9x16,1x1"

    cleanup_source_images_on_complete: bool = False
    cleanup_cron_token: str = ""
    cron_secret: str = ""
    transient_generated_cleanup_hours: int = 6
    transient_generated_cleanup_limit: int = 200

    # Observability
    sentry_dsn: str = ""

    # Alert push (Slack / Feishu / DingTalk incoming webhook URL)
    ops_alert_webhook_url: str = ""

    # Email notifications (Resend)
    resend_api_key: str = ""
    email_from_address: str = "noreply@example.com"
    email_from_name: str = "AI Wedding Studio"

    # Live Portrait / Remote Join
    live_portrait_enabled: bool = False
    remote_join_enabled: bool = False
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

    @field_validator(
        "upload_max_bytes",
        "upload_max_files",
        "upload_max_pixels",
        "upload_requests_per_hour",
        "upload_bytes_per_day",
        "upload_max_concurrent",
        "upload_intent_ttl_seconds",
        "provider_asset_grant_ttl_seconds",
        "provider_asset_grant_max_reads",
        "external_fetch_max_redirects",
        "external_fetch_connect_timeout_seconds",
        "external_fetch_total_timeout_seconds",
        "external_fetch_max_bytes",
    )
    @classmethod
    def enforce_media_security_caps(cls, value: int, info: ValidationInfo) -> int:
        """Allow stricter deployments, but never weaken the documented hard ceilings."""

        caps = {
            "upload_max_bytes": 10_485_760,
            "upload_max_files": 5,
            "upload_max_pixels": 40_000_000,
            "upload_requests_per_hour": 20,
            "upload_bytes_per_day": 209_715_200,
            "upload_max_concurrent": 2,
            "upload_intent_ttl_seconds": 900,
            "provider_asset_grant_ttl_seconds": 600,
            "provider_asset_grant_max_reads": 3,
            "external_fetch_max_redirects": 2,
            "external_fetch_connect_timeout_seconds": 5,
            "external_fetch_total_timeout_seconds": 30,
            "external_fetch_max_bytes": 10_485_760,
        }
        normalized = int(value)
        cap = caps[str(info.field_name)]
        if normalized <= 0 or normalized > cap:
            raise ValueError(f"{info.field_name} must be between 1 and {cap}")
        return normalized

    @property
    def should_auto_create_tables(self) -> bool:
        """Deprecated compatibility property; runtime schema writes stay disabled."""
        return False

    @property
    def deployment_id(self) -> str:
        """Platform-issued deployment coordinate; project `DEPLOYMENT_ID` is ignored."""
        return self.vercel_deployment_id.strip()

    @property
    def source_sha(self) -> str:
        """Platform-issued source coordinate exposed only as a non-secret digest."""
        value = self.vercel_git_commit_sha.strip().lower()
        return value if re.fullmatch(r"[0-9a-f]{40,64}", value) else ""

    @property
    def runtime_coordinate_errors(self) -> list[str]:
        """Return readiness blockers without preventing the liveness process from starting."""
        if self.runtime_environment == "development":
            return []
        errors: list[str] = []
        if not self.deployment_id:
            errors.append("VERCEL_DEPLOYMENT_ID is required")
        if not re.fullmatch(r"rtb_[0-9a-f]{64}", self.runtime_bundle_id.strip()):
            errors.append("RUNTIME_BUNDLE_ID must be a canonical rtb_ SHA-256 identity")
        release_role = self.release_role.strip()
        if self.runtime_environment == "preview" and release_role not in {
            "PREVIEW_IDENTITY",
            "PREVIEW_COMMERCIAL",
        }:
            errors.append("RELEASE_ROLE must identify an approved Preview role")
        if self.runtime_environment == "production" and release_role not in {
            "SAFE_BASELINE",
            "COMMERCIAL_7A",
            "CONTRACT_7B",
        }:
            errors.append("RELEASE_ROLE must identify an approved Production role")
        if len(self.acceptance_identity_hmac_key.strip()) < 32:
            errors.append("ACCEPTANCE_IDENTITY_HMAC_KEY must contain at least 32 characters")
        digest = self.worker_image_digest.strip()
        if digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            errors.append("WORKER_IMAGE_DIGEST must be a sha256 OCI digest")
        return errors

    @property
    def runtime_coordinates_valid(self) -> bool:
        return not self.runtime_coordinate_errors

    @property
    def effective_control_plane_database_url(self) -> str:
        explicit = self.control_plane_database_url.strip()
        if explicit:
            return explicit
        return self.database_url.strip() if self.runtime_environment == "development" else ""

    @property
    def control_plane_database_config_errors(self) -> list[str]:
        if self.runtime_environment == "development":
            return []
        explicit = self.control_plane_database_url.strip()
        if not explicit:
            return ["CONTROL_PLANE_DATABASE_URL is required outside development"]
        try:
            runtime_login, runtime_target = _database_login_and_target(self.database_url)
            writer_login, writer_target = _database_login_and_target(explicit)
        except ValueError:
            return ["DATABASE_URL and CONTROL_PLANE_DATABASE_URL must be valid PostgreSQL URLs"]
        errors: list[str] = []
        if runtime_login == writer_login:
            errors.append(
                "DATABASE_URL and CONTROL_PLANE_DATABASE_URL must use distinct login roles"
            )
        if runtime_target != writer_target:
            errors.append(
                "DATABASE_URL and CONTROL_PLANE_DATABASE_URL must target the same database"
            )
        return errors

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
    def supabase_oauth_enabled(self) -> bool:
        """Whether Google OAuth can complete both redirect and token exchange."""
        return bool(
            self.supabase_url.strip()
            and self.supabase_anon_key.strip()
        )

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
    def using_evolink_generation(self) -> bool:
        return self.generation_engine == "evolink"

    @property
    def is_vercel_runtime(self) -> bool:
        return str(self.vercel or "").strip().lower() in {"1", "true", "yes"}

    @property
    def aws_s3_endpoint_is_loopback(self) -> bool:
        raw = str(self.aws_s3_endpoint or "").strip()
        if not raw:
            return False
        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
        host = (parsed.hostname or "").strip().lower()
        return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.startswith("127.")

    @property
    def effective_storage_provider(self) -> str:
        provider = (self.storage_provider or "").strip().lower()
        if (
            provider == "s3"
            and self.is_vercel_runtime
            and self.aws_s3_endpoint_is_loopback
            and self.blob_token_effective
        ):
            return "vercel"
        return provider

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
    def effective_provider_grant_origin(self) -> str:
        explicit = self._normalize_public_base_url(self.provider_grant_origin)
        if explicit:
            return explicit
        return self.effective_webhook_base_url

    @property
    def blob_token_effective(self) -> str:
        return (
            self.private_blob_read_write_token
            or self.blob_read_write_token
            or ""
        ).strip()

    @property
    def generation_execution_mode(self) -> str:
        mode = (self.task_execution_mode or "").strip().lower()
        if mode in {"inline", "sync", "direct"}:
            return "inline" if self.runtime_environment == "development" and self.debug else "disabled"
        if mode in {"arq", "queue", "redis"}:
            return "arq"
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
        return (self.wenwen_chat_api_key or "").strip()

    @property
    def wenwen_vision_api_key_effective(self) -> str:
        return (self.wenwen_vision_api_key or "").strip()

    @property
    def generation_provider_name(self) -> str:
        return "evolink" if self.using_evolink_generation else "invalid"

    @property
    def generation_poll_timeout(self) -> int:
        return int(self.evolink_poll_timeout)

    @property
    def generation_max_retries(self) -> int:
        return int(self.evolink_max_retries)

    @property
    def generation_allowed_image_model_list(self) -> list[str]:
        configured = [
            item.strip()
            for item in (self.generation_allowed_image_models or "").split(",")
            if item.strip()
        ]
        allowed = list(PRODUCTION_ALLOWED_IMAGE_MODELS)
        if not configured:
            return allowed
        return [model for model in configured if model in allowed]

    def generation_image_model_allowed(self, model: str) -> bool:
        value = str(model or "").strip()
        return bool(value and value in self.generation_allowed_image_model_list)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
