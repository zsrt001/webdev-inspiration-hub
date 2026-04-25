"""Validate ComfyUI workflow JSON + node map alignment.

Usage:
  python backend/scripts/validate_comfyui_workflows.py

This is a fast, offline sanity check to prevent:
- "workflow empty" (missing/empty JSON)
- COMFYUI_NODE_MAP referring to missing node IDs / input keys
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = json.loads(text)
    except Exception as e:
        raise ValueError(f"Invalid JSON: {path} ({e})")
    if not isinstance(data, dict) or not data:
        raise ValueError(f"Workflow empty or not a dict: {path}")
    return data


def _load_node_map(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except Exception as e:
        raise ValueError(f"Invalid node map JSON: {e}")
    if not isinstance(data, dict):
        raise ValueError("Node map must be a JSON object")
    return data


def _validate_map(workflow: dict[str, Any], node_map: dict[str, Any], *, name: str) -> list[str]:
    errors: list[str] = []
    for key, mapping in node_map.items():
        if not isinstance(mapping, dict):
            errors.append(f"[{name}] key={key}: mapping is not an object")
            continue
        node_id = str(mapping.get("id") or "").strip()
        input_key = str(mapping.get("key") or "text").strip()
        if not node_id:
            errors.append(f"[{name}] key={key}: missing mapping.id")
            continue
        node = workflow.get(node_id)
        if node is None:
            errors.append(f"[{name}] key={key}: node id {node_id} not found in workflow")
            continue
        if not isinstance(node, dict):
            errors.append(f"[{name}] key={key}: node id {node_id} is not an object")
            continue
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict):
            errors.append(f"[{name}] key={key}: node id {node_id} has no inputs object")
            continue
        # We allow creating a new input key, but for safety warn if node has no such key and
        # the node is not a generic text/image node.
        if input_key not in node_inputs:
            class_type = str(node.get("class_type") or "")
            safe_to_set = class_type in {"CLIPTextEncode", "LoadImage", "ControlNetApplyAdvanced", "IPAdapterApply"}
            if not safe_to_set:
                errors.append(
                    f"[{name}] key={key}: node {node_id} ({class_type}) missing input '{input_key}'"
                )
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    workflows_dir = repo_root / "backend" / "app" / "workflows"

    base_path = workflows_dir / "comfyui_base.json"
    inpaint_path = workflows_dir / "comfyui_couple_inpaint.json"
    live_path = workflows_dir / "comfyui_live_portrait.json"

    # Prefer env vars, fallback to backend/app/core/config.py defaults via import.
    node_map_raw = os.getenv("COMFYUI_NODE_MAP")
    live_node_map_raw = os.getenv("COMFYUI_LIVE_PORTRAIT_NODE_MAP")
    if node_map_raw is None or live_node_map_raw is None:
        try:
            sys.path.insert(0, str(repo_root / "backend"))
            from app.core.config import get_settings  # type: ignore

            s = get_settings()
            node_map_raw = node_map_raw or s.comfyui_node_map
            live_node_map_raw = live_node_map_raw or s.comfyui_live_portrait_node_map
        except Exception:
            node_map_raw = node_map_raw or "{}"
            live_node_map_raw = live_node_map_raw or "{}"

    base = _load_json(base_path)
    inpaint = _load_json(inpaint_path)
    live = _load_json(live_path)

    node_map = _load_node_map(node_map_raw or "{}")
    live_node_map = _load_node_map(live_node_map_raw or "{}")

    errors: list[str] = []
    errors.extend(_validate_map(base, node_map, name="base"))
    errors.extend(_validate_map(inpaint, node_map, name="inpaint"))
    errors.extend(_validate_map(live, live_node_map, name="live_portrait"))

    if errors:
        print("ComfyUI workflow validation FAILED:\n")
        for e in errors:
            print("-", e)
        return 2

    print("ComfyUI workflow validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

