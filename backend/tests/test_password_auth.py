"""Username/password authentication tests."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
import uuid

from fastapi import HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.user import User  # noqa: E402
from app.models.account_risk_event import AccountRiskEvent  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.schemas.auth import LoginRequest, RegisterRequest  # noqa: E402


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDb:
    def __init__(self):
        self.users_by_username: dict[str, User] = {}
        self.users_by_email: dict[str, User] = {}
        self.pending: User | None = None
        self.risk_events: list[AccountRiskEvent] = []
        self.rolled_back = False

    async def execute(self, statement):
        params = statement.compile().params
        username = str(params.get("username_1") or params.get("username") or "").lower()
        email = str(params.get("email_1") or params.get("email") or "").lower()
        if email:
            return _ScalarResult(self.users_by_email.get(email))
        return _ScalarResult(self.users_by_username.get(username))

    def add(self, value):
        if isinstance(value, User):
            self.pending = value
        elif isinstance(value, AccountRiskEvent):
            self.risk_events.append(value)

    async def flush(self):
        if self.pending is not None:
            if self.pending.id is None:
                self.pending.id = uuid.uuid4()
            self.users_by_username[str(self.pending.username)] = self.pending
            if self.pending.email:
                self.users_by_email[str(self.pending.email)] = self.pending
            self.pending = None

    async def refresh(self, user: User):
        if user.id is None:
            user.id = uuid.uuid4()

    async def rollback(self):
        self.rolled_back = True


def _request(ip: str = "127.0.0.1"):
    return SimpleNamespace(
        headers={
            "x-forwarded-for": ip,
            "x-device-id": f"device-{ip}",
            "user-agent": "password-auth-test",
        },
        client=SimpleNamespace(host=ip),
    )


class PasswordAuthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        auth_router.NEW_ACCOUNT_IP_LIMITER._events.clear()
        auth_router.NEW_ACCOUNT_DEVICE_LIMITER._events.clear()
        self._original_verify_email_code = auth_router.verify_email_code
        self._original_grant_welcome_bonus = auth_router.grant_welcome_bonus
        self._original_check_new_account_risk_limits = auth_router.check_new_account_risk_limits
        self._original_settings = auth_router.settings
        auth_router.settings = Settings(_env_file=None, password_auth_enabled=True)
        auth_router.verify_email_code = lambda _email, _code: True

        async def _grant_welcome_bonus_noop(_db, _user_id, **_kwargs):
            return True

        async def _check_new_account_risk_limits_noop(*_args, **_kwargs):
            return None

        auth_router.grant_welcome_bonus = _grant_welcome_bonus_noop
        auth_router.check_new_account_risk_limits = _check_new_account_risk_limits_noop

    def tearDown(self) -> None:
        auth_router.verify_email_code = self._original_verify_email_code
        auth_router.grant_welcome_bonus = self._original_grant_welcome_bonus
        auth_router.check_new_account_risk_limits = self._original_check_new_account_risk_limits
        auth_router.settings = self._original_settings

    def _register_request(self, username: str, password: str) -> RegisterRequest:
        return RegisterRequest(
            username=username,
            password=password,
            email=f"{username.lower()}@example.com",
            verification_code="123456",
        )

    async def test_register_hashes_password_and_returns_session(self) -> None:
        db = _FakeDb()

        response = await auth_router.register(
            self._register_request("BrideUser", "secret123"),
            _request("10.0.0.1"),
            db,
        )

        self.assertEqual(response.token_type, "bearer")
        self.assertEqual(response.username, "brideuser")
        self.assertIn("brideuser", db.users_by_username)
        user = db.users_by_username["brideuser"]
        self.assertNotEqual(user.password, "secret123")
        self.assertTrue(str(user.password).startswith("$2"))
        self.assertTrue(auth_router._verify_password("secret123", str(user.password)))
        self.assertEqual(str(response.user_id), str(user.id))

    async def test_password_login_uses_existing_user_identity(self) -> None:
        db = _FakeDb()
        registered = await auth_router.register(
            self._register_request("creator01", "secret123"),
            _request("10.0.0.2"),
            db,
        )

        logged_in = await auth_router.login(
            LoginRequest(username="creator01", password="secret123"),
            _request("10.0.0.3"),
            db,
        )

        self.assertEqual(logged_in.username, "creator01")
        self.assertEqual(str(logged_in.user_id), str(registered.user_id))
        self.assertEqual(logged_in.openid, registered.openid)

    async def test_password_login_accepts_verified_email(self) -> None:
        db = _FakeDb()
        registered = await auth_router.register(
            self._register_request("emailuser", "secret123"),
            _request("10.0.0.21"),
            db,
        )

        logged_in = await auth_router.login(
            LoginRequest(username="emailuser@example.com", password="secret123"),
            _request("10.0.0.22"),
            db,
        )

        self.assertEqual(str(logged_in.user_id), str(registered.user_id))
        self.assertEqual(logged_in.username, "emailuser")

    async def test_register_adds_password_to_existing_google_email(self) -> None:
        db = _FakeDb()
        existing = User(
            id=uuid.uuid4(),
            openid="supabase_existing",
            auth_provider="supabase",
            auth_subject="google-subject",
            email="linked@example.com",
        )
        db.users_by_email["linked@example.com"] = existing

        response = await auth_router.register(
            RegisterRequest(
                username="linkeduser",
                password="secret123",
                email="linked@example.com",
                verification_code="123456",
            ),
            _request("10.0.0.23"),
            db,
        )

        self.assertEqual(str(response.user_id), str(existing.id))
        self.assertEqual(existing.username, "linkeduser")
        self.assertTrue(auth_router._verify_password("secret123", str(existing.password)))

    async def test_duplicate_username_is_rejected(self) -> None:
        db = _FakeDb()
        await auth_router.register(self._register_request("same_name", "secret123"), _request("10.0.0.4"), db)

        with self.assertRaises(HTTPException) as raised:
            await auth_router.register(
                RegisterRequest(
                    username="same_name",
                    password="another123",
                    email="same-name-2@example.com",
                    verification_code="123456",
                ),
                _request("10.0.0.5"),
                db,
            )

        self.assertEqual(raised.exception.status_code, 409)

    async def test_wrong_password_is_rejected(self) -> None:
        db = _FakeDb()
        await auth_router.register(self._register_request("safeuser", "secret123"), _request("10.0.0.6"), db)

        with self.assertRaises(HTTPException) as raised:
            await auth_router.login(LoginRequest(username="safeuser", password="wrong123"), _request("10.0.0.7"), db)

        self.assertEqual(raised.exception.status_code, 401)

    async def test_password_auth_can_be_disabled(self) -> None:
        db = _FakeDb()
        auth_router.settings = Settings(_env_file=None, password_auth_enabled=False)

        with self.assertRaises(HTTPException) as register_error:
            await auth_router.register(self._register_request("closeduser", "secret123"), _request("10.0.0.30"), db)
        self.assertEqual(register_error.exception.status_code, 403)

        with self.assertRaises(HTTPException) as login_error:
            await auth_router.login(LoginRequest(username="closeduser", password="secret123"), _request("10.0.0.31"), db)
        self.assertEqual(login_error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
