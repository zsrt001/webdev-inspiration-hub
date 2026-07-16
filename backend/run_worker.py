from __future__ import annotations

import logging
import os
import sys


def _get_vendor_path(backend_dir: str) -> str | None:
    app_dir = os.path.join(backend_dir, "app")
    state_path = os.path.join(app_dir, "_vendor_path.txt")
    if os.path.isfile(state_path):
        try:
            rel = open(state_path, "r", encoding="utf-8").read().strip()
            if rel:
                cand = os.path.abspath(os.path.join(app_dir, rel.replace("/", os.sep)))
                if os.path.isdir(cand):
                    return cand
        except OSError as exc:
            logging.getLogger(__name__).debug(
                "Optional vendor state could not be read: %s", type(exc).__name__
            )
    cand = os.path.join(app_dir, "_vendor")
    if os.path.isdir(cand):
        return cand
    return None


def _prepend_pythonpath(path: str) -> None:
    cur = os.environ.get("PYTHONPATH", "")
    parts = [p for p in cur.split(os.pathsep) if p]
    if path in parts:
        parts.remove(path)
    os.environ["PYTHONPATH"] = os.pathsep.join([path, *parts]) if parts else path


def main() -> int:
    backend_dir = os.path.dirname(os.path.abspath(__file__))

    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    vendor_path = _get_vendor_path(backend_dir)
    if vendor_path:
        _prepend_pythonpath(vendor_path)
    _prepend_pythonpath(backend_dir)

    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    import app  # noqa: F401
    from arq.cli import cli

    # Default to our worker settings if none provided.
    if len(sys.argv) == 1:
        sys.argv.append("app.worker.WorkerSettings")

    cli()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
