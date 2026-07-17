from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import httpx

from scripts.release import manage_edge_lockdown as edge
from scripts.release import edge_lockdown_probes as probes


HOST = "www.vowpic.com"
UUID = "00000000-0000-0000-0000-000000000000"
ROOT = Path(__file__).resolve().parents[2]


def _matches(group: str, method: str, path: str, host: str = HOST) -> bool:
    for condition_group in edge.route_condition_groups(HOST)[group]:
        conditions = {condition["type"]: condition for condition in condition_group["conditions"]}
        if conditions["host"]["value"] != host:
            continue
        if conditions["method"]["value"] != method:
            continue
        if re.fullmatch(conditions["raw_path"]["value"], path):
            return True
    return False


class FakeFirewallApi:
    def __init__(self, rules: list[dict] | None = None) -> None:
        self.active = copy.deepcopy(rules or [])
        self.draft: dict | None = None
        self.next_id = 1

    def config(self) -> dict:
        return {
            "active": {"rules": copy.deepcopy(self.active)},
            "draft": copy.deepcopy(self.draft),
        }

    def patch(self, *, action: str, rule_id: str | None, value) -> None:
        if self.draft is None:
            self.draft = {"rules": copy.deepcopy(self.active), "changes": []}
        rules = self.draft["rules"]
        if action == "rules.insert":
            inserted = copy.deepcopy(value)
            inserted["id"] = f"rule_{self.next_id}"
            self.next_id += 1
            rules.append(inserted)
        elif action == "rules.update":
            index = next(index for index, rule in enumerate(rules) if rule["id"] == rule_id)
            updated = copy.deepcopy(value)
            updated["id"] = rule_id
            rules[index] = updated
        elif action == "rules.remove":
            self.draft["rules"] = [rule for rule in rules if rule["id"] != rule_id]
        elif action == "rules.priority":
            index = next(index for index, rule in enumerate(rules) if rule["id"] == rule_id)
            moved = rules.pop(index)
            rules.insert(int(value), moved)
        else:
            raise AssertionError(action)
        self.draft["changes"].append({"action": action, "id": rule_id})

    def publish(self) -> None:
        assert self.draft is not None
        self.active = copy.deepcopy(self.draft["rules"])
        self.draft = None

    def discard(self) -> None:
        self.draft = None


class FakeProjectApi:
    project_id = "prj_Example123"
    team_id = "team_Example123"

    def __init__(self, project: dict) -> None:
        self._project = project

    def project(self) -> dict:
        return copy.deepcopy(self._project)


