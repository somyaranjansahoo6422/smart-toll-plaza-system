"""Run with:  py -m unittest discover -s tests -v"""
import json
from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
from database import (  # noqa: E402
    BASE_TOLL_RATES,
    CloudSQLAdapter,
    classify_plate,
    get_dynamic_pricing,
    is_exempt,
    validate_indian_plate,
)


class PlateValidationTests(unittest.TestCase):
    def test_standard_plates(self):
        for plate in ["OD02AB1234", "od-02-ab-1234", "DL3CAB1234", "CG04X7777", "TG09A0001"]:
            self.assertEqual(classify_plate(plate), "standard", plate)

    def test_bharat_series(self):
        self.assertEqual(classify_plate("21BH1234AA"), "bharat")

    def test_diplomatic_plates_are_exempt(self):
        for plate in ["77CD12", "101CC3", "UN4567", "12UN345"]:
            self.assertEqual(classify_plate(plate), "diplomatic", plate)
            self.assertTrue(is_exempt(plate), plate)

    def test_cc_inside_a_regular_plate_is_not_exempt(self):
        # Regression: the old matcher treated any plate containing "CC" as diplomatic.
        self.assertFalse(is_exempt("WB11CC9999"))
        self.assertEqual(classify_plate("WB11CC9999"), "standard")

    def test_invalid_plates(self):
        for plate in ["", "HELLO", "ZZ01AB1234", "OD02AB12", "RJILCV0002", "1234567890"]:
            self.assertIsNone(validate_indian_plate(plate), plate)

    def test_validate_returns_normalised_plate(self):
        self.assertEqual(validate_indian_plate(" od 02 ab 1234 "), "OD02AB1234")


class PricingTests(unittest.TestCase):
    def rates_at(self, hour):
        return get_dynamic_pricing(datetime(2026, 1, 1, hour, 30))

    def test_peak_windows(self):
        for hour in (8, 9, 17, 18, 19):
            _, peak, multiplier = self.rates_at(hour)
            self.assertTrue(peak, hour)
            self.assertEqual(multiplier, 1.15)

    def test_off_peak_boundaries(self):
        # Regression: 10:xx and 20:xx were wrongly billed as peak.
        for hour in (0, 7, 10, 16, 20, 23):
            rates, peak, _ = self.rates_at(hour)
            self.assertFalse(peak, hour)
            self.assertEqual(rates, {k: float(v) for k, v in BASE_TOLL_RATES.items()})

    def test_peak_rates_are_rounded(self):
        rates, _, _ = self.rates_at(9)
        self.assertEqual(rates["Car/Jeep/Van"], 115.0)
        self.assertEqual(rates["Oversized Vehicle"], 747.5)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "db.json"
        self.db = CloudSQLAdapter(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_schema_is_seeded(self):
        data = json.loads(self.db_path.read_text())
        self.assertEqual(set(data), {"transactions", "security_audit", "shift_closures", "vahan_blacklist"})
        self.assertEqual(len(data["vahan_blacklist"]), 4)

    def test_blacklist_lookup_normalises(self):
        self.assertEqual(self.db.get_blacklist_entry("od 02 ab 1234")["Reason"], "Stolen Vehicle (Bhubaneswar)")
        self.assertIsNone(self.db.get_blacklist_entry("OD02AB9999"))

    def test_transactions_scoped_by_plaza(self):
        self.db.insert_transaction("OD02XY1111", "Car/Jeep/Van", "Lane 1", "Cash", "Plaza A", 100, False)
        self.db.insert_transaction("OD02XY2222", "Car/Jeep/Van", "Lane 1", "FASTag", "Plaza B", 115.004, False)
        self.assertEqual(len(self.db.get_dataframe("transactions")), 2)
        plaza_b = self.db.get_dataframe("transactions", "Plaza B")
        self.assertEqual(plaza_b["Plate_Number"].tolist(), ["OD02XY2222"])
        self.assertEqual(plaza_b["Toll_Collected"].iloc[0], 115.0)

    def test_empty_tables_have_columns(self):
        df = self.db.get_dataframe("security_audit")
        self.assertTrue(df.empty)
        self.assertIn("Handled_By_Plaza", df.columns)

    def test_last_shift_closure_time(self):
        self.assertIsNone(self.db.last_shift_closure_time("Plaza A"))
        self.db.close_shift("op", "Plaza A", 100, 90, -10)
        self.assertIsNotNone(self.db.last_shift_closure_time("Plaza A"))
        self.assertIsNone(self.db.last_shift_closure_time("Plaza B"))

    def test_migrates_missing_tables(self):
        self.db_path.write_text(json.dumps({"transactions": [], "vahan_blacklist": []}))
        CloudSQLAdapter(self.db_path)
        self.assertIn("shift_closures", json.loads(self.db_path.read_text()))

    def test_recovers_from_corrupt_file(self):
        self.db_path.write_text("{not json")
        self.assertTrue(self.db.get_dataframe("transactions").empty)
        self.assertTrue(list(Path(self.tmp.name).glob("db.corrupt-*.json")))

    def test_sqlite_export(self):
        self.db.insert_audit("VIP Exemption Granted", "77CD12", "Plaza A")
        sqlite_path = Path(self.tmp.name) / "mirror.db"
        counts = self.db.export_to_sqlite(sqlite_path)
        self.assertEqual(counts["security_audit"], 1)
        self.assertEqual(counts["transactions"], 0)
        with closing(sqlite3.connect(sqlite_path)) as conn:
            self.assertEqual(conn.execute("select count(*) from vahan_blacklist").fetchone()[0], 4)


class AuthTests(unittest.TestCase):
    def test_demo_accounts(self):
        self.assertEqual(auth.authenticate("director@nhai.gov", "admin88")["role"], auth.ROLE_DIRECTOR)
        self.assertEqual(auth.authenticate("  OP1@Manguli.NHAI ", "toll2026")["role"], auth.ROLE_OPERATOR)

    def test_rejects_bad_credentials(self):
        self.assertIsNone(auth.authenticate("director@nhai.gov", "wrong"))
        self.assertIsNone(auth.authenticate("nobody@nhai.gov", "admin88"))

    def test_no_plaintext_tokens_stored(self):
        source = Path(auth.__file__).read_text()
        self.assertNotIn('"admin88"', source)
        self.assertNotIn('"toll2026"', source)


if __name__ == "__main__":
    unittest.main()
