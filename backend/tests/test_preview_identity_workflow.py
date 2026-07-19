"""Protected Preview identity activation, origin lease, and cleanup contract."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

import httpx

from app.core.feature_flags import Capability


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    if not path.exists():
        raise AssertionError(f"required Preview identity helper is missing: {relative_path}")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PreviewIdentityWorkflowTest(unittest.TestCase):
    def test_runtime_contract_keeps_identity_worker_free_and_adds_commercial_role(self) -> None:
        contract_path = ROOT / "release" / "preview-runtime-contract.json"
        self.assertTrue(contract_path.exists())
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        self.assertEqual(contract["contract_version"], "preview-protected-runtime.v4")
        self.assertEqual(contract["release_role"], "PREVIEW_IDENTITY")
        self.assertEqual(contract["schema_revision"], "20260710_0020")
        self.assertEqual(
            contract["enabled_capabilities"],
            ["google_auth", "authenticated_upload"],
        )
        self.assertEqual(contract["session"], {
            "access_ttl_seconds": 900,
            "refresh_ttl_seconds": 2592000,
            "storage": "secure-cookie-only",
        })
        self.assertEqual(
            contract["welcome_grant"],
            {
                "identity_claims": 2,
                "ledger_roots": 2,
                "grant_lots": 2,
                "credits_per_identity": 2,
                "duplicate_login_delta": 0,
                "preserved_by_cleanup": True,
            },
        )
        self.assertEqual(
            contract["private_media"],
            {
                "upload_identities": 1,
                "isolation_identities": 2,
                "owner_read": "required",
                "cross_user_read": "denied",
                "deletion": "read-revoked-and-object-cleaned",
            },
        )
        identity_only = {key: value for key, value in contract.items() if key != "commercial_extension"}
        serialized_identity = json.dumps(identity_only, sort_keys=True).lower()
        self.assertNotIn("worker_image", serialized_identity)
        self.assertNotIn("provider_model", serialized_identity)
        commercial = contract["commercial_extension"]
        self.assertEqual(commercial["release_role"], "PREVIEW_COMMERCIAL")
        self.assertEqual(commercial["worker"]["image_identity"], "oci-digest-only")
        self.assertEqual(commercial["provider_grant_origin"], "exact-temporary-vercel-alias")
        self.assertEqual(
            commercial["provider_runtime_probe"],
            "authenticated GET /api/v1/version with an independent app secret",
        )
        self.assertIn("missing-or-invalid", commercial["provider_grant_other_routes"])
        self.assertEqual(commercial["cleanup"]["activation_terminal_phase"], "CLEANED")

    def test_origin_lease_adds_and_restores_one_exact_callback(self) -> None:
        module = _load(
            "configure_preview_auth_origin",
            "scripts/release/configure_preview_auth_origin.py",
        )
        activation = {
            "id": "00000000-0000-0000-0000-000000000007",
            "environment": "preview",
            "kind": "PREVIEW_IDENTITY",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "api_deployment_url": "https://vowpic-git-a.example.vercel.app",
            "api_role": "PREVIEW_IDENTITY",
            "workflow_run_id": "123",
            "workflow_attempt": 1,
            "phase": "COMPLETED",
        }
        state = module.build_origin_state(
            activation,
            project_ref="abcdefghijklmnopqrst",
            uri_allow_list="https://vowpic.com/pages/auth/callback",
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        self.assertEqual(
            state["callback_url"],
            "https://vowpic-git-a.example.vercel.app/pages/auth/callback",
        )
        self.assertEqual(len(state["original_sha256"]), 64)
        self.assertEqual(len(state["target_sha256"]), 64)
        self.assertNotEqual(state["original_sha256"], state["target_sha256"])
        self.assertEqual(
            module.decide_restore_action(state, state["target_uri_allow_list"]),
            "RESTORE",
        )
        self.assertEqual(
            module.decide_restore_action(state, state["original_uri_allow_list"]),
            "ALREADY_RESTORED",
        )
        with self.assertRaises(ValueError):
            module.decide_restore_action(
                state,
                state["target_uri_allow_list"] + ",https://concurrent.example/callback",
            )

    def test_origin_lease_rejects_wildcards_duplicates_and_untrusted_origins(self) -> None:
        module = _load(
            "configure_preview_auth_origin_invalid",
            "scripts/release/configure_preview_auth_origin.py",
        )
        for invalid in (
            "https://*.vercel.app/pages/auth/callback",
            "https://vowpic.example/**",
            "https://vowpic.example/path?next=*",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                module.parse_uri_allow_list(invalid)
        with self.assertRaises(ValueError):
            module.parse_uri_allow_list("https://a.example/callback, https://a.example/callback")
        for invalid_origin in (
            "http://preview.vercel.app",
            "https://preview.vercel.app/path",
            "https://user@preview.vercel.app",
            "https://preview.vercel.app?query=1",
        ):
            with self.subTest(invalid_origin=invalid_origin), self.assertRaises(ValueError):
                module.exact_https_origin(invalid_origin)

    def test_management_api_mutates_only_uri_allow_list_and_reads_back(self) -> None:
        module = _load(
            "configure_preview_auth_origin_http",
            "scripts/release/configure_preview_auth_origin.py",
        )
        reads = iter([
            "https://vowpic.com/pages/auth/callback",
            (
                "https://vowpic.com/pages/auth/callback,"
                "https://preview.vercel.app/pages/auth/callback"
            ),
        ])
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.headers.get("Authorization"), "Bearer management-token")
            if request.method == "GET":
                return httpx.Response(200, json={"uri_allow_list": next(reads)})
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(
                json.loads(request.content.decode("utf-8")),
                {
                    "uri_allow_list": (
                        "https://vowpic.com/pages/auth/callback,"
                        "https://preview.vercel.app/pages/auth/callback"
                    )
                },
            )
            return httpx.Response(200, json={})

        state = module.build_origin_state(
            {
                "id": "00000000-0000-0000-0000-000000000007",
                "environment": "preview",
                "kind": "PREVIEW_IDENTITY",
                "source_sha": "a" * 40,
                "runtime_bundle_id": "rtb_" + "b" * 64,
                "api_deployment_id": "dpl_preview",
                "api_deployment_url": "https://preview.vercel.app",
                "api_role": "PREVIEW_IDENTITY",
                "workflow_run_id": "123",
                "workflow_attempt": 1,
                "phase": "COMPLETED",
            },
            project_ref="abcdefghijklmnopqrst",
            uri_allow_list="https://vowpic.com/pages/auth/callback",
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = module.apply_origin_state(
                state,
                action="add",
                token="management-token",
                client=client,
            )
        self.assertEqual(result["state"], "ADDED")
        self.assertEqual([request.method for request in requests], ["GET", "PATCH", "GET"])

    def test_provider_grant_origin_is_exact_alias_only_and_runtime_bound(self) -> None:
        module = _load(
            "configure_preview_provider_grant_origin",
            "scripts/release/configure_preview_provider_grant_origin.py",
        )
        alias_host = module.build_alias_host(
            source_sha="a" * 40,
            workflow_run_id="123",
            workflow_attempt=2,
        )
        self.assertEqual(alias_host, "vowpic-provider-aaaaaaaaaaaa-123-2.vercel.app")
        activation = {
            "activation_id": "00000000-0000-0000-0000-000000000007",
            "environment": "preview",
            "kind": "PREVIEW_COMMERCIAL",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "api_deployment_url": "https://preview.vercel.app",
            "api_role": "PREVIEW_COMMERCIAL_API",
            "worker_deployment_id": "c" * 64,
            "worker_role": "PREVIEW_COMMERCIAL_WORKER",
            "worker_image_digest": "sha256:" + "d" * 64,
            "workflow_run_id": "123",
            "workflow_attempt": 2,
            "phase": "COMPLETED",
        }
        state = module.build_origin_state(activation, alias_host=alias_host)
        self.assertEqual(state["provider_grant_origin"], f"https://{alias_host}")
        self.assertEqual(state["previous_alias_state"], "ABSENT")
        self.assertEqual(
            module.alias_set_command(
                vercel_cli="vercel",
                state=state,
                token="masked-token",
            )[:4],
            ["vercel", "alias", "set", "https://preview.vercel.app"],
        )
        self.assertEqual(
            module.alias_remove_command(
                vercel_cli="vercel",
                state=state,
                token="masked-token",
            )[:3],
            ["vercel", "alias", "rm"],
        )

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, alias_host)
            self.assertEqual(request.headers["x-vowpic-provider-probe"], "p" * 32)
            return httpx.Response(
                200,
                json={
                    "schema": "vowpic.runtime-bundle-report.v1",
                    "source_sha": activation["source_sha"],
                    "runtime_bundle_id": activation["runtime_bundle_id"],
                    "deployment_id": activation["api_deployment_id"],
                    "release_role": activation["kind"],
                    "runtime_environment": "preview",
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = module.verify_alias_runtime(
                state,
                bypass_secret="",
                probe_secret="p" * 32,
                client=client,
            )
        self.assertEqual(report["api_deployment_id"], "dpl_preview")

        def absent_handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, "api.vercel.com")
            self.assertEqual(request.url.path, f"/v4/aliases/{alias_host}")
            self.assertEqual(request.headers["Authorization"], "Bearer masked-token")
            self.assertEqual(request.url.params["projectId"], "prj_test")
            self.assertEqual(request.url.params["teamId"], "team_test")
            return httpx.Response(404)

        with httpx.Client(
            transport=httpx.MockTransport(absent_handler)
        ) as client:
            self.assertTrue(
                module.verify_alias_absent(
                    state,
                    token="masked-token",
                    project_id="prj_test",
                    team_id="team_test",
                    client=client,
                )
            )
            removed = module.verify_alias_removed(
                state,
                token="masked-token",
                project_id="prj_test",
                team_id="team_test",
                client=client,
            )
        self.assertEqual(removed["state"], "REMOVED")
        same_binding = {
            "alias": alias_host,
            "deploymentId": "dpl_preview",
            "projectId": "prj_test",
        }
        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=same_binding))
        ) as client:
            with self.assertRaisesRegex(ValueError, "still resolves"):
                module.verify_alias_removed(
                    state,
                    token="masked-token",
                    project_id="prj_test",
                    team_id="team_test",
                    client=client,
                    attempts=2,
                    retry_delay_seconds=0,
                    sleep=lambda _seconds: None,
                )
        removal_reads = iter(
            [
                httpx.Response(200, json=same_binding),
                httpx.Response(404),
            ]
        )
        with httpx.Client(
            transport=httpx.MockTransport(lambda _request: next(removal_reads))
        ) as client:
            removed = module.verify_alias_removed(
                state,
                token="masked-token",
                project_id="prj_test",
                team_id="team_test",
                client=client,
                attempts=2,
                retry_delay_seconds=0,
                sleep=lambda _seconds: None,
            )
        self.assertEqual(removed["state"], "REMOVED")
        conflicting_binding = {**same_binding, "deploymentId": "dpl_other"}
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=conflicting_binding)
            )
        ) as client:
            with self.assertRaisesRegex(ValueError, "another deployment"):
                module.verify_alias_absent(
                    state,
                    token="masked-token",
                    project_id="prj_test",
                    team_id="team_test",
                    client=client,
                )

    def test_provider_fetch_verifier_blocks_unverified_before_http_and_redacts_token(self) -> None:
        module = _load(
            "verify_provider_grant_fetch",
            "scripts/release/verify_provider_grant_fetch.py",
        )
        contract = json.loads(
            (ROOT / "release/provider-contracts.json").read_text(encoding="utf-8")
        )
        source_sha = "a" * 40
        grant_reference = {
            "schema": "vowpic.provider-grant-reference.v1",
            "activation_id": "00000000-0000-0000-0000-000000000007",
            "case_id": "00000000-0000-0000-0000-000000000012",
            "source_sha": source_sha,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "worker_deployment_id": "c" * 64,
            "worker_image_digest": "sha256:" + "d" * 64,
            "grant_id": "00000000-0000-0000-0000-000000000008",
            "asset_id": "00000000-0000-0000-0000-000000000009",
            "job_id": "00000000-0000-0000-0000-000000000010",
            "attempt_id": "00000000-0000-0000-0000-000000000011",
            "read_url": (
                "https://vowpic-provider-aaaaaaaaaaaa-123-2.vercel.app/"
                "api/v1/media/grants/" + "s" * 43
            ),
        }
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"data": {"task_id": "task-123"}})
            return httpx.Response(
                200,
                json={
                    "id": "task-123",
                    "object": "image.generation.task",
                    "status": "completed",
                    "progress": 100,
                    "results": ["https://provider.example/result.png"],
                    "task_info": {"can_cancel": False},
                    "type": "image",
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "UNVERIFIED"):
                module.verify_provider_fetch(
                    contract_document=contract,
                    grant_reference=grant_reference,
                    expected_source_sha=source_sha,
                    api_key="provider-key",
                    api_base_url="https://api.evolink.ai",
                    image_model="gemini-3-pro-image-preview",
                    approval_ref="approval-123",
                    signing_key=b"provider-fetch-signing-key-32bytes",
                    client=client,
                    usage_probe=lambda _grant_id: {"used_count": 1},
                    now=datetime(2026, 7, 14, tzinfo=timezone.utc),
                )
        self.assertEqual(requests, [])

        verified = json.loads(json.dumps(contract))
        verified_entry = verified["contracts"]["EVOLINK_SUBMISSION_RECONCILIATION"]
        verified_entry.update(
            {
                "state": "VERIFIED",
                "tested_source_sha": "c" * 40,
                "official_contract_sha256": "e" * 64,
                "test_evidence_sha256": "f" * 64,
                "official_version": "2026-07-14",
                "approval_ref": "contract-approval",
                "correlation_semantics": "documented correlation semantics",
                "idempotency_semantics": "documented idempotency semantics",
            }
        )
        usage_reads = iter(
            [
                {
                    "provider": "evolink",
                    "purpose": "generation-input",
                    "runtime_bundle_id": "rtb_" + "b" * 64,
                    "target_api_deployment_id": "dpl_preview",
                    "used_count": 0,
                    "last_used_at": None,
                },
                {
                    "provider": "evolink",
                    "purpose": "generation-input",
                    "runtime_bundle_id": "rtb_" + "b" * 64,
                    "target_api_deployment_id": "dpl_preview",
                    "used_count": 1,
                    "last_used_at": "2026-07-14T00:00:01+00:00",
                },
            ]
        )
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = module.verify_provider_fetch(
                contract_document=verified,
                grant_reference=grant_reference,
                expected_source_sha=source_sha,
                contract_tested_source_sha="c" * 40,
                api_key="provider-key",
                api_base_url="https://api.evolink.ai",
                image_model="gemini-3-pro-image-preview",
                approval_ref="approval-123",
                signing_key=b"provider-fetch-signing-key-32bytes",
                client=client,
                usage_probe=lambda _grant_id: next(usage_reads),
                now=datetime(2026, 7, 14, 0, 0, 2, tzinfo=timezone.utc),
            )
        self.assertEqual(report["network_submit_count"], 1)
        self.assertEqual(report["provider_fetch_count"], 1)
        self.assertEqual(report["provider_task_terminal_status"], "completed")
        self.assertEqual([request.method for request in requests], ["POST", "GET"])
        self.assertNotIn("s" * 43, json.dumps(report, sort_keys=True))

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(ValueError, "unused"):
                module.verify_provider_fetch(
                    contract_document=verified,
                    grant_reference=grant_reference,
                    expected_source_sha=source_sha,
                    contract_tested_source_sha="c" * 40,
                    api_key="provider-key",
                    api_base_url="https://api.evolink.ai",
                    image_model="gemini-3-pro-image-preview",
                    approval_ref="approval-123",
                    signing_key=b"provider-fetch-signing-key-32bytes",
                    client=client,
                    usage_probe=lambda _grant_id: {
                        "provider": "evolink",
                        "purpose": "generation-input",
                        "runtime_bundle_id": "rtb_" + "b" * 64,
                        "target_api_deployment_id": "dpl_preview",
                        "used_count": 1,
                        "last_used_at": "2026-07-14T00:00:01+00:00",
                    },
                    now=datetime(2026, 7, 14, 0, 0, 2, tzinfo=timezone.utc),
                )

    def test_provider_grant_preparation_requires_real_runtime_bound_rows(self) -> None:
        module = _load(
            "prepare_preview_provider_grant",
            "scripts/release/prepare_preview_provider_grant.py",
        )
        payload = {
            "schema": "vowpic.preview-provider-input.v1",
            "activation_id": "00000000-0000-0000-0000-000000000007",
            "case_id": "00000000-0000-0000-0000-000000000012",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "worker_deployment_id": "c" * 64,
            "worker_image_digest": "sha256:" + "d" * 64,
            "job_id": "00000000-0000-0000-0000-000000000010",
            "attempt_id": "00000000-0000-0000-0000-000000000011",
            "asset_id": "00000000-0000-0000-0000-000000000009",
        }
        normalized = module.validate_input_reference(payload)
        self.assertEqual(normalized, payload)
        prefix = module.provider_case_prefix(normalized)
        self.assertEqual(
            prefix,
            "acceptance/provider-fetch/00000000-0000-0000-0000-000000000007/"
            "00000000-0000-0000-0000-000000000012/",
        )
        self.assertTrue(module.is_provider_case_object_key(normalized, prefix + "source.jpg"))
        self.assertFalse(module.is_provider_case_object_key(normalized, "users/real-user/source.jpg"))
        issued = SimpleNamespace(
            grant=SimpleNamespace(id=UUID("00000000-0000-0000-0000-000000000008")),
            read_url=(
                "https://vowpic-provider-aaaaaaaaaaaa-123-2.vercel.app/"
                "api/v1/media/grants/" + "s" * 43
            ),
        )
        reference = module.build_grant_reference(normalized, issued)
        self.assertEqual(reference["schema"], "vowpic.provider-grant-reference.v1")
        self.assertEqual(reference["grant_id"], str(issued.grant.id))
        self.assertEqual(reference["case_id"], payload["case_id"])
        with self.assertRaises(ValueError):
            module.validate_input_reference({**payload, "asset_id": "not-a-uuid"})

    def test_provider_case_is_deterministic_acceptance_owned_and_cancel_safe(self) -> None:
        prepare = _load(
            "prepare_preview_provider_case",
            "scripts/release/prepare_preview_provider_case.py",
        )
        cleanup = _load(
            "cleanup_preview_provider_case",
            "scripts/release/cleanup_preview_provider_case.py",
        )
        activation = {
            "activation_id": "00000000-0000-0000-0000-000000000007",
            "environment": "preview",
            "kind": "PREVIEW_COMMERCIAL",
            "phase": "COMPLETED",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "b" * 64,
            "api_deployment_id": "dpl_preview",
            "api_role": "PREVIEW_COMMERCIAL_API",
            "worker_deployment_id": "c" * 64,
            "worker_role": "PREVIEW_COMMERCIAL_WORKER",
            "worker_image_digest": "sha256:" + "d" * 64,
        }
        owner_id = UUID("00000000-0000-0000-0000-000000000020")
        ids = {
            "order_id": UUID("00000000-0000-0000-0000-000000000021"),
            "job_id": UUID("00000000-0000-0000-0000-000000000022"),
            "attempt_id": UUID("00000000-0000-0000-0000-000000000023"),
            "asset_id": UUID("00000000-0000-0000-0000-000000000024"),
        }
        reference = prepare.build_case_reference(
            activation,
            owner_user_id=owner_id,
            **ids,
        )
        self.assertEqual(
            reference["case_id"],
            str(prepare.case_id_for_activation(activation["activation_id"])),
        )
        object_key = prepare.provider_case_object_key(
            activation["activation_id"],
            reference["case_id"],
        )
        self.assertTrue(object_key.endswith("/source.jpg"))
        row = {
            **reference,
            "object_key": object_key,
            "owner_user_id": str(owner_id),
            "order_user_id": str(owner_id),
            "order_generation_job_id": str(ids["job_id"]),
            "order_source_asset_ids": [str(ids["asset_id"])],
            "order_status": "QUEUED",
            "order_price_cents": 0,
            "order_payment_id": None,
            "order_paid_at": None,
            "order_reservation_id": None,
            "job_status": "QUEUED",
            "job_active_attempt_id": str(ids["attempt_id"]),
            "job_lease_owner": None,
            "job_api_deployment_id": reference["api_deployment_id"],
            "job_runtime_bundle_id": reference["runtime_bundle_id"],
            "job_worker_image_digest": reference["worker_image_digest"],
            "attempt_status": "PREPARED",
            "attempt_provider": "evolink",
            "attempt_provider_job_id": None,
            "asset_status": "ACTIVE",
            "asset_read_revoked_at": None,
            "acceptance_binding_count": 1,
            "active_identity_count": 1,
        }
        cleanup.validate_case_row(reference, row)
        with self.assertRaisesRegex(ValueError, "source SHA"):
            prepare.build_case_reference(
                {**activation, "source_sha": "not-a-source-sha"},
                owner_user_id=owner_id,
                **ids,
            )
        with self.assertRaisesRegex(ValueError, "dedicated object prefix"):
            cleanup.validate_case_row(
                reference,
                {**row, "object_key": f"users/{owner_id}/uploads/source.jpg"},
            )
        with self.assertRaisesRegex(ValueError, "financial"):
            cleanup.validate_case_row(
                reference,
                {**row, "order_reservation_id": str(UUID(int=31))},
            )
        with self.assertRaisesRegex(ValueError, "runtime coordinates"):
            cleanup.validate_case_row(
                reference,
                {**row, "job_runtime_bundle_id": "rtb_" + "e" * 64},
            )

    def test_stage5_materialization_binds_pr_identity_commercial_and_joint_cleanup(self) -> None:
        module = _load(
            "materialize_stage5_evidence",
            "scripts/release/materialize_stage5_evidence.py",
        )
        source = "a" * 40
        gate_hash = "b" * 64
        pr_runtime = "rtb_" + "1" * 64
        identity_runtime = "rtb_" + "2" * 64
        commercial_runtime = "rtb_" + "3" * 64
        activation_id = "00000000-0000-0000-0000-000000000041"
        api_id = "dpl_commercial"
        worker_id = "4" * 64
        digest = "sha256:" + "5" * 64

        def evidence(case_id: str, runtime: str) -> dict[str, object]:
            return {
                "schema": "vowpic.gate-evidence.v1",
                "case_id": case_id,
                "status": "PASS",
                "source_sha": source,
                "runtime_bundle_id": runtime,
                "gate_contract_sha256": gate_hash,
                "produced_at": "2026-07-14T00:00:00+00:00",
                "execution": "completed",
                "test_count": 1,
                "capability_enabled": False,
            }

        pr_cases = tuple(f"pr_{index}" for index in range(2))
        identity_cases = (
            "preview_google_session",
            "preview_private_media_owner_read",
            "preview_private_media_cross_user",
            "preview_private_media_delete",
        )
        activation = {
            "activation_id": activation_id,
            "source_sha": source,
            "runtime_bundle_id": commercial_runtime,
            "api_deployment_id": api_id,
            "worker_deployment_id": worker_id,
            "worker_image_digest": digest,
        }
        result = module.build_stage5_materialization(
            source_sha=source,
            gate_contract_sha256=gate_hash,
            pr_evidence=[evidence(case_id, pr_runtime) for case_id in pr_cases],
            expected_pr_case_ids=set(pr_cases),
            identity_evidence=[evidence(case_id, identity_runtime) for case_id in identity_cases],
            identity_cleanup={"state": "CLEANED", "source_sha": source, "runtime_bundle_id": identity_runtime},
            activation=activation,
            provider_contract={
                "state": "VERIFIED",
                "tested_source_sha": source,
                "test_evidence_sha256": "6" * 64,
            },
            worker_heartbeat={"state": "RUNNING", "heartbeat": {**activation, "worker_image_digest": digest}},
            provider_fetch={
                "passed": True,
                **activation,
                "provider_contract_evidence_sha256": "6" * 64,
                "provider_task_terminal_status": "completed",
                "provider_fetch_count": 1,
            },
            provider_case_cleanup={"state": "CLEANED", "activation_id": activation_id},
            provider_origin_cleanup={"state": "REMOVED", "activation_id": activation_id},
            worker_cleanup={
                "state": "STOPPED",
                "source_sha": source,
                "runtime_bundle_id": commercial_runtime,
                "api_deployment_id": api_id,
                "worker_deployment_id": worker_id,
                "worker_image_digest": digest,
                "heartbeat_state": "ABSENT",
            },
            commercial_cleanup={
                "state": "CLEANED",
                "activation_id": activation_id,
                "source_sha": source,
                "runtime_bundle_id": commercial_runtime,
                "api_deployment_id": api_id,
                "worker_deployment_id": worker_id,
            },
            now=datetime(2026, 7, 14, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(set(result["runtime_bindings"]), {"pr", "preview_identity", "preview_commercial", "stage5_composite"})
        self.assertEqual(
            {row["case_id"] for row in result["commercial_evidence"]},
            {"preview_provider_sandbox_contract", "preview_worker_heartbeat", "preview_provider_fetch", "preview_cleanup"},
        )
        self.assertEqual(result["commercial_evidence"][-1]["runtime_bundle_id"], result["runtime_bindings"]["stage5_composite"])
        with self.assertRaisesRegex(ValueError, "Worker cleanup"):
            module.build_stage5_materialization(
                source_sha=source,
                gate_contract_sha256=gate_hash,
                pr_evidence=[evidence(case_id, pr_runtime) for case_id in pr_cases],
                expected_pr_case_ids=set(pr_cases),
                identity_evidence=[evidence(case_id, identity_runtime) for case_id in identity_cases],
                identity_cleanup={"state": "CLEANED", "source_sha": source, "runtime_bundle_id": identity_runtime},
                activation=activation,
                provider_contract={"state": "VERIFIED", "tested_source_sha": source, "test_evidence_sha256": "6" * 64},
                worker_heartbeat={"state": "RUNNING", "heartbeat": {**activation, "worker_image_digest": digest}},
                provider_fetch={"passed": True, **activation, "provider_contract_evidence_sha256": "6" * 64, "provider_task_terminal_status": "completed", "provider_fetch_count": 1},
                provider_case_cleanup={"state": "CLEANED", "activation_id": activation_id},
                provider_origin_cleanup={"state": "REMOVED", "activation_id": activation_id},
                worker_cleanup={"state": "STOPPED", "source_sha": source, "runtime_bundle_id": "rtb_" + "9" * 64, "api_deployment_id": api_id, "worker_deployment_id": worker_id, "worker_image_digest": digest, "heartbeat_state": "ABSENT"},
                commercial_cleanup={"state": "CLEANED", "activation_id": activation_id, "source_sha": source, "runtime_bundle_id": commercial_runtime, "api_deployment_id": api_id, "worker_deployment_id": worker_id},
                now=datetime(2026, 7, 14, 0, 1, tzinfo=timezone.utc),
            )

    def test_cleanup_removes_only_its_owned_callback_when_artifact_is_unavailable(self) -> None:
        module = _load(
            "configure_preview_auth_origin_cleanup",
            "scripts/release/configure_preview_auth_origin.py",
        )
        original = "https://vowpic.com/pages/auth/callback"
        callback = "https://preview.vercel.app/pages/auth/callback"
        concurrent = "https://admin.example.com/oauth/callback"
        target = f"{original},{callback}"
        reads = iter([f"{target},{concurrent}", f"{original},{concurrent}"])
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"uri_allow_list": next(reads)})
            self.assertEqual(request.method, "PATCH")
            self.assertEqual(
                json.loads(request.content.decode("utf-8")),
                {"uri_allow_list": f"{original},{concurrent}"},
            )
            return httpx.Response(200, json={})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = module.remove_owned_callback(
                project_ref="abcdefghijklmnopqrst",
                callback_url=callback,
                original_sha256=module.allow_list_sha256(original),
                target_sha256=module.allow_list_sha256(target),
                token="management-token",
                client=client,
            )
        self.assertEqual(result["state"], "OWNED_CALLBACK_REMOVED")
        self.assertTrue(result["concurrent_changes_preserved"])
        self.assertFalse(result["exact_original_restored"])
        self.assertEqual([request.method for request in requests], ["GET", "PATCH", "GET"])

    def test_activation_registration_is_exact_and_report_is_create_once(self) -> None:
        module = _load(
            "register_preview_activation",
            "scripts/release/register_preview_activation.py",
        )
        coordinates = module.validate_coordinates(
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "b" * 64,
            workflow_run_id="123",
            workflow_attempt=2,
        )
        self.assertEqual(coordinates["api_role"], "PREVIEW_IDENTITY")
        with self.assertRaises(ValueError):
            module.validate_coordinates(
                source_sha="main",
                runtime_bundle_id="rtb_" + "b" * 64,
                workflow_run_id="123",
                workflow_attempt=2,
            )
        source = (ROOT / "scripts/release/register_preview_activation.py").read_text(encoding="utf-8")
        self.assertIn('add_argument("--role"', source)
        self.assertIn('add_argument("--worker-build-report"', source)
        self.assertIn('add_argument("--worker-start-report"', source)
        self.assertIn("worker_deployment_id = %s", source)
        with self.assertRaises(ValueError):
            module.validate_coordinates(
                release_role="PREVIEW_IDENTITY",
                source_sha="a" * 40,
                runtime_bundle_id="rtb_" + "b" * 64,
                worker_image_digest="sha256:" + "c" * 64,
                workflow_run_id="123",
                workflow_attempt=2,
            )
        report = module.build_activation_report(
            activation_id="00000000-0000-0000-0000-000000000007",
            coordinates={
                **coordinates,
                "api_deployment_id": "dpl_preview",
                "api_deployment_url": "https://preview.vercel.app",
                "manifest_sha256": "c" * 64,
            },
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )
        self.assertTrue(report["passed"])
        self.assertEqual(report["phase"], "COMPLETED")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "activation-report.json"
            module.write_create_once_json(output, report)
            with self.assertRaises(FileExistsError):
                module.write_create_once_json(output, report)

    def test_preview_commercial_registration_requires_digest_pinned_worker(self) -> None:
        module = _load(
            "register_preview_commercial_activation",
            "scripts/release/register_preview_activation.py",
        )
        parameters = inspect.signature(module.validate_coordinates).parameters
        self.assertIn("release_role", parameters)
        self.assertIn("worker_image_digest", parameters)
        coordinates = module.validate_coordinates(
            release_role="PREVIEW_COMMERCIAL",
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "b" * 64,
            worker_image_digest="sha256:" + "c" * 64,
            workflow_run_id="123",
            workflow_attempt=2,
        )
        self.assertEqual(coordinates["kind"], "PREVIEW_COMMERCIAL")
        self.assertEqual(coordinates["api_role"], "PREVIEW_COMMERCIAL_API")
        self.assertEqual(coordinates["worker_role"], "PREVIEW_COMMERCIAL_WORKER")
        self.assertEqual(coordinates["schema_revision"], "20260710_0020")
        report_coordinates = {
            **coordinates,
            "api_deployment_id": "dpl_preview",
            "api_deployment_url": "https://preview.vercel.app",
            "manifest_sha256": "d" * 64,
        }
        with self.assertRaisesRegex(ValueError, "Worker deployment ID"):
            module.build_activation_report(
                activation_id="00000000-0000-0000-0000-000000000007",
                coordinates=report_coordinates,
            )
        report = module.build_activation_report(
            activation_id="00000000-0000-0000-0000-000000000007",
            coordinates={**report_coordinates, "worker_deployment_id": "worker-preview-123"},
        )
        self.assertEqual(report["worker_deployment_id"], "worker-preview-123")
        with tempfile.TemporaryDirectory() as directory:
            worker_report = Path(directory) / "worker-created.json"
            worker_report.write_text(
                json.dumps(
                    {
                        "schema": "vowpic.preview-worker-process.v1",
                        "state": "CREATED",
                        "source_sha": coordinates["source_sha"],
                        "runtime_bundle_id": coordinates["runtime_bundle_id"],
                        "api_deployment_id": "dpl_preview",
                        "worker_image_digest": coordinates["worker_image_digest"],
                        "container_id": "e" * 64,
                        "container_name": "vowpic-preview-123-2",
                        "observed_at": "2026-07-14T00:00:00+00:00",
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                module._worker_deployment_from_report(
                    str(worker_report),
                    role="PREVIEW_COMMERCIAL",
                    coordinates=coordinates,
                    api_deployment_id="dpl_preview",
                ),
                "e" * 64,
            )
        with self.assertRaises(ValueError):
            module.validate_coordinates(
                release_role="PREVIEW_COMMERCIAL",
                source_sha="a" * 40,
                runtime_bundle_id="rtb_" + "b" * 64,
                worker_image_digest="vowpic-worker:latest",
                workflow_run_id="123",
                workflow_attempt=2,
            )

    def test_preview_commercial_snapshot_requires_the_exact_all_off_set(self) -> None:
        module = _load(
            "snapshot_preview_commercial_flags",
            "scripts/release/snapshot_preview_commercial_flags.py",
        )
        rows = [
            {
                "capability": capability.value,
                "environment": "preview",
                "state": "OFF",
                "deployment_id": None,
                "runtime_bundle_id": None,
                "worker_image_digest": None,
                "release_activation_id": None,
                "target_manifest_sha256": None,
                "cohort_user_ids": [],
                "verified_identity_hashes": [],
                "expires_at": None,
                "version": 1,
            }
            for capability in sorted(Capability, key=lambda item: item.value)
        ]
        snapshot = module.build_all_off_snapshot(rows)
        self.assertRegex(snapshot["snapshot_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(snapshot["capability_count"], len(Capability))
        with self.assertRaisesRegex(ValueError, "all-OFF"):
            module.build_all_off_snapshot([{**rows[0], "state": "ON"}, *rows[1:]])
        with self.assertRaisesRegex(ValueError, "exact capability"):
            module.build_all_off_snapshot(rows[:-1])

    def test_activation_reservation_serializes_runs_and_never_reopens_a_cleaned_bundle(self) -> None:
        module = _load(
            "register_preview_activation_reservation",
            "scripts/release/register_preview_activation.py",
        )
        coordinates = module.validate_coordinates(
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "b" * 64,
            workflow_run_id="123",
            workflow_attempt=2,
        )
        active = [{**coordinates, "id": "00000000-0000-0000-0000-000000000007", "phase": "RESERVED", "version": 1}]
        decision = module.decide_reservation(active, [], [], coordinates)
        self.assertEqual(decision["state"], "ALREADY_RESERVED")

        foreign_active = [{
            **active[0],
            "source_sha": "c" * 40,
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "workflow_run_id": "122",
            "workflow_attempt": 1,
        }]
        with self.assertRaisesRegex(ValueError, "must be cleaned"):
            module.decide_reservation(foreign_active, [], [], coordinates)

        cleaned_same_attempt = [{**active[0], "phase": "CLEANED", "version": 2}]
        with self.assertRaisesRegex(ValueError, "cannot be reopened"):
            module.decide_reservation([], cleaned_same_attempt, [], coordinates)
        cleaned_previous_attempt = [{
            **cleaned_same_attempt[0],
            "workflow_attempt": 1,
        }]
        with self.assertRaisesRegex(ValueError, "runtime bundle cannot be reused"):
            module.decide_reservation([], [], cleaned_previous_attempt, coordinates)
        different_bundle = {
            **coordinates,
            "runtime_bundle_id": "rtb_" + "d" * 64,
            "workflow_attempt": 3,
        }
        self.assertIsNone(module.decide_reservation([], [], [], different_bundle))

    def test_cleanup_contract_revokes_unused_binding_and_preview_sessions(self) -> None:
        cleanup = _load(
            "cleanup_preview_identity_smoke",
            "scripts/release/cleanup_preview_identity_smoke.py",
        )
        self.assertIn("acceptance_identity_bindings", cleanup.CLEANUP_TABLES)
        self.assertIn("auth_sessions", cleanup.CLEANUP_TABLES)
        self.assertEqual(cleanup.EXPECTED_BUSINESS_TABLES, (
            "user_credits",
            "credit_transactions",
            "credit_grant_lots",
            "welcome_grant_claims",
            "credit_purchases",
            "orders",
            "live_portrait_jobs",
            "user_subscriptions",
            "subscription_credit_grants",
            "account_risk_events",
        ))
        reserved = SimpleNamespace(
            kind="PREVIEW_COMMERCIAL",
            api_role="PREVIEW_COMMERCIAL_API",
            worker_role="PREVIEW_COMMERCIAL_WORKER",
            worker_image_digest="sha256:" + "a" * 64,
            worker_deployment_id=None,
            phase="RESERVED",
            runtime_bundle_id="rtb_" + "c" * 64,
            current_snapshot_hash=None,
            target_snapshot_hash=None,
        )
        args = SimpleNamespace(role="PREVIEW_COMMERCIAL", runtime_bundle_id=reserved.runtime_bundle_id)
        cleanup._validate_activation(reserved, args)
        with self.assertRaisesRegex(ValueError, "deployment ID"):
            cleanup._validate_activation(SimpleNamespace(**{**vars(reserved), "phase": "DEPLOYED"}), args)
        model_source = (ROOT / "backend/app/models/acceptance_identity_binding.py").read_text(
            encoding="utf-8"
        )
        service_source = (ROOT / "backend/app/services/acceptance_identity_service.py").read_text(
            encoding="utf-8"
        )
        cleanup_source = (ROOT / "scripts/release/cleanup_preview_identity_smoke.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("revoked_at", model_source)
        self.assertIn("AcceptanceIdentityBinding.revoked_at.is_(None)", service_source)
        self.assertIn("remove_owned_callback", cleanup_source)
        self.assertIn("origin_state_artifact", cleanup_source)
        self.assertIn("AuthSession.acceptance_binding_id.in_", cleanup_source)
        self.assertNotIn("AuthSession.user_id.in_", cleanup_source)
        self.assertNotIn("Preview origin state artifact is required for cleanup", cleanup_source)
        self.assertIn("Preview identity welcome lineage is not exact", cleanup_source)
        self.assertIn("StorageService", cleanup_source)
        self.assertIn("list_private", cleanup_source)
        self.assertIn("storage_objects_remaining", cleanup_source)
        self.assertIn("second_binding", cleanup_source)
        self.assertNotIn("await db.delete", cleanup_source)
        self.assertIn('add_argument("--role"', cleanup_source)

        commercial_activation = SimpleNamespace(
            kind="PREVIEW_COMMERCIAL",
            api_role="PREVIEW_COMMERCIAL_API",
            worker_role="PREVIEW_COMMERCIAL_WORKER",
            worker_image_digest="sha256:" + "a" * 64,
            worker_deployment_id="b" * 64,
            phase="COMPLETED",
            runtime_bundle_id="rtb_" + "c" * 64,
            current_snapshot_hash="d" * 64,
            target_snapshot_hash="d" * 64,
        )
        cleanup._validate_activation(
            commercial_activation,
            SimpleNamespace(
                role="PREVIEW_COMMERCIAL",
                runtime_bundle_id="rtb_" + "c" * 64,
            ),
        )

    def test_private_cleanup_deletes_registered_and_orphaned_objects_in_exact_user_prefix(self) -> None:
        cleanup = _load(
            "cleanup_preview_private_assets",
            "scripts/release/cleanup_preview_identity_smoke.py",
        )
        user_id = UUID("00000000-0000-0000-0000-000000000071")
        prefix = f"users/{user_id}/uploads/"
        registered_key = f"{prefix}registered.jpg"
        orphan_key = f"{prefix}orphan.jpg"
        asset = SimpleNamespace(
            id=UUID("00000000-0000-0000-0000-000000000072"),
            owner_user_id=user_id,
            object_key=registered_key,
            status=None,
            deleted_at=None,
            read_revoked_at=None,
            deletion_reason=None,
            deletion_blockers=None,
            next_delete_at=None,
            lease_owner="worker",
            lease_claim_id=UUID("00000000-0000-0000-0000-000000000073"),
            lease_expires_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )

        class Result:
            def scalars(self):
                return [asset]

        class Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def begin(self):
                return Transaction()

            async def execute(self, _query):
                return Result()

        class Store:
            def __init__(self):
                self.keys = {registered_key, orphan_key}

            def list_private(self, requested_prefix, *, limit):
                self.assertions = (requested_prefix, limit)
                return tuple(sorted(key for key in self.keys if key.startswith(requested_prefix)))

            def delete_private(self, object_key):
                if object_key not in self.keys:
                    return cleanup.DeleteResult.NOT_FOUND
                self.keys.remove(object_key)
                return cleanup.DeleteResult.DELETED

        store = Store()
        with patch.object(cleanup, "StorageService", return_value=store):
            report = asyncio.run(cleanup._cleanup_private_assets(Session, (user_id,)))
        self.assertEqual(report["private_asset_prefix"], [prefix])
        self.assertEqual(report["storage_objects_deleted"], 2)
        self.assertEqual(report["storage_objects_remaining"], 0)
        self.assertEqual(store.keys, set())
        self.assertEqual(asset.status, cleanup.MediaAssetStatus.DELETED)
        self.assertIsNotNone(asset.deleted_at)
        self.assertIsNotNone(asset.read_revoked_at)
        self.assertIsNone(asset.lease_owner)

    def test_migration_bounds_preview_activation_lifecycle_and_retries(self) -> None:
        migration = (ROOT / "backend/alembic/versions/20260710_0014_web_identity_sessions.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("preview COMPLETED activation may only record its one-time snapshot", migration)
        self.assertIn("PREVIEW_IDENTITY", migration)
        self.assertIn("OLD.kind IN ('PREVIEW_IDENTITY', 'PREVIEW_COMMERCIAL')", migration)
        self.assertIn("NEW.phase = 'CLEANED'", migration)
        self.assertIn("OLD.phase = 'COMPLETED'", migration)
        self.assertIn("uq_release_activation_preview_identity_active", migration)
        self.assertIn("uq_release_activation_preview_identity_attempt", migration)
        self.assertIn("uq_release_activation_preview_commercial_active", migration)
        self.assertIn("uq_release_activation_preview_commercial_attempt", migration)
        self.assertIn("phase <> 'CLEANED'", migration)
        self.assertNotIn("DROP INDEX IF EXISTS public.uq_release_activation_runtime_bundle", migration)
        baseline_migration = (
            ROOT / "backend/alembic/versions/20260710_0013_ops_feature_flags.py"
        ).read_text(encoding="utf-8")
        self.assertIn("uq_release_activation_runtime_bundle", baseline_migration)
        self.assertIn("revoked_at", migration)

    def test_workflow_has_fresh_resolver_failures_and_independent_cleanup(self) -> None:
        workflow_path = ROOT / ".github/workflows/integration.yml"
        self.assertTrue(workflow_path.exists())
        workflow = workflow_path.read_text(encoding="utf-8")
        for required in (
            "environment: preview-identity",
            "ci_run_id:",
            "ci_run_attempt:",
            "verify_github_ref.py",
            "build_runtime_bundle_id.py",
            "--release-role PREVIEW_IDENTITY",
            "register_preview_activation.py reserve",
            "register_preview_activation.py deployed",
            "register_preview_activation.py complete",
            "resolve_release_coordinates.py",
            "--coordinate-kind preview-identity",
            "configure_preview_auth_origin.py snapshot",
            "configure_preview_auth_origin.py add",
            "provision_acceptance_identity.py",
            "configure_preview_identity_flag.py",
            "RUN_PREVIEW_E2E: '1'",
            "cleanup_preview_identity_smoke.py",
            "--schema 20260710_0020",
            "20260710_0020=backend/alembic/versions/20260710_0020_partner_consent.py",
            "PREVIEW_SECOND_GOOGLE_STORAGE_STATE_B64",
            "PREVIEW_PRIVATE_STORAGE_BUCKET",
            "--profile stage5_foundation",
            "if: always()",
            "cancel-in-progress: false",
            "commercial:",
            "environment: preview-commercial",
            "--release-role PREVIEW_COMMERCIAL",
            "run_preview_worker.py build",
            "run_preview_worker.py create",
            "snapshot_preview_commercial_flags.py",
            "run_preview_worker.py resume",
            "run_preview_worker.py heartbeat",
            "configure_preview_provider_grant_origin.py add",
            "prepare_preview_provider_case.py",
            "PROVIDER_GRANT_PROBE_SECRET",
            'PROVIDER_ASSET_GRANT_MAX_READS=1',
            "VERCEL_PROJECT_ID",
            "VERCEL_ORG_ID",
            "prepare_preview_provider_grant.py",
            "verify_provider_grant_fetch.py",
            "cleanup_preview_provider_case.py",
            "materialize_stage5_evidence.py",
            "vowpic-pr-gates-${{ inputs.ci_run_id }}-${{ inputs.ci_run_attempt }}",
            "run_preview_worker.py stop",
            "--role PREVIEW_COMMERCIAL",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)
        self.assertNotIn("PREVIEW_PROVIDER_INPUT_B64", workflow)
        aggregate_position = workflow.index("--profile stage5_foundation")
        self.assertGreater(
            aggregate_position,
            workflow.index("Return the database-owned Preview Commercial activation to CLEANED"),
        )
        for scope in ("pr", "preview_identity", "preview_commercial", "stage5_composite"):
            self.assertIn(f"--expected-runtime-binding {scope}=", workflow)
        self.assertIn("artifacts/stage5-aggregate/${SOURCE_SHA}/${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}", workflow)
        self.assertNotIn('--output "$STAGE5_ROOT/', workflow)
        cleanup_job = workflow.index("Revoke any prepared grant and remove the exact temporary alias")
        cleanup_end = workflow.index("Stop the exact Worker", cleanup_job)
        cleanup_block = workflow[cleanup_job:cleanup_end]
        self.assertIn("set +e", cleanup_block)
        self.assertIn("provider_cleanup_status=$?", cleanup_block)
        self.assertNotIn("provider_cleanup_unproven", cleanup_block)
        self.assertLess(
            cleanup_block.index("configure_preview_provider_grant_origin.py remove"),
            cleanup_block.rindex('test "$provider_cleanup_status" -eq 0'),
        )
        for boundary in (
            "reserve",
            "deploy",
            "register",
            "add",
            "login",
            "upload",
            "owner-read",
            "cross-user",
            "delete",
            "refresh",
            "logout",
            "cleanup",
        ):
            self.assertIn(f'"{boundary}"', workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("continue-on-error: true\n      - name: Clean", workflow)
        self.assertLess(
            workflow.index("run_preview_worker.py create"),
            workflow.index("snapshot_preview_commercial_flags.py"),
        )
        self.assertLess(
            workflow.index("snapshot_preview_commercial_flags.py"),
            workflow.index("run_preview_worker.py resume"),
        )
        provider_job = workflow.index("Lease the exact Provider-grant origin")
        self.assertLess(
            workflow.index("_validate_contract", provider_job),
            workflow.index("configure_preview_provider_grant_origin.py add", provider_job),
        )

    def test_preview_runtime_uses_distinct_non_migration_database_logins(self) -> None:
        workflow = (ROOT / ".github/workflows/integration.yml").read_text(encoding="utf-8")
        for required in (
            "PREVIEW_RUNTIME_DATABASE_URL: ${{ secrets.PREVIEW_RUNTIME_DATABASE_URL }}",
            "PREVIEW_CONTROL_PLANE_DATABASE_URL: ${{ secrets.PREVIEW_CONTROL_PLANE_DATABASE_URL }}",
            "PREVIEW_PRIVATE_STORAGE_ENDPOINT: ${{ secrets.PREVIEW_PRIVATE_STORAGE_ENDPOINT }}",
            "EVOLINK_API_KEY: ${{ secrets.EVOLINK_API_KEY }}",
            "EVOLINK_API_BASE_URL: ${{ vars.EVOLINK_API_BASE_URL }}",
            "EVOLINK_IMAGE_MODEL: ${{ vars.EVOLINK_IMAGE_MODEL }}",
            "PROVIDER_EVIDENCE_HMAC_KEY: ${{ secrets.PROVIDER_EVIDENCE_HMAC_KEY }}",
            'test -n "$PREVIEW_RUNTIME_DATABASE_URL"',
            'test -n "$PREVIEW_CONTROL_PLANE_DATABASE_URL"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)

        identity_deploy = workflow[
            workflow.index("Build and deploy the exact Vercel Preview output") :
            workflow.index("Read back Vercel and register DEPLOYED")
        ]
        commercial_deploy = workflow[
            workflow.index("Build once and deploy the exact Preview Commercial API") :
            workflow.index("Resolve the platform API ID and create the Worker without starting it")
        ]
        worker_create = workflow[
            workflow.index("Resolve the platform API ID and create the Worker without starting it") :
            workflow.index("Bind API and dormant Worker, then complete the immutable activation")
        ]
        provider_case = workflow[
            workflow.index("Lease the exact Provider-grant origin") :
            workflow.index("Revoke any prepared grant and remove the exact temporary alias")
        ]
        commercial_preflight = workflow[
            workflow.index("Require the isolated Preview Commercial runtime") :
            workflow.index("Install pinned release tooling and run the commercial boundary tests")
        ]
        for required_name in (
            "PREVIEW_RUNTIME_DATABASE_URL",
            "PREVIEW_CONTROL_PLANE_DATABASE_URL",
            "PREVIEW_REDIS_URL",
            "PREVIEW_PRIVATE_STORAGE_ENDPOINT",
            "EVOLINK_API_KEY",
            "EVOLINK_API_BASE_URL",
            "EVOLINK_IMAGE_MODEL",
            "PROVIDER_EVIDENCE_HMAC_KEY",
        ):
            with self.subTest(preflight=required_name):
                self.assertIn(required_name, commercial_preflight)
        for runtime_block in (identity_deploy, commercial_deploy, worker_create):
            with self.subTest(block=runtime_block.splitlines()[0]):
                self.assertIn("DATABASE_URL=$PREVIEW_RUNTIME_DATABASE_URL", runtime_block)
                self.assertIn(
                    "CONTROL_PLANE_DATABASE_URL=$PREVIEW_CONTROL_PLANE_DATABASE_URL",
                    runtime_block,
                )
                self.assertNotIn("DATABASE_URL=$PREVIEW_MIGRATION_DATABASE_URL", runtime_block)
        self.assertIn(
            "ACCEPTANCE_IDENTITY_HMAC_KEY=$ACCEPTANCE_IDENTITY_HMAC_KEY",
            identity_deploy,
        )

        self.assertIn(
            "DATABASE_URL: ${{ secrets.PREVIEW_RUNTIME_DATABASE_URL }}",
            provider_case,
        )
        self.assertIn(
            "CONTROL_PLANE_DATABASE_URL: ${{ secrets.PREVIEW_CONTROL_PLANE_DATABASE_URL }}",
            provider_case,
        )
        self.assertNotIn(
            "\n          DATABASE_URL: ${{ secrets.PREVIEW_MIGRATION_DATABASE_URL }}",
            provider_case,
        )

    def test_preview_worker_commands_are_digest_pinned_ephemeral_and_shell_free(self) -> None:
        worker = _load(
            "run_preview_worker",
            "scripts/release/run_preview_worker.py",
        )
        build = worker.build_command(
            source_sha="a" * 40,
            oci_output=Path("preview-worker.oci"),
        )
        self.assertIsInstance(build, list)
        self.assertEqual(build[:3], ["docker", "buildx", "build"])
        self.assertIn("type=oci,dest=preview-worker.oci", build)
        self.assertIn("--provenance=true", build)
        self.assertIn("--sbom=true", build)
        self.assertNotIn("shell=True", repr(build))

        self.assertFalse(hasattr(worker, "start_command"))
        created = worker.create_command(
            image_reference="vowpic-preview-worker@sha256:" + "b" * 64,
            container_name="vowpic-preview-123-1",
            env_file=Path("worker.env"),
        )
        self.assertEqual(created[:2], ["docker", "create"])
        for required in (
            "--read-only",
            "--cap-drop=ALL",
            "no-new-privileges:true",
            "--memory=2g",
            "--cpus=2",
            "--restart=no",
        ):
            self.assertIn(required, created)
        with self.assertRaises(ValueError):
            worker.create_command(
                image_reference="vowpic-preview-worker:latest",
                container_name="vowpic-preview-123-1",
                env_file=Path("worker.env"),
            )
        self.assertNotIn("--detach", created)
        created_report = worker.build_process_report(
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "c" * 64,
            api_deployment_id="dpl_preview_123",
            image_reference="vowpic-preview-worker@sha256:" + "b" * 64,
            container_id="d" * 64,
            container_name="vowpic-preview-123-1",
            state="CREATED",
            now=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )
        self.assertEqual(created_report["state"], "CREATED")
        process_report = worker.build_process_report(
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "c" * 64,
            api_deployment_id="dpl_preview_123",
            image_reference="vowpic-preview-worker@sha256:" + "b" * 64,
            container_id="d" * 64,
            container_name="vowpic-preview-123-1",
            now=datetime(2026, 7, 14, tzinfo=timezone.utc),
        )
        self.assertEqual(process_report["worker_image_digest"], "sha256:" + "b" * 64)
        self.assertEqual(process_report["runtime_bundle_id"], "rtb_" + "c" * 64)
        self.assertEqual(process_report["api_deployment_id"], "dpl_preview_123")
        heartbeat_payload = {
            "schema": "vowpic.worker-heartbeat.v1",
            "worker_id": "worker-preview-123",
            "environment": "preview",
            "source_sha": "a" * 40,
            "runtime_bundle_id": "rtb_" + "c" * 64,
            "api_deployment_id": "dpl_preview_123",
            "worker_deployment_id": "d" * 64,
            "worker_image_digest": "sha256:" + "b" * 64,
            "schema_revision": "20260710_0020",
            "payload_min": "generation-job.v1",
            "payload_max": "generation-job.v1",
            "config_hash": "e" * 64,
            "current_feature_snapshot_hash": "f" * 64,
            "target_feature_snapshot_hash": "f" * 64,
            "published_at": "2026-07-14T00:00:00+00:00",
        }
        validated = worker.validate_runtime_heartbeat_payload(
            heartbeat_payload,
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "c" * 64,
            api_deployment_id="dpl_preview_123",
            worker_deployment_id="d" * 64,
            worker_image_digest="sha256:" + "b" * 64,
            current_snapshot_hash="f" * 64,
            target_snapshot_hash="f" * 64,
            now=datetime(2026, 7, 14, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(validated["worker_deployment_id"], "d" * 64)
        with self.assertRaisesRegex(ValueError, "coordinate mismatch"):
            worker.validate_runtime_heartbeat_payload(
                heartbeat_payload,
                source_sha="a" * 40,
                runtime_bundle_id="rtb_" + "c" * 64,
                api_deployment_id="dpl_other",
                worker_deployment_id="d" * 64,
                worker_image_digest="sha256:" + "b" * 64,
                current_snapshot_hash="f" * 64,
                target_snapshot_hash="f" * 64,
                now=datetime(2026, 7, 14, 0, 1, tzinfo=timezone.utc),
            )
        self.assertEqual(
            worker.validate_cleanup_heartbeat_payload(
                None,
                source_sha="a" * 40,
                runtime_bundle_id="rtb_" + "c" * 64,
                api_deployment_id="dpl_preview_123",
                worker_deployment_id="d" * 64,
                worker_image_digest="sha256:" + "b" * 64,
                current_snapshot_hash="f" * 64,
                target_snapshot_hash="f" * 64,
            ),
            None,
        )
        cleanup_heartbeat = worker.validate_cleanup_heartbeat_payload(
            heartbeat_payload,
            source_sha="a" * 40,
            runtime_bundle_id="rtb_" + "c" * 64,
            api_deployment_id="dpl_preview_123",
            worker_deployment_id="d" * 64,
            worker_image_digest="sha256:" + "b" * 64,
            current_snapshot_hash="f" * 64,
            target_snapshot_hash="f" * 64,
        )
        self.assertEqual(cleanup_heartbeat["runtime_bundle_id"], "rtb_" + "c" * 64)
        worker_source = (ROOT / "scripts/release/run_preview_worker.py").read_text(
            encoding="utf-8"
        )
        for required in (
            'stop.add_argument("--runtime-bundle-id", required=True)',
            'stop.add_argument("--redis-url-env"',
            "client.eval",
            '"heartbeat_state": "ABSENT"',
        ):
            self.assertIn(required, worker_source)

    def test_production_workflow_is_manual_serialized_and_promotes_only_after_acceptance(self) -> None:
        path = ROOT / ".github" / "workflows" / "production-release.yml"
        self.assertTrue(path.exists(), "manual Production workflow is missing")
        workflow = path.read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$")
        for forbidden in ("push:", "pull_request:", "schedule:", "repository_dispatch:", "workflow_call:"):
            self.assertNotIn(forbidden, workflow)
        for required in (
            "environment: production",
            "cancel-in-progress: false",
            "required_source_sha",
            "verify_github_ref.py",
            'test "$("$VERCEL_CLI" --version)" = "56.2.0"',
            '"$VERCEL_CLI" build --prod',
            "deploy --prebuilt --prod --skip-domain",
            "RUNTIME_BUNDLE_ID=",
            "resolve_release_coordinates.py",
            "--coordinate-kind preview-commercial-cleaned",
            "register_bundle.py reserve",
            "--kind COMMERCIAL_7A",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)
        self.assertNotIn("--target=production", workflow.lower())
        self.assertNotIn("CONTRACT_7B", workflow)
        self.assertLess(
            workflow.index("register_bundle.py reserve"),
            workflow.index("secrets.VERCEL_TOKEN"),
        )
        self.assertEqual(workflow.lower().count('"$vercel_cli" promote'), 2)
        self.assertLess(
            workflow.index("--phase TARGET_ACCEPTED"),
            workflow.lower().rindex('"$vercel_cli" promote'),
        )

    def test_runtime_version_route_is_not_a_mutating_or_secret_surface(self) -> None:
        main_source = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
        router_source = (ROOT / "backend/app/routers/runtime.py").read_text(encoding="utf-8")
        service_source = (ROOT / "backend/app/services/runtime_bundle_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('@app.get("/version")', main_source)
        self.assertIn('@router.get("/version")', router_source)
        self.assertIn("release_role = active_settings.release_role.strip()", service_source)
        self.assertIn("release_role=release_role", service_source)
        self.assertNotIn('@app.post("/version")', main_source)
        self.assertNotIn('@router.post("/version")', router_source)
        for forbidden in (
            "active_settings.redis_url",
            "active_settings.database_url",
            "worker_heartbeat",
            "active_settings.sentry_dsn",
            "active_settings.private_media_grant_secret",
        ):
            self.assertNotIn(forbidden, service_source.lower())


if __name__ == "__main__":
    unittest.main()
