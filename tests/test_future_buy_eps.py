import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import sheet_formatter
from sheet_formatter import (
    get_mcap_category,
    get_future_buy_eps_colors,
    build_github_data_format_requests,
)
import future_buy_builder
import github_data_builder


class TestFutureBuyEPSFormatting(unittest.TestCase):
    """
    Tests for EPS conditional formatting rules in Future Buy tab:
    - Small Cap:  ₹5 to ₹20  -> Green, outside -> Red
    - Mid Cap:    ₹20 to ₹50 -> Green, outside -> Red
    - Large Cap:  ₹50 and up -> Green, outside -> Red
    """

    def test_mcap_category_classification(self):
        # Numeric values in Crores (>= 25000: Large, >= 5000: Mid, < 5000: Small)
        self.assertEqual(get_mcap_category(50000), "Large Cap")
        self.assertEqual(get_mcap_category(25000), "Large Cap")
        self.assertEqual(get_mcap_category(24999), "Mid Cap")
        self.assertEqual(get_mcap_category(5000), "Mid Cap")
        self.assertEqual(get_mcap_category(4999), "Small Cap")
        self.assertEqual(get_mcap_category(100), "Small Cap")

        # Formatted Indian Rupee Cr strings
        self.assertEqual(get_mcap_category("₹50,000 Cr"), "Large Cap")
        self.assertEqual(get_mcap_category("₹15,000 Cr"), "Mid Cap")
        self.assertEqual(get_mcap_category("₹2,500 Cr"), "Small Cap")

        # Direct text labels (case-insensitive)
        self.assertEqual(get_mcap_category("Large Cap"), "Large Cap")
        self.assertEqual(get_mcap_category("large cap"), "Large Cap")
        self.assertEqual(get_mcap_category("Mid Cap"), "Mid Cap")
        self.assertEqual(get_mcap_category("mid cap"), "Mid Cap")
        self.assertEqual(get_mcap_category("Small Cap"), "Small Cap")
        self.assertEqual(get_mcap_category("small cap"), "Small Cap")

        # Edge cases: None, blank, non-numeric
        self.assertIsNone(get_mcap_category(None))
        self.assertIsNone(get_mcap_category(""))
        self.assertIsNone(get_mcap_category("   "))
        self.assertIsNone(get_mcap_category("N/A"))
        self.assertIsNone(get_mcap_category("unknown"))

    def test_small_cap_eps_boundaries(self):
        green = ("d9ead3", "0b8043")
        red = ("fde9d9", "c62828")

        # Small Cap: EPS >= 5 and <= 20 -> Green, outside -> Red
        self.assertEqual(get_future_buy_eps_colors(4, "Small Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(4.99, "Small Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(5, "Small Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(5.0, "Small Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(12.5, "Small Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(20, "Small Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(20.0, "Small Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(20.01, "Small Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(21, "Small Cap"), red)

    def test_mid_cap_eps_boundaries(self):
        green = ("d9ead3", "0b8043")
        red = ("fde9d9", "c62828")

        # Mid Cap: EPS >= 20 and <= 50 -> Green, outside -> Red
        self.assertEqual(get_future_buy_eps_colors(19, "Mid Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(19.99, "Mid Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(20, "Mid Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(20.0, "Mid Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(35.0, "Mid Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(50, "Mid Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(50.0, "Mid Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(50.01, "Mid Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(51, "Mid Cap"), red)

    def test_large_cap_eps_boundaries(self):
        green = ("d9ead3", "0b8043")
        red = ("fde9d9", "c62828")

        # Large Cap: EPS >= 50 -> Green, < 50 -> Red
        self.assertEqual(get_future_buy_eps_colors(49, "Large Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(49.99, "Large Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(50, "Large Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(50.0, "Large Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(100, "Large Cap"), green)
        self.assertEqual(get_future_buy_eps_colors(250.5, "Large Cap"), green)

    def test_special_and_edge_cases(self):
        red = ("fde9d9", "c62828")

        # Zero is outside acceptable ranges -> Red
        self.assertEqual(get_future_buy_eps_colors(0, "Small Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(0, "Mid Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(0, "Large Cap"), red)

        # Negative EPS -> Red
        self.assertEqual(get_future_buy_eps_colors(-5, "Small Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(-15, "Mid Cap"), red)
        self.assertEqual(get_future_buy_eps_colors(-50, "Large Cap"), red)

        # String representations with symbols
        self.assertEqual(get_future_buy_eps_colors("₹15.5", "Small Cap"), ("d9ead3", "0b8043"))
        self.assertEqual(get_future_buy_eps_colors("₹30", "Mid Cap"), ("d9ead3", "0b8043"))
        self.assertEqual(get_future_buy_eps_colors("₹75.00", "Large Cap"), ("d9ead3", "0b8043"))

        # Missing / invalid inputs return None safely (no crash)
        self.assertIsNone(get_future_buy_eps_colors(None, "Large Cap"))
        self.assertIsNone(get_future_buy_eps_colors("", "Small Cap"))
        self.assertIsNone(get_future_buy_eps_colors("N/A", "Mid Cap"))
        self.assertIsNone(get_future_buy_eps_colors(25, None))

    def test_build_format_requests_future_buy_vs_github_data(self):
        C = github_data_builder.GITHUB_DATA_COLS
        eps_col = C["eps"]

        # Row 1: Small Cap, EPS = 4 (In Future Buy: Red; In GITHUB DATA: Green because 4 > 0)
        r_small_4 = [""] * len(C)
        r_small_4[C["symbol"]] = "SMALL4"
        r_small_4[C["mcap"]] = "₹2,000 Cr"
        r_small_4[C["eps"]] = 4.0

        # Row 2: Small Cap, EPS = 5 (In Future Buy: Green)
        r_small_5 = [""] * len(C)
        r_small_5[C["symbol"]] = "SMALL5"
        r_small_5[C["mcap"]] = "₹2,000 Cr"
        r_small_5[C["eps"]] = 5.0

        # Row 3: Mid Cap, EPS = 19 (In Future Buy: Red)
        r_mid_19 = [""] * len(C)
        r_mid_19[C["symbol"]] = "MID19"
        r_mid_19[C["mcap"]] = "₹10,000 Cr"
        r_mid_19[C["eps"]] = 19.0

        # Row 4: Mid Cap, EPS = 20 (In Future Buy: Green)
        r_mid_20 = [""] * len(C)
        r_mid_20[C["symbol"]] = "MID20"
        r_mid_20[C["mcap"]] = "₹10,000 Cr"
        r_mid_20[C["eps"]] = 20.0

        # Row 5: Large Cap, EPS = 49 (In Future Buy: Red)
        r_large_49 = [""] * len(C)
        r_large_49[C["symbol"]] = "LARGE49"
        r_large_49[C["mcap"]] = "₹50,000 Cr"
        r_large_49[C["eps"]] = 49.0

        # Row 6: Large Cap, EPS = 50 (In Future Buy: Green)
        r_large_50 = [""] * len(C)
        r_large_50[C["symbol"]] = "LARGE50"
        r_large_50[C["mcap"]] = "₹50,000 Cr"
        r_large_50[C["eps"]] = 50.0

        rows = [r_small_4, r_small_5, r_mid_19, r_mid_20, r_large_49, r_large_50]

        # 1. Format for Future Buy
        fb_reqs = build_github_data_format_requests(0, rows, start_row=0, tab_name="Future Buy")
        # Extract EPS repeatCell color for each row (rows start at index 2)
        fb_eps_colors = {}
        for req in fb_reqs:
            if "repeatCell" in req:
                rng = req["repeatCell"]["range"]
                if rng.get("startColumnIndex") == eps_col and rng.get("endColumnIndex") == eps_col + 1:
                    uef = req["repeatCell"]["cell"].get("userEnteredFormat", {})
                    if "backgroundColor" in uef:
                        row_idx = rng.get("startRowIndex")
                        fb_eps_colors[row_idx] = uef["backgroundColor"]

        green_rgb = sheet_formatter.hex_rgb("d9ead3")
        red_rgb = sheet_formatter.hex_rgb("fde9d9")

        # Row 0 (sheet row 2): Small Cap EPS 4 -> Red
        self.assertEqual(fb_eps_colors[2], red_rgb)
        # Row 1 (sheet row 3): Small Cap EPS 5 -> Green
        self.assertEqual(fb_eps_colors[3], green_rgb)
        # Row 2 (sheet row 4): Mid Cap EPS 19 -> Red
        self.assertEqual(fb_eps_colors[4], red_rgb)
        # Row 3 (sheet row 5): Mid Cap EPS 20 -> Green
        self.assertEqual(fb_eps_colors[5], green_rgb)
        # Row 4 (sheet row 6): Large Cap EPS 49 -> Red
        self.assertEqual(fb_eps_colors[6], red_rgb)
        # Row 5 (sheet row 7): Large Cap EPS 50 -> Green
        self.assertEqual(fb_eps_colors[7], green_rgb)

        # 2. Format for GITHUB DATA (or tab_name=None) — must preserve original logic
        gh_reqs = build_github_data_format_requests(0, rows, start_row=0, tab_name="GITHUB DATA")
        gh_eps_colors = {}
        for req in gh_reqs:
            if "repeatCell" in req:
                rng = req["repeatCell"]["range"]
                if rng.get("startColumnIndex") == eps_col and rng.get("endColumnIndex") == eps_col + 1:
                    uef = req["repeatCell"]["cell"].get("userEnteredFormat", {})
                    if "backgroundColor" in uef:
                        row_idx = rng.get("startRowIndex")
                        gh_eps_colors[row_idx] = uef["backgroundColor"]

        # In GITHUB DATA, all positive EPS are Green
        for r_idx in range(2, 8):
            self.assertEqual(gh_eps_colors[r_idx], green_rgb)

    def test_write_future_buy_tab_e2e_formatting(self):
        """End-to-end test verifying write_future_buy_tab applies Market Cap-based EPS formatting."""
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 12345
        mock_sh.worksheet.return_value = mock_ws

        C = github_data_builder.GITHUB_DATA_COLS
        eps_col = C["eps"]

        # Small Cap with EPS 4 (Red), Small Cap with EPS 5 (Green)
        r1 = [""] * len(C)
        r1[C["symbol"]] = "S_RED"
        r1[C["mcap"]] = "₹1,500 Cr"
        r1[C["eps"]] = 4.0
        r1[C["buying_zone"]] = "🟢 ACCUMULATE"
        r1[C["total"]] = 75
        r1[C["action"]] = "BUY"

        r2 = [""] * len(C)
        r2[C["symbol"]] = "S_GRN"
        r2[C["mcap"]] = "₹1,500 Cr"
        r2[C["eps"]] = 5.0
        r2[C["buying_zone"]] = "🟢 ACCUMULATE"
        r2[C["total"]] = 70
        r2[C["action"]] = "BUY"

        rows = [r1, r2]

        captured_reqs = []
        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe"), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, r: captured_reqs.extend(r)):
            future_buy_builder.write_future_buy_tab(mock_sh, rows, tab_name="Future Buy")

        # In write_future_buy_tab:
        # top10 has 2 rows (indices 2, 3), sep is 4, group_hdr is 5, col_hdr is 6,
        # data rows start at index 7 (r1=7, r2=8)
        eps_styles = {}
        for req in captured_reqs:
            if "repeatCell" in req:
                rng = req["repeatCell"]["range"]
                if rng.get("startColumnIndex") == eps_col and rng.get("endColumnIndex") == eps_col + 1:
                    uef = req["repeatCell"]["cell"].get("userEnteredFormat", {})
                    if "backgroundColor" in uef:
                        eps_styles[rng.get("startRowIndex")] = uef["backgroundColor"]

        green_rgb = sheet_formatter.hex_rgb("d9ead3")
        red_rgb = sheet_formatter.hex_rgb("fde9d9")

        self.assertEqual(eps_styles[7], red_rgb)
        self.assertEqual(eps_styles[8], green_rgb)


if __name__ == "__main__":
    unittest.main()
