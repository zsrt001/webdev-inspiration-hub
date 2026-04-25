"""Run an end-to-end commercial regression against a running API."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def _default_image() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "backend" / "static" / "custom_free.jpg"


def _pick_template(templates_payload: dict[str, Any], requested: str | None) -> str:
    templates = templates_payload.get("templates")
    if not isinstance(templates, list) or not templates:
        raise RuntimeError("templates endpoint returned empty list")
    if requested:
        for item in templates:
            if isinstance(item, dict) and str(item.get("id")) == requested:
                return requested
        raise RuntimeError(f"template_id not found: {requested}")
    for item in templates:
        if isinstance(item, dict) and str(item.get("category")) == "single" and item.get("id"):
            return str(item["id"])
    first = templates[0]
    if not isinstance(first, dict) or not first.get("id"):
        raise RuntimeError("invalid template payload")
    return str(first["id"])


def _extract_urls(urls_obj: Any) -> list[str]:
    if isinstance(urls_obj, dict):
        return [str(v) for v in urls_obj.values() if v]
    if isinstance(urls_obj, list):
        return [str(v) for v in urls_obj if v]
    return []


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    expected: tuple[int, ...] = (200,),
    **kwargs,
) -> dict[str, Any]:
    resp = await client.request(method, url, **kwargs)
    if resp.status_code not in expected:
        detail = ""
        try:
            detail = json.dumps(resp.json(), ensure_ascii=False)
        except Exception:
            detail = resp.text
        raise RuntimeError(f"{method} {url} -> {resp.status_code}: {detail}")
    try:
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"{method} {url} returned non-JSON: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"{method} {url} returned non-object payload")
    return data


async def run(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    api_url = f"{base_url}/api/v1"
    image_path = Path(args.image).resolve()
    if not image_path.exists():
        raise RuntimeError(f"image file not found: {image_path}")

    timeout = float(args.timeout)
    poll_interval = max(1.0, float(args.poll_interval))
    headers: dict[str, str] = {}
    if args.admin_token:
        headers["X-Admin-Token"] = args.admin_token

    summary: dict[str, Any] = {
        "api_url": api_url,
        "image": str(image_path),
        "started_at": time.time(),
    }

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        readiness = await _request_json(
            client,
            "GET",
            f"{api_url}/ops/readiness",
            params={"probe_storage": "false", "strict": "false"},
        )
        summary["readiness"] = {
            "commercial_ready": readiness.get("commercial_ready"),
            "blockers": readiness.get("blockers"),
        }

        await _request_json(client, "POST", f"{api_url}/credits/add", params={"amount": args.credit_topup})
        balance = await _request_json(client, "GET", f"{api_url}/credits/balance")
        summary["balance_before"] = balance.get("balance")

        templates_payload = await _request_json(client, "GET", f"{api_url}/templates")
        template_id = _pick_template(templates_payload, args.template_id)
        summary["template_id"] = template_id

        with image_path.open("rb") as fh:
            upload_resp = await _request_json(
                client,
                "POST",
                f"{api_url}/upload",
                files={"file": (image_path.name, fh, "image/jpeg")},
            )
        image_url = str(upload_resp.get("url") or "")
        if not image_url:
            raise RuntimeError("upload returned empty url")
        summary["uploaded_image_url"] = image_url

        gate = await _request_json(client, "POST", f"{api_url}/gatekeeper/check", json={"image_url": image_url})
        summary["gatekeeper"] = {
            "passed": gate.get("passed"),
            "reasons": gate.get("reasons"),
        }
        if not gate.get("passed"):
            raise RuntimeError(f"gatekeeper rejected image: {gate.get('reasons')}")

        order_payload = {
            "template_id": template_id,
            "user_images": [image_url],
            "director_mode": True,
            "prompt_override": args.prompt_override,
        }
        order = await _request_json(client, "POST", f"{api_url}/orders", json=order_payload)
        order_id = str(order.get("id") or "")
        if not order_id:
            raise RuntimeError("create order returned empty id")
        summary["order_id"] = order_id

        deadline = time.time() + timeout
        while True:
            polled = await _request_json(client, "GET", f"{api_url}/orders/{order_id}")
            status = str(polled.get("status") or "")
            if status == "COMPLETED":
                order = polled
                break
            if status == "CREATED" and polled.get("error_message"):
                raise RuntimeError(f"order failed: {polled.get('error_message')}")
            if time.time() >= deadline:
                raise RuntimeError(f"order polling timeout, last status={status}")
            await asyncio.sleep(poll_interval)

        final_urls = _extract_urls(order.get("final_image_urls"))
        if not final_urls:
            raise RuntimeError("completed order has empty final_image_urls")
        summary["final_image_count"] = len(final_urls)
        summary["final_image_urls"] = final_urls

        first_image_url = final_urls[0]
        image_resp = await client.get(first_image_url)
        if image_resp.status_code != 200:
            raise RuntimeError(f"final image unreachable: {first_image_url}")

        if not args.skip_live_portrait:
            lp_req = {"image_url": first_image_url, "seconds": int(args.live_seconds)}
            lp_job = await _request_json(client, "POST", f"{api_url}/live_portrait/generate", json=lp_req)
            lp_job_id = str(lp_job.get("job_id") or "")
            if not lp_job_id:
                raise RuntimeError("live_portrait returned empty job_id")
            summary["live_portrait_job_id"] = lp_job_id

            lp_deadline = time.time() + timeout
            while True:
                lp = await _request_json(client, "GET", f"{api_url}/live_portrait/{lp_job_id}")
                lp_status = str(lp.get("status") or "")
                if lp_status == "COMPLETED":
                    video_url = str(lp.get("video_url") or "")
                    if not video_url:
                        raise RuntimeError("live_portrait completed without video_url")
                    summary["live_portrait_video_url"] = video_url
                    video_resp = await client.get(video_url)
                    if video_resp.status_code != 200:
                        raise RuntimeError(f"live_portrait video unreachable: {video_url}")
                    break
                if lp_status in {"FAILED", "CANCELLED"}:
                    raise RuntimeError(f"live_portrait failed: {lp.get('message')}")
                if time.time() >= lp_deadline:
                    raise RuntimeError(f"live_portrait timeout, last status={lp_status}")
                await asyncio.sleep(poll_interval)

        lead_payload = {
            "name": "E2E Regression",
            "phone": f"188{random.randint(10000000, 99999999)}",
            "city": "Shanghai",
            "notes": "commercial_e2e",
        }
        lead = await _request_json(client, "POST", f"{api_url}/leads/submit", json=lead_payload)
        summary["lead_id"] = lead.get("lead_id")

        await _request_json(
            client,
            "POST",
            f"{api_url}/analytics/click",
            json={"event_type": "commercial_e2e", "source_page": "script", "template_id": template_id},
        )
        summary["analytics_click_recorded"] = True

    summary["finished_at"] = time.time()
    summary["duration_sec"] = round(summary["finished_at"] - summary["started_at"], 2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run commercial E2E regression")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001", help="API base URL")
    parser.add_argument("--image", default=str(_default_image()), help="Local image path for upload")
    parser.add_argument("--template-id", default=None, help="Template id to force")
    parser.add_argument("--prompt-override", default="classic studio wedding portrait", help="Prompt override text")
    parser.add_argument("--timeout", type=float, default=420.0, help="Polling timeout in seconds")
    parser.add_argument("--poll-interval", type=float, default=3.0, help="Polling interval in seconds")
    parser.add_argument("--credit-topup", type=int, default=30, help="Credits to top up before run")
    parser.add_argument("--skip-live-portrait", action="store_true", help="Skip live portrait add-on flow")
    parser.add_argument("--live-seconds", type=int, default=5, help="Live portrait duration")
    parser.add_argument("--admin-token", default=None, help="Optional admin token")
    args = parser.parse_args()

    try:
        return asyncio.run(run(args))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
