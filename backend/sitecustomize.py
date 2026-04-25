from __future__ import annotations

import os
import sys


def _add_vendor_to_syspath() -> None:
    backend_dir = os.path.dirname(__file__)
    app_dir = os.path.join(backend_dir, "app")
    state_path = os.path.join(app_dir, "_vendor_path.txt")

    candidates: list[str] = []
    if os.path.isfile(state_path):
        try:
            rel = open(state_path, "r", encoding="utf-8").read().strip()
            if rel:
                candidates.append(os.path.abspath(os.path.join(app_dir, rel.replace("/", os.sep))))
        except OSError:
            pass

    candidates.append(os.path.join(app_dir, "_vendor"))

    for path in candidates:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
            break


_add_vendor_to_syspath()

