"""Route introspection that follows FastAPI's effective include graph."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def effective_routes(router: Any) -> Iterable[Any]:
    """Yield concrete routes from direct and lazily included routers."""

    for route in router.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            yield from contexts()
        elif hasattr(route, "path"):
            yield route


def effective_paths(router: Any) -> set[str]:
    return {route.path for route in effective_routes(router)}


def effective_operations(router: Any) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in effective_routes(router)
        for method in (getattr(route, "methods", None) or set())
    }
