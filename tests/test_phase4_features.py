import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import history_tracker
import portfolio_analytics
import future_buy_builder
from github_data_builder import GITHUB_DATA_COLS


class MockWorksheet:
    def __init__(self, title="Sheet1", data=None):
        self.title = title
        self.id = 12345
        self._data = data or []

    def get_all_values(self):
        return [list(map(str, r)) for r in self._data]

    def row_values(self, row_idx):
        idx = row_idx - 1
        return [str(c) for c in self._data[idx]] if idx < len(self._data) else []

    def col_values(self, col_idx):
        idx = col_idx - 1
        return [str(r[idx]) if idx < len(r) else "" for r in self._data]

    def append_row(self, row):
        self._data.append(row)

    def append_rows(self, rows):
        self._data.extend(rows)

    def update(self, *args, **kwargs):
        if len(args) >= 2 and isinstance(args[1], list):
            self._data = args[1]
        elif "values" in kwargs and isinstance(kwargs["values"], list):
            self._data = kwargs["values"]

    def clear(self):
        self._data = []


class MockSpreadsheet:
    def __init__(self):
        self._sheets = {}

    def worksheet(self, title):
        if title not in self._sheets:
            raise Exception(f"Worksheet {title} not found")
        return self._sheets[title]

    def add_worksheet(self, title, rows=100, cols=20):
        ws = MockWorksheet(title)
        self._sheets[title] = ws
        return ws

    def batch_update(self, body):
        return {"replies": []}

    def fetch_sheet_metadata(self, *args, **kwargs):
        return {"sheets": []}


