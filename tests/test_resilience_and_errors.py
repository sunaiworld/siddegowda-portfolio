import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import github_data_builder
import future_buy_builder
import sheet_writer
from sheet_writer import FormattingWriteError
import sheet_formatter


class TestResilienceAndErrors(unittest.TestCase):

    def setUp(self):
        self.mock_sh = MagicMock()
        self.mock_ws = MagicMock()
        self.mock_ws.id = 101
        self.mock_sh.worksheet.return_value = self.mock_ws

    @patch("sheet_writer.time.sleep", return_value=None)
    def test_write_github_data_normal_execution(self, mock_sleep):
        C = github_data_builder.GITHUB_DATA_COLS
        sample_row = [""] * len(C)
        sample_row[C["symbol"]] = "INFY"
        sample_row[C["action"]] = "STRONG BUY"
        sample_row[C["buying_zone"]] = "🟢🟢 ADD AGGRESSIVELY"

        with patch("sheet_writer.clear_sheet_safe") as mock_clear,              patch("sheet_writer.batch_update_safe") as mock_batch,              patch("sheet_writer.update_sheet_safe") as mock_update:
            ws = github_data_builder.write_github_data(self.mock_sh, [sample_row], tab_name="GITHUB DATA")
            self.assertEqual(ws, self.mock_ws)
            self.assertTrue(mock_clear.called)
            self.assertTrue(mock_update.called)
            self.assertTrue(mock_batch.called)

    @patch("sheet_writer.time.sleep", return_value=None)
    def test_write_future_buy_tab_normal_execution(self, mock_sleep):
        C = github_data_builder.GITHUB_DATA_COLS
        sample_row = [""] * len(C)
        sample_row[C["symbol"]] = "TCS"
        sample_row[C["action"]] = "BUY"
        sample_row[C["buying_zone"]] = "🟢 ACCUMULATE"

        with patch("sheet_writer.clear_sheet_safe") as mock_clear,              patch("sheet_writer.batch_update_safe") as mock_batch,              patch("sheet_writer.update_sheet_safe") as mock_update:
            ws = future_buy_builder.write_future_buy_tab(
                self.mock_sh, [sample_row], tab_name="Future Buy",
                sector_weights={"Technology": 12.0}, portfolio_value=10000000
            )
            self.assertEqual(ws, self.mock_ws)
            self.assertTrue(mock_clear.called)
            self.assertTrue(mock_update.called)
            self.assertTrue(mock_batch.called)

    @patch("sheet_writer.time.sleep", return_value=None)
    def test_write_github_data_clear_failure_raises_explicitly(self, mock_sleep):
        C = github_data_builder.GITHUB_DATA_COLS
        sample_row = [""] * len(C)
        sample_row[C["symbol"]] = "RELIANCE"

        with patch("sheet_writer.clear_sheet_safe", side_effect=Exception("API clear error 503")),              self.assertLogs("portfolio", level="ERROR") as cm:
            with self.assertRaises(Exception) as ctx:
                github_data_builder.write_github_data(self.mock_sh, [sample_row], tab_name="GITHUB DATA")
            self.assertIn("API clear error 503", str(ctx.exception))
            # Verify log contains watchlist, tab, stage, and exception details
            log_output = " ".join(cm.output)
            self.assertIn("stage='clear worksheet'", log_output)
            self.assertIn("tab='GITHUB DATA'", log_output)

    @patch("sheet_writer.time.sleep", return_value=None)
    def test_write_future_buy_formatting_failure_raises_explicitly(self, mock_sleep):
        C = github_data_builder.GITHUB_DATA_COLS
        sample_row = [""] * len(C)
        sample_row[C["symbol"]] = "HDFCBANK"

        def batch_side_effect(sh, reqs):
            # Fail only on the large formatting batch, not clear_all_formatting_reqs
            if len(reqs) > 5:
                raise FormattingWriteError("Formatting failed after retries")
            return None

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe"), \
             patch("sheet_writer.batch_update_safe", side_effect=batch_side_effect), \
             self.assertLogs("portfolio", level="ERROR") as cm:
            with self.assertRaises(FormattingWriteError) as ctx:
                future_buy_builder.write_future_buy_tab(
                    self.mock_sh, [sample_row], tab_name="Future Buy"
                )
            self.assertIn("Formatting failed after retries", str(ctx.exception))
            log_output = " ".join(cm.output)
            self.assertIn("stage='apply formatting'", log_output)
            self.assertIn("tab='Future Buy'", log_output)

    @patch("sheet_writer.time.sleep", return_value=None)
    def test_process_all_watchlists_raises_on_failure(self, mock_sleep):
        with patch("future_buy_builder.process_watchlist_tab", side_effect=Exception("Watchlist Sheets quota 429")),              self.assertLogs("portfolio", level="ERROR") as cm:
            with self.assertRaises(Exception) as ctx:
                future_buy_builder.process_all_watchlists(self.mock_sh)
            self.assertIn("Watchlist Sheets quota 429", str(ctx.exception))
            log_output = " ".join(cm.output)
            self.assertIn("[process_all_watchlists]", log_output)
            self.assertIn("FAILED", log_output)

    def test_centralized_color_constants_integrity(self):
        # Verify color constants are defined in sheet_formatter with exact hex values
        self.assertIn("STRONG BUY", sheet_formatter.GITHUB_DATA_ACTION_COLORS)
        self.assertEqual(sheet_formatter.GITHUB_DATA_ACTION_COLORS["STRONG BUY"], ("c6efce", "276221"))
        self.assertIn("🟢🟢 ADD AGGRESSIVELY", sheet_formatter.GITHUB_DATA_BUYING_ZONE_COLORS)
        self.assertEqual(sheet_formatter.GITHUB_DATA_BUYING_ZONE_COLORS["🟢🟢 ADD AGGRESSIVELY"], ("c6efce", "276221"))
        self.assertIn("🟢🟢 ADD AGGRESSIVELY", sheet_formatter.GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS)
        self.assertEqual(sheet_formatter.GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS["🟢🟢 ADD AGGRESSIVELY"], ("c8f5dc", "0b5e2a"))


    def test_identical_cell_formatting_between_github_data_and_future_buy(self):
        C = github_data_builder.GITHUB_DATA_COLS
        sample_rows = []
        for i in range(5):
            r = [""] * len(C)
            r[C["symbol"]] = f"SYM{i}"
            r[C["mcap"]] = "₹50,000 Cr"
            r[C["day_chg_pct"]] = 2.5
            r[C["return_1w"]] = -1.2
            r[C["return_1m"]] = 5.0
            r[C["trend"]] = "Uptrend"
            r[C["technical_setup"]] = "🟢 Tight Base"
            r[C["rsi"]] = 45
            r[C["pe"]] = 22.5
            r[C["eps"]] = 100.0
            r[C["roe"]] = 18.0
            r[C["action"]] = "BUY"
            r[C["buying_zone"]] = "🟢 ACCUMULATE"
            sample_rows.append(r)

        gh_reqs = []
        fb_reqs = []

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe"), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, r: gh_reqs.extend(r)):
            github_data_builder.write_github_data(self.mock_sh, sample_rows, tab_name="GITHUB DATA")

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe"), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, r: fb_reqs.extend(r)):
            future_buy_builder.write_future_buy_tab(self.mock_sh, sample_rows, tab_name="Future Buy")

        # In GITHUB DATA: row i is at row_index 2 + i (sheet row 3 + i)
        # In Future Buy: 5 rows means top10 has 5 items (indices 2..6), blank sep is index 7,
        # group header is index 8, col header is index 9, data starts at index 10 (sheet row 11)
        fb_data_start = 2 + len(sample_rows) + 1 + 2  # 10
        for row_idx in range(len(sample_rows)):
            gh_r = 2 + row_idx
            fb_r = fb_data_start + row_idx

            gh_cells = {
                (r["repeatCell"]["range"].get("startColumnIndex"), r["repeatCell"]["range"].get("endColumnIndex")): r["repeatCell"]["cell"]
                for r in gh_reqs if "repeatCell" in r and r["repeatCell"]["range"].get("startRowIndex") == gh_r and r["repeatCell"]["range"].get("endRowIndex") == gh_r + 1
            }
            fb_cells = {
                (r["repeatCell"]["range"].get("startColumnIndex"), r["repeatCell"]["range"].get("endColumnIndex")): r["repeatCell"]["cell"]
                for r in fb_reqs if "repeatCell" in r and r["repeatCell"]["range"].get("startRowIndex") == fb_r and r["repeatCell"]["range"].get("endRowIndex") == fb_r + 1
            }

            self.assertEqual(gh_cells.keys(), fb_cells.keys(), f"Mismatch in formatted column ranges on row {row_idx}")
            for col_key in gh_cells:
                self.assertEqual(
                    gh_cells[col_key], fb_cells[col_key],
                    f"Format difference at row {row_idx}, column {col_key}: GH={gh_cells[col_key]} vs FB={fb_cells[col_key]}"
                )


if __name__ == "__main__":
    unittest.main()
