"""
Compatibility entrypoint for the historical backend root app.

The authoritative backend application is now `backend/app/main.py`.
This module re-exports that app so older commands that still target
`backend/main.py` do not boot a stale historical service.
"""

from __future__ import annotations

import uvicorn

from app.main import app


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
