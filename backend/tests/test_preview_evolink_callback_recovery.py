"""Real-submit callback recovery verifier contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
import unittest
from unittest.mock import patch
from uuid import UUID

import httpx

from backend.scripts.verify_preview_evolink_callback_recovery import (
    _is_fresh_prepared_graph,
    _prepare_unknown,
    _record_definitive_rejection,
    verify_callback_recovery,
)


class PreviewEvolinkCallbackRecoveryTest(unittest.TestCase):
    def test_lost_response_probe_uses_only_legal_generation_transitions(self) -> None:
        source = inspect.getsource(_prepare_unknown)
        transitions = (
            "SET status = 'ACTIVE'",
            "SET status = 'SUBMITTING'",
            "SET status = 'GENERATING'",
            "SET status = 'UNKNOWN'",
            "SET status = 'RECONCILING'",
            "SET status = 'UNKNOWN_EXTERNAL_STATE'",
        )
        positions = [source.index(transition) for transition in transitions]

        self.assertEqual(positions, sorted(positions))
        self.assertIn("WHERE id = %s AND status = 'SUBMITTING'", source)
        self.assertIn("WHERE id = %s AND status = 'ACTIVE'", source)
        self.assertIn("WHERE generation_job_id = %s AND status = 'GENERATING'", source)
        self.assertNotIn("SET status = 'UNKNOWN',\n                    submit_started_at", source)

    def test_definitive_rejection_uses_only_legal_terminal_transitions(self) -> None:
        source = inspect.getsource(_record_definitive_rejection)
        transitions = (
            "SET status = 'FAILED', finished_at = CURRENT_TIMESTAMP",
            "SET status = 'FAILED', finished_at = CURRENT_TIMESTAMP",
            "SET status = 'FAILED', error_message = %s",
        )
        positions: list[int] = []
        offset = 0
        for transition in transitions:
            position = source.index(transition, offset)
            positions.append(position)
            offset = position + len(transition)

        self.assertEqual(positions, sorted(positions))
        self.assertIn("WHERE id = %s AND status = 'UNKNOWN'", source)
        self.assertIn("WHERE id = %s AND status = 'RECONCILING'", source)
        self.assertIn("status = 'UNKNOWN_EXTERNAL_STATE'", source)
        self.assertIn("provider_job_id IS NULL", source)

    def test_fresh_graph_accepts_psycopg_uuid_text_or_registered_uuid(self) -> None:
        reference = {
            "attempt_id": "00000000-0000-4000-8000-000000000076",
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
        }
        row = {
            "attempt_status": "PREPARED",
            "provider_job_id": None,
            "job_status": "QUEUED",
            "active_attempt_id": reference["attempt_id"],
            "runtime_bundle_id": reference["runtime_bundle_id"],
            "api_deployment_id": reference["api_deployment_id"],
            "order_status": "QUEUED",
        }

        self.assertTrue(_is_fresh_prepared_graph(row, reference))
        self.assertTrue(
            _is_fresh_prepared_graph(
                {**row, "active_attempt_id": UUID(reference["attempt_id"])},
                reference,
            )
        )
        self.assertFalse(_is_fresh_prepared_graph({**row, "job_status": "ACTIVE"}, reference))

    def test_one_submit_is_bound_only_by_callback(self) -> None:
        source = "a" * 40
        grant = {
            "schema": "vowpic.provider-grant-reference.v1",
            "activation_id": "00000000-0000-4000-8000-000000000071",
            "case_id": "00000000-0000-4000-8000-000000000072",
            "source_sha": source,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "backend_executor_digest": "sha256:" + "c" * 64,
            "grant_id": "00000000-0000-4000-8000-000000000073",
            "asset_id": "00000000-0000-4000-8000-000000000074",
            "job_id": "00000000-0000-4000-8000-000000000075",
            "attempt_id": "00000000-0000-4000-8000-000000000076",
            "read_url": (
                "https://vowpic-provider-aaaaaaaaaaaa-123-1.vercel.app/"
                "api/v1/media/grants/" + "s" * 43
            ),
        }
        capabilities = {
            "schema": "vowpic.provider-capabilities.v1",
            "providers": {
                "evolink": {
                    "submit_endpoint": "/v1/images/generations",
                    "task_query_endpoint": "/v1/tasks/{task_id}",
                    "credit_endpoint": "/v1/credits",
                    "acceptance_minimum_credits": "10",
                    "ambiguous_submission_policy": "hold_without_resubmit",
                    "official_sources": [
                        "https://docs.evolink.ai/en/api-manual/image-series/nanobanana/nanobanana-pro-image-generate",
                        "https://docs.evolink.ai/en/api-manual/task-management/get-task-detail",
                    ],
                    "implemented_by": [
                        "backend/app/services/evolink_service.py",
                        "backend/app/services/generation_attempt_service.py",
                        "backend/app/services/evolink_reconciliation_service.py",
                    ],
                },
                "creem": {
                    "cancel_endpoint": "/v1/subscriptions/{subscription_id}/cancel",
                    "refund_confirmation": "refund.created_webhook",
                    "subscription_events": [
                        "subscription.paid",
                        "subscription.scheduled_cancel",
                    ],
                    "official_sources": [
                        "https://docs.creem.io/code/webhooks",
                        "https://docs.creem.io/api-reference/endpoint/cancel-subscription",
                        "https://docs.creem.io/features/subscriptions/refunds-and-cancellations",
                    ],
                    "implemented_by": [
                        "backend/app/services/creem_event_service.py",
                        "backend/app/services/payment_service.py",
                        "backend/app/services/subscription_service.py",
                    ],
                },
            },
        }
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={
                        "id": "task-unified-1756817821-test",
                        "status": "pending",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "task-unified-1756817821-test",
                    "status": "completed",
                    "results": ["https://results.example.com/result.png"],
                },
            )

        prepared: list[bool] = []
        usage_rows = iter(
            [
                {
                    "provider": "evolink",
                    "purpose": "generation-input",
                    "runtime_bundle_id": grant["runtime_bundle_id"],
                    "target_api_deployment_id": grant["api_deployment_id"],
                    "used_count": 0,
                    "last_used_at": None,
                },
                {
                    "provider": "evolink",
                    "purpose": "generation-input",
                    "runtime_bundle_id": grant["runtime_bundle_id"],
                    "target_api_deployment_id": grant["api_deployment_id"],
                    "used_count": 1,
                    "last_used_at": "2026-07-23T00:00:01+00:00",
                },
            ]
        )
        binding_rows = iter(
            [
                None,
                {
                    "provider_job_id": "task-unified-1756817821-test",
                    "attempt_status": "SUBMITTED",
                    "submitted_at": "2026-07-23T00:00:02+00:00",
                    "job_status": "RECONCILING",
                    "job_lease_owner": "callback-worker",
                },
                {
                    "provider_job_id": "task-unified-1756817821-test",
                    "attempt_status": "FINISHED",
                    "submitted_at": "2026-07-23T00:00:02+00:00",
                    "job_status": "FINISHED",
                    "job_lease_owner": None,
                },
            ]
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = verify_callback_recovery(
                capability_document=capabilities,
                grant_reference=grant,
                expected_source_sha=source,
                api_key="provider-key",
                api_base_url="https://api.evolink.ai",
                image_model="gemini-3.1-flash-image-preview",
                image_quality="2K",
                image_size="3:4",
                callback_origin=(
                    "https://vowpic-evolink-aaaaaaaaaaaa-123-1.vercel.app"
                ),
                callback_secret="callback-test-secret-" + "x" * 32,
                approval_ref="approval-123",
                signing_key=b"provider-fetch-signing-key-32bytes",
                client=client,
                prepare_unknown=lambda: prepared.append(True),
                record_rejection=lambda _status_code: self.fail(
                    "successful submission must not record a rejection"
                ),
                usage_probe=lambda _grant_id: next(usage_rows),
                binding_probe=lambda: next(binding_rows),
                now=datetime(2026, 7, 23, 0, 0, 2, tzinfo=timezone.utc),
                poll_interval_seconds=0,
            )

        self.assertEqual(prepared, [True])
        self.assertEqual([request.method for request in requests], ["POST", "GET"])
        submitted = json.loads(requests[0].content)
        self.assertIn("callback_url", submitted)
        self.assertEqual(submitted["quality"], "2K")
        self.assertEqual(submitted["size"], "3:4")
        self.assertEqual(report["network_submit_count"], 1)
        self.assertEqual(report["submitter_provider_task_write_count"], 0)
        self.assertEqual(
            report["callback_recovery"],
            "BOUND_FROM_PROVIDER_CALLBACK",
        )
        self.assertEqual(report["callback_attempt_status"], "FINISHED")
        self.assertEqual(report["callback_job_status"], "FINISHED")
        self.assertNotIn("s" * 43, json.dumps(report, sort_keys=True))

    def test_http_402_is_terminalized_but_ambiguous_500_is_not(self) -> None:
        source = "a" * 40
        grant = {
            "attempt_id": "00000000-0000-4000-8000-000000000076",
            "grant_id": "00000000-0000-4000-8000-000000000073",
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "read_url": (
                "https://vowpic-provider-aaaaaaaaaaaa-123-1.vercel.app/"
                "api/v1/media/grants/" + "s" * 43
            ),
        }
        for status_code, expected_rejections in ((402, [402]), (500, [])):
            with self.subTest(status_code=status_code):
                prepared: list[bool] = []
                rejections: list[int] = []

                def handler(_request: httpx.Request) -> httpx.Response:
                    return httpx.Response(status_code, json={"error": "redacted"})

                with (
                    patch(
                        "backend.scripts.verify_preview_evolink_callback_recovery."
                        "validate_provider_capabilities"
                    ),
                    patch(
                        "backend.scripts.verify_preview_evolink_callback_recovery."
                        "_validate_grant_reference"
                    ),
                    patch(
                        "backend.scripts.verify_preview_evolink_callback_recovery."
                        "_validate_initial_usage"
                    ),
                    httpx.Client(transport=httpx.MockTransport(handler)) as client,
                    self.assertRaisesRegex(ValueError, f"HTTP {status_code}"),
                ):
                    verify_callback_recovery(
                        capability_document={},
                        grant_reference=grant,
                        expected_source_sha=source,
                        api_key="provider-key",
                        api_base_url="https://api.evolink.ai",
                        image_model="gemini-3.1-flash-image-preview",
                        image_quality="2K",
                        image_size="3:4",
                        callback_origin=(
                            "https://vowpic-evolink-aaaaaaaaaaaa-123-1.vercel.app"
                        ),
                        callback_secret="callback-test-secret-" + "x" * 32,
                        approval_ref="approval-123",
                        signing_key=b"provider-fetch-signing-key-32bytes",
                        client=client,
                        prepare_unknown=lambda: prepared.append(True),
                        record_rejection=rejections.append,
                        usage_probe=lambda _grant_id: {},
                        binding_probe=lambda: None,
                        now=datetime(2026, 7, 23, 0, 0, 2, tzinfo=timezone.utc),
                        poll_interval_seconds=0,
                    )

                self.assertEqual(prepared, [True])
                self.assertEqual(rejections, expected_rejections)

    def test_callback_must_bind_the_same_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "different Provider task"):
            from backend.scripts.verify_preview_evolink_callback_recovery import (
                _wait_for_callback_binding,
            )

            _wait_for_callback_binding(
                probe=lambda: {
                    "provider_job_id": "task-other",
                    "attempt_status": "SUBMITTED",
                    "submitted_at": "2026-07-23T00:00:00+00:00",
                    "job_status": "RECONCILING",
                    "job_lease_owner": None,
                },
                task_id="task-expected",
                timeout_seconds=0,
                poll_interval_seconds=0,
            )


if __name__ == "__main__":
    unittest.main()
