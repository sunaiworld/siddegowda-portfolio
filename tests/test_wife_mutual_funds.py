import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import mutual_fund_builder
import mf_data_fetcher
import sheet_formatter


class TestWifeMutualFunds(unittest.TestCase):
    """
    Tests for Wife Mutual Funds:
    - Loading tradebook from data/imports/Wife/
    - AMFI ISIN resolution and NAV fetcher
    - FIFO holdings and tax calculations
    - Mutual Funds sheet formatting with 'WIFE MUTUAL FUNDS' section and '- Wife mutual funds' naming
    """

    def test_load_all_mf_trades_includes_wife(self):
        trades = mutual_fund_builder.load_all_mf_trades()
        self.assertGreater(len(trades), 0)
        wife_trades = [t for t in trades if t.get("broker") == "Wife"]
        self.assertEqual(len(wife_trades), 19, "Expected 19 trades in Wife mutual fund order history")

        # Verify canonical MF schema fields
        for t in wife_trades:
            self.assertEqual(t["broker"], "Wife")
            self.assertIn("fund_name", t)
            self.assertIn("date", t)
            self.assertIn("action", t)
            self.assertGreater(t["units"], 0)
            self.assertGreater(t["amount"], 0)

    def test_compute_wife_mf_holdings(self):
        trades = mutual_fund_builder.load_all_mf_trades()
        holdings = mutual_fund_builder.compute_mf_holdings(trades)

        wife_holdings = [h for k, h in holdings.items() if h.get("broker") == "Wife"]
        self.assertEqual(len(wife_holdings), 8, "Expected 8 unique Wife mutual fund holdings")

        total_cost = sum(h["total_invested"] for h in wife_holdings)
        self.assertAlmostEqual(total_cost, 950028.0, places=1)

        # Verify ISINs and categories are resolved
        fund_names = {h["fund_name"] for h in wife_holdings}
        expected_funds = {
            "Bandhan Small Cap Fund Direct Growth",
            "Quant Small Cap Fund Direct Plan Growth",
            "ITI Small Cap Fund Direct Growth",
            "Motilal Oswal Midcap Fund Direct Growth",
            "Canara Robeco Small Cap Fund Direct Growth",
            "Mahindra Manulife Small Cap Fund Direct Growth",
            "Axis Small Cap Fund Direct Growth",
            "Bank of India Small Cap Fund Direct Growth"
        }
        self.assertEqual(fund_names, expected_funds)

        for h in wife_holdings:
            self.assertTrue(h["isin"].startswith("INF"), f"Expected valid ISIN for {h['fund_name']}")
            self.assertIn(h["category"], ["Small Cap", "Mid Cap"])

    def test_write_mutual_funds_wife_section_and_naming(self):
        trades = mutual_fund_builder.load_all_mf_trades()
        holdings = mutual_fund_builder.compute_mf_holdings(trades)
        tax_data = mutual_fund_builder.compute_tax_harvest(holdings)

        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 55555
        mock_ws.get_all_values.return_value = []
        mock_sh.worksheet.return_value = mock_ws

        captured_updates = []
        captured_reqs = []

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe", side_effect=lambda ws, cell, data, **kw: captured_updates.append(data)), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, reqs: captured_reqs.extend(reqs)):
            mutual_fund_builder.write_mutual_funds(mock_sh, holdings, tax_data)

        self.assertTrue(len(captured_updates) > 0)
        table = captured_updates[0]

        # 1. Header row
        self.assertEqual(table[0][0], "Fund Name")

        # 2. Wife Section Banner in column 0
        wife_banner_idx = next(i for i, r in enumerate(table) if r[0] == "WIFE MUTUAL FUNDS")
        self.assertIsNotNone(wife_banner_idx)

        # 3. Every Wife fund row must mention 'Wife mutual funds' in Column 0 ('Fund Name')
        wife_rows = [r for r in table if " - Wife mutual funds" in str(r[0])]
        self.assertEqual(len(wife_rows), 8, "All 8 Wife funds must mention 'Wife mutual funds' in Fund Name column")

        # 4. Wife subtotal row
        wife_subtotal = next(r for r in table if r[0] == "WIFE MUTUAL FUNDS SUBTOTAL")
        self.assertAlmostEqual(float(wife_subtotal[5]), 950028.0, places=1)

        # 5. Combined total row
        combined_total = next(r for r in table if r[0] == "COMBINED TOTAL")
        self.assertGreater(float(combined_total[5]), 950028.0)

        # 6. Formatting requests include basic filter and column widths
        has_filter = any("setBasicFilter" in r for r in captured_reqs)
        self.assertTrue(has_filter, "Expected basic filter on Mutual Funds table")

        # Dimension pixelSize for Fund Name column must be at least 260
        widths_reqs = [r for r in captured_reqs if "updateDimensionProperties" in r and r["updateDimensionProperties"].get("range", {}).get("startIndex") == 0 and r["updateDimensionProperties"].get("range", {}).get("dimension") == "COLUMNS"]
        if widths_reqs:
            self.assertGreaterEqual(widths_reqs[0]["updateDimensionProperties"]["properties"]["pixelSize"], 260)


if __name__ == "__main__":
    unittest.main()
