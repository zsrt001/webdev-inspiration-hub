"""Vercel serverless entry point for FastAPI."""

import sys
from pathlib import Path

# Add backend to path for imports
backend_path = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from app.main import app

# Vercel expects handler function or ASGI app
# For Python runtime, we export the app directly
handler = app
