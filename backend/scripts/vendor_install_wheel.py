from __future__ import annotations

import argparse
import os
import shutil
import zipfile


def _safe_rmtree(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a wheel by extracting into a target directory (vendoring).")
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()

    wheel_path = os.path.abspath(args.wheel)
    target_dir = os.path.abspath(args.target)

    if not os.path.isfile(wheel_path):
        raise FileNotFoundError(f"Wheel not found: {wheel_path}")
    os.makedirs(target_dir, exist_ok=True)

    wheel_lower = os.path.basename(wheel_path).lower()
    if wheel_lower.startswith("pillow-"):
        # Best-effort cleanup for Pillow.
        _safe_rmtree(os.path.join(target_dir, "PIL"))
        for name in os.listdir(target_dir):
            lower = name.lower()
            if lower.startswith("pillow-") and lower.endswith(".dist-info"):
                _safe_rmtree(os.path.join(target_dir, name))
            if lower.startswith("pillow-") and lower.endswith(".data"):
                _safe_rmtree(os.path.join(target_dir, name))
            if lower in ("pillow.libs",):
                _safe_rmtree(os.path.join(target_dir, name))

    with zipfile.ZipFile(wheel_path) as zf:
        zf.extractall(target_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
