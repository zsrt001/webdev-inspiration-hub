#!/usr/bin/env python3
"""Bind six signed human reviews to exact PostgreSQL generation facts."""

from __future__ import annotations

from typing import Any

from scripts.release._acceptance_phase_facts import _coordinate, _one


def collect_quality(
    cursor,
    *,
    browser: dict[str, Any],
    human_review: dict[str, Any],
    cases_contract: dict[str, Any],
    rubric: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        cases_contract.get("schema") != "vowpic.quality-cases.v1"
        or rubric.get("schema") != "vowpic.quality-rubric.v1"
    ):
        raise ValueError("quality cases/rubric contract is invalid")
    expected_ids = [item.get("id") for item in cases_contract.get("cases", [])]
    if len(expected_ids) != 6 or len(set(expected_ids)) != 6:
        raise ValueError("quality contract must contain exactly six unique cases")
    browser_cases = browser.get("cases")
    review_cases = human_review.get("cases")
    if not isinstance(browser_cases, list) or not isinstance(review_cases, list):
        raise ValueError("quality browser/reviewer case sets are missing")
    browser_by_id = {
        item.get("id"): item for item in browser_cases if isinstance(item, dict)
    }
    review_by_id = {
        item.get("id"): item for item in review_cases if isinstance(item, dict)
    }
    if set(browser_by_id) != set(expected_ids) or set(review_by_id) != set(expected_ids):
        raise ValueError("quality browser/reviewer case sets are not exact")
    if human_review.get("review_complete") is not True:
        raise ValueError("quality human review is incomplete")
    user_id = _coordinate(browser.get("user_id"), "quality user")
    dimensions = list(rubric.get("dimensions") or [])
    results: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    for case_id in expected_ids:
        browser_case = browser_by_id[case_id]
        review = review_by_id[case_id]
        if set(browser_case) != {"id", "order_id"}:
            raise ValueError(f"quality browser case {case_id} fields are not exact")
        if set(review) != {
            "id",
            "order_id",
            "job_id",
            "selected_candidate_id",
            "review_asset_id",
            "reviewer_ref",
            "scores",
            "hard_defects",
            "reviewed_at",
        }:
            raise ValueError(f"quality review case {case_id} fields are not exact")
        row = _one(
            cursor,
            """
            SELECT o.id AS order_id, o.user_id, o.status AS order_status,
                   j.id AS job_id, j.status AS job_status,
                   q.candidate_asset_id AS selected_candidate_id,
                   ma.status AS candidate_status, ma.role AS candidate_role,
                   review_asset.id AS review_asset_id,
                   review_asset.status AS review_asset_status,
                   review_asset.role AS review_asset_role,
                   (SELECT count(*) FROM generation_attempts initial
                    WHERE initial.job_id = j.id AND initial.kind = 'INITIAL'
                      AND initial.result_asset_id IS NOT NULL
                   ) AS initial_candidate_count,
                   (SELECT count(*) FROM generation_attempts repair
                    WHERE repair.job_id = j.id AND repair.kind = 'REPAIR'
                      AND repair.result_asset_id IS NOT NULL
                   ) AS repair_candidate_count
            FROM orders o
            JOIN generation_jobs j ON j.id = o.generation_job_id
            JOIN qa_verdicts q ON q.job_id = j.id AND q.decision = 'PASS'
            JOIN media_assets ma ON ma.id = q.candidate_asset_id
            JOIN media_assets review_asset
              ON review_asset.id = %s
             AND review_asset.parent_asset_id = q.candidate_asset_id
             AND review_asset.owner_user_id = o.user_id
             AND o.final_asset_ids @> jsonb_build_array(review_asset.id::text)
            WHERE o.id = %s AND o.user_id = %s
              AND q.candidate_asset_id = %s
            """,
            (
                review["review_asset_id"],
                browser_case["order_id"],
                user_id,
                review["selected_candidate_id"],
            ),
            f"quality case {case_id}",
        )
        if (
            str(review["order_id"]) != str(row["order_id"])
            or str(review["job_id"]) != str(row["job_id"])
            or str(review["selected_candidate_id"])
            != str(row["selected_candidate_id"])
            or str(review["review_asset_id"]) != str(row["review_asset_id"])
            or row["order_status"] != "READY"
            or row["job_status"] != "FINISHED"
            or row["candidate_status"] != "ACTIVE"
            or row["candidate_role"] != "candidate"
            or row["review_asset_status"] != "ACTIVE"
            or row["review_asset_role"] != "final_master"
            or int(row["initial_candidate_count"]) != 1
            or int(row["repair_candidate_count"]) < 0
            or int(row["repair_candidate_count"]) > 2
        ):
            raise ValueError(f"quality case {case_id} generation lineage is invalid")
        scores = review.get("scores")
        hard_defects = review.get("hard_defects")
        if (
            not isinstance(scores, dict)
            or set(scores) != set(dimensions)
            or not isinstance(hard_defects, list)
        ):
            raise ValueError(f"quality case {case_id} human rubric is invalid")
        numeric = [scores[name] for name in dimensions]
        if not all(
            isinstance(score, int)
            and rubric["score_minimum"] <= score <= rubric["score_maximum"]
            for score in numeric
        ):
            raise ValueError(f"quality case {case_id} score is outside the rubric")
        average = sum(numeric) / len(numeric)
        passed = (
            not hard_defects
            and average >= rubric["average_minimum"]
            and all(score >= rubric["dimension_minimum"] for score in numeric)
        )
        results.append(
            {
                "id": case_id,
                "order_id": _coordinate(row["order_id"], f"{case_id} order"),
                "job_id": _coordinate(row["job_id"], f"{case_id} job"),
                "status": "READY",
                "initial_candidate_count": 1,
                "repair_candidate_count": int(row["repair_candidate_count"]),
                "selected_candidate_id": _coordinate(
                    row["selected_candidate_id"], f"{case_id} candidate"
                ),
                "review_asset_id": _coordinate(
                    row["review_asset_id"], f"{case_id} review asset"
                ),
                "reviewer_ref": _coordinate(
                    review["reviewer_ref"], f"{case_id} reviewer"
                ),
                "scores": scores,
                "hard_defects": hard_defects,
                "passed": passed,
            }
        )
        facts.append({"case": row, "reviewed_at": review["reviewed_at"]})
    return {
        "schema": "vowpic.quality-acceptance-input.v1",
        "source_sha": browser["source_sha"],
        "runtime_bundle_id": browser["runtime_bundle_id"],
        "deployment_id": browser["deployment_id"],
        "manifest_sha256": browser["manifest_sha256"],
        "user_subject_hmac_sha256": browser["user_subject_hmac_sha256"],
        "cases": results,
    }, {
        "cases": facts,
        "human_review_produced_at": human_review.get("produced_at"),
    }
