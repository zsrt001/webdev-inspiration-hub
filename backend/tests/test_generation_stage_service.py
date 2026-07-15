"""Generation stage state helpers."""

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.generation_stage_service import merge_generation_stage  # noqa: E402


class GenerationStageServiceTest(unittest.TestCase):
    def test_merge_generation_stage_tracks_compact_history(self) -> None:
        params = merge_generation_stage({}, "queued")
        params = merge_generation_stage(params, "identity_refs_ready")
        params = merge_generation_stage(params, "identity_refs_ready")

        self.assertEqual(params["generation_stage"], "identity_refs_ready")
        self.assertEqual([item["stage"] for item in params["generation_stage_history"]], ["queued", "identity_refs_ready"])
        self.assertTrue(params["generation_stage_history"][0]["at"])

    def test_unknown_stage_fails_closed(self) -> None:
        with self.assertRaises(ValueError) as raised:
            merge_generation_stage({"generation_stage_history": []}, "bad_stage")

        self.assertEqual(str(raised.exception), "unknown_generation_stage:bad_stage")


if __name__ == "__main__":
    unittest.main()
