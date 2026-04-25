"""
Style cover generator for template previews.

Default behavior (safe/no API usage):
- Build a prompt manifest from current templates
- Write manifest to `backend/artifacts/style_cover_manifest.json`

Optional behavior:
- Generate images via OpenAI Images API and write to `frontend/static`
- Generate images via Jiekou.ai Gemini image API and write to `frontend/static`
- Generate images via Evolink Gemini image API and write to `frontend/static`

Examples:
  python generate_assets.py
  python generate_assets.py --provider openai --model gpt-image-1
  python generate_assets.py --provider jiekou --only couple_chn_xiuhe solo_korean_minimal
  python generate_assets.py --provider evolink --size 4:5 --only royal_castle
  python generate_assets.py --provider openai --only cyberpunk_city classic_bw
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import os
from pathlib import Path
from typing import Dict, List, TypedDict
from datetime import datetime, timezone

import httpx


class CoverJob(TypedDict):
    filename: str
    template_id: str
    title: str
    prompt: str


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_STATIC_DIR = ROOT_DIR.parent / "frontend" / "static"
MANIFEST_PATH = ROOT_DIR / "artifacts" / "style_cover_manifest.json"
TEMPLATE_SERVICE_FILE = ROOT_DIR / "app" / "services" / "template_service.py"


def build_cover_prompt(template_id: str, title: str, clothing_prompt: str, scene_prompt: str) -> str:
    quality_guard = (
        "high-end wedding editorial style, photorealistic, premium texture, natural skin detail, "
        "no text overlay, no watermark, no logo, no collage, centered portrait composition, "
        "clean depth of field, realistic lighting"
    )
    prompt = f"{title}. {clothing_prompt}. Scene: {scene_prompt}. {quality_guard}."

    if template_id == "classic_bw" or "black and white" in clothing_prompt.lower():
        prompt += " Strict black-and-white monochrome grayscale, subtle film grain, high contrast."
    if template_id.startswith("golden_"):
        prompt += " Elderly couple, respectful expression, authentic age texture, avoid beauty smoothing."

    return prompt


def _kw_str(call: ast.Call, key: str) -> str:
    for kw in call.keywords:
        if kw.arg != key:
            continue
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
        return ""
    return ""


def load_templates_from_source(file_path: Path) -> List[Dict[str, str]]:
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    templates: List[Dict[str, str]] = []

    for node in tree.body:
        list_node: ast.List | None = None

        if isinstance(node, ast.Assign):
            target_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "_templates" in target_names and isinstance(node.value, ast.List):
                list_node = node.value

        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "_templates" and isinstance(node.value, ast.List):
                list_node = node.value

        if list_node is None:
            continue

        for element in list_node.elts:
            if not isinstance(element, ast.Call):
                continue
            if not isinstance(element.func, ast.Name) or element.func.id != "Template":
                continue
            template_id = _kw_str(element, "id")
            image_url = _kw_str(element, "image_url")
            if not template_id or not image_url:
                continue
            templates.append(
                {
                    "id": template_id,
                    "title": _kw_str(element, "title"),
                    "image_url": image_url,
                    "clothing_prompt": _kw_str(element, "clothing_prompt"),
                    "default_background_prompt": _kw_str(element, "default_background_prompt"),
                }
            )
        break

    return templates


def build_cover_jobs(only_template_ids: List[str] | None = None) -> List[CoverJob]:
    only_set = set(only_template_ids or [])
    jobs: List[CoverJob] = []

    for template in load_templates_from_source(TEMPLATE_SERVICE_FILE):
        if only_set and template["id"] not in only_set:
            continue

        filename = Path(template["image_url"] or "").name.strip()
        if not filename:
            continue

        prompt = build_cover_prompt(
            template["id"],
            template["title"],
            template.get("clothing_prompt", ""),
            template.get("default_background_prompt", ""),
        )

        jobs.append(
            CoverJob(
                filename=filename,
                template_id=template["id"],
                title=template["title"],
                prompt=prompt,
            )
        )

    return sorted(jobs, key=lambda item: item["filename"])


def save_manifest(jobs: List[CoverJob], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "job_count": len(jobs),
        "jobs": jobs,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _download_or_decode_image(data_item: dict, save_path: Path) -> None:
    if data_item.get("b64_json"):
        image_bytes = base64.b64decode(data_item["b64_json"])
        save_path.write_bytes(image_bytes)
        return

    image_url = data_item.get("url")
    if not image_url:
        raise RuntimeError("OpenAI image response missing both b64_json and url")

    response = httpx.get(image_url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    save_path.write_bytes(response.content)


def _extract_url_or_base64(payload: Any) -> tuple[str, str] | None:
    if isinstance(payload, dict):
        for key in ("url", "image_url", "result_url", "output_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return ("url", value)
        image_urls = payload.get("image_urls")
        if isinstance(image_urls, list):
            for item in image_urls:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return ("url", item)
        for key in ("b64_json", "base64", "image_base64", "output_base64"):
            value = payload.get(key)
            if isinstance(value, str) and len(value) > 100:
                return ("base64", value)
        for key in ("data", "result", "results", "images", "output"):
            if key in payload:
                found = _extract_url_or_base64(payload[key])
                if found:
                    return found
    if isinstance(payload, list):
        for item in payload:
            found = _extract_url_or_base64(item)
            if found:
                return found
    return None


def generate_with_jiekou(
    jobs: List[CoverJob],
    output_dir: Path,
    endpoint: str,
    size: str,
    aspect_ratio: str,
    output_format: str,
) -> None:
    api_key = os.getenv("JIEKOU_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("JIEKOU_API_KEY is not set.")

    output_dir.mkdir(parents=True, exist_ok=True)
    auth_header = api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"
    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
    }

    success = 0
    fail = 0
    normalized_size = size
    if size and "x" in size.lower():
        normalized_size = "1K"
    normalized_output_format = output_format
    if output_format.lower() in {"jpg", "jpeg"}:
        normalized_output_format = "image/jpeg"
    elif output_format.lower() == "png":
        normalized_output_format = "image/png"

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for job in jobs:
            filename = job["filename"]
            save_path = output_dir / filename
            print(f"[GEN] {filename}  <- {job['template_id']}")
            payload = {
                "size": normalized_size,
                "prompt": job["prompt"],
                "aspect_ratio": aspect_ratio,
                "output_format": normalized_output_format,
            }
            try:
                response = client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                result_payload = response.json()
                extracted = _extract_url_or_base64(result_payload)
                if not extracted:
                    raise RuntimeError("Jiekou response missing image URL/base64")
                mode, value = extracted
                if mode == "url":
                    image_resp = client.get(value)
                    image_resp.raise_for_status()
                    save_path.write_bytes(image_resp.content)
                else:
                    save_path.write_bytes(base64.b64decode(value))
                success += 1
            except Exception as exc:
                fail += 1
                detail = ""
                try:
                    if "response" in locals() and response is not None:
                        detail = str(response.text)[:500]
                except Exception:
                    detail = ""
                if detail:
                    print(f"[ERR] {filename}: {exc} | {detail}")
                else:
                    print(f"[ERR] {filename}: {exc}")

    print(f"[DONE] {success}/{len(jobs)} covers generated into {output_dir}")
    print(f"[SUMMARY_OK]={success}")
    print(f"[SUMMARY_FAIL]={fail}")


def _poll_evolink_task(
    client: httpx.Client,
    *,
    endpoint: str,
    task_id: str,
    auth_header: str,
    poll_interval_seconds: float = 4.0,
    max_polls: int = 30,
) -> dict:
    base = endpoint.rstrip("/")
    if "/images/generations" in base:
        task_url = base.replace("/images/generations", f"/tasks/{task_id}")
    else:
        task_url = f"{base}/tasks/{task_id}"

    headers = {"Authorization": auth_header}
    last_payload: dict | None = None
    for _ in range(max_polls):
        response = client.get(task_url, headers=headers)
        response.raise_for_status()
        payload = response.json()
        last_payload = payload
        status = str(payload.get("status") or "").lower()
        if status == "completed":
            return payload
        if status == "failed":
            raise RuntimeError(json.dumps(payload, ensure_ascii=False))
        import time
        time.sleep(poll_interval_seconds)

    raise RuntimeError(f"Evolink task timeout: {json.dumps(last_payload or {}, ensure_ascii=False)}")


def generate_with_evolink(
    jobs: List[CoverJob],
    output_dir: Path,
    *,
    endpoint: str,
    model: str,
    size: str,
) -> None:
    api_key = os.getenv("EVOLINK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("EVOLINK_API_KEY is not set.")

    output_dir.mkdir(parents=True, exist_ok=True)
    auth_header = api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"
    headers = {
        "Authorization": auth_header,
        "Content-Type": "application/json",
    }

    success = 0
    fail = 0

    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        for job in jobs:
            filename = job["filename"]
            save_path = output_dir / filename
            print(f"[GEN] {filename}  <- {job['template_id']}")
            payload = {
                "model": model,
                "prompt": job["prompt"],
                "size": size,
            }
            try:
                create_resp = client.post(endpoint, json=payload, headers=headers)
                create_resp.raise_for_status()
                task_payload = create_resp.json()
                task_id = str(task_payload.get("id") or "").strip()
                if not task_id:
                    raise RuntimeError(f"Evolink response missing task id: {json.dumps(task_payload, ensure_ascii=False)}")

                result_payload = _poll_evolink_task(
                    client,
                    endpoint=endpoint,
                    task_id=task_id,
                    auth_header=auth_header,
                )

                extracted = _extract_url_or_base64(result_payload)
                if not extracted:
                    raise RuntimeError("Evolink response missing image URL/base64")
                mode, value = extracted
                if mode == "url":
                    image_resp = client.get(value)
                    image_resp.raise_for_status()
                    save_path.write_bytes(image_resp.content)
                else:
                    save_path.write_bytes(base64.b64decode(value))
                success += 1
            except Exception as exc:
                fail += 1
                print(f"[ERR] {filename}: {exc}")

    print(f"[DONE] {success}/{len(jobs)} covers generated into {output_dir}")
    print(f"[SUMMARY_OK]={success}")
    print(f"[SUMMARY_FAIL]={fail}")


def generate_with_openai(jobs: List[CoverJob], output_dir: Path, model: str, size: str) -> None:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Missing dependency: openai. Install with `pip install openai`.") from exc

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    output_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=api_key)

    success = 0
    for job in jobs:
        filename = job["filename"]
        save_path = output_dir / filename
        print(f"[GEN] {filename}  <- {job['template_id']}")
        try:
            response = client.images.generate(
                model=model,
                prompt=job["prompt"],
                size=size,
                n=1,
            )
            data_item = response.data[0]
            _download_or_decode_image(data_item.model_dump() if hasattr(data_item, "model_dump") else data_item, save_path)
            success += 1
        except Exception as exc:
            print(f"[ERR] {filename}: {exc}")

    print(f"[DONE] {success}/{len(jobs)} covers generated into {output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate or export style cover jobs from current templates.")
    parser.add_argument(
        "--provider",
        choices=["none", "openai", "jiekou", "evolink"],
        default="none",
        help="Image generation provider. Default only exports manifest.",
    )
    parser.add_argument(
        "--model",
        default="gpt-image-1",
        help="OpenAI image model when --provider openai.",
    )
    parser.add_argument(
        "--size",
        default="1K",
        help="Image size for OpenAI/Jiekou generation.",
    )
    parser.add_argument(
        "--aspect-ratio",
        default="2:3",
        help="Jiekou image aspect ratio.",
    )
    parser.add_argument(
        "--output-format",
        default="image/jpeg",
        help="Jiekou output format.",
    )
    parser.add_argument(
        "--jiekou-endpoint",
        default="https://api.jiekou.ai/v3/gemini-3.1-flash-image-text-to-image",
        help="Jiekou image generation endpoint.",
    )
    parser.add_argument(
        "--evolink-endpoint",
        default="https://api.evolink.ai/v1/images/generations",
        help="Evolink image generation endpoint.",
    )
    parser.add_argument(
        "--evolink-model",
        default="gemini-3.1-flash-image-preview",
        help="Evolink image generation model.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(FRONTEND_STATIC_DIR),
        help="Directory for generated images.",
    )
    parser.add_argument(
        "--manifest-path",
        default=str(MANIFEST_PATH),
        help="Path to write manifest JSON.",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional template ids to include.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    jobs = build_cover_jobs(args.only)
    manifest_path = Path(args.manifest_path)
    output_dir = Path(args.output_dir)

    if not jobs:
        print("[WARN] No jobs generated from template list.")
        return

    save_manifest(jobs, manifest_path)
    print(f"[OK] Manifest saved: {manifest_path}")
    print(f"[OK] Jobs: {len(jobs)}")

    if args.provider == "openai":
        generate_with_openai(jobs, output_dir=output_dir, model=args.model, size=args.size)
    elif args.provider == "jiekou":
        generate_with_jiekou(
            jobs,
            output_dir=output_dir,
            endpoint=args.jiekou_endpoint,
            size=args.size,
            aspect_ratio=args.aspect_ratio,
            output_format=args.output_format,
        )
    elif args.provider == "evolink":
        generate_with_evolink(
            jobs,
            output_dir=output_dir,
            endpoint=args.evolink_endpoint,
            model=args.evolink_model,
            size=args.size,
        )
    else:
        print("[INFO] Provider=none, skip image generation.")


if __name__ == "__main__":
    main()