class EdgeRouteContractTest(unittest.TestCase):
    def test_lockdown_http_proves_deny_rules_and_fail_closed_runner_bypass(self) -> None:
        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def request(self, method, path, *, headers=None, json=None):
                del method, json
                if probes.BYPASS_HEADER_NAME in (headers or {}):
                    return httpx.Response(503, json={"code": "runtime_not_ready"})
                if path in {probe.path for probe in probes.APPLICATION_PROBES.values()}:
                    return httpx.Response(403)
                return httpx.Response(200)

        with patch.object(probes.httpx, "Client", return_value=FakeClient()):
            evidence = probes.verify_lockdown_http("https://www.vowpic.com", "a" * 48)

        self.assertEqual(set(evidence["groups"]), edge.EDGE_ROUTE_GROUPS)
        self.assertTrue(all(item["status"] == 403 for item in evidence["groups"].values()))
        self.assertEqual(evidence["runner_bypass_status"], 503)
        self.assertEqual(evidence["runner_bypass_code"], "runtime_not_ready")
        self.assertTrue(all(status != 403 for status in evidence["preserved_paths"].values()))

    def test_initial_bypass_accepts_the_exact_fail_closed_runtime_boundary(self) -> None:
        client = Mock()
        client.request.return_value = httpx.Response(
            503,
            json={"code": "runtime_not_ready"},
        )
        response = probes.poll_probe(
            client,
            probes.APPLICATION_PROBES["auth_upload"],
            extra_headers={probes.BYPASS_HEADER_NAME: "a" * 48},
            expected_status=410,
            expected_code="auth_method_retired",
            alternate_outcomes=((503, "runtime_not_ready"),),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(probes.response_code(response), "runtime_not_ready")

    def test_handoff_probe_does_not_accept_the_bootstrap_runtime_boundary(self) -> None:
        client = Mock()
        client.request.return_value = httpx.Response(
            503,
            json={"code": "runtime_not_ready"},
        )
        with patch.object(probes.time, "sleep", return_value=None), self.assertRaisesRegex(
            edge.EdgeLockdownError,
            "expected 410/auth_method_retired, observed 503/runtime_not_ready",
        ):
            probes.poll_probe(
                client,
                probes.APPLICATION_PROBES["auth_upload"],
                extra_headers=None,
                expected_status=410,
                expected_code="auth_method_retired",
            )
        self.assertEqual(client.request.call_count, 6)

    def test_direct_script_entrypoint_resolves_repository_package(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/release/manage_edge_lockdown.py"), "--help"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("cleanup-bypass", completed.stdout)

    def test_project_safety_uses_the_current_vercel_project_fields(self) -> None:
        project = {
            "id": FakeProjectApi.project_id,
            "accountId": FakeProjectApi.team_id,
            "autoAssignCustomDomains": False,
            "link": {},
        }
        self.assertEqual(
            edge.verify_project_safety(FakeProjectApi(project)),
            {
                "auto_assign_custom_production_domains": False,
                "active_deploy_hook_count": 0,
            },
        )
        with self.assertRaisesRegex(edge.EdgeLockdownError, "auto-assignment"):
            edge.verify_project_safety(
                FakeProjectApi(
                    {
                        **project,
                        "autoAssignCustomDomains": True,
                        "autoAssignCustomProductionDomains": False,
                    }
                )
            )
        with self.assertRaisesRegex(edge.EdgeLockdownError, "deploy hooks"):
            edge.verify_project_safety(
                FakeProjectApi({**project, "link": {"deployHooks": [{"active": True}]}})
            )

    def test_every_required_method_path_is_covered_by_its_logical_group(self) -> None:
        expected = {
            "auth_upload": [
                ("POST", "/api/v1/auth/oauth-intents"),
                ("POST", "/api/v1/auth/supabase/session"),
                ("POST", "/api/v1/auth/login"),
                ("POST", "/api/v1/media/uploads"),
                ("POST", "/api/v1/gatekeeper/check"),
                ("POST", "/api/v1/upload"),
                ("POST", "/api/v1/upload/multiple"),
                ("POST", "/api/v1/upload/delete"),
            ],
            "generation": [
                ("POST", "/api/v1/orders/create"),
                ("DELETE", f"/api/v1/orders/{UUID}"),
                ("POST", "/api/v1/admin/generation_probe"),
                ("POST", "/api/v1/admin/orders/order-any/regenerate"),
                ("POST", "/api/v1/admin/cleanup_expired_assets"),
                ("POST", "/api/v1/ops/cleanup_expired_assets"),
                ("POST", "/api/v1/ops/poll_pending_orders"),
            ],
            "credit_checkout": [
                ("GET", "/api/v1/credits/packages"),
                ("POST", "/api/v1/payments/checkout"),
                ("POST", "/api/v1/payments/purchase-any/refund"),
                ("POST", "/api/v1/admin/grant_credits"),
                ("POST", "/api/v1/credits/purchase"),
                ("POST", "/api/v1/credits/deduct"),
                ("POST", "/api/v1/credits/add"),
                ("GET", "/api/v1/payments/manual/checkout"),
                ("POST", "/api/v1/payments/manual/submit"),
                ("POST", "/api/v1/payments/manual/admin/complete"),
                ("POST", "/api/v1/payments/manual/admin/fail"),
            ],
            "subscription": [
                ("GET", "/api/v1/subscriptions/plans"),
                ("GET", "/api/v1/subscriptions/me"),
                ("POST", "/api/v1/subscriptions/checkout"),
                ("POST", "/api/v1/subscriptions/cancel"),
                ("GET", "/api/v1/admin/creem_product_check"),
                ("POST", "/api/v1/admin/creem_checkout_probe"),
            ],
            "partner_invite": [
                ("POST", "/api/v1/partner-invites"),
                ("POST", "/api/v1/partner-invites/accept"),
                ("GET", f"/api/v1/partner-invites/{UUID}"),
                ("POST", f"/api/v1/partner-invites/{UUID}/consent"),
                ("POST", f"/api/v1/partner-invites/{UUID}/order"),
                ("POST", f"/api/v1/partner-invites/{UUID}/revoke"),
                ("POST", f"/api/v1/partner-invites/{UUID}/withdraw"),
                ("POST", "/api/v1/session/create"),
                ("GET", "/api/v1/session/legacy/status"),
                ("POST", "/api/v1/session/legacy/upload/host"),
                ("POST", "/api/v1/session/legacy/upload/guest"),
                ("GET", "/api/v1/session/legacy/images"),
                ("GET", "/api/v1/session/legacy/share_meta"),
                ("POST", "/api/v1/session/legacy/processing"),
                ("POST", "/api/v1/session/legacy/complete"),
                ("POST", "/api/v1/session/legacy/bind_order"),
            ],
            "retired_addons": [
                ("POST", "/api/v1/users/"),
                ("GET", "/api/v1/users/legacy"),
                ("PATCH", "/api/v1/users/legacy"),
                ("POST", "/api/v1/live_portrait/generate"),
                ("GET", "/api/v1/live_portrait/list"),
                ("GET", "/api/v1/live_portrait/legacy"),
            ],
            "leads_recommendations": [
                ("GET", "/api/v1/recommendations/local_studios"),
                ("POST", "/api/v1/leads/submit"),
                ("GET", "/api/v1/leads/list"),
                ("GET", "/api/v1/leads/export.csv"),
                ("GET", "/api/v1/admin/crm_preview"),
                ("POST", "/api/v1/admin/crm_push"),
                ("GET", "/api/v1/admin/crm_push_history"),
            ],
        }
        self.assertEqual(set(expected), edge.EDGE_ROUTE_GROUPS)
        for group, probes in expected.items():
            for method, path in probes:
                with self.subTest(group=group, method=method, path=path):
                    self.assertTrue(_matches(group, method, path))

    def test_preserved_routes_and_wrong_method_host_do_not_match_any_deny_group(self) -> None:
        preserved = (
            ("POST", "/api/v1/payments/webhook/creem"),
            ("GET", "/api/v1/payments/status/purchase-any"),
            ("POST", "/api/v1/auth/logout"),
            ("GET", "/api/v1/ops/readiness"),
            ("GET", "/api/v1/ops/public_config"),
            ("GET", "/health"),
        )
        for method, path in preserved:
            self.assertFalse(any(_matches(group, method, path) for group in edge.EDGE_ROUTE_GROUPS))
        self.assertFalse(_matches("auth_upload", "GET", "/api/v1/auth/login"))
        self.assertFalse(_matches("auth_upload", "POST", "/api/v1/auth/login", "preview.vowpic.com"))

    def test_two_physical_rules_stay_within_hobby_limits(self) -> None:
        rules = edge.build_deny_rules(HOST)
        self.assertEqual([rule["name"] for rule in rules], list(edge.DENY_RULE_GROUPS))
        self.assertEqual(len(rules), 2)
        self.assertTrue(all(len(rule["conditionGroup"]) <= 25 for rule in rules))
        bypass = edge.build_bypass_rule(HOST, "a" * 48)
        self.assertEqual(bypass["action"], {"mitigate": {"action": "bypass"}})

    def test_target_application_is_exact_ordered_and_create_then_read_back(self) -> None:
        api = FakeFirewallApi()
        target = [edge.build_bypass_rule(HOST, "a" * 48), *edge.build_deny_rules(HOST)]
        active = edge.apply_target_rules(api, target)
        self.assertEqual([rule["name"] for rule in active], [rule["name"] for rule in target])
        self.assertTrue(all(rule.get("id") for rule in active))
        self.assertIsNone(api.draft)
        self.assertEqual(edge.apply_target_rules(api, target), active)

    def test_target_application_repairs_order_only_drift(self) -> None:
        target = [edge.build_bypass_rule(HOST, "a" * 48), *edge.build_deny_rules(HOST)]
        existing = []
        for index, rule in enumerate([*target[1:], target[0]], start=1):
            existing.append({**copy.deepcopy(rule), "id": f"existing_{index}"})
        api = FakeFirewallApi(existing)
        active = edge.apply_target_rules(api, target)
        self.assertEqual([rule["name"] for rule in active], [rule["name"] for rule in target])
        self.assertIsNone(api.draft)

    def test_unrelated_custom_rule_fails_before_mutation(self) -> None:
        api = FakeFirewallApi(
            [
                {
                    "id": "unrelated",
                    "name": "owner-rule",
                    "active": True,
                    "conditionGroup": [],
                    "action": {"mitigate": {"action": "log"}},
                }
            ]
        )
        with self.assertRaisesRegex(edge.EdgeLockdownError, "unrelated"):
            edge.apply_target_rules(api, edge.build_deny_rules(HOST))
        self.assertIsNone(api.draft)


if __name__ == "__main__":
    unittest.main()
