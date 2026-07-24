"""Order route contract tests."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import HTTPException
from starlette.requests import Request


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.main import app  # noqa: E402
from app.routers import orders as order_routes  # noqa: E402
from app.schemas.order import TrialUnlockRequest  # noqa: E402
from app.services.idempotency_service import IdempotencyConflict  # noqa: E402
from app.services.private_download_service import (  # noqa: E402
    PrivateDownloadError,
    PrivateDownloadResult,
)
from tests.route_contract import effective_paths  # noqa: E402


class OrderRoutesTest(unittest.TestCase):
    def test_order_list_supports_slash_and_no_slash_paths(self) -> None:
        paths = effective_paths(app)

        self.assertIn("/api/v1/orders", paths)
        self.assertIn("/api/v1/orders/", paths)


class PrivateOrderRoutesTest(unittest.IsolatedAsyncioTestCase):
    async def test_trial_unlock_maps_idempotency_conflict_to_http_409(self) -> None:
        order_id = uuid.uuid4()
        root_transaction_id = uuid.uuid4()
        current_user = SimpleNamespace(id=uuid.uuid4())
        request = Request({"type": "http", "method": "POST", "path": "/"})

        with (
            patch.object(order_routes, "require_request_capability", AsyncMock()),
            patch.object(
                order_routes,
                "unlock_trial_order",
                AsyncMock(side_effect=IdempotencyConflict("idempotency_payload_mismatch")),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await order_routes.unlock_trial_order_route(
                    str(order_id),
                    TrialUnlockRequest(root_transaction_id=root_transaction_id),
                    request,
                    "unlock-once",
                    current_user,
                    AsyncMock(),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail,
            {
                "code": "idempotency_payload_mismatch",
                "message": "Idempotency conflict.",
            },
        )

    async def test_private_download_response_is_non_cacheable_and_discloses_no_object_key(self) -> None:
        order_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        current_user = SimpleNamespace(id=uuid.uuid4())
        request = Request({"type": "http", "method": "GET", "path": "/"})
        download = PrivateDownloadResult(
            asset_id=asset_id,
            content=b"private-jpeg",
            mime_type="image/jpeg",
            filename=f"vowpic-final-master-{asset_id}.jpg",
        )

        with (
            patch.object(order_routes, "require_request_capability", AsyncMock()),
            patch.object(
                order_routes,
                "resolve_private_download",
                AsyncMock(return_value=download),
            ),
        ):
            response = await order_routes.download_order_asset(
                str(order_id),
                str(asset_id),
                request,
                current_user,
                AsyncMock(),
            )

        self.assertEqual(response.body, b"private-jpeg")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(
            response.headers["content-disposition"],
            f'inline; filename="vowpic-final-master-{asset_id}.jpg"',
        )
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("object_key", str(response.headers).lower())
        self.assertNotIn("http", str(response.headers).lower())

    async def test_cross_user_funding_denial_keeps_not_found_disclosure(self) -> None:
        current_user = SimpleNamespace(id=uuid.uuid4())
        with patch.object(
            order_routes,
            "read_order_funding",
            AsyncMock(
                side_effect=PrivateDownloadError("order_not_found", status_code=404)
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await order_routes.get_order_funding(
                    str(uuid.uuid4()),
                    current_user,
                    AsyncMock(),
                )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(
            raised.exception.detail,
            {
                "code": "order_not_found",
                "message": "Private order access was denied.",
            },
        )


if __name__ == "__main__":
    unittest.main()
