"""Pruebas del dual_writer — JSONL siempre + Postgres opcional."""

from __future__ import annotations

import json
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

# Hacer importable el paquete root del proyecto sin instalar como paquete.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dual_writer  # noqa: E402  — sys.path tweak debe ir antes


class LogAssetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_logs")
        # Limpieza defensiva — ningún test debe ver basura del anterior.
        if self._tmp.exists():
            for p in self._tmp.glob("*.jsonl"):
                p.unlink()
        self._patcher = mock.patch.object(dual_writer, "_LOG_DIR", self._tmp)
        self._patcher.start()
        # Resetear cache de PG para que no se filtre estado entre suites.
        dual_writer._pg_available = False

    def tearDown(self) -> None:
        self._patcher.stop()

    def test_log_asset_writes_jsonl(self) -> None:
        asset = {
            "id": "AST-001",
            "product": "CrowdStrike Falcon",
            "vendor": "CrowdStrike",
            "annual_cost_usd": 50000,
        }
        ok = dual_writer.log_asset(asset)
        self.assertTrue(ok)
        log_path = self._tmp / "assets.jsonl"
        self.assertTrue(log_path.exists())
        line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        record = json.loads(line)
        self.assertEqual(record["id"], "AST-001")
        self.assertEqual(record["product"], "CrowdStrike Falcon")

    def test_log_asset_returns_true_when_log_to_file(self) -> None:
        self.assertTrue(dual_writer.log_asset({"id": "AST-002"}))

    def test_log_asset_returns_false_when_path_unwritable(self) -> None:
        # Apunta el log dir a una ruta dentro de /dev/null (no es directorio
        # creable). mkdir(parents=True, exist_ok=True) y open() ambos fallan.
        with mock.patch.object(dual_writer, "_LOG_DIR", Path("/dev/null/nope")):
            with self.assertLogs("cafom.writer", level=logging.ERROR):
                ok = dual_writer.log_asset({"id": "AST-003"})
        self.assertFalse(ok)

    def test_log_asset_skips_jsonl_when_log_to_file_false(self) -> None:
        # log_to_file=False → no JSONL → success queda False (no PG en tests).
        ok = dual_writer.log_asset({"id": "AST-004"}, log_to_file=False)
        self.assertFalse(ok)
        self.assertFalse((self._tmp / "assets.jsonl").exists())

    def test_log_vendor_check_writes_separate_file(self) -> None:
        ok = dual_writer.log_vendor_check(
            "https://api.okta.com",
            "Healthy",
            response_ms=42.0,
            status_code=200,
        )
        self.assertTrue(ok)
        log_path = self._tmp / "vendor_checks.jsonl"
        self.assertTrue(log_path.exists())
        record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(record["vendor_url"], "https://api.okta.com")
        self.assertEqual(record["status"], "Healthy")
        self.assertEqual(record["status_code"], 200)
        self.assertAlmostEqual(record["response_ms"], 42.0)

    def test_log_portfolio_refresh_writes_separate_file(self) -> None:
        summary = {"total_assets": 12, "annual_spend_usd": 500_000, "renewals_30d": 3}
        ok = dual_writer.log_portfolio_refresh(summary)
        self.assertTrue(ok)
        log_path = self._tmp / "portfolio_snapshots.jsonl"
        record = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(record["total_assets"], 12)


class PgAvailabilityTest(unittest.TestCase):
    def test_pg_available_is_false_when_module_missing(self) -> None:
        # Reset y forzar reevaluación.
        dual_writer._pg_available = None
        self.assertFalse(dual_writer.pg_available())

    def test_pg_available_caches_result(self) -> None:
        dual_writer._pg_available = None
        first = dual_writer.pg_available()
        # Forzar que la siguiente llamada NO reintente import: si lo
        # hiciera, el segundo None reactivaría el except. Lo confirmamos
        # comprobando que el atributo quedó cacheado a False.
        self.assertEqual(first, False)
        self.assertEqual(dual_writer._pg_available, False)


if __name__ == "__main__":
    unittest.main()
