"""Authoritative feature-flag decision and persistence contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[2]


def _flags_module():
    module = importlib.import_module("app.core.feature_flags")
    for name in ("Capability", "FeatureFlagState", "FeatureFlagContext", "FeatureFlagDecision"):
        if not hasattr(module, name):
            raise AssertionError(f"missing public feature-flag interface: {name}")
    return module


def _service_module():
    path = ROOT / "backend" / "app" / "services" / "feature_flag_service.py"
    if not path.exists():
        raise AssertionError("authoritative PostgreSQL feature-flag service is missing")
    return importlib.import_module("app.services.feature_flag_service")


class FeatureFlagDecisionTest(unittest.TestCase):
    def test_retired_environment_switch_cannot_enable_runtime_capability(self) -> None:
        flags = _flags_module()
        with patch.object(flags.settings, "generation_enabled", True):
            self.assertFalse(flags.bootstrap_capability_enabled(flags.Capability.GENERATION))

    def test_runtime_coordinates_use_only_vercel_system_deployment_id(self) -> None:
        ignored = Settings(_env_file=None, deployment_id="caller-controlled")
        self.assertEqual(ignored.deployment_id, "")

        configured = Settings(
            _env_file=None,
            runtime_environment="production",
            vercel_deployment_id="dpl_system",
            runtime_bundle_id="rtb_" + "a" * 64,
            release_role="SAFE_BASELINE",
            acceptance_identity_hmac_key="k" * 32,
        )
        self.assertTrue(configured.runtime_coordinates_valid)
        self.assertEqual(configured.deployment_id, "dpl_system")

    def test_missing_flag_fails_closed(self) -> None:
        flags = _flags_module()
        service = _service_module()
        decision = service.decide_flag(
            flags.Capability.GENERATION,
            None,
            flags.FeatureFlagContext(environment="production"),
        )
        self.assertEqual(decision.state, flags.FeatureFlagState.OFF)
        self.assertFalse(decision.allowed)

    def test_acceptance_cohort_requires_exact_user_deployment_bundle_and_expiry(self) -> None:
        flags = _flags_module()
        service = _service_module()
        user_id = uuid4()
        now = datetime.now(timezone.utc)
        context = flags.FeatureFlagContext(
            environment="production",
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_target",
            user_id=user_id,
            now=now,
        )
        row = {
            "environment": "production",
            "state": "ACCEPTANCE_COHORT",
            "deployment_id": "dpl_target",
            "runtime_bundle_id": "rtb_target",
            "cohort_user_ids": [str(user_id)],
            "expires_at": now + timedelta(minutes=30),
        }
        self.assertTrue(service.decide_flag(flags.Capability.GENERATION, row, context).allowed)

        for changed in (
            {"deployment_id": "dpl_other"},
            {"runtime_bundle_id": "rtb_other"},
            {"user_id": None},
            {"now": now + timedelta(hours=1)},
            {"environment": "preview"},
        ):
            values = {
                "environment": context.environment,
                "deployment_id": context.deployment_id,
                "runtime_bundle_id": context.runtime_bundle_id,
                "user_id": context.user_id,
                "now": context.now,
            }
            values.update(changed)
            rejected = flags.FeatureFlagContext(**values)
            self.assertFalse(service.decide_flag(flags.Capability.GENERATION, row, rejected).allowed)

    def test_on_requires_exact_active_coordinates_and_worker_digest_when_bound(self) -> None:
        flags = _flags_module()
        service = _service_module()
        row = {
            "environment": "production",
            "state": "ON",
            "deployment_id": "dpl_target",
            "runtime_bundle_id": "rtb_target",
            "worker_image_digest": "sha256:" + "a" * 64,
        }
        allowed = flags.FeatureFlagContext(
            environment="production",
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_target",
            worker_image_digest="sha256:" + "a" * 64,
        )
        self.assertTrue(service.decide_flag(flags.Capability.GENERATION, row, allowed).allowed)

        wrong_worker = flags.FeatureFlagContext(
            environment="production",
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_target",
            worker_image_digest="sha256:" + "b" * 64,
        )
        self.assertFalse(service.decide_flag(flags.Capability.GENERATION, row, wrong_worker).allowed)

    def test_cohort_ttl_above_86400_is_rejected(self) -> None:
        service = _service_module()
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            service.validate_cohort_expiry(now, now + timedelta(seconds=86401))

    def test_unknown_capability_is_rejected(self) -> None:
        service = _service_module()
        with self.assertRaises(ValueError):
            service.coerce_capability("invented_capability")


class FeatureFlagAuthorityTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin_mutation_cannot_enable_preview_or_production_before_later_gates(self) -> None:
        flags = _flags_module()
        service = _service_module()
        for environment in ("preview", "production"):
            db = AsyncMock()
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                await service.set_capability_state(
                    db,
                    flags.Capability.GENERATION,
                    environment=environment,
                    state=flags.FeatureFlagState.ON,
                    actor="google-admin:test",
                    reason="should remain closed",
                    deployment_id="dpl_target",
                    runtime_bundle_id="rtb_" + "a" * 64,
                    release_activation_id=uuid4(),
                )
            db.execute.assert_not_awaited()

    async def test_database_error_fails_closed_and_never_reuses_enabled_cache(self) -> None:
        flags = _flags_module()
        service = _service_module()
        context = flags.FeatureFlagContext(
            environment="production",
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_target",
        )
        with patch.object(
            service,
            "_load_authoritative_row",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ), patch.object(service, "_cache_off_decision", new=AsyncMock()) as cache_off:
            decision = await service.resolve_capability(
                AsyncMock(), flags.Capability.GENERATION, context
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.state, flags.FeatureFlagState.OFF)
        self.assertEqual(decision.reason, "authority_unavailable")
        cache_off.assert_awaited_once()

    async def test_off_cache_ttl_is_capped_at_thirty_seconds(self) -> None:
        flags = _flags_module()
        service = _service_module()
        redis = AsyncMock()
        decision = flags.FeatureFlagDecision(
            capability=flags.Capability.GENERATION,
            state=flags.FeatureFlagState.OFF,
            allowed=False,
            snapshot_hash="0" * 64,
            reason="disabled",
        )
        with patch.object(service, "get_redis", new=AsyncMock(return_value=redis)):
            await service._cache_off_decision(decision, "production", ttl_seconds=999)
        self.assertEqual(redis.set.await_args.kwargs["ex"], 30)

    async def test_emergency_disable_deletes_cache_and_writes_audit(self) -> None:
        flags = _flags_module()
        service = _service_module()
        row = SimpleNamespace(
            id=uuid4(),
            environment="production",
            capability=flags.Capability.GENERATION.value,
            state=flags.FeatureFlagState.ON.value,
            deployment_id="dpl_target",
            runtime_bundle_id="rtb_target",
            worker_image_digest=None,
            cohort_user_ids=[],
            verified_identity_hashes=[],
            expires_at=None,
            version=3,
        )
        db = AsyncMock()
        db.add = MagicMock()
        result = SimpleNamespace(scalar_one_or_none=lambda: row)
        db.execute.return_value = result
        with patch.object(service, "_delete_cached_off", new=AsyncMock()) as delete_cache:
            decision = await service.emergency_disable(
                db,
                flags.Capability.GENERATION,
                environment="production",
                actor="admin-user:test",
                reason="rollback",
            )
        self.assertFalse(decision.allowed)
        self.assertEqual(row.state, flags.FeatureFlagState.OFF.value)
        self.assertGreaterEqual(len(db.add.call_args_list), 1)
        delete_cache.assert_awaited_once_with("production", flags.Capability.GENERATION)


if __name__ == "__main__":
    unittest.main()
