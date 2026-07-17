"""Pure validation for non-owner database login evidence."""

from __future__ import annotations

from typing import Any


def validate_database_role_proof(
    proof: dict[str, Any],
    *,
    required_group: str,
    forbidden_group: str,
) -> str:
    current_user = str(proof.get("current_user") or "").strip()
    owner = str(proof.get("control_table_owner") or "").strip()
    if not current_user or not owner:
        raise RuntimeError("database role proof is incomplete")
    boolean_fields = (
        "role_can_login",
        "role_inherit",
        "role_superuser",
        "role_create_db",
        "role_create_role",
        "role_replication",
        "role_bypass_rls",
        "required_group_member",
        "forbidden_group_member",
    )
    if any(type(proof.get(field)) is not bool for field in boolean_fields):
        raise RuntimeError("database role proof has missing or invalid role facts")
    if not proof["role_can_login"] or not proof["role_inherit"]:
        raise RuntimeError("database runtime role must be a LOGIN INHERIT role")
    if bool(proof.get("role_superuser")):
        raise RuntimeError("database runtime role must not be a superuser")
    if bool(proof.get("role_create_db")):
        raise RuntimeError("database runtime role must not create databases")
    if bool(proof.get("role_create_role")):
        raise RuntimeError("database runtime role must not create roles")
    if bool(proof.get("role_replication")):
        raise RuntimeError("database runtime role must not use replication")
    if bool(proof.get("role_bypass_rls")):
        raise RuntimeError("database runtime role must be NOBYPASSRLS")
    if current_user == owner:
        raise RuntimeError("database runtime role must not own control-plane tables")
    if not bool(proof.get("required_group_member")):
        raise RuntimeError(f"database role must be a member of {required_group}")
    if bool(proof.get("forbidden_group_member")):
        raise RuntimeError(f"database role must not be a member of {forbidden_group}")
    return f"{current_user}:{required_group}"
