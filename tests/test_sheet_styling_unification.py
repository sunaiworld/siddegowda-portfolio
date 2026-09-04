import unittest
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import importlib
import github_data_builder
import portfolio_builder
import dividend_builder
import mutual_fund_builder

# Reload in case previous test files (e.g. test_p1_features) stubbed them in sys.modules
if not hasattr(dividend_builder, "write_dividends_tab"):
    importlib.reload(dividend_builder)
if not hasattr(mutual_fund_builder, "write_mutual_funds"):
    importlib.reload(mutual_fund_builder)

from github_data_builder import GITHUB_DATA_COL_WIDTHS, GITHUB_DATA_COLS

class TestSheetStylingUnification(unittest.TestCase):

    def test_first_column_widths_unified_to_100(self):
        """Verify first column (Symbol/Stock) width is unified to 100 across tabs."""
        self.assertEqual(GITHUB_DATA_COL_WIDTHS["symbol"], 100)

    @patch("sheet_writer.batch_update_safe")
    @patch("sheet_writer.clear_sheet_safe")
    @patch("sheet_writer.update_sheet_safe")
    def test_portfolio_styling_requests(self, mock_update, mock_clear, mock_batch):
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 10
        mock_sh.worksheet.return_value = mock_ws

        portfolio_data = {
            "combined": [
                {
                    "symbol": "TCS",
                    "investment_source": "Zerodha",
                    "shares": 10,
                    "avg_buy": 3500.0,
                    "cmp": 3800.0,
                    "invested": 35000.0,
                    "value": 38000.0,
                    "pnl": 3000.0,
                    "return_pct": 8.57,
                    "wt_pct": 5.0,
                    "sl_price": 3255.0,
                    "target": 4200.0,
                    "buy_more": 3150.0,
                    "signal": "BUY MORE",
                    "day_chg_pct": 1.2,
                    "return_1w": 2.5,
                    "return_1m": -0.8,
                    "return_3m": 4.1,
                    "return_6m": 7.3,
                }
            ],
            "groww": [],
            "zerodha": []
        }

        portfolio_builder.write_portfolio(mock_sh, portfolio_data)
        self.assertTrue(mock_clear.called)
        self.assertTrue(mock_update.called)
        self.assertTrue(mock_batch.called)

        reqs = mock_batch.call_args[0][1]
        # Check that no mergeCells request is generated across frozen/unfrozen boundary
        merge_reqs = [r for r in reqs if "mergeCells" in r]
        self.assertEqual(len(merge_reqs), 0)

        # Check that banner repeatCell was generated for section header with dark blue background
        banner_reqs = [
            r for r in reqs
            if "repeatCell" in r
            and r["repeatCell"].get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor", {}).get("red") == 31 / 255
        ]
        self.assertTrue(len(banner_reqs) >= 1)

        # Check that color_cell_req was generated for P&L, Return %, and Signal
        repeat_reqs = [r for r in reqs if "repeatCell" in r and "fields" in r["repeatCell"] and "userEnteredFormat.backgroundColor" in r["repeatCell"]["fields"]]
        self.assertTrue(len(repeat_reqs) > 0)

    @patch("sheet_writer.batch_update_safe")
    @patch("sheet_writer.clear_sheet_safe")
    @patch("sheet_writer.update_sheet_safe")
    def test_dividends_styling_requests(self, mock_update, mock_clear, mock_batch):
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 20
        mock_sh.worksheet.return_value = mock_ws
        mock_sh.fetch_sheet_metadata.return_value = {"sheets": []}

        sum_rows = [
            ["Stock", "2023 Dividend", "2024 Dividend", "Total Dividend", "Amount Invested", "Dividend %", "Market Dividend Yield %"],
            ["TCS", 1000.0, 1500.0, 2500.0, 50000.0, 5.0, 0.025],
            ["INFY", 500.0, 600.0, 1100.0, 60000.0, 1.83, 0.015]
        ]

        dividend_builder.write_dividends_tab(mock_sh, sum_rows)
        self.assertTrue(mock_clear.called)
        self.assertTrue(mock_update.called)
        self.assertTrue(mock_batch.called)

        # Ensure batch_update_safe was called with requests
        reqs = mock_batch.call_args[0][1]
        # Dimension properties for columns: first col width must be 100
        col_width_reqs = [r for r in reqs if "updateDimensionProperties" in r and r["updateDimensionProperties"]["range"]["dimension"] == "COLUMNS"]
        self.assertEqual(col_width_reqs[0]["updateDimensionProperties"]["properties"]["pixelSize"], 100)

if __name__ == "__main__":
    unittest.main()
