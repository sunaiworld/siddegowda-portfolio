import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import shutil
import tempfile
import csv
import json
from datetime import date, datetime

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import monthly_snapshot
from github_data_builder import GITHUB_DATA_COLS, GITHUB_DATA_HEADER_NAMES


class MockWorksheet:
    def __init__(self, title="Sheet1", data=None):
        self.title = title
        self.id = 12345
        self._data = data or []

    def get_all_values(self):
        return [list(map(str, r)) for r in self._data]

    def row_values(self, row_idx):
        idx = row_idx - 1
        return [str(c) for c in self._data[idx]] if idx < len(self._data) else []

    def col_values(self, col_idx):
        idx = col_idx - 1
        return [str(r[idx]) if idx < len(r) else "" for r in self._data]

    def append_row(self, row):
        self._data.append(row)

    def append_rows(self, rows, value_input_option=None):
        self._data.extend(rows)

    def update(self, *args, **kwargs):
        if len(args) >= 2 and isinstance(args[1], list):
            self._data = args[1]
        elif "values" in kwargs and isinstance(kwargs["values"], list):
            self._data = kwargs["values"]

    def clear(self):
        self._data = []


class MockSpreadsheet:
    def __init__(self):
        self._sheets = {}

    def worksheet(self, title):
        if title not in self._sheets:
            raise Exception(f"Worksheet {title} not found")
        return self._sheets[title]

    def add_worksheet(self, title, rows=100, cols=20):
        ws = MockWorksheet(title)
        self._sheets[title] = ws
        return ws


