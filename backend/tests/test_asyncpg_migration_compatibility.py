"""Asyncpg compatibility checks for PostgreSQL migration DDL."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = tuple(
    ROOT / f"backend/alembic/versions/20260710_{revision}_{name}.py"
    for revision, name in (
        ("0016", "commercial_ledger"),
        ("0017", "creem_payment_facts"),
        ("0018", "subscription_facts"),
        ("0019", "generation_jobs"),
        ("0020", "partner_consent"),
    )
)
CREATE_DDL = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+FUNCTION|(?:CONSTRAINT\s+)?TRIGGER)\b",
    re.IGNORECASE | re.MULTILINE,
)


class AsyncpgMigrationCompatibilityTest(unittest.TestCase):
    def test_each_execute_contains_only_one_function_or_trigger_statement(self) -> None:
        violations: list[str] = []

        for migration in MIGRATIONS:
            source = migration.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                if call.func.attr != "execute" or not call.args:
                    continue
                text_call = call.args[0]
                if not isinstance(text_call, ast.Call) or not text_call.args:
                    continue
                sql = text_call.args[0]
                if not isinstance(sql, ast.Constant) or not isinstance(sql.value, str):
                    continue
                statement_count = len(CREATE_DDL.findall(sql.value))
                if statement_count > 1:
                    violations.append(
                        f"{migration.name}:{call.lineno} contains {statement_count} DDL statements"
                    )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
