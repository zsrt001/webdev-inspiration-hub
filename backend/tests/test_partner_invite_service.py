"""Authenticated partner invite token, identity, intent, and state contracts."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from app.models.media_asset import MediaAssetRole, MediaAssetStatus
from app.models.partner_invite import PartnerInviteStatus
from app.models.partner_invite_event import PartnerInviteEvent
from app.services.partner_invite_service import (
    PartnerActorRole,
    PartnerInviteCommand,
    PartnerInviteError,
    authorize_partner_transition,
    accept_partner_invite,
    build_partner_order_intent,
    consent_partner_invite,
    create_partner_invite,
    generate_partner_invite_token,
    hash_partner_invite_token,
    validate_verified_partner_identities,
)


NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


class PartnerInviteServiceTest(unittest.TestCase):
    def test_token_contains_exactly_32_random_bytes_and_only_keyed_hash_is_persistable(self) -> None:
        token, token_hash = generate_partner_invite_token(hmac_key=b"k" * 32)
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))

        self.assertEqual(len(raw), 32)
        self.assertEqual(len(token_hash), 64)
        self.assertNotIn(token, token_hash)
        self.assertEqual(hash_partner_invite_token(token, hmac_key=b"k" * 32), token_hash)
        self.assertNotEqual(hash_partner_invite_token(token, hmac_key=b"x" * 32), token_hash)

    def test_intent_is_immutable_and_excludes_price_or_provider_facts(self) -> None:
        invite_id = uuid.uuid4()
        host_user_id = uuid.uuid4()
        intent = build_partner_order_intent(
            invite_id=invite_id,
            host_user_id=host_user_id,
            order_intent_id=uuid.uuid4(),
            template_id="royal_castle",
        )

        self.assertEqual(intent.purpose, "COUPLE")
        self.assertEqual(intent.template_id, "royal_castle")
        self.assertEqual(intent.allowed_subject_roles, ("host", "partner"))
        dumped = intent.model_dump(mode="json")
        for forbidden in ("price", "credit", "provider", "product"):
            self.assertNotIn(forbidden, str(dumped).lower())
        self.assertEqual(len(intent.canonical_hash()), 64)

    def test_verified_identities_must_be_distinct_active_and_email_verified(self) -> None:
        host = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="supabase",
            verified_email_snapshot="host@example.com",
            revoked_at=None,
        )
        partner = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="supabase",
            verified_email_snapshot="partner@example.com",
            revoked_at=None,
        )
        validate_verified_partner_identities(host, partner)

        partner.user_id = host.user_id
        with self.assertRaisesRegex(PartnerInviteError, "partner_identity_not_distinct"):
            validate_verified_partner_identities(host, partner)

    def test_only_documented_state_transitions_are_authorized(self) -> None:
        expires_at = NOW + timedelta(days=1)
        self.assertEqual(
            authorize_partner_transition(
                status=PartnerInviteStatus.CREATED,
                actor_role=PartnerActorRole.PARTNER,
                command=PartnerInviteCommand.ACCEPT,
                expires_at=expires_at,
                now=NOW,
            ),
            PartnerInviteStatus.ACCEPTED,
        )
        self.assertEqual(
            authorize_partner_transition(
                status=PartnerInviteStatus.ACCEPTED,
                actor_role=PartnerActorRole.PARTNER,
                command=PartnerInviteCommand.CONSENT,
                expires_at=expires_at,
                now=NOW,
            ),
            PartnerInviteStatus.CONSENTED,
        )
        self.assertEqual(
            authorize_partner_transition(
                status=PartnerInviteStatus.CONSENTED,
                actor_role=PartnerActorRole.HOST,
                command=PartnerInviteCommand.COMPLETE_ORDER,
                expires_at=expires_at,
                now=NOW,
            ),
            PartnerInviteStatus.COMPLETED,
        )
        with self.assertRaisesRegex(PartnerInviteError, "partner_invite_transition_invalid"):
            authorize_partner_transition(
                status=PartnerInviteStatus.CREATED,
                actor_role=PartnerActorRole.HOST,
                command=PartnerInviteCommand.COMPLETE_ORDER,
                expires_at=expires_at,
                now=NOW,
            )
        with self.assertRaisesRegex(PartnerInviteError, "partner_invite_expired"):
            authorize_partner_transition(
                status=PartnerInviteStatus.CREATED,
                actor_role=PartnerActorRole.PARTNER,
                command=PartnerInviteCommand.ACCEPT,
                expires_at=NOW,
                now=NOW,
            )


class _Db:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush = AsyncMock()

    def add(self, value: object) -> None:
        self.added.append(value)


class PartnerInvitePersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_returns_raw_token_once_and_persists_only_hash_plus_audit(self) -> None:
        db = _Db()
        host_user_id = uuid.uuid4()
        host_identity = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=host_user_id,
            provider="supabase",
            verified_email_snapshot="host@example.com",
            revoked_at=None,
        )
        with (
            patch(
                "app.services.partner_invite_service._lock_verified_identity",
                AsyncMock(return_value=host_identity),
            ),
            patch(
                "app.services.partner_invite_service.generate_partner_invite_token",
                return_value=("r" * 43, "a" * 64),
            ),
        ):
            created = await create_partner_invite(
                db,
                host_user_id=host_user_id,
                hmac_key=b"k" * 32,
                frontend_base_url="https://example.test",
                template_id="royal_castle",
                request_id="request-create",
                now=NOW,
            )

        invite = next(item for item in db.added if item.__class__.__name__ == "PartnerInvite")
        event = next(item for item in db.added if isinstance(item, PartnerInviteEvent))
        self.assertEqual(created.token, "r" * 43)
        self.assertEqual(invite.token_hash, "a" * 64)
        self.assertNotIn("r" * 43, str(invite.__dict__))
        self.assertEqual(invite.expires_at - invite.created_at, timedelta(seconds=86400))
        self.assertEqual(event.to_status, PartnerInviteStatus.CREATED.value)
        self.assertNotIn("r" * 43, str(event.details_json))

    async def test_accept_binds_token_once_to_distinct_verified_partner(self) -> None:
        db = _Db()
        host_user_id = uuid.uuid4()
        partner_user_id = uuid.uuid4()
        invite = SimpleNamespace(
            id=uuid.uuid4(),
            host_user_id=host_user_id,
            host_identity_id=uuid.uuid4(),
            partner_user_id=None,
            partner_identity_id=None,
            status=PartnerInviteStatus.CREATED,
            version=1,
            expires_at=NOW + timedelta(days=1),
            order_intent_id=uuid.uuid4(),
            order_intent_hash="b" * 64,
            intent_policy_version="partner-consent.v1",
            purpose="COUPLE",
            template_id="royal_castle",
            consent_event_id=None,
            order_id=None,
            job_id=None,
            accepted_at=None,
            consented_at=None,
            completed_at=None,
            revoked_at=None,
            cancelled_at=None,
        )
        partner_identity = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=partner_user_id,
            provider="supabase",
            verified_email_snapshot="partner@example.com",
            revoked_at=None,
        )
        host_identity = SimpleNamespace(
            id=invite.host_identity_id,
            user_id=host_user_id,
            provider="supabase",
            verified_email_snapshot="host@example.com",
            revoked_at=None,
        )
        with (
            patch(
                "app.services.partner_invite_service._lock_invite_by_token",
                AsyncMock(return_value=invite),
            ),
            patch(
                "app.services.partner_invite_service._lock_verified_identity",
                AsyncMock(side_effect=(partner_identity, host_identity)),
            ),
        ):
            snapshot = await accept_partner_invite(
                db,
                token="valid-token",
                partner_user_id=partner_user_id,
                hmac_key=b"k" * 32,
                request_id="request-accept",
                now=NOW,
            )

        self.assertEqual(invite.partner_user_id, partner_user_id)
        self.assertEqual(invite.status, PartnerInviteStatus.ACCEPTED)
        self.assertEqual(invite.version, 2)
        self.assertEqual(snapshot.role, "PARTNER")
        self.assertIsNone(snapshot.order_id)

    async def test_consent_binds_exact_intent_asset_and_checksum_in_one_event(self) -> None:
        db = _Db()
        partner_user_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        invite = SimpleNamespace(
            id=uuid.uuid4(),
            host_user_id=uuid.uuid4(),
            host_identity_id=uuid.uuid4(),
            partner_user_id=partner_user_id,
            partner_identity_id=uuid.uuid4(),
            status=PartnerInviteStatus.ACCEPTED,
            version=2,
            expires_at=NOW + timedelta(days=1),
            order_intent_id=uuid.uuid4(),
            order_intent_hash="c" * 64,
            intent_policy_version="partner-consent.v1",
            purpose="COUPLE",
            template_id="royal_castle",
            partner_asset_id=None,
            partner_asset_sha256=None,
            consent_event_id=None,
            order_id=None,
            job_id=None,
            accepted_at=NOW,
            consented_at=None,
            completed_at=None,
            revoked_at=None,
            cancelled_at=None,
        )
        asset = SimpleNamespace(
            id=asset_id,
            owner_user_id=partner_user_id,
            role=MediaAssetRole.SOURCE,
            status=MediaAssetStatus.ACTIVE,
            read_revoked_at=None,
            sha256="d" * 64,
        )
        with (
            patch(
                "app.services.partner_invite_service._lock_partner_invite",
                AsyncMock(return_value=invite),
            ),
            patch(
                "app.services.partner_invite_service._lock_active_source_asset",
                AsyncMock(return_value=asset),
            ),
        ):
            snapshot = await consent_partner_invite(
                db,
                invite_id=invite.id,
                partner_user_id=partner_user_id,
                expected_version=2,
                order_intent_id=invite.order_intent_id,
                order_intent_hash=invite.order_intent_hash,
                partner_asset_id=asset_id,
                request_id="request-consent",
                now=NOW,
            )

        event = next(item for item in db.added if isinstance(item, PartnerInviteEvent))
        self.assertEqual(invite.partner_asset_id, asset_id)
        self.assertEqual(invite.partner_asset_sha256, "d" * 64)
        self.assertEqual(invite.consent_event_id, event.id)
        self.assertEqual(invite.status, PartnerInviteStatus.CONSENTED)
        self.assertEqual(snapshot.consent_event_id, event.id)


if __name__ == "__main__":
    unittest.main()
