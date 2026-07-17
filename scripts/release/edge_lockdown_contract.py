"""Pure route, rule, and evidence-hash contract for the Production WAF lockdown."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable


BYPASS_HEADER_NAME = "x-vowpic-release-bypass"
BYPASS_RULE_NAME = "vowpic-release-runner-bypass"
DENY_RULE_GROUPS = {
    "vowpic-lock-identity-generation": (
        "auth_upload",
        "generation",
        "partner_invite",
    ),
    "vowpic-lock-commercial-retired": (
        "credit_checkout",
        "subscription",
        "retired_addons",
        "leads_recommendations",
    ),
}
EDGE_ROUTE_GROUPS = frozenset(
    group for groups in DENY_RULE_GROUPS.values() for group in groups
)
MANAGED_RULE_NAMES = frozenset({BYPASS_RULE_NAME, *DENY_RULE_GROUPS})
UUID_PATH = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


@dataclass(frozen=True)
class ApplicationProbe:
    method: str
    path: str
    expected_status: int
    expected_code: str
    json_body: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


APPLICATION_PROBES = {
    "auth_upload": ApplicationProbe(
        "POST",
        "/api/v1/auth/login",
        410,
        "auth_method_retired",
        json_body={"code": "safe-baseline"},
    ),
    "generation": ApplicationProbe(
        "POST",
        "/api/v1/admin/generation_probe",
        410,
        "admin_generation_execution_retired",
        json_body={"image_url": "https://example.invalid/probe.jpg"},
        headers={"X-Admin-Token": "retired-safe-baseline-probe"},
    ),
    "credit_checkout": ApplicationProbe(
        "POST",
        "/api/v1/credits/purchase",
        410,
        "legacy_credit_mutation_retired",
        json_body={"package_id": "pack_50"},
    ),
    "subscription": ApplicationProbe(
        "POST",
        "/api/v1/subscriptions/checkout",
        401,
        "session_missing",
        json_body={
            "plan_code": "starter_monthly",
            "return_url": "https://example.invalid/return",
        },
        headers={
            "Authorization": "Bearer retired-safe-baseline-probe",
            "Idempotency-Key": "safe-baseline-edge-probe",
        },
    ),
    "partner_invite": ApplicationProbe(
        "POST",
        "/api/v1/session/create",
        410,
        "partner_session_retired",
        json_body={"template_id": "safe-baseline"},
    ),
    "retired_addons": ApplicationProbe(
        "GET",
        "/api/v1/live_portrait/list",
        410,
        "live_portrait_retired",
    ),
    "leads_recommendations": ApplicationProbe(
        "GET",
        "/api/v1/recommendations/local_studios",
        410,
        "local_recommendations_retired",
    ),
}


def _condition_group(host: str, method: str, path_expression: str) -> dict[str, Any]:
    return {
        "conditions": [
            {"type": "host", "op": "eq", "value": host},
            {"type": "method", "op": "eq", "value": method},
            {"type": "raw_path", "op": "re", "value": path_expression},
        ]
    }


def route_condition_groups(host: str) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        "auth_upload": (
            _condition_group(
                host,
                "POST",
                r"^/api/v1/(?:auth/(?:oauth-intents|supabase/session|login)|media/uploads|gatekeeper/check|upload(?:/multiple|/delete)?)$",
            ),
        ),
        "generation": (
            _condition_group(
                host,
                "POST",
                r"^/api/v1/(?:orders/create|admin/(?:generation_probe|orders/[^/]+/regenerate|cleanup_expired_assets)|ops/(?:poll_pending_orders|cleanup_expired_assets))$",
            ),
            _condition_group(host, "DELETE", rf"^/api/v1/orders/{UUID_PATH}$"),
        ),
        "partner_invite": (
            _condition_group(
                host,
                "POST",
                rf"^/api/v1/partner-invites(?:/accept|/{UUID_PATH}/(?:consent|order|revoke|withdraw))?$",
            ),
            _condition_group(host, "GET", rf"^/api/v1/partner-invites/{UUID_PATH}$"),
            _condition_group(
                host,
                "POST",
                r"^/api/v1/session/(?:create|[^/]+/(?:upload/(?:host|guest)|processing|complete|bind_order))$",
            ),
            _condition_group(
                host,
                "GET",
                r"^/api/v1/session/[^/]+/(?:status|images|share_meta)$",
            ),
        ),
        "credit_checkout": (
            _condition_group(
                host,
                "POST",
                r"^/api/v1/(?:payments/(?:checkout|[^/]+/refund|manual/(?:submit|admin/(?:complete|fail)))|admin/grant_credits|credits/(?:purchase|deduct|add))$",
            ),
            _condition_group(
                host,
                "GET",
                r"^/api/v1/(?:credits/packages|payments/manual/checkout)$",
            ),
        ),
        "subscription": (
            _condition_group(
                host,
                "GET",
                r"^/api/v1/(?:subscriptions/(?:plans|me)|admin/creem_product_check)$",
            ),
            _condition_group(
                host,
                "POST",
                r"^/api/v1/(?:subscriptions/(?:checkout|cancel)|admin/creem_checkout_probe)$",
            ),
        ),
        "retired_addons": (
            _condition_group(
                host,
                "POST",
                r"^/api/v1/(?:users/?|live_portrait/generate)$",
            ),
            _condition_group(
                host,
                "GET",
                r"^/api/v1/(?:users/[^/]+|live_portrait/(?:list|[^/]+))$",
            ),
            _condition_group(host, "PATCH", r"^/api/v1/users/[^/]+$"),
        ),
        "leads_recommendations": (
            _condition_group(
                host,
                "GET",
                r"^/api/v1/(?:recommendations/local_studios|leads/(?:list|export\.csv)|admin/(?:crm_preview|crm_push_history))$",
            ),
            _condition_group(
                host,
                "POST",
                r"^/api/v1/(?:leads/submit|admin/crm_push)$",
            ),
        ),
    }


def build_deny_rules(host: str, included_groups: Iterable[str] | None = None) -> list[dict[str, Any]]:
    selected = set(EDGE_ROUTE_GROUPS if included_groups is None else included_groups)
    if not selected <= EDGE_ROUTE_GROUPS:
        raise ValueError("unknown edge route group")
    definitions = route_condition_groups(host)
    rules: list[dict[str, Any]] = []
    for rule_name, logical_groups in DENY_RULE_GROUPS.items():
        condition_groups = [
            condition_group
            for logical_group in logical_groups
            if logical_group in selected
            for condition_group in definitions[logical_group]
        ]
        if not condition_groups:
            continue
        if len(condition_groups) > 25:
            raise ValueError(f"{rule_name} exceeds Vercel's 25 OR-group limit")
        rules.append(
            {
                "name": rule_name,
                "description": "Temporary VowPic safe-baseline release lockdown",
                "active": True,
                "conditionGroup": condition_groups,
                "action": {"mitigate": {"action": "deny"}},
            }
        )
    return rules


def build_bypass_rule(host: str, bypass_value: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", bypass_value):
        raise ValueError("runner bypass value is invalid")
    return {
        "name": BYPASS_RULE_NAME,
        "description": "Ephemeral GitHub Runner bypass for VowPic release verification",
        "active": True,
        "conditionGroup": [
            {
                "conditions": [
                    {"type": "host", "op": "eq", "value": host},
                    {
                        "type": "header",
                        "key": BYPASS_HEADER_NAME,
                        "op": "eq",
                        "value": bypass_value,
                    },
                ]
            }
        ],
        "action": {"mitigate": {"action": "bypass"}},
    }


def _normalize_condition(condition: dict[str, Any]) -> dict[str, Any]:
    result = {"type": condition.get("type"), "op": condition.get("op")}
    for key in ("key", "value"):
        if key in condition:
            result[key] = condition[key]
    if condition.get("neg") is True:
        result["neg"] = True
    return result


def normalize_rule(rule: dict[str, Any], *, include_id: bool = False) -> dict[str, Any]:
    normalized = {
        "name": str(rule.get("name") or ""),
        "description": str(rule.get("description") or ""),
        "active": rule.get("active") is not False,
        "conditionGroup": [
            {
                "conditions": [
                    _normalize_condition(condition)
                    for condition in group.get("conditions", [])
                ]
            }
            for group in rule.get("conditionGroup", [])
        ],
        "action": {
            "mitigate": {
                key: value
                for key, value in (rule.get("action", {}).get("mitigate", {}) or {}).items()
                if value is not None
            }
        },
    }
    if include_id:
        normalized["id"] = str(rule.get("id") or "")
    return normalized


def config_sha256(rules: list[dict[str, Any]]) -> str:
    canonical = [normalize_rule(rule, include_id=True) for rule in rules]
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
