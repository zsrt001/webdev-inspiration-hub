#!/usr/bin/env python3
"""Install, verify, hand off, and recover the temporary Production WAF lockdown."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx

from scripts.release.edge_lockdown_contract import (
    BYPASS_HEADER_NAME,
    BYPASS_RULE_NAME,
    DENY_RULE_GROUPS,
    EDGE_ROUTE_GROUPS,
    MANAGED_RULE_NAMES,
    build_bypass_rule,
    build_deny_rules,
    config_sha256,
    normalize_rule,
    route_condition_groups,
)
from scripts.release.edge_lockdown_probes import (
    verify_application_group,
    verify_lockdown_http,
)
from scripts.release.vercel_firewall import (
    EdgeLockdownError,
    VercelFirewallApi,
    active_rules,
    apply_target_rules,
    assert_managed_only,
    assert_target_rules,
    verify_project_safety,
)
from scripts.release.verify_edge_lockdown import compute_report_hmac


NOT_RUN_EXIT = 3
REPORT_LIFETIME = timedelta(minutes=50)


def _host_from_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("formal domain must be one HTTPS hostname without a path")
    return parsed.hostname.lower()


def _write_json_create_once(path: Path, payload: dict[str, Any], *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR if private else 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2)
            handle.write("\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


def _read_bypass_state(path: Path, host: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != "vowpic.edge-bypass-state.v1"
        or payload.get("host") != host
        or payload.get("header_name") != BYPASS_HEADER_NAME
    ):
        raise EdgeLockdownError("runner bypass state is invalid")
    return build_bypass_rule(host, str(payload.get("header_value") or ""))


def _verify_cli_version(vercel_cli: Path) -> None:
    completed = subprocess.run(
        [str(vercel_cli), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "VERCEL_DISABLE_UPDATE_CHECK": "1"},
    )
    observed = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    if observed != "56.2.0":
        raise EdgeLockdownError(f"Vercel CLI 56.2.0 is required, observed {observed or 'missing'}")


def _sign_report(payload: dict[str, Any], hmac_key: bytes) -> dict[str, Any]:
    payload["signature_hmac_sha256"] = compute_report_hmac(payload, hmac_key)
    return payload


def run_lockdown(args: argparse.Namespace, api: VercelFirewallApi, hmac_key: bytes) -> None:
    host = _host_from_base_url(args.base_url)
    project_safety = verify_project_safety(api)
    initial = active_rules(api.config(), require_clean_draft=True)
    assert_managed_only(initial)
    before_hash = config_sha256(initial)
    baseline_rules = [
        rule for rule in initial if str(rule.get("name") or "") not in MANAGED_RULE_NAMES
    ]
    baseline_hash = config_sha256(baseline_rules)
    bypass_value = secrets.token_urlsafe(48)
    active = apply_target_rules(
        api,
        [build_bypass_rule(host, bypass_value), *build_deny_rules(host)],
    )
    after_hash = config_sha256(active)
    by_name = {str(rule["name"]): rule for rule in active}
    route_groups: dict[str, dict[str, Any]] = {}
    for rule_name, groups in DENY_RULE_GROUPS.items():
        rule_id = str(by_name[rule_name].get("id") or "")
        if not rule_id:
            raise EdgeLockdownError(f"Vercel did not return a rule ID for {rule_name}")
        for group in groups:
            route_groups[group] = {"rule_id": rule_id, "denied": True, "read_back": True}
    bypass_rule_id = str(by_name[BYPASS_RULE_NAME].get("id") or "")
    if not bypass_rule_id:
        raise EdgeLockdownError("Vercel did not return the runner bypass rule ID")

    _write_json_create_once(
        Path(args.bypass_state_output),
        {
            "schema_version": "vowpic.edge-bypass-state.v1",
            "host": host,
            "header_name": BYPASS_HEADER_NAME,
            "header_value": bypass_value,
            "rule_id": bypass_rule_id,
        },
        private=True,
    )
    http_evidence = verify_lockdown_http(args.base_url, bypass_value)
    now = datetime.now(timezone.utc)
    expires_at = now + REPORT_LIFETIME
    report = _sign_report(
        {
            "schema_version": "vowpic.edge-lockdown.v1",
            "passed": True,
            "source_sha": args.source_sha,
            "workflow_run_id": args.workflow_run_id,
            "workflow_attempt": args.workflow_attempt,
            "project_id": args.project_id,
            "formal_domain": host,
            "generated_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            **project_safety,
            "last_known_deployment_id": api.last_production_deployment_id(),
            "before_config_sha256": before_hash,
            "after_config_sha256": after_hash,
            "baseline_config_sha256": baseline_hash,
            "route_groups": route_groups,
            "runner_bypass": {
                "rule_id": bypass_rule_id,
                "read_back": True,
                "lease_expires_at": expires_at.isoformat(),
            },
            "http": http_evidence,
        },
        hmac_key,
    )
    _write_json_create_once(Path(args.output), report)


def _application_evidence(
    args: argparse.Namespace,
    group: str,
    database_url: str,
) -> dict[str, Any]:
    return verify_application_group(
        args.base_url,
        group,
        database_url=database_url,
        expected_schema=args.expected_schema,
        source_sha=args.source_sha,
        runtime_bundle_id=args.runtime_bundle_id,
    )


def _clean_retry_handoff(
    args: argparse.Namespace,
    database_url: str,
) -> dict[str, dict[str, Any]]:
    return {
        group: {
            "rule_id": "clean-retry",
            "rule_removed": True,
            "read_back": True,
            **_application_evidence(args, group, database_url),
        }
        for group in sorted(EDGE_ROUTE_GROUPS)
    }


def _remove_groups_with_verification(
    args: argparse.Namespace,
    api: VercelFirewallApi,
    *,
    host: str,
    database_url: str,
    bypass_rule: dict[str, Any],
    initial: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    original_ids = {
        group: str(
            next(
                rule.get("id")
                for rule in initial
                if str(rule.get("name") or "") == rule_name
            )
        )
        for rule_name, groups in DENY_RULE_GROUPS.items()
        for group in groups
    }
    results: dict[str, dict[str, Any]] = {}
    remaining = set(EDGE_ROUTE_GROUPS)
    group_order = (
        "auth_upload",
        "generation",
        "partner_invite",
        "credit_checkout",
        "subscription",
        "retired_addons",
        "leads_recommendations",
    )
    for group in group_order:
        remaining.remove(group)
        active = apply_target_rules(api, [bypass_rule, *build_deny_rules(host, remaining)])
        removed_definitions = route_condition_groups(host)[group]
        active_condition_groups = [
            condition_group
            for rule in active
            if str(rule.get("name") or "") in DENY_RULE_GROUPS
            for condition_group in normalize_rule(rule)["conditionGroup"]
        ]
        if any(definition in active_condition_groups for definition in removed_definitions):
            raise EdgeLockdownError(f"logical firewall group {group} remains after publish")
        results[group] = {
            "rule_id": original_ids[group],
            "rule_removed": True,
            "read_back": True,
            **_application_evidence(args, group, database_url),
        }
    return apply_target_rules(api, []), results


def run_handoff(args: argparse.Namespace, api: VercelFirewallApi, hmac_key: bytes) -> None:
    host = _host_from_base_url(args.base_url)
    verify_project_safety(api)
    initial = active_rules(api.config(), require_clean_draft=True)
    assert_managed_only(initial)
    before_hash = config_sha256(initial)
    expected_lockdown_hash = str(args.expected_lockdown_after_config_sha256 or "").strip()
    expected_baseline_hash = str(args.expected_baseline_config_sha256 or "").strip()
    if expected_lockdown_hash and before_hash != expected_lockdown_hash:
        raise EdgeLockdownError("active firewall does not match the verified lockdown hash")
    if expected_baseline_hash and not re.fullmatch(r"[0-9a-f]{64}", expected_baseline_hash):
        raise ValueError("expected baseline firewall hash is invalid")
    database_url = os.environ.get(args.database_url_env, "").strip()
    if not database_url:
        raise EdgeLockdownError("the read-only Production database URL is required for handoff")

    if not expected_lockdown_hash:
        if initial:
            raise EdgeLockdownError("clean formal retry found residual managed firewall rules")
        route_results = _clean_retry_handoff(args, database_url)
        active = initial
        expected_baseline_hash = expected_baseline_hash or before_hash
    else:
        bypass_rule = _read_bypass_state(Path(args.bypass_state), host)
        full_target = [bypass_rule, *build_deny_rules(host)]
        assert_target_rules(initial, full_target)
        try:
            active, route_results = _remove_groups_with_verification(
                args,
                api,
                host=host,
                database_url=database_url,
                bypass_rule=bypass_rule,
                initial=initial,
            )
        except Exception:
            try:
                apply_target_rules(api, full_target)
            except Exception as restore_exc:
                raise EdgeLockdownError(
                    "edge handoff failed and lockdown restoration also failed: "
                    f"{type(restore_exc).__name__}"
                ) from restore_exc
            raise

    after_hash = config_sha256(active)
    expected_baseline_hash = expected_baseline_hash or config_sha256([])
    if after_hash != expected_baseline_hash:
        raise EdgeLockdownError("edge handoff did not restore the pre-lockdown firewall baseline")
    now = datetime.now(timezone.utc)
    report = _sign_report(
        {
            "schema_version": "vowpic.edge-handoff.v1",
            "passed": True,
            "source_sha": args.source_sha,
            "runtime_bundle_id": args.runtime_bundle_id,
            "deployment_id": args.deployment_id,
            "workflow_run_id": args.workflow_run_id,
            "workflow_attempt": args.workflow_attempt,
            "project_id": args.project_id,
            "formal_domain": host,
            "generated_at": now.isoformat(),
            "expires_at": (now + REPORT_LIFETIME).isoformat(),
            "route_groups": route_results,
            "runner_bypass_removed": True,
            "before_config_sha256": before_hash,
            "after_config_sha256": after_hash,
            "lockdown_after_config_sha256": before_hash,
            "lockdown_baseline_config_sha256": expected_baseline_hash,
        },
        hmac_key,
    )
    _write_json_create_once(Path(args.output), report)


def run_cleanup_bypass(api: VercelFirewallApi) -> None:
    rules = active_rules(api.config(), require_clean_draft=True)
    assert_managed_only(rules)
    target = [rule for rule in rules if str(rule.get("name") or "") != BYPASS_RULE_NAME]
    apply_target_rules(api, [normalize_rule(rule) for rule in target])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("lockdown", "handoff", "cleanup-bypass"))
    parser.add_argument("--vercel-cli", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--source-sha")
    parser.add_argument("--runtime-bundle-id")
    parser.add_argument("--deployment-id")
    parser.add_argument("--workflow-run-id")
    parser.add_argument("--workflow-attempt", type=int)
    parser.add_argument("--project-id-env", default="VERCEL_PROJECT_ID")
    parser.add_argument("--team-id-env", default="VERCEL_ORG_ID")
    parser.add_argument("--token-env", default="VERCEL_TOKEN")
    parser.add_argument("--hmac-key-env", default="EDGE_EVIDENCE_HMAC_KEY")
    parser.add_argument("--database-url-env", default="PRODUCTION_READ_ONLY_DATABASE_URL")
    parser.add_argument("--expected-schema", default="20260712_0014")
    parser.add_argument("--expected-lockdown-after-config-sha256")
    parser.add_argument("--expected-baseline-config-sha256")
    parser.add_argument("--bypass-state")
    parser.add_argument("--bypass-state-output")
    parser.add_argument("--output")
    return parser


def _validate_args(args: argparse.Namespace) -> int | None:
    required_common = (
        args.source_sha,
        args.workflow_run_id,
        args.workflow_attempt,
        args.output,
    )
    if args.action != "cleanup-bypass" and not all(required_common):
        print("NOT_RUN: release coordinates and output are required", file=sys.stderr)
        return NOT_RUN_EXIT
    if args.action == "lockdown" and not args.bypass_state_output:
        print("NOT_RUN: a private runner bypass-state output is required", file=sys.stderr)
        return NOT_RUN_EXIT
    if args.action == "handoff" and not all((args.runtime_bundle_id, args.deployment_id)):
        print("NOT_RUN: runtime and deployment coordinates are required", file=sys.stderr)
        return NOT_RUN_EXIT
    if args.action == "handoff" and args.expected_lockdown_after_config_sha256 and not args.bypass_state:
        print("NOT_RUN: the private runner bypass state is required for first handoff", file=sys.stderr)
        return NOT_RUN_EXIT
    return None


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get(args.token_env, "").strip()
    project_id = os.environ.get(args.project_id_env, "").strip()
    team_id = os.environ.get(args.team_id_env, "").strip()
    hmac_key = os.environ.get(args.hmac_key_env, "").encode("utf-8")
    if not token or not project_id or not team_id:
        print("NOT_RUN: protected Vercel token, project ID, and team ID are required", file=sys.stderr)
        return NOT_RUN_EXIT
    if args.action != "cleanup-bypass" and len(hmac_key) < 32:
        print("NOT_RUN: protected edge evidence HMAC key is required", file=sys.stderr)
        return NOT_RUN_EXIT
    invalid_result = _validate_args(args)
    if invalid_result is not None:
        return invalid_result
    args.project_id = project_id
    try:
        _verify_cli_version(Path(args.vercel_cli))
        api = VercelFirewallApi(token=token, project_id=project_id, team_id=team_id)
        try:
            if args.action == "lockdown":
                run_lockdown(args, api, hmac_key)
            elif args.action == "handoff":
                run_handoff(args, api, hmac_key)
            else:
                run_cleanup_bypass(api)
        finally:
            api.close()
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        httpx.HTTPError,
        EdgeLockdownError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"passed": True, "action": args.action, "output": args.output}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
