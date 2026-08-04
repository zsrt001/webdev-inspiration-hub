"""Schema contracts for normalized Web identity and revocable sessions."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


MODEL_MODULES = {
    "UserIdentity": "user_identity",
    "OAuthLoginIntent": "oauth_login_intent",
    "AuthSession": "auth_session",
    "AuthRefreshToken": "auth_refresh_token",
    "UserAccountMerge": "user_account_merge",
    "AccountClaimProof": "account_claim_proof",
    "IdentityEmailConflict": "identity_email_conflict",
    "AccountTombstone": "account_tombstone",
}


class IdentitySessionSchemaTest(unittest.TestCase):
    def _model(self, class_name: str):
        module_name = MODEL_MODULES[class_name]
        path = BACKEND_DIR / "app" / "models" / f"{module_name}.py"
        self.assertTrue(path.is_file(), f"missing Task 6 model: {path.name}")
        module = importlib.import_module(f"app.models.{module_name}")
        return getattr(module, class_name)

    @staticmethod
    def _constraint_names(model) -> set[str]:
        return {
            constraint.name
            for constraint in model.__table__.constraints
            if constraint.name is not None
        }

    @staticmethod
    def _foreign_key_ondelete(model, column_name: str) -> str | None:
        foreign_keys = list(model.__table__.columns[column_name].foreign_keys)
        if len(foreign_keys) != 1:
            raise AssertionError(f"expected one foreign key on {model.__name__}.{column_name}")
        return foreign_keys[0].ondelete

    def test_identity_subject_and_legacy_merge_are_unique(self) -> None:
        user_identity = self._model("UserIdentity")
        user_account_merge = self._model("UserAccountMerge")

        self.assertIn(
            "uq_user_identities_provider_subject",
            self._constraint_names(user_identity),
        )
        self.assertIn(
            "uq_user_account_merges_legacy_user",
            self._constraint_names(user_account_merge),
        )
        self.assertIn(
            "ck_user_account_merges_distinct_users",
            self._constraint_names(user_account_merge),
        )
        self.assertIn(
            "ck_user_identities_provider_supabase",
            self._constraint_names(user_identity),
        )

    def test_refresh_tokens_are_hash_only_and_generation_is_unique(self) -> None:
        refresh_token = self._model("AuthRefreshToken")
        columns = set(refresh_token.__table__.columns.keys())

        self.assertIn("token_hash", columns)
        self.assertNotIn("refresh_token", columns)
        self.assertIn(
            "uq_auth_refresh_tokens_session_generation",
            self._constraint_names(refresh_token),
        )
        self.assertIn(
            "ck_auth_refresh_tokens_state_timestamps",
            self._constraint_names(refresh_token),
        )

    def test_session_family_and_oauth_intent_hash_are_unique(self) -> None:
        auth_session = self._model("AuthSession")
        oauth_intent = self._model("OAuthLoginIntent")

        self.assertIn(
            "uq_auth_sessions_family_id",
            self._constraint_names(auth_session),
        )
        self.assertIn(
            "uq_auth_sessions_acceptance_binding_id",
            self._constraint_names(auth_session),
        )
        self.assertEqual(
            self._foreign_key_ondelete(auth_session, "acceptance_binding_id"),
            "RESTRICT",
        )
        self.assertIn(
            "uq_oauth_login_intents_token_hash",
            self._constraint_names(oauth_intent),
        )
        self.assertTrue(
            {
                "token_hash",
                "browser_binding_hash",
                "redirect_path",
                "expires_at",
                "consumed_at",
            }.issubset(oauth_intent.__table__.columns.keys())
        )
        self.assertTrue(
            {"state", "nonce", "pkce_verifier", "access_token"}.isdisjoint(
                oauth_intent.__table__.columns.keys()
            )
        )

    def test_claim_conflict_merge_and_tombstone_bind_users_with_restrict(self) -> None:
        expected_user_columns = {
            "AccountClaimProof": ("canonical_user_id", "legacy_user_id"),
            "IdentityEmailConflict": ("canonical_user_id", "legacy_user_id"),
            "UserAccountMerge": ("canonical_user_id", "legacy_user_id"),
            "AccountTombstone": ("user_id",),
        }

        for class_name, columns in expected_user_columns.items():
            model = self._model(class_name)
            for column in columns:
                with self.subTest(model=class_name, column=column):
                    self.assertEqual(self._foreign_key_ondelete(model, column), "RESTRICT")

    def test_legacy_identity_is_nullable_and_email_is_profile_data(self) -> None:
        from app.models.user import User

        self.assertTrue(User.__table__.columns.openid.nullable)
        self.assertIsNot(User.__table__.columns.email.unique, True)
        self.assertIn("uq_users_openid", self._constraint_names(User))
        openid_index = next(
            index for index in User.__table__.indexes if index.name == "ix_users_openid"
        )
        self.assertFalse(openid_index.unique)

    def test_financial_order_subscription_and_retained_job_fks_restrict_delete(self) -> None:
        from app.models.credit_purchase import CreditPurchase
        from app.models.credit_transaction import CreditTransaction
        from app.models.live_portrait_job import LivePortraitJob
        from app.models.order import Order
        from app.models.subscription_credit_grant import SubscriptionCreditGrant
        from app.models.user_credit import UserCredit
        from app.models.user_subscription import UserSubscription

        models = (
            CreditPurchase,
            CreditTransaction,
            LivePortraitJob,
            Order,
            SubscriptionCreditGrant,
            UserCredit,
            UserSubscription,
        )
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertEqual(self._foreign_key_ondelete(model, "user_id"), "RESTRICT")

    def test_all_task6_models_are_exported(self) -> None:
        models_path = BACKEND_DIR / "app" / "models" / "__init__.py"
        source = models_path.read_text(encoding="utf-8")

        for class_name in MODEL_MODULES:
            with self.subTest(model=class_name):
                self.assertIn(f'"{class_name}"', source)

    def test_migration_follows_current_head_and_contains_hardened_rls(self) -> None:
        migration = (
            BACKEND_DIR
            / "alembic"
            / "versions"
            / "20260710_0014_web_identity_sessions.py"
        )
        self.assertTrue(migration.is_file(), "Task 6 identity migration is missing")
        source = migration.read_text(encoding="utf-8")

        self.assertIn('revision = "20260710_0014"', source)
        self.assertIn('down_revision = "20260712_0014"', source)
        self.assertIn("ENABLE ROW LEVEL SECURITY", source)
        self.assertIn("FORCE ROW LEVEL SECURITY", source)
        self.assertIn("SECURITY DEFINER", source)
        self.assertIn("SET search_path = pg_catalog, public", source)
        self.assertIn("REVOKE ALL ON FUNCTION public.app_current_user_id() FROM PUBLIC", source)
        self.assertIn("identity_legacy_fallback_uses_seq", source)
        sequence_revoke = source.index(
            "REVOKE ALL ON SEQUENCE public.identity_legacy_fallback_uses_seq FROM PUBLIC"
        )
        sequence_grant = source.index(
            "GRANT SELECT ON SEQUENCE public.identity_legacy_fallback_uses_seq"
        )
        sequence_owner = source.index(
            "ALTER SEQUENCE public.identity_legacy_fallback_uses_seq "
            '"\n            "OWNER TO vowpic_identity_owner'
        )
        self.assertLess(sequence_revoke, sequence_owner)
        self.assertLess(sequence_grant, sequence_owner)
        function_revoke = source.index(
            "REVOKE ALL ON FUNCTION public.app_current_user_id() FROM PUBLIC"
        )
        function_grant = source.index(
            "GRANT EXECUTE ON FUNCTION public.app_current_user_id() TO authenticated"
        )
        function_owner = source.index(
            "ALTER FUNCTION public.app_current_user_id() OWNER TO vowpic_identity_owner"
        )
        self.assertLess(function_revoke, function_owner)
        self.assertLess(function_grant, function_owner)
        for guard_name in (
            "guard_user_identity_update",
            "guard_oauth_login_intent_update",
            "guard_auth_session_update",
            "guard_auth_refresh_token_update",
            "guard_account_claim_proof_update",
            "guard_identity_email_conflict_update",
            "guard_account_tombstone_update",
        ):
            with self.subTest(guard=guard_name):
                self.assertIn(guard_name, source)

    def test_migration_persists_task6_legacy_identity_comments(self) -> None:
        from app.models.user import User

        migration = (
            BACKEND_DIR
            / "alembic"
            / "versions"
            / "20260710_0014_web_identity_sessions.py"
        )
        source = migration.read_text(encoding="utf-8")
        columns = (
            "openid",
            "username",
            "password",
            "auth_provider",
            "auth_subject",
            "email",
            "unionid",
        )

        for column_name in columns:
            comment = User.__table__.columns[column_name].comment
            with self.subTest(column=column_name):
                self.assertIsNotNone(comment)
                self.assertIn(comment, source)


if __name__ == "__main__":
    unittest.main()
