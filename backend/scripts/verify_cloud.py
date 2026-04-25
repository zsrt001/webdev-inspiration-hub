"""Compatibility wrapper for the old cloud verification script."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


TARGET = Path(__file__).resolve().with_name("preflight_production.py")


def main() -> None:
    forwarded_args = sys.argv[1:] or ["--probe-storage", "--write-artifacts"]
    sys.argv = [str(TARGET), *forwarded_args]
    runpy.run_path(str(TARGET), run_name="__main__")


if __name__ == "__main__":
    main()
