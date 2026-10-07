import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import PORTFOLIO_COLUMNS, SYMBOL_COL
import portfolio_builder
import sheet_formatter


class TestPortfolioFormattingUpdate(unittest.TestCase):
    """
    Verification tests for:
    1. Investment Source removal from Portfolio and wife_Portfolio.
    2. Symbol color coding matching GITHUB DATA (Market Cap Tier: Large/Mid/Small Cap).
    3. Invested vs Value color coding (Value > Invested -> Value=Green, Invested=Red, and vice-versa).
    4. Not > 5% continuous colour scale (0.1% Green -> 5.0% Red, >5% Red, <=0% / blank neutral).
    5. Structural integrity and no broken/shifted column references.
    """

    def test_investment_source_removed_from_schema(self):
        """Verify 'Investment Source' is completely absent from PORTFOLIO_COLUMNS."""
        self.assertNotIn("Investment Source", PORTFOLIO_COLUMNS)
        self.assertEqual(PORTFOLIO_COLUMNS[0], "Symbol")
        self.assertEqual(PORTFOLIO_COLUMNS[1], "Shares")
        self.assertEqual(PORTFOLIO_COLUMNS[10], "Invested")
        self.assertEqual(PORTFOLIO_COLUMNS[11], "Value")
        self.assertEqual(PORTFOLIO_COLUMNS[12], "P&L")
        self.assertEqual(PORTFOLIO_COLUMNS[13], "Not > 5%")
        self.assertEqual(PORTFOLIO_COLUMNS[14], "XIRR")
        self.assertEqual(PORTFOLIO_COLUMNS[15], "Return %")
        self.assertEqual(PORTFOLIO_COLUMNS[16], "Wt %")
        self.assertEqual(len(PORTFOLIO_COLUMNS), 21)

    def test_continuous_gradient_values(self):
        """Verify continuous colour scale for Not > 5% values."""
        # 0.1% -> Green (bg: d9ead3, fg: 0b8043)
        bg_01, fg_01 = sheet_formatter.get_continuous_green_to_red_rgb(0.1, min_val=0.1, max_val=5.0)
        self.assertEqual(bg_01, "d9ead3")
        self.assertEqual(fg_01, "0b8043")

        # 1.0% -> Transitioning from green toward yellow
        bg_1, fg_1 = sheet_formatter.get_continuous_green_to_red_rgb(1.0, min_val=0.1, max_val=5.0)
        self.assertIsNotNone(bg_1)
        self.assertNotEqual(bg_1, "d9ead3")

        # 3.0% -> Transitioning from yellow toward red
        bg_3, fg_3 = sheet_formatter.get_continuous_green_to_red_rgb(3.0, min_val=0.1, max_val=5.0)
        self.assertIsNotNone(bg_3)

        # 5.0% -> Red (bg: fde9d9, fg: c62828)
        bg_5, fg_5 = sheet_formatter.get_continuous_green_to_red_rgb(5.0, min_val=0.1, max_val=5.0)
        self.assertEqual(bg_5, "fde9d9")
        self.assertEqual(fg_5, "c62828")

        # > 5.0% (e.g. 7.5%) -> Red (bg: fde9d9, fg: c62828)
        bg_gt5, fg_gt5 = sheet_formatter.get_continuous_green_to_red_rgb(7.5, min_val=0.1, max_val=5.0)
        self.assertEqual(bg_gt5, "fde9d9")
        self.assertEqual(fg_gt5, "c62828")

        # 0% or blank -> Sensibly neutral / None
        bg_0, fg_0 = sheet_formatter.get_continuous_green_to_red_rgb(0.0, min_val=0.1, max_val=5.0)
        self.assertIsNone(bg_0)
        self.assertIsNone(fg_0)

        bg_none, fg_none = sheet_formatter.get_continuous_green_to_red_rgb(None, min_val=0.1, max_val=5.0)
        self.assertIsNone(bg_none)
        self.assertIsNone(fg_none)

    def test_write_portfolio_formatting_and_rules(self):
        """Verify write_portfolio generates the correct symbol colours, Invested vs Value colours, and CF rules."""
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 12345
        mock_sh.worksheet.return_value = mock_ws
        mock_sh.fetch_sheet_metadata.return_value = {"sheets": []}

        sample_holdings = [
            # Row 2 (rn=2): Value > Invested -> Value=Green, Invested=Red; Large Cap (>=25k Cr) -> Symbol=Green
            {
                "symbol": "RELIANCE", "shares": 10.0, "avg_buy": 2500.0, "cmp": 3000.0,
                "invested": 25000.0, "value": 30000.0, "pnl": 5000.0, "not_gt_5pct": 0.1,
                "xirr": 20.0, "return_pct": 20.0, "wt_pct": 3.0, "mcap_cr": 2000000.0,
                "sl_price": 2325.0, "target": 3000.0, "buy_more": 2250.0, "signal": "HOLD"
            },
            # Row 3 (rn=3): Value < Invested -> Value=Red, Invested=Green; Mid Cap (>=5k Cr) -> Symbol=Blue
            {
                "symbol": "POLYCAB", "shares": 5.0, "avg_buy": 7000.0, "cmp": 6000.0,
                "invested": 35000.0, "value": 30000.0, "pnl": -5000.0, "not_gt_5pct": 3.0,
                "xirr": -10.0, "return_pct": -14.29, "wt_pct": 3.0, "mcap_cr": 15000.0,
                "sl_price": 6510.0, "target": 8400.0, "buy_more": 6300.0, "signal": "BUY MORE"
            },
            # Row 4 (rn=4): Value == Invested -> Both neutral; Small Cap (<5k Cr) -> Symbol=Red
            {
                "symbol": "SMALLCO", "shares": 100.0, "avg_buy": 100.0, "cmp": 100.0,
                "invested": 10000.0, "value": 10000.0, "pnl": 0.0, "not_gt_5pct": 6.5,
                "xirr": 0.0, "return_pct": 0.0, "wt_pct": 6.5, "mcap_cr": 2500.0,
                "sl_price": 93.0, "target": 120.0, "buy_more": 90.0, "signal": "HOLD"
            }
        ]

        portfolio_dict = {"combined": sample_holdings, "portfolio_xirr": 12.0}

        captured_updates = []
        captured_reqs = []

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe", side_effect=lambda ws, cell, data, **kw: captured_updates.append(data)), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, reqs: captured_reqs.extend(reqs)):
            portfolio_builder.write_portfolio(mock_sh, portfolio_dict, tab_name="Portfolio")

        # 1. Verification of written table data
        self.assertTrue(len(captured_updates) > 0)
        table_data = captured_updates[0]
        headers = table_data[0]
        self.assertEqual(headers, PORTFOLIO_COLUMNS)
        self.assertNotIn("Investment Source", headers)
        self.assertEqual(len(headers), 21)

        inv_col = headers.index("Invested")
        val_col = headers.index("Value")
        n5_col = headers.index("Not > 5%")

        # 2. Verification of Symbol colour coding matching GITHUB DATA
        green_bg = sheet_formatter.hex_rgb("d9ead3")
        blue_bg = sheet_formatter.hex_rgb("d9eaf7")
        red_bg = sheet_formatter.hex_rgb("fde9d9")

        def get_cell_bg(rn, c_idx):
            for r in captured_reqs:
                rc = r.get("repeatCell", {})
                rng = rc.get("range", {})
                if (rng.get("startRowIndex") == rn and rng.get("endRowIndex") == rn + 1
                        and rng.get("startColumnIndex") == c_idx and rng.get("endColumnIndex") == c_idx + 1):
                    return rc.get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor")
            return None

        # Row 2: RELIANCE (Large Cap) -> Green
        self.assertEqual(get_cell_bg(2, SYMBOL_COL), green_bg)
        # Row 3: POLYCAB (Mid Cap) -> Blue
        self.assertEqual(get_cell_bg(3, SYMBOL_COL), blue_bg)
        # Row 4: SMALLCO (Small Cap) -> Red
        self.assertEqual(get_cell_bg(4, SYMBOL_COL), red_bg)

        # 3. Verification of Invested vs Value colour coding
        # Row 2: Value (30000) > Invested (25000) -> Value = Green, Invested = Red
        self.assertEqual(get_cell_bg(2, val_col), green_bg)
        self.assertEqual(get_cell_bg(2, inv_col), red_bg)

        # Row 3: Value (30000) < Invested (35000) -> Value = Red, Invested = Green
        self.assertEqual(get_cell_bg(3, val_col), red_bg)
        self.assertEqual(get_cell_bg(3, inv_col), green_bg)

        # Row 4: Value (10000) == Invested (10000) -> Neither is colored green/red
        self.assertIsNone(get_cell_bg(4, val_col))
        self.assertIsNone(get_cell_bg(4, inv_col))

        # 4. Verification of Not > 5% continuous colour scale
        # Row 2: 0.1% -> Green
        self.assertEqual(get_cell_bg(2, n5_col), green_bg)
        # Row 4: 6.5% (>5%) -> Red
        self.assertEqual(get_cell_bg(4, n5_col), red_bg)

        # 5. Verification of Google Sheets conditional formatting rules
        cf_rules = [r["addConditionalFormatRule"]["rule"] for r in captured_reqs if "addConditionalFormatRule" in r]
        self.assertGreater(len(cf_rules), 0)

        # Invested & Value rules present
        val_rules = [r for r in cf_rules if any(rng.get("startColumnIndex") == val_col for rng in r.get("ranges", []))]
        inv_rules = [r for r in cf_rules if any(rng.get("startColumnIndex") == inv_col for rng in r.get("ranges", []))]
        self.assertEqual(len(val_rules), 2)
        self.assertEqual(len(inv_rules), 2)

        # Not > 5% gradient and threshold rules present
        n5_rules = [r for r in cf_rules if any(rng.get("startColumnIndex") == n5_col for rng in r.get("ranges", []))]
        self.assertEqual(len(n5_rules), 4)
        has_gradient = any("gradientRule" in r for r in n5_rules)
        self.assertTrue(has_gradient, "Expected gradientRule on Not > 5% column")


if __name__ == "__main__":
    unittest.main()
