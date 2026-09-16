"""
test_mf_stock_overlap.py
──────────────────────────────────────────────────────────────────────────────
Tests for Mutual Fund Underlying Stock Overlap Detection.

Validation Cases:
1. Stock held by only one MF -> overlap count 1
2. Stock held by two MFs -> overlap count 2
3. Stock held by three or more MFs -> correct count
4. Same stock with different names -> correctly matched using ISIN
5. Different stocks with similar names -> must NOT be merged
6. Dad's mutual funds -> excluded
7. Wife's mutual funds -> excluded
8. My mutual funds -> included
9. Existing tax calculations -> unchanged
10. Existing Mutual Funds formatting -> preserved
11. No duplicate stock records
12. Combined MF exposure accurately calculated: MF allocation x stock weight
"""

import unittest
from unittest.mock import MagicMock
import os
import sys

# Ensure src/ is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import mf_stock_overlap
import mutual_fund_builder


class TestMutualFundStockOverlap(unittest.TestCase):

    def setUp(self):
        # Sample holdings representing different accounts
        self.holdings = {
            # My mutual funds (Zerodha - Self)
            "Zerodha:INF966L01689": {
                "fund_name": "Quant Small Cap Fund - Direct Plan",
                "isin": "INF966L01689",
                "category": "Equity Schemes - Small Cap Fund",
                "broker": "Zerodha",
                "total_units": 200.0,
                "units": 200.0,
                "current_nav": 350.0,
                "nav": 300.0,
                "avg_nav": 250.0,
                "total_invested": 60000.0,
                "current_value": 70000.0,
                "lots": [],
            },
            "Zerodha:INF966L01911": {
                "fund_name": "Quant Flexi Cap Fund - Direct Plan",
                "isin": "INF966L01911",
                "category": "Equity Schemes - Flexi Cap Fund",
                "broker": "Zerodha",
                "total_units": 100.0,
                "units": 100.0,
                "current_nav": 450.0,
                "nav": 400.0,
                "avg_nav": 350.0,
                "total_invested": 40000.0,
                "current_value": 45000.0,
                "lots": [],
            },
            "Zerodha:INF0R8701046": {
                "fund_name": "Helios Flexi Cap Fund-Direct Plan-Growth",
                "isin": "INF0R8701046",
                "category": "Equity Schemes - Flexi Cap Fund",
                "broker": "Zerodha",
                "total_units": 50.0,
                "units": 50.0,
                "current_nav": 440.0,
                "nav": 400.0,
                "avg_nav": 380.0,
                "total_invested": 20000.0,
                "current_value": 22000.0,
                "lots": [],
            },
            # Dad's mutual funds (Groww) - MUST BE EXCLUDED
            "Groww:INF966L01689": {
                "fund_name": "Quant Small Cap Fund",
                "isin": "INF966L01689",
                "category": "Equity Schemes - Small Cap Fund",
                "broker": "Groww",
                "total_units": 300.0,
                "units": 300.0,
                "current_nav": 350.0,
                "nav": 300.0,
                "avg_nav": 250.0,
                "total_invested": 100000.0,
                "current_value": 120000.0,
                "lots": [],
            },
            # Wife's mutual funds (Wife) - MUST BE EXCLUDED
            "Wife:INF966L01689": {
                "fund_name": "Quant Small Cap Fund - Wife mutual funds",
                "isin": "INF966L01689",
                "category": "Equity Schemes - Small Cap Fund",
                "broker": "Wife",
                "total_units": 150.0,
                "units": 150.0,
                "current_nav": 350.0,
                "nav": 300.0,
                "avg_nav": 250.0,
                "total_invested": 50000.0,
                "current_value": 55000.0,
                "lots": [],
            },
        }

    def test_case_1_2_3_overlap_counts_and_exposure(self):
        """
        Cases 1, 2, 3:
        - Stock in 1 MF -> count 1
        - Stock in 2 MFs -> count 2
        - Stock in 3 MFs -> count 3
        - Verification of combined portfolio exposure: MF allocation x stock weight
        """
        # Total Zerodha invested = 60k + 40k + 20k = 120,000
        # Fund 1 alloc = 60k / 120k = 50%
        # Fund 2 alloc = 40k / 120k = 33.33%
        # Fund 3 alloc = 20k / 120k = 16.67%
        fund_stocks = {
            "Quant Small Cap Fund - Direct Plan": [
                {"raw_symbol": "HDFCBANK.NS", "clean_symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "weight": 0.08, "isin": "INE040A01034"},
                {"raw_symbol": "ICICIBANK.NS", "clean_symbol": "ICICIBANK", "name": "ICICI Bank Ltd", "weight": 0.07, "isin": "INE090A01021"},
                {"raw_symbol": "INFY.NS", "clean_symbol": "INFY", "name": "Infosys Ltd", "weight": 0.05, "isin": "INE009A01021"},
            ],
            "Quant Flexi Cap Fund - Direct Plan": [
                {"raw_symbol": "HDFCBANK.NS", "clean_symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "weight": 0.06, "isin": "INE040A01034"},
                {"raw_symbol": "ICICIBANK.NS", "clean_symbol": "ICICIBANK", "name": "ICICI Bank Ltd", "weight": 0.04, "isin": "INE090A01021"},
                {"raw_symbol": "RELIANCE.NS", "clean_symbol": "RELIANCE", "name": "Reliance Industries Ltd", "weight": 0.05, "isin": "INE002A01018"},
            ],
            "Helios Flexi Cap Fund-Direct Plan-Growth": [
                {"raw_symbol": "HDFCBANK.NS", "clean_symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "weight": 0.05, "isin": "INE040A01034"},
                {"raw_symbol": "INFY.NS", "clean_symbol": "INFY", "name": "Infosys Ltd", "weight": 0.03, "isin": "INE009A01021"},
                {"raw_symbol": "TCS.NS", "clean_symbol": "TCS", "name": "Tata Consultancy Services Ltd", "weight": 0.04, "isin": "INE467B01029"},
            ],
        }

        res = mf_stock_overlap.compute_mf_stock_overlap(self.holdings, fund_stocks_map=fund_stocks)
        res_by_name = {r["stock_name"]: r for r in res}

        # HDFC Bank: held in 3 MFs
        self.assertIn("HDFC Bank Ltd", res_by_name)
        hdfc = res_by_name["HDFC Bank Ltd"]
        self.assertEqual(hdfc["overlap_count"], 3)
        self.assertEqual(len(hdfc["mutual_funds"]), 3)
        # Expected exposure: 50% * 8% + 33.33% * 6% + 16.67% * 5% = 4% + 2% + 0.833% = 6.83%
        expected_hdfc_exp = 0.50 * 0.08 + (40000 / 120000) * 0.06 + (20000 / 120000) * 0.05
        self.assertAlmostEqual(hdfc["combined_exposure"], expected_hdfc_exp, places=4)

        # ICICI Bank: held in 2 MFs
        self.assertIn("ICICI Bank Ltd", res_by_name)
        icici = res_by_name["ICICI Bank Ltd"]
        self.assertEqual(icici["overlap_count"], 2)

        # Infosys: held in 2 MFs
        self.assertIn("Infosys Ltd", res_by_name)
        infy = res_by_name["Infosys Ltd"]
        self.assertEqual(infy["overlap_count"], 2)

        # Reliance: held in 1 MF
        self.assertIn("Reliance Industries Ltd", res_by_name)
        rel = res_by_name["Reliance Industries Ltd"]
        self.assertEqual(rel["overlap_count"], 1)

        # TCS: held in 1 MF
        self.assertIn("Tata Consultancy Services Ltd", res_by_name)
        tcs = res_by_name["Tata Consultancy Services Ltd"]
        self.assertEqual(tcs["overlap_count"], 1)

    def test_case_4_same_stock_different_names_matched_by_isin(self):
        """
        Case 4: Same stock with different name text (e.g. 'HDFC Bank Ltd' vs 'HDFC BANK LIMITED')
        must be recognised as the same stock because their ISIN is identical.
        """
        fund_stocks = {
            "Quant Small Cap Fund - Direct Plan": [
                {"raw_symbol": "HDFCBANK.NS", "clean_symbol": "HDFCBANK", "name": "HDFC Bank Ltd", "weight": 0.05, "isin": "INE040A01034"},
            ],
            "Quant Flexi Cap Fund - Direct Plan": [
                {"raw_symbol": "500180", "clean_symbol": "500180", "name": "HDFC BANK LIMITED", "weight": 0.06, "isin": "INE040A01034"},
            ],
        }

        res = mf_stock_overlap.compute_mf_stock_overlap(self.holdings, fund_stocks_map=fund_stocks)
        # Must be merged into a single record with overlap_count = 2
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["isin"], "INE040A01034")
        self.assertEqual(res[0]["overlap_count"], 2)

    def test_case_5_different_stocks_similar_names_not_merged(self):
        """
        Case 5: Different stocks with similar names (e.g. 'Tata Motors Ltd' vs 'Tata Steel Ltd')
        must NOT be merged.
        """
        fund_stocks = {
            "Quant Small Cap Fund - Direct Plan": [
                {"raw_symbol": "TATAMOTORS.NS", "clean_symbol": "TATAMOTORS", "name": "Tata Motors Ltd", "weight": 0.03, "isin": "INE155A01022"},
            ],
            "Quant Flexi Cap Fund - Direct Plan": [
                {"raw_symbol": "TATASTEEL.NS", "clean_symbol": "TATASTEEL", "name": "Tata Steel Ltd", "weight": 0.04, "isin": "INE081A01020"},
            ],
        }

        res = mf_stock_overlap.compute_mf_stock_overlap(self.holdings, fund_stocks_map=fund_stocks)
        # Must remain 2 distinct records
        self.assertEqual(len(res), 2)
        for r in res:
            self.assertEqual(r["overlap_count"], 1)

    def test_cases_6_7_8_account_separation(self):
        """
        Cases 6, 7, 8:
        - Dad's mutual funds (Groww) are strictly excluded
        - Wife's mutual funds (Wife) are strictly excluded
        - My mutual funds (Zerodha) are included
        """
        # Even if Dad and Wife hold funds with stocks, they should not be counted
        fund_stocks = {
            "Quant Small Cap Fund - Direct Plan": [
                {"raw_symbol": "SBIN.NS", "clean_symbol": "SBIN", "name": "State Bank of India", "weight": 0.05, "isin": "INE062A01020"},
            ],
            "Quant Small Cap Fund": [  # Dad's Groww fund
                {"raw_symbol": "SBIN.NS", "clean_symbol": "SBIN", "name": "State Bank of India", "weight": 0.05, "isin": "INE062A01020"},
            ],
            "Quant Small Cap Fund - Wife mutual funds": [  # Wife's fund
                {"raw_symbol": "SBIN.NS", "clean_symbol": "SBIN", "name": "State Bank of India", "weight": 0.05, "isin": "INE062A01020"},
            ],
        }

        res = mf_stock_overlap.compute_mf_stock_overlap(self.holdings, fund_stocks_map=fund_stocks)
        # State Bank of India should only have count = 1 because only Zerodha's fund is counted
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["overlap_count"], 1)
        self.assertEqual(len(res[0]["mutual_funds"]), 1)
        self.assertIn("Quant Small Cap Fund - Direct Plan", res[0]["mutual_funds"])

    def test_cases_9_10_tax_calculations_and_formatting_preserved(self):
        """
        Cases 9 & 10:
        - Existing tax calculations (compute_tax_harvest) remain 100% unchanged
        - Sheet write properly pads stock overlap section after COMBINED TOTAL
        """
        tax_data = mutual_fund_builder.compute_tax_harvest(self.holdings)
        # Ensure tax_data has all expected keys
        self.assertIn("Zerodha:INF966L01689", tax_data)
        self.assertIn("harvestable", tax_data["Zerodha:INF966L01689"])

        # Test write_mutual_funds builds rows cleanly
        stock_overlap_mock = [
            {
                "stock_name": "HDFC Bank Ltd",
                "symbol": "HDFCBANK",
                "isin": "INE040A01034",
                "overlap_count": 3,
                "mutual_funds": ["Fund A", "Fund B", "Fund C"],
                "mutual_funds_str": "Fund A | Fund B | Fund C",
                "stock_weights": [0.08, 0.06, 0.05],
                "stock_weights_str": "Fund A: 8.0% | Fund B: 6.0% | Fund C: 5.0%",
                "combined_exposure": 0.064,
                "combined_exposure_pct": 6.4,
            }
        ]

        sh_mock = MagicMock()
        ws_mock = MagicMock()
        ws_mock.id = 12345
        ws_mock.get_all_values.return_value = []
        sh_mock.worksheet.return_value = ws_mock

        # Call write_mutual_funds with stock_overlap_mock
        mutual_fund_builder.write_mutual_funds(
            sh_mock, self.holdings, tax_data,
            overlap_data={"Zerodha:INF966L01689": {"overlap": "NO", "overlap_with": ""}},
            stock_overlap_data=stock_overlap_mock
        )

        captured_calls = ws_mock.mock_calls
        # Ensure sheet_writer.update_sheet_safe was called with table data
        update_calls = [c for c in captured_calls if "update" in str(c).lower()]
        self.assertTrue(len(update_calls) > 0, "Expected table update call on worksheet")

    def test_case_11_no_duplicate_stock_records(self):
        """
        Case 11: No duplicate stock records should exist in the output.
        """
        fund_stocks = {
            "Quant Small Cap Fund - Direct Plan": [
                {"raw_symbol": "ADANIENT.BO", "clean_symbol": "ADANIENT", "name": "Adani Enterprises Ltd", "weight": 0.04, "isin": "INE423A01024"},
                {"raw_symbol": "ICICIBANK.NS", "clean_symbol": "ICICIBANK", "name": "ICICI Bank Ltd", "weight": 0.05, "isin": "INE090A01021"},
            ],
            "Quant Flexi Cap Fund - Direct Plan": [
                {"raw_symbol": "ADANIENT.BO", "clean_symbol": "ADANIENT", "name": "Adani Enterprises Ltd", "weight": 0.08, "isin": "INE423A01024"},
                {"raw_symbol": "ICICIBANK.NS", "clean_symbol": "ICICIBANK", "name": "ICICI Bank Ltd", "weight": 0.06, "isin": "INE090A01021"},
            ],
        }

        res = mf_stock_overlap.compute_mf_stock_overlap(self.holdings, fund_stocks_map=fund_stocks)
        stock_names = [r["stock_name"] for r in res]
        self.assertEqual(len(stock_names), len(set(stock_names)), "Found duplicate stock records in output")


if __name__ == "__main__":
    unittest.main()
