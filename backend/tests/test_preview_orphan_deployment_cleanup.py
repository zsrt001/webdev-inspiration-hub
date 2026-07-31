from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release" / "delete_unbound_preview_deployment.py"
SPEC = importlib.util.spec_from_file_location("delete_unbound_preview_deployment", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Preview deployment cleanup module cannot be loaded")
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object] | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP status {self.status_code}")


class _Client:
    def __init__(self, deployment: dict[str, object]):
        self.deployment = deployment
        self.get_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        self.get_calls += 1
        if self.get_calls == 1:
            return _Response(200, self.deployment)
        return _Response(404)

    def delete(self, *_args, **_kwargs):
        return _Response(204)


class PreviewOrphanDeploymentCleanupTests(unittest.TestCase):
    source_sha = "a" * 40
    deployment_url = "https://vowpic-preview-example.vercel.app"

    def _stage(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "schema": "vowpic.preview-deploy-stage.v1",
                    "source_sha": self.source_sha,
                    "workflow_run_id": "123",
                    "workflow_attempt": 1,
                    "deploy_step_outcome": "success",
                    "deploy_attempted": True,
                    "deployment_url": self.deployment_url,
                    "deployment_url_recorded": True,
                    "deployment_bound": False,
                    "safe_predeployment_cleanup": False,
                }
            ),
            encoding="utf-8",
        )

    def test_deletes_only_the_exact_unbound_preview_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage.json"
            self._stage(stage)
            client = _Client(
                {
                    "id": "dpl_Example123",
                    "project": {"id": "prj_Example123"},
                    "meta": {
                        "vowpicSourceSha": self.source_sha,
                        "vowpicReleaseRole": "PREVIEW_IDENTITY",
                    },
                }
            )
            with mock.patch.object(cleanup.httpx, "Client", return_value=client):
                proof = cleanup.delete_unbound_preview_deployment(
                    stage_path=stage,
                    token="secret",
                    expected_project_id="prj_Example123",
                    expected_team_id="team_Example123",
                    source_sha=self.source_sha,
                    workflow_run_id="123",
                    workflow_attempt="1",
                )
            self.assertEqual(proof["state"], "DELETED")
            self.assertEqual(proof["delete_status"], 204)
            self.assertEqual(proof["readback_status"], 404)

    def test_rejects_a_deployment_from_another_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory) / "stage.json"
            self._stage(stage)
            client = _Client(
                {
                    "id": "dpl_Example123",
                    "projectId": "prj_Other123",
                    "meta": {
                        "vowpicSourceSha": self.source_sha,
                        "vowpicReleaseRole": "PREVIEW_IDENTITY",
                    },
                }
            )
            with mock.patch.object(cleanup.httpx, "Client", return_value=client):
                with self.assertRaisesRegex(ValueError, "does not match"):
                    cleanup.delete_unbound_preview_deployment(
                        stage_path=stage,
                        token="secret",
                        expected_project_id="prj_Example123",
                        expected_team_id="team_Example123",
                        source_sha=self.source_sha,
                        workflow_run_id="123",
                        workflow_attempt="1",
                    )


if __name__ == "__main__":
    unittest.main()
