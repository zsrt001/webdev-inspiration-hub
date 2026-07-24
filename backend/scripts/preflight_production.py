from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


def _bootstrap_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    backend_dir = repo_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    return repo_root


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}***{value[-2:]}"


def _item(name: str, value: str, *, secret: bool = False) -> dict[str, Any]:
    normalized = (value or "").strip()
    return {
        "name": name,
        "present": bool(normalized),
        "preview": _mask_secret(normalized) if secret and normalized else normalized,
    }


def _build_env_summary(settings: Any) -> dict[str, list[dict[str, Any]]]:
    provider = (settings.storage_provider or "").strip().lower()

    summary: dict[str, list[dict[str, Any]]] = {
        "runtime": [
            _item("DEBUG", str(settings.debug)),
            _item("GENERATION_ENGINE", str(settings.generation_engine)),
            _item("LLM_PROVIDER", str(settings.llm_provider)),
            _item("TASK_EXECUTION_MODE", str(settings.generation_execution_mode)),
            _item("PAYMENT_PROVIDER", str(settings.payment_provider)),
            _item("STORAGE_PROVIDER", str(settings.storage_provider)),
            _item("LIVE_PORTRAIT_ENABLED", str(settings.live_portrait_enabled)),
            _item("REMOTE_JOIN_ENABLED", str(settings.remote_join_enabled)),
            _item("QA_REQUIRE_VISION", str(settings.qa_require_vision)),
        ],
        "connectivity": [
            _item("DATABASE_URL", str(settings.database_url), secret=True),
            _item("REDIS_URL", str(settings.redis_url), secret=True),
            _item("FRONTEND_BASE_URL", str(settings.effective_frontend_base_url)),
            _item("WEBHOOK_BASE_URL", str(settings.effective_webhook_base_url)),
        ],
        "security": [
            _item("SECRET_KEY", str(settings.secret_key), secret=True),
            _item("ADMIN_TOKEN", str(settings.admin_token), secret=True),
            _item("EVOLINK_API_KEY", str(settings.evolink_api_key), secret=True),
            _item("WENWEN_CHAT_API_KEY", str(settings.wenwen_text_api_key_effective), secret=True),
            _item("WENWEN_VISION_API_KEY", str(settings.wenwen_vision_api_key_effective), secret=True),
        ],
        "payments": [_item("PAYMENT_PROVIDER", str(settings.payment_provider))],
        "storage": [],
        "platform": [
            _item("VERCEL", str(settings.vercel)),
            _item("VERCEL_URL", str(settings.vercel_url)),
            _item("VERCEL_PROJECT_PRODUCTION_URL", str(settings.vercel_project_production_url)),
        ],
    }

    summary["connectivity"].extend(
        [
            _item("EVOLINK_API_BASE_URL", str(settings.evolink_api_base_url)),
            _item("EVOLINK_IMAGE_MODEL", str(settings.evolink_image_model)),
            _item("EVOLINK_IMAGE_SIZE", str(settings.evolink_image_size)),
            _item("EVOLINK_IMAGE_QUALITY", str(settings.evolink_image_quality)),
        ]
    )

    if provider == "s3":
        summary["storage"] = [
            _item("AWS_ACCESS_KEY_ID", str(settings.aws_access_key_id), secret=True),
            _item("AWS_SECRET_ACCESS_KEY", str(settings.aws_secret_access_key), secret=True),
            _item("AWS_S3_BUCKET", str(settings.aws_s3_bucket)),
            _item("AWS_REGION", str(settings.aws_region)),
            _item("AWS_S3_ENDPOINT", str(settings.aws_s3_endpoint)),
            _item("AWS_S3_PUBLIC_URL_BASE", str(settings.aws_s3_public_url_base)),
        ]
    elif provider == "vercel":
        summary["storage"] = [
            _item("BLOB_READ_WRITE_TOKEN", str(settings.blob_read_write_token), secret=True),
            _item("BLOB_TOKEN_EFFECTIVE", str(settings.blob_token_effective), secret=True),
        ]
    else:
        summary["storage"] = [_item("STORAGE_PROVIDER", str(settings.storage_provider))]

    if settings.payment_mode == "creem":
        summary["payments"].extend(
            [
                _item("CREEM_API_KEY", str(settings.creem_api_key), secret=True),
                _item("CREEM_WEBHOOK_SECRET", str(settings.creem_webhook_secret), secret=True),
                _item("CREEM_PRODUCT_PACK_50", str(settings.creem_product_pack_50)),
                _item("CREEM_PRODUCT_PACK_120", str(settings.creem_product_pack_120)),
                _item("CREEM_PRODUCT_PACK_300", str(settings.creem_product_pack_300)),
            ]
        )
    else:
        summary["payments"].extend(
            [
                _item("MANUAL_PAYMENT_DISPLAY_NAME", str(settings.manual_payment_display_name)),
                _item("MANUAL_PAYMENT_CONTACT", str(settings.manual_payment_contact)),
                _item("MANUAL_PAYMENT_INSTRUCTIONS", str(settings.manual_payment_instructions)),
            ]
        )

    return summary