class TestPhase4Features(unittest.TestCase):

    # --------------------------------------------------------------------------
    # P1-1: Portfolio Risk & Drawdown Monitor
    # --------------------------------------------------------------------------
    def test_drawdown_metrics_with_history(self):
        sh = MockSpreadsheet()
        ws = sh.add_worksheet(history_tracker.PORTFOLIO_HISTORY_TAB)
        ws._data = [
            ["Date", "Portfolio Value", "Health Score"],
            ["2026-06-01", "10000000", "70"],
            ["2026-07-01", "12000000", "75"], # Peak 1 = 1.2 Cr (ATH)
            ["2026-07-15", "10800000", "72"], # Drop 1 = -10.0%
            ["2026-08-01", "10200000", "68"], # Drop 2 = -15.0% (MDD)
            ["2026-08-15", "11000000", "74"],
            ["2026-08-28", "11400000", "75"],
        ]

        # Live value today = 11,500,000
        metrics = history_tracker.compute_portfolio_drawdown_metrics(sh, 11500000)

        self.assertTrue(metrics["has_history"])
        self.assertEqual(metrics["ath_value"], 12000000.0)
        self.assertEqual(metrics["ath_date"], "2026-07-01")
        # Current DD: (11.5M - 12M) / 12M * 100 = -4.17%
        self.assertAlmostEqual(metrics["current_drawdown_pct"], -4.17, places=2)
        # MDD: (10.2M - 12M) / 12M * 100 = -15.0%
        self.assertAlmostEqual(metrics["max_drawdown_pct"], -15.0, places=1)
        # 30-day and 90-day returns should be computed
        self.assertIsNotNone(metrics["return_30d_pct"])
        self.assertIsNotNone(metrics["return_90d_pct"])

    def test_drawdown_metrics_insufficient_history(self):
        sh = MockSpreadsheet()
        # Empty Portfolio History
        metrics = history_tracker.compute_portfolio_drawdown_metrics(sh, 15000000)
        self.assertFalse(metrics["has_history"])
        self.assertEqual(metrics["current_drawdown_pct"], 0.0)
        self.assertEqual(metrics["max_drawdown_pct"], 0.0)
        self.assertIsNone(metrics["return_30d_pct"])

    @patch("sheet_writer.time.sleep", return_value=None)
    def test_dashboard_tab_drawdown_monitor(self, mock_sleep):
        sh = MockSpreadsheet()
        sh.add_worksheet("Dashboard")

        dash = {
            "portfolio_value": 15000000,
            "invested_value": 12000000,
            "total_pnl": 3000000,
            "return_pct": 25.0,
            "positions": [["RELIANCE", 1500000, 10.0, ""]],
            "num_holdings": 1,
            "top5_weight": 10.0,
            "signal_counts": {"HOLD": 1},
            "action_required": [],
            "source_summary": {"SELF": {"count": 1, "invested": 12000000, "value": 15000000}},
            "sector_detail": [{"sector": "Energy", "count": 1, "weight_pct": 10.0, "invested": 12000000, "value": 15000000, "pnl": 3000000, "return_pct": 25.0, "beta": 1.1}],
            "top_gainers": [],
            "top_losers": [],
        }

        drawdown = {
            "has_history": True,
            "ath_value": 16000000.0,
            "ath_date": "2026-08-01",
            "current_drawdown_pct": -6.25,
            "max_drawdown_pct": -12.5,
            "return_30d_pct": 3.5,
            "return_90d_pct": 10.2,
        }

        portfolio_analytics.write_dashboard_tab(sh, dash, drawdown_metrics=drawdown)
        d_ws = sh.worksheet("Dashboard")

        # Verify Risk & Drawdown Monitor section exists in rows
        content_str = " ".join([str(cell) for row in d_ws._data for cell in row])
        self.assertIn("Portfolio Risk & Drawdown Monitor", content_str)
        self.assertIn("All-Time High (ATH)", content_str)
        self.assertIn("Current Drawdown from ATH", content_str)
        self.assertIn("Max Historical Drawdown (MDD)", content_str)

    # --------------------------------------------------------------------------
    # P1-2: Smart Capital Allocation & Sizing Guide
    # --------------------------------------------------------------------------
    @patch("sheet_writer.time.sleep", return_value=None)
    def test_future_buy_portfolio_fit_and_sizing(self, mock_sleep):
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_ws.id = 999
        mock_sh.worksheet.return_value = mock_ws

        C = GITHUB_DATA_COLS
        sample_row = [""] * len(C)
        sample_row[C["symbol"]] = "INFY"
        sample_row[C["sector"]] = "Technology"
        sample_row[C["buying_zone"]] = "🟢🟢 ADD AGGRESSIVELY"
        sample_row[C["total"]] = 85
        sample_row[C["cmp"]] = 1800.0
        sample_row[C["price_range"]] = "1750-1850"
        sample_row[C["action"]] = "STRONG BUY"

        sector_weights = {
            "Technology": 24.5, # Overweight (> 20%)
            "Pharma": 8.0,      # High Fit (< 15%)
            "Banking": 17.0,    # Balanced (15-20%)
        }

        rows = [sample_row]
        with patch("sheet_writer.clear_sheet_safe"),              patch("sheet_writer.update_sheet_safe") as mock_update,              patch("sheet_writer.batch_update_safe"):
            future_buy_builder.write_future_buy_tab(
                mock_sh, rows, tab_name="Future Buy",
                sector_weights=sector_weights, portfolio_value=10000000 # 1 Cr portfolio
            )

            self.assertTrue(mock_update.called)
            written_data = mock_update.call_args_list[0][0][2]

            self.assertEqual(written_data[0][0], "TOP 10 BUY OPPORTUNITIES")
            headers = written_data[1]
            self.assertEqual(headers[6], "Portfolio Fit")
            self.assertEqual(headers[7], "Tranche Guidance")

            data_row = written_data[2]
            self.assertEqual(data_row[1], "INFY")
            # Overweight sector warning
            self.assertIn("Overweight (24.5%)", data_row[6])
            # 2% Tranche for 1 Cr = ₹200,000
            self.assertIn("₹200,000 (2.0%)", data_row[7])

    # --------------------------------------------------------------------------
    # P1-3: High-Signal Tiered Telegram Alert Engine
    # --------------------------------------------------------------------------
    def test_telegram_tiered_alerts_and_capital_ranking(self):
        # Import directly to avoid any namespace collision
        import telegram_alerts
        if not hasattr(telegram_alerts, "build_alert_message"):
            import importlib
            importlib.reload(telegram_alerts)

        alerts = {
            "sl_breach": [
                {"sym": "SMALL_STOCK", "cmp": 100, "sl": 107, "loss_amount": 2500, "return_pct": -7.5, "is_smallcase": False},
                {"sym": "BIG_CORE", "cmp": 2500, "sl": 2680, "loss_amount": 65000, "return_pct": -7.2, "is_smallcase": True},
            ],
            "target_hit": [
                {"sym": "WINNER_1", "cmp": 500, "tgt": 450, "gain_amount": 12000, "return_pct": 22.0, "is_smallcase": False},
                {"sym": "WINNER_2", "cmp": 1200, "tgt": 1000, "gain_amount": 85000, "return_pct": 25.0, "is_smallcase": True},
            ],
            "strong_buy": [{"sym": "INFY", "score": 88, "action": "STRONG BUY"}],
            "sell_watch": [],
        }

        watchlist_opps = [
            {"sym": "ZYDUSLIFE", "score": 82, "action": "ADD AGGRESSIVELY", "rsi": 38, "fit": "⭐ High Fit (Pharma: 8.2%)"}
        ]

        msg = telegram_alerts.build_alert_message(
            alerts, portfolio_value=15000000, top_results=[],
            watchlist_opps=watchlist_opps, health_score=78, health_trend=("📈 Improving", 3.0)
        )

        # Verify pulse
        self.assertIn("<b>Portfolio Value</b>: ₹15,000,000", msg)
        self.assertIn("<b>Health Score</b>: 78/100 | 📈 Improving (+3.0)", msg)

        # Verify SL breach ranked by loss amount (BIG_CORE with 65k loss first)
        self.assertIn("URGENT ACTION: STOP-LOSS BREACHES", msg)
        big_core_pos = msg.find("BIG_CORE (SC)")
        small_stock_pos = msg.find("SMALL_STOCK")
        self.assertTrue(big_core_pos < small_stock_pos, "BIG_CORE must appear before SMALL_STOCK due to higher capital at risk")

        # Verify Target hit ranked by gain amount (WINNER_2 with 85k gain first)
        winner2_pos = msg.find("WINNER_2 (SC)")
        winner1_pos = msg.find("WINNER_1")
        self.assertTrue(winner2_pos < winner1_pos, "WINNER_2 must appear before WINNER_1 due to higher realized profit")

        # Verify Top Fresh Buy Opportunities with Fit
        self.assertIn("Top Fresh Buy Opportunities", msg)
        self.assertIn("ZYDUSLIFE", msg)
        self.assertIn("⭐ High Fit (Pharma: 8.2%)", msg)

        # Verify character limit
        self.assertTrue(len(msg) < 4000)


if __name__ == "__main__":
    unittest.main()
