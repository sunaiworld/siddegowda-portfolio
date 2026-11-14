import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import mutual_fund_builder
import sheet_formatter


class TestMutualFundOverlap(unittest.TestCase):
    """
    Tests for Mutual Fund Overlap Detection:
    1. Same fund in Groww + Zerodha -> Overlap = YES
    2. Fund only in Groww -> Overlap = NO
    3. Fund only in Zerodha -> Overlap = NO
    4. Different schemes from the same AMC -> Overlap = NO
    5. Same fund with naming variations -> correctly detected
    6. No duplicate rows created
    7. Existing tax calculations remain unchanged
    8. Header and column structure in written sheet
    """

    def test_same_fund_in_groww_and_zerodha(self):
        """Case 1: Same fund held in Groww and Zerodha -> Overlap = YES."""
        holdings = {
            "Groww:INF966L01689": {
                "fund_name": "Quant Small Cap Fund Direct Plan Growth",
                "isin": "INF966L01689",
                "broker": "Groww",
                "total_invested": 50000.0,
            },
            "Zerodha:INF966L01689": {
                "fund_name": "Quant Small Cap Fund - Direct Plan",
                "isin": "INF966L01689",
                "broker": "Zerodha",
                "total_invested": 75000.0,
            }
        }
        overlap_info = mutual_fund_builder.compute_mf_overlap(holdings)

        # Groww holding
        self.assertEqual(overlap_info["Groww:INF966L01689"]["overlap"], "YES")
        self.assertEqual(overlap_info["Groww:INF966L01689"]["overlap_with"], "ZERODHA - SELF")

        # Zerodha holding
        self.assertEqual(overlap_info["Zerodha:INF966L01689"]["overlap"], "YES")
        self.assertEqual(overlap_info["Zerodha:INF966L01689"]["overlap_with"], "GROWW - DAD")

    def test_fund_only_in_groww(self):
        """Case 2: Fund held only in Groww -> Overlap = NO, Overlap With = empty."""
        holdings = {
            "Groww:INF123456789": {
                "fund_name": "Unique Groww Fund Direct Growth",
                "isin": "INF123456789",
                "broker": "Groww",
                "total_invested": 25000.0,
            },
            "Zerodha:INF999999999": {
                "fund_name": "Different Zerodha Fund Direct Growth",
                "isin": "INF999999999",
                "broker": "Zerodha",
                "total_invested": 30000.0,
            }
        }
        overlap_info = mutual_fund_builder.compute_mf_overlap(holdings)
        self.assertEqual(overlap_info["Groww:INF123456789"]["overlap"], "NO")
        self.assertEqual(overlap_info["Groww:INF123456789"]["overlap_with"], "")

    def test_fund_only_in_zerodha(self):
        """Case 3: Fund held only in Zerodha -> Overlap = NO, Overlap With = empty."""
        holdings = {
            "Zerodha:INF966L01721": {
                "fund_name": "Quant Infrastructure Fund - Direct Plan",
                "isin": "INF966L01721",
                "broker": "Zerodha",
                "total_invested": 40000.0,
            }
        }
        overlap_info = mutual_fund_builder.compute_mf_overlap(holdings)
        self.assertEqual(overlap_info["Zerodha:INF966L01721"]["overlap"], "NO")
        self.assertEqual(overlap_info["Zerodha:INF966L01721"]["overlap_with"], "")

    def test_different_schemes_same_amc_no_overlap(self):
        """Case 4: Different schemes from the same AMC -> Overlap = NO."""
        holdings = {
            "Groww:INF966L01689": {
                "fund_name": "Quant Small Cap Fund Direct Growth",
                "isin": "INF966L01689",
                "broker": "Groww",
                "total_invested": 50000.0,
            },
            "Zerodha:INF966L01721": {
                "fund_name": "Quant Infrastructure Fund - Direct Plan",
                "isin": "INF966L01721",
                "broker": "Zerodha",
                "total_invested": 60000.0,
            },
            "Zerodha:INF966L01911": {
                "fund_name": "Quant Flexi Cap Fund - Direct Plan",
                "isin": "INF966L01911",
                "broker": "Zerodha",
                "total_invested": 70000.0,
            }
        }
        overlap_info = mutual_fund_builder.compute_mf_overlap(holdings)
        for key in holdings:
            self.assertEqual(
                overlap_info[key]["overlap"], "NO",
                f"{key} should NOT overlap as schemes are different"
            )
            self.assertEqual(overlap_info[key]["overlap_with"], "")

    def test_naming_variations_without_isin(self):
        """Case 5: Same fund with slight naming variations when ISIN is missing -> Overlap = YES."""
        holdings = {
            "Groww:custom_key_1": {
                "fund_name": "Bandhan Small Cap Fund Direct Growth",
                "isin": "",
                "broker": "Groww",
                "total_invested": 20000.0,
            },
            "Zerodha:custom_key_2": {
                "fund_name": "Bandhan Small Cap Fund - Direct Plan - Growth Option",
                "isin": "",
                "broker": "Zerodha",
                "total_invested": 20000.0,
            }
        }
        overlap_info = mutual_fund_builder.compute_mf_overlap(holdings)
        self.assertEqual(overlap_info["Groww:custom_key_1"]["overlap"], "YES")
        self.assertEqual(overlap_info["Groww:custom_key_1"]["overlap_with"], "ZERODHA - SELF")
        self.assertEqual(overlap_info["Zerodha:custom_key_2"]["overlap"], "YES")
        self.assertEqual(overlap_info["Zerodha:custom_key_2"]["overlap_with"], "GROWW - DAD")

    def test_three_way_overlap_with_wife(self):
        """Test a fund held across Groww, Zerodha, and Wife accounts."""
        holdings = {
            "Groww:INF760K01JC6": {
                "fund_name": "Canara Robeco Small Cap Fund Direct Growth",
                "isin": "INF760K01JC6",
                "broker": "Groww",
                "total_invested": 50000.0,
            },
            "Zerodha:INF760K01JC6": {
                "fund_name": "Canara Robeco Small Cap Fund - Direct Plan",
                "isin": "INF760K01JC6",
                "broker": "Zerodha",
                "total_invested": 50000.0,
            },
            "Wife:INF760K01JC6": {
                "fund_name": "Canara Robeco Small Cap Fund Direct Growth",
                "isin": "INF760K01JC6",
                "broker": "Wife",
                "total_invested": 50000.0,
            }
        }
        overlap_info = mutual_fund_builder.compute_mf_overlap(holdings)
        self.assertEqual(overlap_info["Groww:INF760K01JC6"]["overlap"], "YES")
        self.assertEqual(overlap_info["Groww:INF760K01JC6"]["overlap_with"], "ZERODHA - SELF, WIFE MUTUAL FUNDS")

        self.assertEqual(overlap_info["Zerodha:INF760K01JC6"]["overlap"], "YES")
        self.assertEqual(overlap_info["Zerodha:INF760K01JC6"]["overlap_with"], "GROWW - DAD, WIFE MUTUAL FUNDS")

        self.assertEqual(overlap_info["Wife:INF760K01JC6"]["overlap"], "YES")
        self.assertEqual(overlap_info["Wife:INF760K01JC6"]["overlap_with"], "GROWW - DAD, ZERODHA - SELF")

    def test_write_mutual_funds_overlap_columns(self):
        """Case 6 & 8: Verify Overlap columns are written and no duplicate rows are created."""
        trades = mutual_fund_builder.load_all_mf_trades()
        holdings = mutual_fund_builder.compute_mf_holdings(trades)
        tax_data = mutual_fund_builder.compute_tax_harvest(holdings)
        overlap_data = mutual_fund_builder.compute_mf_overlap(holdings)

        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 12345
        mock_ws.get_all_values.return_value = []
        mock_sh.worksheet.return_value = mock_ws

        captured_updates = []
        captured_reqs = []

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe", side_effect=lambda ws, cell, data, **kw: captured_updates.append(data)), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, reqs: captured_reqs.extend(reqs)):
            mutual_fund_builder.write_mutual_funds(mock_sh, holdings, tax_data, overlap_data=overlap_data)

        self.assertTrue(len(captured_updates) > 0)
        table = captured_updates[0]

        # 1. Header checks
        headers = table[0]
        self.assertEqual(len(headers), 26, "Expected 26 columns in Mutual Funds tab")
        self.assertNotIn("Day Gain Rs", headers)
        self.assertNotIn("Day Gain%", headers)
        self.assertNotIn("Harvestable Units", headers)
        self.assertNotIn("Trend", headers)
        self.assertEqual(headers[24], "Overlap")
        self.assertEqual(headers[25], "Overlap With")

        # 2. Row counts: exactly total active holdings rows
        total_data_rows = [r for r in table[1:] if r[24] in ("YES", "NO")]
        self.assertEqual(len(total_data_rows), len(holdings), "No duplicate rows should be created")

        # 3. Check overlapping and non-overlapping fund rows
        overlap_yes_rows = [r for r in total_data_rows if r[24] == "YES"]
        self.assertGreater(len(overlap_yes_rows), 0, "Expected at least one overlapping fund in active data")
        for r in overlap_yes_rows:
            self.assertNotEqual(r[25], "", "Overlap With must not be empty when Overlap is YES")

        overlap_no_rows = [r for r in total_data_rows if r[24] == "NO"]
        self.assertGreater(len(overlap_no_rows), 0, "Expected at least one non-overlapping fund in active data")
        for r in overlap_no_rows:
            self.assertEqual(r[25], "", "Overlap With must be empty when Overlap is NO")

        # 4. Tax calculations check: combined total invested untouched
        combined_total = next(r for r in table if r[0] == "COMBINED TOTAL")
        self.assertGreater(float(combined_total[5]), 0)
        # Overlap columns in subtotal/total rows must be blank
        self.assertEqual(combined_total[24], "")
        self.assertEqual(combined_total[25], "")

        # 5. Format requests: basic filter spans all 26 columns
        filter_req = next((r["setBasicFilter"]["filter"]["range"] for r in captured_reqs if "setBasicFilter" in r), None)
        self.assertIsNotNone(filter_req)
        self.assertEqual(filter_req["endColumnIndex"], 26)

        # 6. Overlap cell color formatting applied
        has_overlap_yes_color = any(
            r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 24 and
            r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == sheet_formatter.hex_rgb("fff2cc")
            for r in captured_reqs
        )
        self.assertTrue(has_overlap_yes_color, "Expected amber highlight for Overlap = YES cells")

        # 7. Fund Name (col 0) & Category (col 1) light red highlighting for non-core funds
        has_cat_highlight = any(
            r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 1 and
            r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == sheet_formatter.hex_rgb("fde9d9")
            for r in captured_reqs
        )
        self.assertTrue(has_cat_highlight, "Expected light red highlight for non-core Category cells")

        # 8. Weight% (col 9) colour formatting applied
        has_weight_colors = any(
            r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 9 and
            "backgroundColor" in r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {})
            for r in captured_reqs
        )
        self.assertTrue(has_weight_colors, "Expected color formatting for Weight% cells")

        # 9. Decision Reason (col 23) colour formatting applied
        has_decision_reason_colors = any(
            r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 23 and
            "backgroundColor" in r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {})
            for r in captured_reqs
        )
        self.assertTrue(has_decision_reason_colors, "Expected color formatting for Decision Reason cells")

    def test_mutual_funds_conditional_highlighting(self):
        """Verify explicit highlighting rules for Category, Decision Reason, and Weight%."""
        holdings = {
            "Zerodha:F1": {
                "fund_name": "Mid Cap Alpha",
                "category": "Mid Cap",
                "avg_nav": 100.0,
                "total_units": 400.0,
                "total_invested": 40000.0,
                "broker": "Zerodha",
                "isin": "INF001",
            },
            "Zerodha:F2": {
                "fund_name": "Small Cap Beta",
                "category": "Small Cap",
                "avg_nav": 50.0,
                "total_units": 600.0,
                "total_invested": 30000.0,
                "broker": "Zerodha",
                "isin": "INF002",
            },
            "Zerodha:F3": {
                "fund_name": "Flexi Cap Gamma",
                "category": "Flexi Cap",
                "avg_nav": 200.0,
                "total_units": 100.0,
                "total_invested": 20000.0,
                "broker": "Zerodha",
                "isin": "INF003",
            },
            "Zerodha:F4": {
                "fund_name": "Thematic Infra Delta",
                "category": "Sectoral/Thematic",
                "avg_nav": 25.0,
                "total_units": 400.0,
                "total_invested": 10000.0,
                "broker": "Zerodha",
                "isin": "INF004",
            },
        }
        tax_data = {}
        overlap_data = {}

        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 999
        mock_ws.get_all_values.return_value = []
        mock_sh.worksheet.return_value = mock_ws

        captured_reqs = []
        captured_data = []

        def mock_analyzer(isin):
            if isin == "INF001":
                return {"ret_1y": 0.2, "ret_3y": 0.15, "ret_5y": 0.18, "overall_score": 80, "decision": "BUY / ADD", "reason": "Strong long-term performance and good risk metrics."}
            elif isin == "INF002":
                return {"ret_1y": 0.1, "ret_3y": 0.12, "ret_5y": 0.10, "overall_score": 65, "decision": "HOLD", "reason": "Stable performance, meets standard expectations."}
            elif isin == "INF003":
                return {"ret_1y": 0.05, "ret_3y": 0.08, "ret_5y": 0.06, "overall_score": 50, "decision": "WATCH", "reason": "Average performance or lack of sufficient history."}
            else:
                return {"ret_1y": -0.05, "ret_3y": 0.02, "ret_5y": 0.01, "overall_score": 30, "decision": "REVIEW / REDUCE", "reason": "Underperforming over long periods or high downside risk."}

        with patch("sheet_writer.clear_sheet_safe"), \
             patch("sheet_writer.update_sheet_safe", side_effect=lambda ws, cell, data, **kw: captured_data.append(data)), \
             patch("sheet_writer.batch_update_safe", side_effect=lambda sh, reqs: captured_reqs.extend(reqs)), \
             patch("mf_analyzer.analyze_fund", side_effect=mock_analyzer):
            mutual_fund_builder.write_mutual_funds(mock_sh, holdings, tax_data, overlap_data=overlap_data)

        # Check rows in table
        table = captured_data[0]
        # F1 (Mid Cap) -> row index 2 (0: header, 1: ZERODHA section header, 2: F1)
        # F2 (Small Cap) -> row index 3
        # F3 (Flexi Cap) -> row index 4
        # F4 (Sectoral/Thematic) -> row index 5

        # Check that Mid Cap (F1) at row index 2 has col 0 and col 1 colored in light red (fde9d9)
        f1_col0_color = next((r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 2 and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 0 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 1), None)
        f1_col1_color = next((r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 2 and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 1 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 2), None)
        self.assertIsNotNone(f1_col0_color, "F1 Fund Name should be highlighted")
        self.assertIsNotNone(f1_col1_color, "F1 Category should be highlighted")
        self.assertEqual(f1_col0_color["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"], sheet_formatter.hex_rgb("fde9d9"))
        self.assertEqual(f1_col1_color["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"], sheet_formatter.hex_rgb("fde9d9"))

        # Check that Small Cap (F2), Flexi Cap (F3), Sectoral/Thematic (F4) do NOT have light red on col 0 or col 1
        for row_idx in [3, 4, 5]:
            col0 = next((r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == row_idx and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 0 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 1 and r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == sheet_formatter.hex_rgb("fde9d9")), None)
            col1 = next((r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == row_idx and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 1 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 2 and r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == sheet_formatter.hex_rgb("fde9d9")), None)
            self.assertIsNone(col0, f"Row {row_idx} should NOT have light red on Fund Name")
            self.assertIsNone(col1, f"Row {row_idx} should NOT have light red on Category")

        # Check Decision Reason (col 23) colors:
        # Row 2 (Strong) -> d9ead3 (light green)
        # Row 3 (Stable) -> e8eaf6 (light indigo/blue)
        # Row 4 (Average) -> fff2cc (light yellow/amber)
        # Row 5 (Underperforming) -> fce8b2 (light orange/red)
        strong_cell = next(r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 2 and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 23 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 24)
        stable_cell = next(r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 3 and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 23 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 24)
        avg_cell = next(r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 4 and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 23 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 24)
        under_cell = next(r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 5 and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 23 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 24)

        self.assertEqual(strong_cell["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"], sheet_formatter.hex_rgb("d9ead3"))
        self.assertEqual(stable_cell["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"], sheet_formatter.hex_rgb("e8eaf6"))
        self.assertEqual(avg_cell["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"], sheet_formatter.hex_rgb("fff2cc"))
        self.assertEqual(under_cell["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"], sheet_formatter.hex_rgb("fce8b2"))

        # Check Weight% (col 9) has background color applied for data rows
        for row_idx in [2, 3, 4, 5]:
            wt_cell = next((r for r in captured_reqs if r.get("repeatCell", {}).get("range", {}).get("startRowIndex") == row_idx and r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 9 and r.get("repeatCell", {}).get("range", {}).get("endColumnIndex") == 10 and "backgroundColor" in r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {})), None)
            self.assertIsNotNone(wt_cell, f"Row {row_idx} should have color applied to Weight%")


if __name__ == "__main__":
    unittest.main()
