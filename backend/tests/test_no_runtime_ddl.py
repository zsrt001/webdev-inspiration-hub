"""Alembic is the only permitted database schema writer."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re
import unittest
from unittest.mock import AsyncMock, patch

from app.core.config import Settings


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
RUNTIME_ROOTS = (BACKEND_ROOT / "app", REPO_ROOT / "api")
DDL_PATTERN = re.compile(
    r"(?:metadata\.create_all|\bCREATE\s+TABLE\b|\bCREATE\s+(?:UNIQUE\s+)?INDEX\b|\bALTER\s+TABLE\b|\bDROP\s+(?:TABLE|INDEX)\b)",
    re.IGNORECASE,
)


class NoRuntimeDdlTest(unittest.TestCase):
    def test_runtime_python_contains_no_schema_writers(self) -> None:
        hits: list[str] = []
        for runtime_root in RUNTIME_ROOTS:
            for path in sorted(runtime_root.rglob("*.py")):
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if DDL_PATTERN.search(line):
                        hits.append(f"{path.relative_to(REPO_ROOT)}:{line_number}: {line.strip()}")

        self.assertEqual(hits, [], "Runtime DDL is forbidden; use Alembic:\n" + "\n".join(hits))

    def test_credit_runtime_guardrail_writers_are_removed(self) -> None:
        source = (BACKEND_ROOT / "app" / "services" / "credit_service.py").read_text(encoding="utf-8")

        self.assertNotIn("ensure_credit_guardrails", source)
        self.assertNotIn("ensure_generation_refund_guardrails", source)


class ColdStartNoDdlTest(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_does_not_open_schema_transaction_even_when_legacy_flag_is_true(self) -> None:
        from app import main

        begin_calls = 0

        @asynccontextmanager
        async def fake_begin():
            nonlocal begin_calls
            begin_calls += 1
            connection = AsyncMock()
            yield connection

        fake_engine = AsyncMock()
        fake_engine.begin = fake_begin

        with (
            patch.object(main, "settings", Settings(_env_file=None, debug=True, auto_create_tables=True)),
            patch("app.core.database.engine", fake_engine),
        ):
            async with main.lifespan(main.app):
                pass

        self.assertEqual(begin_calls, 0)

    async def test_user_schema_guard_uses_read_only_catalog_queries(self) -> None:
        from app.services import schema_guard_service

        schema_guard_service._runtime_schema_validated = False
        schema_guard_service._user_account_schema_validated = False
        statements: list[str] = []
        results = iter(
            [
                [schema_guard_service._MINIMUM_SCHEMA_REVISION],
                sorted(schema_guard_service._REQUIRED_TABLES),
                sorted(schema_guard_service._REQUIRED_INDEXES),
                sorted(schema_guard_service._REQUIRED_USER_COLUMNS),
                sorted(schema_guard_service._REQUIRED_CLICK_STATS_COLUMNS),
            ]
        )

        class FakeScalars:
            def __init__(self, values):
                self.values = values

            def all(self):
                return self.values

        class FakeResult:
            def __init__(self, values):
                self.values = values

            def scalars(self):
                return FakeScalars(self.values)

        class FakeDb:
            async def execute(self, statement):
                statements.append(str(statement))
                return FakeResult(next(results))

        try:
            await schema_guard_service.ensure_user_account_columns(FakeDb())
        finally:
            schema_guard_service._runtime_schema_validated = False
            schema_guard_service._user_account_schema_validated = False

        self.assertEqual(len(statements), 5)
        self.assertTrue(all(statement.lstrip().upper().startswith("SELECT") for statement in statements))
        self.assertTrue(all(DDL_PATTERN.search(statement) is None for statement in statements))

    async def test_schema_validation_fails_traceably_instead_of_repairing_or_continuing(self) -> None:
        from app.services import schema_guard_service

        schema_guard_service._runtime_schema_validated = False
        schema_guard_service._user_account_schema_validated = False

        class EmptyScalars:
            def all(self):
                return []

        class EmptyResult:
            def scalars(self):
                return EmptyScalars()

        class EmptyDb:
            async def execute(self, _statement):
                return EmptyResult()

        with self.assertRaisesRegex(RuntimeError, "database_schema_incomplete"):
            await schema_guard_service.validate_runtime_schema(EmptyDb())


if __name__ == "__main__":
    unittest.main()
