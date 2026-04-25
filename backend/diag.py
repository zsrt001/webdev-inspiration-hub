"""
Compatibility entrypoint for legacy backend diagnostics.

Use the commercial readiness script as the current diagnostic baseline.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


TARGET = Path(__file__).resolve().parent / "scripts" / "check_commercial_readiness.py"


def main() -> None:
    sys.argv[0] = str(TARGET)
    runpy.run_path(str(TARGET), run_name="__main__")


if __name__ == "__main__":
    main()
