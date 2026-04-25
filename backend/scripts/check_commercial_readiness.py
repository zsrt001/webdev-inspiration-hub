"""Run commercial readiness checks without starting FastAPI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    backend_dir = repo_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


async def _run(probe_storage: bool, probe_generation_queue: bool, strict: bool) -> int:
    from app.core.runtime_checks import run_readiness_checks

    report = await run_readiness_checks(
        probe_storage=probe_storage,
        probe_generation_queue=probe_generation_queue,
        strict_mode=strict,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("commercial_ready", False) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Commercial readiness checker")
    parser.add_argument("--probe-storage", action="store_true", help="Run storage upload/delete probe")
    parser.add_argument("--probe-generation-queue", action="store_true", help="Run a real generation queue probe")
    parser.add_argument(
        "--non-strict",
        action="store_true",
        help="Run in non-strict mode (does not enforce production config constraints)",
    )
    args = parser.parse_args()

    _bootstrap_path()
    return asyncio.run(
        _run(
            probe_storage=bool(args.probe_storage),
            probe_generation_queue=bool(args.probe_generation_queue),
            strict=not args.non_strict,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
