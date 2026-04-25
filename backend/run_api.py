from __future__ import annotations

import argparse
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
        except OSError:
            pass
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

    # Ensure `import app` works even when invoked from repo root.
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    vendor_path = _get_vendor_path(backend_dir)
    if vendor_path:
        _prepend_pythonpath(vendor_path)
    _prepend_pythonpath(backend_dir)

    # Avoid writing .pyc in locked-down environments.
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    # Importing app triggers vendoring (see `backend/app/__init__.py`).
    import app  # noqa: F401
    import uvicorn

    parser = argparse.ArgumentParser(description="Run FastAPI server (vendored deps).")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8001")))
    parser.add_argument("--reload", action="store_true", default=os.environ.get("RELOAD", "1") == "1")
    args = parser.parse_args()

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
