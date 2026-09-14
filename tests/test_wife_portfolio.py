import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import portfolio_builder
from config import PORTFOLIO_COLUMNS, SYMBOL_COL
import sheet_formatter


class TestWifePortfolio(unittest.TestCase):
    """
    Tests for Wife_Portfolio loading, calculation, and sheet styling.
    Verifies that Wife_Portfolio uses the identical structure and styling as the Portfolio tab.
    """

    def test_load_wife_trades(self):
        trades = portfolio_builder.load_wife_trades()
        self.assertGreater(len(trades), 0, "Expected non-zero trades in Wife_Tradebook_2026.csv")
        
        # Verify master-schema fields
        sample = trades[0]
        for key in ("symbol", "isin", "date", "action", "quantity", "price"):
            self.assertIn(key, sample)
            self.assertTrue(sample[key], f"Expected {key} to be non-empty in trade record")

    def test_compute_wife_holdings(self):
        trades = portfolio_builder.load_wife_trades()
        holdings = portfolio_builder.compute_holdings(trades)
        self.assertGreater(len(holdings), 0, "Expected active holdings from wife trades")
        
        # All quantities and costs must be positive
        for isin, h in holdings.items():
            self.assertGreater(h["qty"], 0, f"Expected positive quantity for {h['symbol']}")
            self.assertGreater(h["cost"], 0, f"Expected positive cost for {h['symbol']}")
            self.assertGreater(h["avg_buy"], 0, f"Expected positive avg_buy for {h['symbol']}")

    def test_build_wife_portfolio(self):
        trades = portfolio_builder.load_wife_trades()
        holdings = portfolio_builder.compute_holdings(trades)
        
        # Mock prices for symbols
        mock_prices = {h["symbol"]: h["avg_buy"] * 1.05 for h in holdings.values()}
        mock_tech = {
            h["symbol"]: {
                "day_chg_pct": 1.25,
                "return_1w": 2.5,
                "return_1m": -1.0,
                "return_3m": 5.0,
                "return_6m": 12.0
            } for h in holdings.values()
        }

        portfolio_dict = portfolio_builder.build_portfolio(
            mock_prices, tech_map=mock_tech, trades=trades
        )

        combined = portfolio_dict.get("combined", [])
        self.assertGreater(len(combined), 0)

        # Verify each row has exact Portfolio columns and calculated metrics
        for row in combined:
            self.assertIn("symbol", row)
            self.assertIn("shares", row)
            self.assertIn("avg_buy", row)
            self.assertIn("cmp", row)
            self.assertIn("invested", row)
            self.assertIn("value", row)
            self.assertIn("pnl", row)
            self.assertIn("return_pct", row)
            self.assertIn("wt_pct", row)
            self.assertIn("buy_more", row)
            self.assertIn("signal", row)

            # Buy More@ must be 10% below avg buy
            self.assertEqual(row["buy_more"], round(row["avg_buy"] * 0.90, 2))

    def test_write_wife_portfolio_styling_and_sheet_creation(self):
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 88888
        # Simulate worksheet not found initially -> calls add_worksheet
        mock_sh.worksheet.side_effect = Exception("WorksheetNotFound")
        mock_sh.add_worksheet.return_value = mock_ws

        sample_rows = [
            {
                "symbol": "DIXON", "investment_source": "SELF", "shares": 10.0, "avg_buy": 10000.0,
                "cmp": 11000.0, "day_chg_pct": 1.5, "return_1w": 3.0, "return_1m": 5.0,
                "return_3m": 10.0, "return_6m": 20.0, "invested": 100000.0, "value": 110000.0,
                "pnl": 10000.0, "return_pct": 10.0, "wt_pct": 100.0, "sl_price": 9300.0,
                "target": 12000.0, "buy_more": 9000.0, "signal": "HOLD", "isins": {"INE935N01020"}
            }
        ]
        portfolio_dict = {"groww": [], "zerodha": [], "combined": sample_rows}

        captured_updates = []
        captured_reqs = []

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe", side_effect=lambda ws, cell, data, **kw: captured_updates.append(data)), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, reqs: captured_reqs.extend(reqs)):
            portfolio_builder.write_portfolio(mock_sh, portfolio_dict, tab_name="Wife_Portfolio")

        # 1. Verify add_worksheet called with "Wife_Portfolio"
        mock_sh.add_worksheet.assert_called_once()
        call_args = mock_sh.add_worksheet.call_args
        self.assertEqual(call_args[0][0], "Wife_Portfolio")

        # 2. Verify table data and headers
        self.assertTrue(len(captured_updates) > 0)
        table_data = captured_updates[0]
        # Row 0: PORTFOLIO_COLUMNS
        self.assertEqual(table_data[0], PORTFOLIO_COLUMNS)
        # Row 1: Banner title
        self.assertEqual(table_data[1][SYMBOL_COL], "WIFE PORTFOLIO - VIEW ONLY")
        # Row 2: Data row
        self.assertEqual(table_data[2][SYMBOL_COL], "DIXON")
        # Row 3: Subtotal row
        self.assertEqual(table_data[3][SYMBOL_COL], "WIFE PORTFOLIO TOTAL")

        # 3. Verify batch update formatting requests
        self.assertTrue(len(captured_reqs) > 0)

        # Basic filter present
        has_filter = any("setBasicFilter" in r for r in captured_reqs)
        self.assertTrue(has_filter, "Expected basic filter on Wife_Portfolio table")

        # Freeze row 1 and col 1 present
        has_freeze = any("updateSheetProperties" in r for r in captured_reqs)
        self.assertTrue(has_freeze, "Expected freeze row/col properties")

        # Check for currency formatting
        has_currency = any("numberFormat" in str(r) and "CURRENCY" in str(r) for r in captured_reqs)
        self.assertTrue(has_currency, "Expected currency formatting on currency columns")

        # Check for percentage formatting
        has_pct = any("numberFormat" in str(r) and ('"%"' in str(r) or "PERCENT" in str(r)) for r in captured_reqs)
        self.assertTrue(has_pct, "Expected percentage formatting on return columns")

        # Check for Stop Loss, Target, Buy More@ colors
        sl_rgb = sheet_formatter.hex_rgb("fde9d9")
        target_rgb = sheet_formatter.hex_rgb("d9ead3")
        buy_more_rgb = sheet_formatter.hex_rgb("e8f0fe")

        has_sl_color = any(r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == sl_rgb for r in captured_reqs)
        has_target_color = any(r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == target_rgb for r in captured_reqs)
        has_buy_more_color = any(r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == buy_more_rgb for r in captured_reqs)
        self.assertTrue(has_sl_color, "Expected Stop Loss cell color")
        self.assertTrue(has_target_color, "Expected Target cell color")
        self.assertTrue(has_buy_more_color, "Expected Buy More@ cell color")


if __name__ == "__main__":
    unittest.main()
