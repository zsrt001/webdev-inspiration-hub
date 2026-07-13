"""Real PostgreSQL proof for the stamped-0012 click_stats repair migration."""

from __future__ import annotations

import os
from pathlib import Path
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError


ROOT = Path(__file__).resolve().parents[3]
PRE_REPAIR_REVISION = "20260710_0013"
REPAIR_REVISION = "20260712_0014"


@unittest.skipUnless(
    os.environ.get("RUN_CLICK_STATS_REPAIR_INTEGRATION") == "1",
    "set RUN_CLICK_STATS_REPAIR_INTEGRATION=1 with CLICK_STATS_REPAIR_TEST_DATABASE_URL",
)
class ClickStatsRepairPostgresIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_url = os.environ.get(
            "CLICK_STATS_REPAIR_TEST_DATABASE_URL", ""
        ).strip()
        if not cls.database_url:
            raise unittest.SkipTest("CLICK_STATS_REPAIR_TEST_DATABASE_URL is missing")
        parsed = make_url(cls.database_url)
        if parsed.host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("click_stats repair integration requires a local PostgreSQL host")
        if not (parsed.database or "").endswith("_test"):
            raise RuntimeError("click_stats repair integration requires a *_test database")
        cls.engine = create_engine(cls.database_url, pool_pre_ping=True)

    @classmethod
    def tearDownClass(cls) -> None:
        engine = getattr(cls, "engine", None)
        if engine is None:
            return
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS click_stats"))
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()

    def setUp(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS click_stats"))
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
            connection.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": PRE_REPAIR_REVISION},
            )
            connection.execute(
                text(
                    "CREATE TABLE click_stats ("
                    "id BIGSERIAL PRIMARY KEY, event_type VARCHAR(64) NOT NULL)"
                )
            )

    def _upgrade(self) -> None:
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, REPAIR_REVISION)

    def _shape(self) -> dict[str, tuple[str, str, str | None]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'click_stats' "
                    "AND column_name IN ('value_sum', 'value_count') "
                    "ORDER BY column_name"
                )
            ).all()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def _revision(self) -> str:
        with self.engine.connect() as connection:
            return str(
                connection.execute(text("SELECT version_num FROM alembic_version"))
                .scalar_one()
            )

    def _assert_repaired_shape(self) -> None:
        self.assertEqual(self._revision(), REPAIR_REVISION)
        shape = self._shape()
        self.assertEqual(set(shape), {"value_sum", "value_count"})
        for column in ("value_sum", "value_count"):
            data_type, is_nullable, column_default = shape[column]
            self.assertEqual(data_type, "integer")
            self.assertEqual(is_nullable, "NO")
            self.assertIn("0", column_default or "")

    def test_missing_columns_are_added_with_safe_values(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO click_stats (event_type) VALUES ('missing')")
            )

        self._upgrade()

        self._assert_repaired_shape()
        with self.engine.connect() as connection:
            values = connection.execute(
                text("SELECT value_sum, value_count FROM click_stats")
            ).one()
        self.assertEqual(tuple(values), (0, 0))

    def test_existing_correct_columns_are_idempotent(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE click_stats "
                    "ADD COLUMN value_sum INTEGER NOT NULL DEFAULT 0, "
                    "ADD COLUMN value_count INTEGER NOT NULL DEFAULT 0"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO click_stats "
                    "(event_type, value_sum, value_count) "
                    "VALUES ('existing', 17, 3)"
                )
            )

        self._upgrade()

        self._assert_repaired_shape()
        with self.engine.connect() as connection:
            values = connection.execute(
                text("SELECT value_sum, value_count FROM click_stats")
            ).one()
        self.assertEqual(tuple(values), (17, 3))

    def test_nullable_columns_are_backfilled_and_hardened(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE click_stats "
                    "ADD COLUMN value_sum INTEGER, "
                    "ADD COLUMN value_count INTEGER"
                )
            )
            connection.execute(
                text("INSERT INTO click_stats (event_type) VALUES ('nullable')")
            )

        self._upgrade()

        self._assert_repaired_shape()
        with self.engine.connect() as connection:
            values = connection.execute(
                text("SELECT value_sum, value_count FROM click_stats")
            ).one()
        self.assertEqual(tuple(values), (0, 0))

    def test_incompatible_type_rolls_back_without_advancing_revision(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE click_stats "
                    "ADD COLUMN value_sum BIGINT NOT NULL DEFAULT 0, "
                    "ADD COLUMN value_count INTEGER NOT NULL DEFAULT 0"
                )
            )

        with self.assertRaises(DBAPIError):
            self._upgrade()

        self.assertEqual(self._revision(), PRE_REPAIR_REVISION)
        self.assertEqual(self._shape()["value_sum"][0], "bigint")


if __name__ == "__main__":
    unittest.main()
