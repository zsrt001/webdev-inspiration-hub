"""Narrow Vercel Firewall API adapter with exact-draft and readback enforcement."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from scripts.release.edge_lockdown_contract import MANAGED_RULE_NAMES, normalize_rule


class EdgeLockdownError(RuntimeError):
    pass


class VercelFirewallApi:
    def __init__(self, *, token: str, project_id: str, team_id: str) -> None:
        self.project_id = project_id
        self.team_id = team_id
        self.client = httpx.Client(
            base_url="https://api.vercel.com",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    @property
    def params(self) -> dict[str, str]:
        return {"projectId": self.project_id, "teamId": self.team_id}

    def close(self) -> None:
        self.client.close()

    def config(self) -> dict[str, Any]:
        response = self.client.get("/v1/security/firewall/config", params=self.params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise EdgeLockdownError("Vercel firewall returned invalid JSON")
        return payload

    def patch(self, *, action: str, rule_id: str | None, value: Any) -> None:
        response = self.client.patch(
            "/v1/security/firewall/config/draft",
            params=self.params,
            json={"action": action, "id": rule_id, "value": value},
        )
        response.raise_for_status()

    def publish(self) -> None:
        response = self.client.post(
            "/v1/security/firewall/config/draft/activate",
            params=self.params,
            json={},
        )
        response.raise_for_status()

    def discard(self) -> None:
        response = self.client.delete(
            "/v1/security/firewall/config/draft",
            params=self.params,
        )
        if response.status_code not in {200, 204, 404}:
            response.raise_for_status()

    def project(self) -> dict[str, Any]:
        response = self.client.get(
            f"/v9/projects/{quote(self.project_id, safe='')}",
            params={"teamId": self.team_id},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise EdgeLockdownError("Vercel project returned invalid JSON")
        return payload

    def generate_automation_bypass(self, *, secret: str, note: str) -> dict[str, Any]:
        response = self.client.patch(
            f"/v1/projects/{quote(self.project_id, safe='')}/protection-bypass",
            params={"teamId": self.team_id},
            json={"generate": {"secret": secret, "note": note}},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise EdgeLockdownError("Vercel protection bypass returned invalid JSON")
        return payload

    def last_production_deployment_id(self) -> str:
        response = self.client.get(
            "/v6/deployments",
            params={**self.params, "target": "production", "limit": "1"},
        )
        response.raise_for_status()
        payload = response.json()
        deployments = payload.get("deployments") if isinstance(payload, dict) else None
        if not isinstance(deployments, list) or not deployments:
            raise EdgeLockdownError("Vercel has no known Production deployment")
        deployment_id = str(deployments[0].get("uid") or deployments[0].get("id") or "")
        if not deployment_id or len(deployment_id) > 200 or not deployment_id.replace("-", "").replace("_", "").isalnum():
            raise EdgeLockdownError("Vercel last Production deployment ID is invalid")
        return deployment_id


def active_rules(config: dict[str, Any], *, require_clean_draft: bool) -> list[dict[str, Any]]:
    active = config.get("active")
    if not isinstance(active, dict) or not isinstance(active.get("rules"), list):
        raise EdgeLockdownError("Vercel active firewall configuration is malformed")
    draft = config.get("draft")
    changes = draft.get("changes", []) if isinstance(draft, dict) else []
    if require_clean_draft and changes:
        raise EdgeLockdownError("unpublished Vercel firewall changes already exist")
    rules = active["rules"]
    if not all(isinstance(rule, dict) for rule in rules):
        raise EdgeLockdownError("Vercel firewall rule list is malformed")
    return rules


def _draft_rules(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    draft = config.get("draft")
    if not isinstance(draft, dict) or not isinstance(draft.get("rules"), list):
        raise EdgeLockdownError("Vercel did not create a firewall draft")
    changes = draft.get("changes")
    if not isinstance(changes, list) or not changes:
        raise EdgeLockdownError("Vercel firewall draft has no recorded changes")
    return draft["rules"], changes


def assert_managed_only(rules: list[dict[str, Any]]) -> None:
    names = [str(rule.get("name") or "") for rule in rules]
    if len(names) != len(set(names)):
        raise EdgeLockdownError("duplicate Vercel custom-rule names are forbidden")
    unexpected = sorted(set(names) - MANAGED_RULE_NAMES)
    if unexpected:
        raise EdgeLockdownError(f"unrelated Vercel custom rules are present: {unexpected}")


def _same_rule(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return normalize_rule(left) == normalize_rule(right)


def assert_target_rules(actual: list[dict[str, Any]], target: list[dict[str, Any]]) -> None:
    if len(actual) != len(target):
        raise EdgeLockdownError("Vercel firewall rule count does not match the protected target")
    for observed, expected in zip(actual, target, strict=True):
        if not _same_rule(observed, expected):
            raise EdgeLockdownError(
                f"Vercel firewall rule readback mismatch for {expected.get('name')}"
            )


def apply_target_rules(api: VercelFirewallApi, target: list[dict[str, Any]]) -> list[dict[str, Any]]:
    initial = active_rules(api.config(), require_clean_draft=True)
    assert_managed_only(initial)
    current_by_name = {str(rule["name"]): rule for rule in initial}
    target_by_name = {str(rule["name"]): rule for rule in target}
    if len(target_by_name) != len(target):
        raise EdgeLockdownError("target firewall rule names are not unique")
    if len(initial) == len(target) and all(
        str(observed.get("name")) == str(expected.get("name"))
        and _same_rule(observed, expected)
        for observed, expected in zip(initial, target, strict=True)
    ):
        return initial

    own_draft = False
    try:
        for name, observed in current_by_name.items():
            if name not in target_by_name:
                api.patch(action="rules.remove", rule_id=str(observed.get("id") or ""), value=None)
                own_draft = True
        for name, expected in target_by_name.items():
            observed = current_by_name.get(name)
            if observed is None:
                api.patch(action="rules.insert", rule_id=None, value=expected)
                own_draft = True
            elif not _same_rule(observed, expected):
                api.patch(
                    action="rules.update",
                    rule_id=str(observed.get("id") or ""),
                    value=expected,
                )
                own_draft = True

        if own_draft:
            staged, changes = _draft_rules(api.config())
        else:
            staged, changes = [dict(rule) for rule in initial], []
        staged_by_name = {str(rule.get("name") or ""): rule for rule in staged}
        if set(staged_by_name) != set(target_by_name):
            raise EdgeLockdownError("staged Vercel firewall names do not match the target")
        for target_position, expected in enumerate(target):
            current_position = next(
                index
                for index, rule in enumerate(staged)
                if str(rule.get("name") or "") == str(expected["name"])
            )
            if current_position != target_position:
                api.patch(
                    action="rules.priority",
                    rule_id=str(staged[current_position].get("id") or ""),
                    value=target_position,
                )
                own_draft = True
                staged, changes = _draft_rules(api.config())
        if not own_draft:
            raise EdgeLockdownError("firewall target differs but no draft change was produced")
        staged, changes = _draft_rules(api.config())
        assert_target_rules(staged, target)
        allowed_actions = {"rules.insert", "rules.update", "rules.remove", "rules.priority"}
        if any(str(change.get("action") or "") not in allowed_actions for change in changes):
            raise EdgeLockdownError("Vercel firewall draft contains an unexpected change")
        api.publish()
        active = active_rules(api.config(), require_clean_draft=True)
        assert_target_rules(active, target)
        return active
    except Exception:
        if own_draft:
            try:
                api.discard()
            except Exception:
                pass
        raise


def verify_project_safety(api: VercelFirewallApi) -> dict[str, Any]:
    project = api.project()
    if (
        str(project.get("id") or "") != api.project_id
        or str(project.get("accountId") or "") != api.team_id
    ):
        raise EdgeLockdownError("Vercel project resolved outside the protected project or team")
    if project.get("autoAssignCustomDomains") is not False:
        raise EdgeLockdownError("Vercel Production domain auto-assignment is not disabled")
    link = project.get("link") if isinstance(project.get("link"), dict) else {}
    deploy_hooks = link.get("deployHooks", [])
    if not isinstance(deploy_hooks, list):
        raise EdgeLockdownError("Vercel deploy-hook inventory is unavailable")
    active_hooks = [hook for hook in deploy_hooks if not isinstance(hook, dict) or hook.get("active", True)]
    if active_hooks:
        raise EdgeLockdownError("active Vercel deploy hooks remain")
    return {
        "auto_assign_custom_production_domains": False,
        "active_deploy_hook_count": 0,
    }
