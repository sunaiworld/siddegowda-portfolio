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
        self.assertEqual(len(headers), 30, "Expected 30 columns in Mutual Funds tab")
        self.assertEqual(headers[28], "Overlap")
        self.assertEqual(headers[29], "Overlap With")

        # 2. Row counts: exactly total active holdings rows
        total_data_rows = [r for r in table[1:] if r[28] in ("YES", "NO")]
        self.assertEqual(len(total_data_rows), len(holdings), "No duplicate rows should be created")

        # 3. Check overlapping and non-overlapping fund rows
        overlap_yes_rows = [r for r in total_data_rows if r[28] == "YES"]
        self.assertGreater(len(overlap_yes_rows), 0, "Expected at least one overlapping fund in active data")
        for r in overlap_yes_rows:
            self.assertNotEqual(r[29], "", "Overlap With must not be empty when Overlap is YES")

        overlap_no_rows = [r for r in total_data_rows if r[28] == "NO"]
        self.assertGreater(len(overlap_no_rows), 0, "Expected at least one non-overlapping fund in active data")
        for r in overlap_no_rows:
            self.assertEqual(r[29], "", "Overlap With must be empty when Overlap is NO")

        # 4. Tax calculations check: combined total invested untouched
        combined_total = next(r for r in table if r[0] == "COMBINED TOTAL")
        self.assertGreater(float(combined_total[5]), 0)
        # Overlap columns in subtotal/total rows must be blank
        self.assertEqual(combined_total[28], "")
        self.assertEqual(combined_total[29], "")

        # 5. Format requests: basic filter spans all 30 columns
        filter_req = next((r["setBasicFilter"]["filter"]["range"] for r in captured_reqs if "setBasicFilter" in r), None)
        self.assertIsNotNone(filter_req)
        self.assertEqual(filter_req["endColumnIndex"], 30)

        # 6. Overlap cell color formatting applied
        has_overlap_yes_color = any(
            r.get("repeatCell", {}).get("range", {}).get("startColumnIndex") == 28 and
            r.get("repeatCell", {}).get("cell", {}).get("userEnteredFormat", {}).get("backgroundColor") == sheet_formatter.hex_rgb("fff2cc")
            for r in captured_reqs
        )
        self.assertTrue(has_overlap_yes_color, "Expected amber highlight for Overlap = YES cells")


if __name__ == "__main__":
    unittest.main()
