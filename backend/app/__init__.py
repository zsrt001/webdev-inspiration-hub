# AI Wedding Photo Backend

from __future__ import annotations

import logging
import os
import sys

# Offline-friendly vendoring: load deps from a local vendor dir without touching
# global site-packages. We prefer a versioned vendor directory selected by
# `backend/app/_vendor_path.txt`, and fall back to `backend/app/_vendor`.
_APP_DIR = os.path.dirname(__file__)
_VENDOR_STATE = os.path.join(_APP_DIR, "_vendor_path.txt")
_VENDOR_CANDIDATES: list[str] = []
_ENABLE_VENDORED_DEPS = sys.platform.startswith("win") and os.environ.get("APP_SKIP_VENDORED_DEPS", "0") != "1"

if _ENABLE_VENDORED_DEPS:
    if os.path.isfile(_VENDOR_STATE):
        try:
            rel = open(_VENDOR_STATE, "r", encoding="utf-8").read().strip()
            if rel:
                _VENDOR_CANDIDATES.append(os.path.abspath(os.path.join(_APP_DIR, rel.replace("/", os.sep))))
        except OSError as exc:
            logging.getLogger(__name__).debug(
                "Optional vendor state could not be read: %s", type(exc).__name__
            )

    _VENDOR_CANDIDATES.append(os.path.join(_APP_DIR, "_vendor"))

    for _cand in _VENDOR_CANDIDATES:
        if os.path.isdir(_cand) and _cand not in sys.path:
            sys.path.insert(0, _cand)
            break
