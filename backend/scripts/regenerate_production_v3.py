"""
Compatibility shim for a legacy production asset regeneration script.

Use `backend/generate_assets.py` as the authoritative asset generation entrypoint.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


TARGET = Path(__file__).resolve().parents[1] / "generate_assets.py"


def main() -> None:
    sys.argv[0] = str(TARGET)
    runpy.run_path(str(TARGET), run_name="__main__")


if __name__ == "__main__":
    main()