class TestMonthlySnapshot(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_snapshot_dir = monthly_snapshot.LOCAL_SNAPSHOT_DIR
        self.orig_manifest_file = monthly_snapshot.MANIFEST_FILE
        monthly_snapshot.LOCAL_SNAPSHOT_DIR = self.test_dir
        monthly_snapshot.MANIFEST_FILE = os.path.join(self.test_dir, "snapshots_manifest.json")

    def tearDown(self):
        monthly_snapshot.LOCAL_SNAPSHOT_DIR = self.orig_snapshot_dir
        monthly_snapshot.MANIFEST_FILE = self.orig_manifest_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_last_trading_day_calculation(self):
        # January 2027 ends on Sunday Jan 31 -> Last trading day is Friday Jan 29
        self.assertEqual(monthly_snapshot.get_last_trading_day_of_month(2027, 1), date(2027, 1, 29))
        # February 2027 ends on Sunday Feb 28 -> Last trading day is Friday Feb 26
        self.assertEqual(monthly_snapshot.get_last_trading_day_of_month(2027, 2), date(2027, 2, 26))
        # March 2027 ends on Wednesday Mar 31 -> Last trading day is Wednesday Mar 31
        self.assertEqual(monthly_snapshot.get_last_trading_day_of_month(2027, 3), date(2027, 3, 31))
        # April 2027 ends on Friday Apr 30 -> Last trading day is Friday Apr 30
        self.assertEqual(monthly_snapshot.get_last_trading_day_of_month(2027, 4), date(2027, 4, 30))
        # May 2027 ends on Monday May 31 -> Last trading day is Monday May 31
        self.assertEqual(monthly_snapshot.get_last_trading_day_of_month(2027, 5), date(2027, 5, 31))
        # October 2027 ends on Sunday Oct 31 -> Last trading day is Friday Oct 29
        self.assertEqual(monthly_snapshot.get_last_trading_day_of_month(2027, 10), date(2027, 10, 29))

        # Check is_last_trading_day_of_month helper
        self.assertTrue(monthly_snapshot.is_last_trading_day_of_month(date(2027, 1, 29)))
        self.assertFalse(monthly_snapshot.is_last_trading_day_of_month(date(2027, 1, 28)))
        self.assertFalse(monthly_snapshot.is_last_trading_day_of_month(date(2027, 1, 31)))

    def _generate_dummy_row(self, sym="INFY", total=85, action="STRONG BUY", zone="🟢 ACCUMULATE"):
        row = [""] * len(GITHUB_DATA_COLS)
        row[GITHUB_DATA_COLS["symbol"]] = sym
        row[GITHUB_DATA_COLS["sector"]] = "Information Technology"
        row[GITHUB_DATA_COLS["cmp"]] = 1850.50
        row[GITHUB_DATA_COLS["pe"]] = 28.5
        row[GITHUB_DATA_COLS["rsi"]] = 45.2
        row[GITHUB_DATA_COLS["total"]] = total
        row[GITHUB_DATA_COLS["buying_zone"]] = zone
        row[GITHUB_DATA_COLS["action"]] = action
        row[GITHUB_DATA_COLS["return_1w"]] = 1.5
        row[GITHUB_DATA_COLS["return_1m"]] = 4.2
        row[GITHUB_DATA_COLS["fair_val"]] = 2100.0
        return row

    def test_build_github_data_snapshot_rows(self):
        dummy_row = self._generate_dummy_row("TCS", total=78, action="BUY")
        rows = monthly_snapshot.build_github_data_snapshot_rows([dummy_row], "2027-01-29", "2027-01")

        self.assertEqual(len(rows), 1)
        snap_r = rows[0]
        # Col 0: Snapshot Date, Col 1: Snapshot Month, followed by all 41 columns
        self.assertEqual(snap_r[0], "2027-01-29")
        self.assertEqual(snap_r[1], "2027-01")
        self.assertEqual(snap_r[2 + GITHUB_DATA_COLS["symbol"]], "TCS")
        self.assertEqual(snap_r[2 + GITHUB_DATA_COLS["total"]], 78)
        self.assertEqual(snap_r[2 + GITHUB_DATA_COLS["action"]], "BUY")
        self.assertEqual(len(snap_r), 2 + len(GITHUB_DATA_COLS))

    def test_build_future_buy_snapshot_rows(self):
        dummy_row = self._generate_dummy_row("ZYDUSLIFE", total=90, action="STRONG BUY")
        rows = monthly_snapshot.build_future_buy_snapshot_rows(
            [dummy_row], "2027-01-29", "2027-01",
            sector_weights={"Information Technology": 12.0},
            portfolio_value=1000000.0
        )

        self.assertEqual(len(rows), 1)
        snap_r = rows[0]
        # Col 0: Snapshot Date, Col 1: Snapshot Month, Col 2: Rank, Col 3: Fit, Col 4: Tranche, + 41 cols
        self.assertEqual(snap_r[0], "2027-01-29")
        self.assertEqual(snap_r[1], "2027-01")
        self.assertEqual(snap_r[2], 1)
        self.assertIn("High Fit", snap_r[3])
        self.assertEqual(snap_r[4], "₹20,000 (2.0%)")
        self.assertEqual(snap_r[5 + GITHUB_DATA_COLS["symbol"]], "ZYDUSLIFE")
        self.assertEqual(snap_r[5 + GITHUB_DATA_COLS["total"]], 90)
        self.assertEqual(len(snap_r), 5 + len(GITHUB_DATA_COLS))

    @patch("sheet_writer.batch_update_safe")
    def test_duplicate_protection(self, mock_batch_update):
        sh = MockSpreadsheet()
        gh_data = [self._generate_dummy_row("INFY", total=80), self._generate_dummy_row("TCS", total=75)]
        fb_data = [self._generate_dummy_row("ZYDUSLIFE", total=88)]

        snapshot_day = "2027-01-29" # Last trading day of Jan 2027

        # 1. First execution -> Should succeed and create snapshots
        res1 = monthly_snapshot.check_and_record_monthly_snapshots(
            sh,
            github_results=gh_data,
            future_buy_rows=fb_data,
            run_date=snapshot_day
        )
        self.assertTrue(res1["recorded"])
        self.assertEqual(res1["status"], "success")
        self.assertEqual(res1["github_rows_recorded"], 2)
        self.assertEqual(res1["future_buy_rows_recorded"], 1)

        gh_ws = sh.worksheet(monthly_snapshot.GITHUB_DATA_HISTORY_TAB)
        fb_ws = sh.worksheet(monthly_snapshot.FUTURE_BUY_HISTORY_TAB)
        self.assertEqual(len(gh_ws.get_all_values()), 3) # Header + 2 rows
        self.assertEqual(len(fb_ws.get_all_values()), 2) # Header + 1 row

        # Verify local CSV files created
        month_dir = os.path.join(self.test_dir, "2027-01")
        self.assertTrue(os.path.exists(os.path.join(month_dir, "github_data_snapshot.csv")))
        self.assertTrue(os.path.exists(os.path.join(month_dir, "future_buy_snapshot.csv")))
        self.assertTrue(os.path.exists(monthly_snapshot.MANIFEST_FILE))

        # 2. Second execution for the same month -> Must be skipped due to duplicate protection!
        res2 = monthly_snapshot.check_and_record_monthly_snapshots(
            sh,
            github_results=gh_data,
            future_buy_rows=fb_data,
            run_date=snapshot_day
        )
        self.assertFalse(res2["recorded"])
        self.assertEqual(res2["status"], "skipped_duplicate_exists")

        # Row counts in Google Sheets must remain strictly unchanged!
        self.assertEqual(len(gh_ws.get_all_values()), 3)
        self.assertEqual(len(fb_ws.get_all_values()), 2)

    @patch("sheet_writer.batch_update_safe")
    def test_skips_on_non_snapshot_day(self, mock_batch_update):
        sh = MockSpreadsheet()
        gh_data = [self._generate_dummy_row("INFY", total=80)]
        fb_data = [self._generate_dummy_row("ZYDUSLIFE", total=88)]

        non_snapshot_day = "2027-01-15" # Mid-month Friday

        res = monthly_snapshot.check_and_record_monthly_snapshots(
            sh,
            github_results=gh_data,
            future_buy_rows=fb_data,
            run_date=non_snapshot_day
        )
        self.assertFalse(res["recorded"])
        self.assertEqual(res["status"], "skipped_not_snapshot_day")

    @patch("sheet_writer.batch_update_safe")
    def test_force_flag_overrides_date(self, mock_batch_update):
        sh = MockSpreadsheet()
        gh_data = [self._generate_dummy_row("INFY", total=80)]
        fb_data = [self._generate_dummy_row("ZYDUSLIFE", total=88)]

        mid_month_day = "2027-02-15"

        res = monthly_snapshot.check_and_record_monthly_snapshots(
            sh,
            github_results=gh_data,
            future_buy_rows=fb_data,
            force=True,
            run_date=mid_month_day
        )
        self.assertTrue(res["recorded"])
        self.assertEqual(res["status"], "success")


    @patch("sheet_writer.batch_update_safe")
    def test_sync_missing_local_snapshots_from_sheet(self, mock_batch_update):
        sh = MockSpreadsheet()
        gh_ws = sh.add_worksheet(monthly_snapshot.GITHUB_DATA_HISTORY_TAB)
        fb_ws = sh.add_worksheet(monthly_snapshot.FUTURE_BUY_HISTORY_TAB)

        gh_ws.append_row(monthly_snapshot.GITHUB_DATA_HISTORY_HEADERS)
        fb_ws.append_row(monthly_snapshot.FUTURE_BUY_HISTORY_HEADERS)

        # Append existing August 2026 rows
        gh_ws.append_row(["2026-08-31", "2026-08", "INFY", "IT"] + [""] * 39)
        fb_ws.append_row(["2026-08-31", "2026-08", 1, "High Fit", "2.0% Tranche", "ZYDUSLIFE"] + [""] * 40)

        # Before sync: local snapshot dir is empty
        month_dir = os.path.join(self.test_dir, "2026-08")
        self.assertFalse(os.path.exists(month_dir))

        # Run sync
        synced = monthly_snapshot.sync_missing_local_snapshots_from_sheet(sh)
        self.assertEqual(synced, ["2026-08"])

        # After sync: local CSV files and manifest exist with correct content
        self.assertTrue(os.path.exists(os.path.join(month_dir, "github_data_snapshot.csv")))
        self.assertTrue(os.path.exists(os.path.join(month_dir, "future_buy_snapshot.csv")))
        self.assertTrue(os.path.exists(monthly_snapshot.MANIFEST_FILE))

        with open(monthly_snapshot.MANIFEST_FILE, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertIn("2026-08", manifest)
        self.assertEqual(manifest["2026-08"]["github_data_rows"], 1)
        self.assertEqual(manifest["2026-08"]["future_buy_rows"], 1)

    @patch("sheet_writer.batch_update_safe")
    def test_sync_missing_local_snapshots_skips_when_files_exist(self, mock_batch_update):
        sh = MockSpreadsheet()
        gh_ws = sh.add_worksheet(monthly_snapshot.GITHUB_DATA_HISTORY_TAB)
        fb_ws = sh.add_worksheet(monthly_snapshot.FUTURE_BUY_HISTORY_TAB)

        gh_ws.append_row(monthly_snapshot.GITHUB_DATA_HISTORY_HEADERS)
        fb_ws.append_row(monthly_snapshot.FUTURE_BUY_HISTORY_HEADERS)

        gh_ws.append_row(["2026-08-31", "2026-08", "INFY", "IT"] + [""] * 39)
        fb_ws.append_row(["2026-08-31", "2026-08", 1, "High Fit", "2.0% Tranche", "ZYDUSLIFE"] + [""] * 40)

        # Pre-create files
        month_dir = os.path.join(self.test_dir, "2026-08")
        os.makedirs(month_dir, exist_ok=True)
        with open(os.path.join(month_dir, "github_data_snapshot.csv"), "w") as f:
            f.write("test")
        with open(os.path.join(month_dir, "future_buy_snapshot.csv"), "w") as f:
            f.write("test")

        synced = monthly_snapshot.sync_missing_local_snapshots_from_sheet(sh)
        self.assertEqual(synced, [])


if __name__ == "__main__":
    unittest.main()
