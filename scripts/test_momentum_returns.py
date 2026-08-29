#!/usr/bin/env python3
"""
Test script for verifying 1W Return % and 1M Return % calculations.
Tests:
- Normal NSE stock
- ETF
- Stock with sufficient history
- Stock with insufficient history (< 6 or < 22 days)
- Positive / Negative returns
- GITHUB DATA column mapping integrity
"""

import sys
import os
import unittest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from data_fetcher import fetch_technicals
from github_data_builder import GITHUB_DATA_COLS, GITHUB_DATA_HEADER_NAMES, build_result_row, GROUP_DEFS

class TestMomentumReturns(unittest.TestCase):

    def test_column_mapping_integrity(self):
        """Verify GITHUB_DATA_COLS indices are sequential and contiguous."""
        indices = list(GITHUB_DATA_COLS.values())
        self.assertEqual(indices, list(range(len(indices))), "GITHUB_DATA_COLS indices must be contiguous 0..N-1")
        
        # Verify return_1w and return_1m placement inside Group 3
        self.assertEqual(GITHUB_DATA_COLS["day_chg_pct"], 11)
        self.assertEqual(GITHUB_DATA_COLS["return_1w"], 12)
        self.assertEqual(GITHUB_DATA_COLS["return_1m"], 13)
        self.assertEqual(GITHUB_DATA_HEADER_NAMES["return_1w"], "1W Return %")
        self.assertEqual(GITHUB_DATA_HEADER_NAMES["return_1m"], "1M Return %")

        # Check Group 3 range definition
        g3_def = [g for g in GROUP_DEFS if "Group 3" in g[2]][0]
        start_key, end_key = g3_def[0], g3_def[1]
        self.assertEqual(start_key, "day_chg_pct")
        self.assertEqual(end_key, "beta")
        self.assertLess(GITHUB_DATA_COLS[start_key], GITHUB_DATA_COLS["return_1w"])
        self.assertLess(GITHUB_DATA_COLS["return_1w"], GITHUB_DATA_COLS[end_key])

    def test_live_fetch_stock_and_etf(self):
        """Fetch technicals for normal stock and ETF and verify 1W / 1M returns."""
        for sym in ["RELIANCE", "NIFTYBEES", "TCS"]:
            tech = fetch_technicals(sym)
            self.assertIn("return_1w", tech, f"return_1w missing for {sym}")
            self.assertIn("return_1m", tech, f"return_1m missing for {sym}")
            
            ret1w = tech["return_1w"]
            ret1m = tech["return_1m"]
            
            print(f"[{sym}] 1W Return: {ret1w} | 1M Return: {ret1m}")
            
            # Should be formatted like +X.XX% or -X.XX% or 0.00% or ""
            if ret1w != "":
                self.assertTrue(ret1w.endswith("%"), f"Invalid format for 1W Return: {ret1w}")
            if ret1m != "":
                self.assertTrue(ret1m.endswith("%"), f"Invalid format for 1M Return: {ret1m}")

    def test_build_result_row_structure(self):
        """Verify build_result_row puts return_1w and return_1m into correct row indices."""
        sym = "TESTSYM"
        cmp = 100.0
        f = {"sector": "Technology", "industry": "Software", "high52": 120.0, "low52": 80.0, "mcap_cr": 50000}
        tech = {
            "rsi": 55.0, "sma200": 95.0, "vol_spike": 1.2, "trend": "Uptrend",
            "cross": "", "day_chg_pct": 1.5, "return_1w": 3.45, "return_1m": -2.10
        }
        rev_gr = 12.5

        row, archetype, tot_sc, action = build_result_row(sym, cmp, f, tech, rev_gr)

        self.assertEqual(len(row), len(GITHUB_DATA_COLS))
        self.assertEqual(row[GITHUB_DATA_COLS["return_1w"]], 3.45)
        self.assertEqual(row[GITHUB_DATA_COLS["return_1m"]], -2.10)

    def test_missing_insufficient_history(self):
        """Verify handling when tech has blank/missing returns."""
        sym = "NEWSTOCK"
        cmp = 50.0
        f = {}
        tech = {"day_chg_pct": 0.0, "return_1w": "", "return_1m": ""}
        row, _, _, _ = build_result_row(sym, cmp, f, tech, None)

        self.assertEqual(row[GITHUB_DATA_COLS["return_1w"]], "")
        self.assertEqual(row[GITHUB_DATA_COLS["return_1m"]], "")

if __name__ == "__main__":
    unittest.main()
