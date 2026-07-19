"""Read-only aggregate inventory for legacy migration and release gates."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import hmac
import json
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.production_inventory_policy import (
    inventory_policy_proof_sql,
    validate_inventory_policy_proof,
)


MIN_IDENTIFIER_HMAC_KEY_BYTES = 32


class ProductionInventoryReport(BaseModel):
    """Sanitized aggregates only; raw rows and storage references are forbidden."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )

    schema_name: str = Field(
        default="vowpic.production-inventory.v2",
        alias="schema",
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_revision: str
    source_database_identity_hmac_sha256: str
    users: dict[str, int]
    ledger: dict[str, int]
    orders: dict[str, int]
    objects: dict[str, int]
    conflict_group_hmacs: dict[str, list[str]] = Field(default_factory=dict)
    url_inventory_hmac_sha256: str
    read_only_proof: dict[str, bool | int | str]

    @field_validator("users", "ledger", "orders", "objects")
    @classmethod
    def validate_counts(cls, value: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for key, count in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("inventory count keys must be non-empty strings")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"inventory count must be a non-negative integer: {key}")
            normalized[key] = count
        return normalized

    @field_validator("conflict_group_hmacs")
    @classmethod
    def validate_group_hmacs(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for group, identifiers in value.items():
            if not group.strip():
                raise ValueError("conflict group name is required")
            if identifiers != sorted(set(identifiers)):
                raise ValueError("conflict group HMACs must be sorted and unique")
            if any(len(identifier) != 64 or any(char not in "0123456789abcdef" for char in identifier) for identifier in identifiers):
                raise ValueError("conflict identifiers must be lowercase SHA-256 HMACs")
        return value

    @field_validator(
        "source_database_identity_hmac_sha256",
        "url_inventory_hmac_sha256",
    )
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("inventory checksum must be lowercase SHA-256")
        return value

    @field_validator("read_only_proof")
    @classmethod
    def validate_report_read_only_proof(
        cls,
        value: dict[str, bool | int | str],
    ) -> dict[str, bool | int | str]:
        validate_read_only_proof(value)
        return value


def hmac_identifier(key: bytes, namespace: str, identifier: str) -> str:
    if len(key) < MIN_IDENTIFIER_HMAC_KEY_BYTES:
        raise ValueError("identifier HMAC key must contain at least 32 bytes")
    clean_namespace = namespace.strip().lower()
    clean_identifier = identifier.strip().lower()
    if not clean_namespace or not clean_identifier:
        raise ValueError("identifier namespace and value are required")
    payload = f"vowpic.production-inventory.v1\0{clean_namespace}\0{clean_identifier}".encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def validate_read_only_proof(proof: dict[str, Any]) -> None:
    required_true = ("transaction_read_only", "default_transaction_read_only")
    if any(proof.get(field) is not True for field in required_true):
        raise ValueError("inventory source transaction and role default must both be read-only")
    privileged = (
        "role_superuser",
        "role_create_db",
        "role_create_role",
        "role_replication",
    )
    if any(proof.get(field) is True for field in privileged):
        raise ValueError("inventory source role has privileged write capabilities")
    validate_inventory_policy_proof(proof)
    if int(proof.get("writable_table_count") or 0) != 0:
        raise ValueError("inventory source role has table mutation privileges")
    if proof.get("write_probe_sqlstate") != "25006":
        raise ValueError("inventory source write probe did not fail with read-only SQLSTATE 25006")


async def _read_only_proof(db: AsyncSession) -> dict[str, bool | int | str]:
    transaction_read_only = str(await db.scalar(text("SHOW transaction_read_only")) or "").lower() == "on"
    default_transaction_read_only = (
        str(await db.scalar(text("SHOW default_transaction_read_only")) or "").lower() == "on"
    )
    role_result = await db.execute(
        text(
            """
            SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
    )
    role = role_result.mappings().one()
    policy_result = await db.execute(text(inventory_policy_proof_sql()))
    policy_proof = dict(policy_result.mappings().one())
    writable_table_count = int(
        await db.scalar(
            text(
                """
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND (
                    has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'INSERT')
                    OR has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'UPDATE')
                    OR has_table_privilege(current_user, format('%I.%I', table_schema, table_name), 'DELETE')
                  )
                """
            )
        )
        or 0
    )

    write_probe_sqlstate = "write_probe_succeeded"
    try:
        async with db.begin_nested():
            await db.execute(
                text("UPDATE alembic_version SET version_num = version_num WHERE false")
            )
    except DBAPIError as exc:
        origin = getattr(exc, "orig", None)
        write_probe_sqlstate = str(
            getattr(origin, "sqlstate", None) or getattr(origin, "pgcode", None) or "unknown"
        )

    proof: dict[str, bool | int | str] = {
        **policy_proof,
        "transaction_read_only": transaction_read_only,
        "default_transaction_read_only": default_transaction_read_only,
        "role_superuser": bool(role["rolsuper"]),
        "role_create_db": bool(role["rolcreatedb"]),
        "role_create_role": bool(role["rolcreaterole"]),
        "role_replication": bool(role["rolreplication"]),
        "role_bypass_rls": bool(role["rolbypassrls"]),
        "writable_table_count": writable_table_count,
        "write_probe_sqlstate": write_probe_sqlstate,
    }
    validate_read_only_proof(proof)
    return proof


FULL_SCHEMA_SHAPE: dict[str, set[str]] = {
    "users": {"id", "openid", "auth_provider", "auth_subject", "email", "username", "password", "avatar_url"},
    "orders": {"user_id", "source_image_urls", "preview_image_urls", "final_image_urls"},
    "user_credits": {"user_id", "balance"},
    "user_subscriptions": {"user_id", "status"},
    "subscription_credit_grants": {"user_id"},
    "live_portrait_jobs": {"user_id", "source_image_url", "video_url"},
    "credit_purchases": {"user_id", "checkout_url"},
}


async def _inventory_schema(db: AsyncSession) -> dict[str, set[str]]:
    result = await db.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        )
    )
    schema: dict[str, set[str]] = defaultdict(set)
    for row in result.mappings().all():
        schema[str(row["table_name"])].add(str(row["column_name"]))
    return dict(schema)


def _legacy_user_predicate(user_columns: set[str], *, alias: str = "users") -> str:
    if not {"auth_provider", "auth_subject"} <= user_columns:
        return "TRUE"
    conditions = [
        f"{alias}.auth_provider IS NULL",
        f"{alias}.auth_provider <> 'supabase'",
        f"NULLIF({alias}.auth_subject, '') IS NULL",
    ]
    if "openid" in user_columns:
        conditions.extend(
            f"{alias}.openid LIKE '{prefix}'" for prefix in ("guest_%", "visitor_%", "anon_%")
        )
    if "username" in user_columns:
        conditions.append(f"{alias}.username IS NOT NULL")
    if "password" in user_columns:
        conditions.append(f"{alias}.password IS NOT NULL")
    return " OR ".join(conditions)


def build_user_inventory_sql(schema: dict[str, set[str]]) -> str:
    users = schema.get("users", set())
    if not {"id", "openid"} <= users:
        raise ValueError("inventory requires users.id and users.openid")
    auth = "users.auth_provider" if "auth_provider" in users else "NULL::text"
    subject = "users.auth_subject" if "auth_subject" in users else "NULL::text"
    password_conditions = [f"{auth} = 'password'"]
    if "username" in users:
        password_conditions.append("users.username IS NOT NULL")
    if "password" in users:
        password_conditions.append("users.password IS NOT NULL")
    metrics = [
        "count(*)::bigint AS total",
        f"count(*) FILTER (WHERE {auth} IN ('guest', 'anonymous') OR users.openid LIKE 'guest_%')::bigint AS guest",
        f"count(*) FILTER (WHERE {' OR '.join(password_conditions)})::bigint AS password",
        f"count(*) FILTER (WHERE {auth} IS NOT NULL AND {auth} <> 'supabase' "
        f"AND {auth} NOT IN ('guest', 'anonymous', 'password'))::bigint AS other_retired_provider",
        "count(*) FILTER (WHERE users.openid LIKE 'visitor_%' OR users.openid LIKE 'anon_%')::bigint AS visitor",
        f"count(*) FILTER (WHERE {auth} IS NOT NULL AND {auth} NOT IN ('guest', 'anonymous', 'password') "
        f"AND NULLIF({subject}, '') IS NULL)::bigint AS missing_subject",
    ]
    if "email" in users:
        metrics.append(
            "(SELECT count(*) FROM (SELECT lower(email) FROM users WHERE NULLIF(email, '') IS NOT NULL "
            "GROUP BY lower(email) HAVING count(*) > 1) duplicate_emails)::bigint AS duplicate_email_groups"
        )
    else:
        metrics.append("0::bigint AS duplicate_email_groups")
    if {"auth_provider", "auth_subject"} <= users:
        metrics.append(
            "(SELECT count(*) FROM (SELECT auth_provider, auth_subject FROM users "
            "WHERE NULLIF(auth_provider, '') IS NOT NULL AND NULLIF(auth_subject, '') IS NOT NULL "
            "GROUP BY auth_provider, auth_subject HAVING count(*) > 1) duplicate_subjects)::bigint "
            "AS duplicate_subject_groups"
        )
    else:
        metrics.append("0::bigint AS duplicate_subject_groups")
    if "user_id" in schema.get("orders", set()):
        metrics.extend(
            (
                "(SELECT count(DISTINCT orders.user_id) FROM orders LEFT JOIN users owners "
                "ON owners.id = orders.user_id WHERE owners.id IS NULL)::bigint AS asset_owners_unknown",
                "(SELECT count(DISTINCT orders.user_id) FROM orders JOIN legacy_accounts "
                "ON legacy_accounts.id = orders.user_id)::bigint AS legacy_accounts_with_orders",
                "(SELECT count(*) FROM orders JOIN legacy_accounts ON legacy_accounts.id = orders.user_id)::bigint "
                "AS legacy_account_orders",
            )
        )
    else:
        metrics.extend(("0::bigint AS asset_owners_unknown", "0::bigint AS legacy_accounts_with_orders", "0::bigint AS legacy_account_orders"))
    metrics.append("(SELECT count(*) FROM legacy_accounts)::bigint AS legacy_accounts")
    if {"user_id", "balance"} <= schema.get("user_credits", set()):
        metrics.extend(
            (
                "(SELECT count(DISTINCT user_credits.user_id) FROM user_credits JOIN legacy_accounts "
                "ON legacy_accounts.id = user_credits.user_id WHERE user_credits.balance <> 0)::bigint "
                "AS legacy_accounts_with_credit_balance",
                "(SELECT COALESCE(sum(GREATEST(user_credits.balance, 0)), 0) FROM user_credits "
                "JOIN legacy_accounts ON legacy_accounts.id = user_credits.user_id)::bigint AS legacy_credit_balance",
            )
        )
    else:
        metrics.extend(("0::bigint AS legacy_accounts_with_credit_balance", "0::bigint AS legacy_credit_balance"))
    if {"user_id", "status"} <= schema.get("user_subscriptions", set()):
        metrics.extend(
            (
                "(SELECT count(*) FROM user_subscriptions JOIN legacy_accounts ON legacy_accounts.id = "
                "user_subscriptions.user_id WHERE lower(user_subscriptions.status) IN ('trialing', 'active'))::bigint "
                "AS legacy_active_subscriptions",
                "(SELECT count(DISTINCT user_subscriptions.user_id) FROM user_subscriptions JOIN legacy_accounts "
                "ON legacy_accounts.id = user_subscriptions.user_id WHERE lower(user_subscriptions.status) "
                "IN ('trialing', 'active'))::bigint AS legacy_accounts_with_active_subscriptions",
            )
        )
    else:
        metrics.extend(("0::bigint AS legacy_active_subscriptions", "0::bigint AS legacy_accounts_with_active_subscriptions"))
    if "user_id" in schema.get("subscription_credit_grants", set()):
        metrics.append(
            "(SELECT count(*) FROM subscription_credit_grants JOIN legacy_accounts ON legacy_accounts.id = "
            "subscription_credit_grants.user_id)::bigint AS legacy_subscription_credit_grants"
        )
    else:
        metrics.append("0::bigint AS legacy_subscription_credit_grants")
    metric_sql = ",\n              ".join(metrics)
    return f"""
            WITH legacy_accounts AS (
              SELECT id FROM users WHERE {_legacy_user_predicate(users)}
            )
            SELECT
              {metric_sql}
            FROM users
            """


def _conflict_inventory_sql(schema: dict[str, set[str]]) -> str:
    users = schema.get("users", set())
    selections: list[str] = []
    if "email" in users:
        selections.append(
            "SELECT 'duplicate_email' AS kind, lower(email) AS identifier FROM users "
            "WHERE NULLIF(email, '') IS NOT NULL GROUP BY lower(email) HAVING count(*) > 1"
        )
    if {"auth_provider", "auth_subject"} <= users:
        selections.append(
            "SELECT 'duplicate_subject' AS kind, auth_provider || ':' || auth_subject AS identifier FROM users "
            "WHERE NULLIF(auth_provider, '') IS NOT NULL AND NULLIF(auth_subject, '') IS NOT NULL "
            "GROUP BY auth_provider, auth_subject HAVING count(*) > 1"
        )
    if not selections:
        return "SELECT NULL::text AS kind, NULL::text AS identifier WHERE false"
    return " UNION ALL ".join(selections) + " ORDER BY kind, identifier"


async def _user_inventory(
    db: AsyncSession,
    identifier_hmac_key: bytes,
    schema: dict[str, set[str]] | None = None,
) -> tuple[dict[str, int], dict[str, list[str]]]:
    effective_schema = FULL_SCHEMA_SHAPE if schema is None else schema
    result = await db.execute(text(build_user_inventory_sql(effective_schema)))
    row = result.mappings().one()
    counts = {key: int(value or 0) for key, value in row.items()}

    conflict_result = await db.execute(text(_conflict_inventory_sql(effective_schema)))
    groups: dict[str, list[str]] = defaultdict(list)
    for item in conflict_result.mappings().all():
        groups[str(item["kind"])].append(
            hmac_identifier(identifier_hmac_key, str(item["kind"]), str(item["identifier"]))
        )
    return counts, {key: sorted(set(values)) for key, values in sorted(groups.items())}


async def _ledger_inventory(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        text(
            """
            WITH ledger AS (
              SELECT user_id, COALESCE(sum(amount), 0)::bigint AS amount
              FROM credit_transactions
              GROUP BY user_id
            ), balances AS (
              SELECT user_id, balance::bigint AS balance
              FROM user_credits
            )
            SELECT
              (SELECT count(*) FROM credit_transactions)::bigint AS total_transactions,
              count(*) FILTER (
                WHERE COALESCE(balances.balance, 0) <> COALESCE(ledger.amount, 0)
              )::bigint AS balance_mismatch_users,
              (
                SELECT count(*) FROM credit_transactions
                WHERE amount < 0 AND (source IS NULL OR source_id IS NULL)
              )::bigint AS legacy_unlinked_debits,
              (
                SELECT count(*) FROM credit_transactions tx
                LEFT JOIN users ON users.id = tx.user_id
                WHERE users.id IS NULL
              )::bigint AS orphan_transactions
            FROM balances FULL OUTER JOIN ledger USING (user_id)
            """
        )
    )
    row = result.mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


async def _order_inventory(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(
        text(
            """
            SELECT
              count(*)::bigint AS total,
              count(*) FILTER (
                WHERE orders.deleted_at IS NULL
                  AND upper(orders.status) IN ('CREATED', 'CHECKING', 'GENERATING', 'PAID')
              )::bigint AS active,
              count(*) FILTER (
                WHERE owners.id IS NULL
                  OR owners.auth_provider IS NULL
                  OR owners.auth_provider <> 'supabase'
                  OR (owners.auth_provider = 'supabase' AND NULLIF(owners.auth_subject, '') IS NULL)
              )::bigint AS legacy_unverified,
              count(*) FILTER (WHERE orders.deleted_at IS NOT NULL)::bigint AS deleted,
              count(*) FILTER (
                WHERE orders.source_images_expires_at < CURRENT_TIMESTAMP
                  AND orders.source_image_urls IS NOT NULL
                  AND orders.source_image_urls <> '{}'::jsonb
              )::bigint AS expired_sources_still_referenced
            FROM orders
            LEFT JOIN users owners ON owners.id = orders.user_id
            """
        )
    )
    row = result.mappings().one()
    return {key: int(value or 0) for key, value in row.items()}


def _normalize_reference(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))
    return raw.split("?", 1)[0].split("#", 1)[0]


def _object_role(raw: str, normalized: str) -> str:
    lower_raw = raw.lower()
    lower_normalized = normalized.lower()
    parsed = urlsplit(raw)
    query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & {
        "token",
        "signature",
        "x-amz-credential",
        "x-amz-security-token",
        "x-amz-signature",
    }:
        return "private"
    if any(marker in lower_raw for marker in ("x-amz-signature=", "x-amz-credential=", "?token=")):
        return "private"
    if any(marker in lower_normalized for marker in ("/private/", "/protected/", "/object/sign/")):
        return "private"
    if lower_normalized.startswith(("http://", "https://", "/static/", "/public/")):
        return "public"
    return "unknown"


def build_object_reference_sql(schema: dict[str, set[str]]) -> str:
    users = schema.get("users", set())
    if "id" not in users:
        raise ValueError("object inventory requires users.id")
    legacy_predicate = _legacy_user_predicate(users)
    fragments: list[str] = []
    orders = schema.get("orders", set())
    if "user_id" in orders:
        for column, source_kind in (
            ("source_image_urls", "order_source"),
            ("preview_image_urls", "order_preview"),
            ("final_image_urls", "order_final"),
        ):
            if column in orders:
                fragments.append(
                    f"SELECT orders.user_id::text AS owner_id, '{source_kind}'::text AS source_kind, "
                    f"jsonb_path_query(orders.{column}, '$.** ? (@.type() == \"string\")') #>> '{{}}' AS reference, "
                    "true AS is_asset, EXISTS (SELECT 1 FROM legacy_accounts WHERE legacy_accounts.id = "
                    f"orders.user_id) AS is_legacy_owner FROM orders WHERE orders.{column} IS NOT NULL"
                )
    live_portrait = schema.get("live_portrait_jobs", set())
    if "user_id" in live_portrait:
        for column, source_kind in (
            ("source_image_url", "live_portrait_source"),
            ("video_url", "live_portrait_video"),
        ):
            if column in live_portrait:
                fragments.append(
                    f"SELECT user_id::text, '{source_kind}', {column}, true, "
                    "EXISTS (SELECT 1 FROM legacy_accounts WHERE legacy_accounts.id = "
                    f"live_portrait_jobs.user_id) FROM live_portrait_jobs WHERE NULLIF({column}, '') IS NOT NULL"
                )
    if "avatar_url" in users:
        fragments.append(
            "SELECT users.id::text, 'user_avatar', users.avatar_url, true, "
            "EXISTS (SELECT 1 FROM legacy_accounts WHERE legacy_accounts.id = users.id) "
            "FROM users WHERE NULLIF(users.avatar_url, '') IS NOT NULL"
        )
    purchases = schema.get("credit_purchases", set())
    if {"user_id", "checkout_url"} <= purchases:
        fragments.append(
            "SELECT credit_purchases.user_id::text, 'credit_checkout', credit_purchases.checkout_url, false, "
            "EXISTS (SELECT 1 FROM legacy_accounts WHERE legacy_accounts.id = credit_purchases.user_id) "
            "FROM credit_purchases WHERE NULLIF(credit_purchases.checkout_url, '') IS NOT NULL"
        )
    if not fragments:
        fragments.append(
            "SELECT NULL::text AS owner_id, NULL::text AS source_kind, NULL::text AS reference, "
            "true AS is_asset, false AS is_legacy_owner WHERE false"
        )
    union_sql = " UNION ALL ".join(fragments)
    return f"""
        WITH legacy_accounts AS (
          SELECT id FROM users WHERE {legacy_predicate}
        ), object_references AS (
          {union_sql}
        )
        SELECT owner_id, source_kind, reference, is_asset, is_legacy_owner
        FROM object_references
        WHERE NULLIF(reference, '') IS NOT NULL
        ORDER BY source_kind, owner_id
        """


async def _object_inventory(
    db: AsyncSession,
    identifier_hmac_key: bytes,
    schema: dict[str, set[str]] | None = None,
) -> tuple[dict[str, int], str]:
    effective_schema = FULL_SCHEMA_SHAPE if schema is None else schema
    statement = text(build_object_reference_sql(effective_schema))
    streamed = await db.stream(statement)
    role_counts = {
        "public_user_assets": 0,
        "private_user_assets": 0,
        "unknown_role": 0,
        "legacy_asset_references": 0,
        "legacy_accounts_with_asset_references": 0,
        "non_asset_url_references": 0,
    }
    owners_by_hmac: dict[str, set[str]] = defaultdict(set)
    public_asset_hmacs: set[str] = set()
    asset_hmacs: set[str] = set()
    legacy_asset_owners: set[str] = set()
    reference_hmacs: list[str] = []
    async for row in streamed.mappings():
        raw = str(row["reference"] or "")
        normalized = _normalize_reference(raw)
        if not normalized:
            continue
        reference_hmac = hmac_identifier(identifier_hmac_key, "object_reference", normalized)
        reference_hmacs.append(reference_hmac)
        if not bool(row["is_asset"]):
            role_counts["non_asset_url_references"] += 1
            continue
        owner_id = str(row["owner_id"] or "unknown")
        asset_hmacs.add(reference_hmac)
        owners_by_hmac[reference_hmac].add(owner_id)
        if bool(row["is_legacy_owner"]):
            role_counts["legacy_asset_references"] += 1
            legacy_asset_owners.add(owner_id)
        role = _object_role(raw, normalized)
        if role == "public":
            role_counts["public_user_assets"] += 1
            public_asset_hmacs.add(reference_hmac)
        elif role == "private":
            role_counts["private_user_assets"] += 1
        else:
            role_counts["unknown_role"] += 1

    unique_hmacs = sorted(set(reference_hmacs))
    role_counts["total_references"] = len(reference_hmacs)
    role_counts["unique_assets"] = len(asset_hmacs)
    role_counts["legacy_accounts_with_asset_references"] = len(legacy_asset_owners)
    role_counts["shared_public_assets"] = sum(
        1 for reference_hmac, owners in owners_by_hmac.items()
        if len(owners) > 1 and reference_hmac in public_asset_hmacs
    )
    canonical = json.dumps(unique_hmacs, separators=(",", ":"), ensure_ascii=True)
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return role_counts, checksum


async def build_inventory_report(
    db: AsyncSession,
    identifier_hmac_key: bytes,
    *,
    source_database_identity: str = "database-identity-unavailable",
) -> ProductionInventoryReport:
    if len(identifier_hmac_key) < MIN_IDENTIFIER_HMAC_KEY_BYTES:
        raise ValueError("identifier HMAC key must contain at least 32 bytes")
    await db.execute(text("SET TRANSACTION READ ONLY"))
    read_only_proof = await _read_only_proof(db)
    revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
    schema = await _inventory_schema(db)
    users, conflict_group_hmacs = await _user_inventory(db, identifier_hmac_key, schema)
    ledger = await _ledger_inventory(db)
    orders = await _order_inventory(db)
    objects, url_inventory_hmac_sha256 = await _object_inventory(db, identifier_hmac_key, schema)
    return ProductionInventoryReport(
        schema_revision=str(revision or "unknown"),
        source_database_identity_hmac_sha256=hmac_identifier(
            identifier_hmac_key,
            "source_database",
            source_database_identity,
        ),
        users=users,
        ledger=ledger,
        orders=orders,
        objects=objects,
        conflict_group_hmacs=conflict_group_hmacs,
        url_inventory_hmac_sha256=url_inventory_hmac_sha256,
        read_only_proof=read_only_proof,
    )
