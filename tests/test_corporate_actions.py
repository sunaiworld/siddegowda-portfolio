import unittest
import sys
from pathlib import Path

project_root = Path(__file__).parents[1]
sys.path.insert(0, str(project_root / "src"))

import corporate_actions
import portfolio_builder as pb


class TestCorporateActions(unittest.TestCase):

    def test_get_splits_for_symbol(self):
        splits = corporate_actions.get_splits_for_symbol("BECTORFOOD")
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0], ("2025-12-12", 5.0))

        splits_angel = corporate_actions.get_splits_for_symbol("ANGELONE")
        self.assertEqual(splits_angel, [("2026-02-26", 10.0)])

        self.assertEqual(corporate_actions.get_splits_for_symbol("UNKNOWN_SYM"), [])

    def test_split_applied_to_holdings(self):
        # 10 shares @ 1000 before 1:5 split -> becomes 50 shares @ 200
        mock_trades = [
            {
                "symbol": "BECTORFOOD",
                "isin": "INE495P01012",
                "date": "2024-05-10",
                "action": "BUY",
                "quantity": 10.0,
                "price": 1000.0,
                "broker": "Zerodha",
            }
        ]
        holdings = pb.compute_holdings(mock_trades)
        h = holdings["Zerodha:BECTORFOOD"]
        self.assertEqual(h["qty"], 50.0)
        self.assertEqual(h["avg_buy"], 200.0)
        self.assertEqual(h["cost"], 10000.0)

    def test_sold_out_across_split(self):
        # 35 shares bought pre-split, 1:10 split, then 350 sold post-split -> 0 open shares
        mock_trades = [
            {
                "symbol": "ANGELONE",
                "isin": "INE732I01013",
                "date": "2024-09-18",
                "action": "BUY",
                "quantity": 35.0,
                "price": 2600.0,
                "broker": "Zerodha",
            },
            {
                "symbol": "ANGELONE",
                "isin": "INE732I01021",
                "date": "2026-04-17",
                "action": "SELL",
                "quantity": 350.0,
                "price": 320.0,
                "broker": "Zerodha",
            },
        ]
        holdings = pb.compute_holdings(mock_trades)
        self.assertNotIn("Zerodha:ANGELONE", holdings)

    def test_split_aware_get_avg_buy_and_qty(self):
        # Test get_avg_buy_and_qty with legacy row format
        legacy_rows = [
            ["BECTORFOOD", "2024-02-20", "BUY", "10", "1000.0", "", ""],
        ]
        avg_buy, qty = pb.get_avg_buy_and_qty("BECTORFOOD", legacy_rows)
        self.assertEqual(qty, 50.0)
        self.assertEqual(avg_buy, 200.0)


if __name__ == "__main__":
    unittest.main()