def _build_next_steps(report: dict[str, Any]) -> list[str]:
    blockers = set(report.get("blockers") or [])
    steps: list[str] = []

    if "commercial_config" in blockers:
        steps.append("补齐严格运行时配置，并确保受保护环境使用 `DEBUG=false`。")
    if "database" in blockers:
        steps.append("检查 `DATABASE_URL` 是否指向可连接且权限正确的 PostgreSQL。")
    if "generation_runtime" in blockers:
        steps.append("检查 EvoLink 凭据、网站后端生成执行配置和任务查询链路。")
    if "storage_config" in blockers or "storage_rw_probe" in blockers:
        steps.append("检查私有对象存储配置；如使用 Vercel Blob，确认读写令牌只存在于服务端。")
    if "payments_config" in blockers:
        steps.append("检查 Creem 产品目录、回跳地址和 webhook 签名配置。")
    if not steps:
        steps.append("配置预检已通过；这不代表真实商业主链路或生产验收通过。")

    steps.append("查看 `docs/PRODUCTION_ACCEPTANCE.md` 中尚未满足的真实验收门槛。")
    return steps


def _render_console(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=== AI Wedding Studio Production Preflight ===")
    lines.append(f"strict_mode: {payload['strict_mode']}")
    lines.append(f"probe_storage: {payload['probe_storage']}")
    lines.append(f"probe_generation_backend: {payload['probe_generation_backend']}")
    lines.append(f"commercial_ready: {payload['commercial_ready']}")
    lines.append("")
    lines.append("Checks:")
    for name, result in payload["readiness"]["checks"].items():
        status = "OK" if result.get("ok") else "FAIL"
        latency = result.get("latency_ms", 0.0)
        detail = result.get("detail", "")
        lines.append(f"- [{status}] {name} ({latency} ms) :: {detail}")
    lines.append("")
    lines.append("Environment summary:")
    for group, items in payload["env_summary"].items():
        lines.append(f"- {group}:")
        for item in items:
            status = "OK" if item["present"] else "MISSING"
            preview = item["preview"]
            suffix = f" = {preview}" if preview else ""
            lines.append(f"  - [{status}] {item['name']}{suffix}")
    lines.append("")
    lines.append("Next steps:")
    for step in payload["next_steps"]:
        lines.append(f"- {step}")
    return "\n".join(lines)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Production Preflight Report")
    lines.append("")
    lines.append(f"- `strict_mode`: `{payload['strict_mode']}`")
    lines.append(f"- `probe_storage`: `{payload['probe_storage']}`")
    lines.append(
        f"- `probe_generation_backend`: `{payload['probe_generation_backend']}`"
    )
    lines.append(f"- `commercial_ready`: `{payload['commercial_ready']}`")
    lines.append("")
    lines.append("## Checks")
    for name, result in payload["readiness"]["checks"].items():
        status = "OK" if result.get("ok") else "FAIL"
        lines.append(f"- `{name}`: **{status}** - `{result.get('detail', '')}`")
    lines.append("")
    lines.append("## Environment Summary")
    for group, items in payload["env_summary"].items():
        lines.append(f"### `{group}`")
        for item in items:
            status = "OK" if item["present"] else "MISSING"
            preview = item["preview"]
            suffix = f" = `{preview}`" if preview else ""
            lines.append(f"- `{item['name']}`: **{status}**{suffix}")
        lines.append("")
    lines.append("## Next Steps")
    for step in payload["next_steps"]:
        lines.append(f"- {step}")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    from app.core.config import get_settings
    from app.core.runtime_checks import run_readiness_checks

    settings = get_settings()
    readiness = await run_readiness_checks(
        probe_storage=bool(args.probe_storage),
        probe_generation_backend=bool(args.probe_generation_backend),
        strict_mode=not bool(args.non_strict),
    )
    payload = {
        "strict_mode": readiness.get("strict_mode", True),
        "probe_storage": readiness.get("probe_storage", False),
        "probe_generation_backend": readiness.get(
            "probe_generation_backend",
            False,
        ),
        "commercial_ready": readiness.get("commercial_ready", False),
        "blockers": readiness.get("blockers", []),
        "readiness": readiness,
        "env_summary": _build_env_summary(settings),
        "next_steps": _build_next_steps(readiness),
    }
    return (0 if payload["commercial_ready"] else 2), payload


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Production preflight for commercial deployment")
    parser.add_argument("--probe-storage", action="store_true", help="Run a real storage upload/delete probe")
    parser.add_argument(
        "--probe-generation-backend",
        action="store_true",
        help="Run the website-backend generation capability probe",
    )
    parser.add_argument("--non-strict", action="store_true", help="Run in non-strict mode")
    parser.add_argument("--write-artifacts", action="store_true", help="Write JSON and Markdown reports to backend/artifacts")
    parser.add_argument("--json-out", default=None, help="Optional JSON output path")
    parser.add_argument("--markdown-out", default=None, help="Optional Markdown output path")
    args = parser.parse_args()

    repo_root = _bootstrap_path()
    exit_code, payload = asyncio.run(_run(args))

    console_output = _render_console(payload)
    print(console_output)

    json_output = json.dumps(payload, ensure_ascii=False, indent=2)
    markdown_output = _render_markdown(payload)

    if args.write_artifacts:
        artifacts_dir = repo_root / "backend" / "artifacts"
        _write_text(artifacts_dir / "production_preflight.json", json_output)
        _write_text(artifacts_dir / "production_preflight.md", markdown_output)

    if args.json_out:
        _write_text(Path(args.json_out), json_output)
    if args.markdown_out:
        _write_text(Path(args.markdown_out), markdown_output)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
