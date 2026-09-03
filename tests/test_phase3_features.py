"""
test_phase3_features.py
=======================
Unit test suite for Phase 3 improvements:
  P1-1  history_tracker integration, same-day deduplication, delta analysis & error safety
  P1-2  ETF stop-loss exemption & signal handling (normal stocks & Smallcase unchanged)
  P1-3  future_buy_builder Top 10 Opportunity Callout block & 111-row preservation
  P2-1  github_data_builder condensed news columns (41 columns contiguous)

Run with:
    python tests/test_phase3_features.py
"""

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("SHEET_ID", "test_id")
os.environ.setdefault("GOOGLE_CREDENTIALS_JSON", '{"type":"service_account"}')

# Stubs for external I/O
def _stub(name):
    if name in sys.modules:
        return sys.modules[name]
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

for _m in ["yfinance", "gspread", "requests",
           "google", "google.oauth2", "google.oauth2.service_account",
           "news_engine", "news_engine.sources", "news_engine.sources.google_news_rss",
           "news_engine.classifier", "fund_cache", "mutual_fund_builder", "dividend_builder",
           "telegram_alerts"]:
    _stub(_m)

sys.modules["news_engine.sources"].google_news_rss = getattr(sys.modules["news_engine.sources"], "google_news_rss", MagicMock())
sys.modules["google.oauth2.service_account"].Credentials = MagicMock()
sys.modules["fund_cache"].load_cache = MagicMock(return_value={})
sys.modules["fund_cache"].save_cache = MagicMock()
sys.modules["fund_cache"].get_or_fetch_fundamentals = MagicMock(return_value={})

_prof_m = _stub("profiler")
_prof_obj = MagicMock()
_prof_obj.increment = MagicMock()
_prof_obj.start_stage = MagicMock()
_prof_obj.stop_stage = MagicMock()
_prof_obj.stage = MagicMock()
_prof_obj.stage.return_value.__enter__ = MagicMock(return_value=None)
_prof_obj.stage.return_value.__exit__ = MagicMock(return_value=False)
_prof_m.profiler = _prof_obj

_gse = _stub("gspread.exceptions")
class _APIError(Exception):
    pass
_gse.APIError = _APIError
sys.modules["gspread"].exceptions = _gse

SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC_DIR)

import config
import sheet_formatter
import sheet_writer
import github_data_builder
import future_buy_builder
import history_tracker
import portfolio_builder
import portfolio_analytics
import score_engine


class TestHistoryTrackerIntegration(unittest.TestCase):
    """Tests for P1-1: History Tracker integration, delta comparison, and same-day idempotency."""

    def test_first_run_no_previous_history(self):
        """When History tab is empty or missing, first run behaves safely."""
        mock_sh = MagicMock()
        mock_sh.worksheet.side_effect = Exception("Worksheet not found")

        prev_date, prev_health = history_tracker.get_previous_health_score(mock_sh)
        self.assertIsNone(prev_date)
        self.assertIsNone(prev_health)

        results = [
            ["RELIANCE"] + [""] * 33 + [75, "🟢 ACCUMULATE", "2400-2600", "BUY"],
        ]
        changes = history_tracker.compute_todays_changes(mock_sh, results)
        self.assertIsNone(changes["prev_date"])
        self.assertEqual(changes["top_improvements"], [])
        self.assertEqual(changes["top_deteriorations"], [])

    def test_normal_previous_vs_current_comparison(self):
        """Score deltas and action transitions are calculated correctly."""
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_sh.worksheet.return_value = mock_ws

        # Mock previous history rows: Date, Symbol, CMP, PE, RSI, Total Score, Final Action, Quality, Valuation, Timing
        mock_ws.get_all_values.return_value = [
            history_tracker.HISTORY_HEADERS,
            ["2026-08-28", "RELIANCE", "2500", "24.0", "45.0", "65", "HOLD", "28", "20", "17"],
            ["2026-08-28", "INFY", "1500", "28.0", "65.0", "72", "BUY", "32", "18", "22"],
        ]

        C = github_data_builder.GITHUB_DATA_COLS
        row_rel = [""] * len(C)
        row_rel[C["symbol"]] = "RELIANCE"
        row_rel[C["pe"]] = 23.5
        row_rel[C["rsi"]] = 40.0
        row_rel[C["total"]] = 73
        row_rel[C["action"]] = "BUY"
        row_rel[C["quality"]] = 30
        row_rel[C["valuation"]] = 22
        row_rel[C["timing"]] = 21

        row_infy = [""] * len(C)
        row_infy[C["symbol"]] = "INFY"
        row_infy[C["pe"]] = 29.0
        row_infy[C["rsi"]] = 72.0
        row_infy[C["total"]] = 66
        row_infy[C["action"]] = "HOLD"
        row_infy[C["quality"]] = 32
        row_infy[C["valuation"]] = 16
        row_infy[C["timing"]] = 18

        changes = history_tracker.compute_todays_changes(mock_sh, [row_rel, row_infy])
        self.assertEqual(changes["prev_date"], "2026-08-28")
        self.assertEqual(len(changes["top_improvements"]), 1)
        self.assertEqual(changes["top_improvements"][0]["symbol"], "RELIANCE")
        self.assertEqual(changes["top_improvements"][0]["score_delta"], 8.0)
        self.assertEqual(changes["top_improvements"][0]["today_action"], "BUY")

        self.assertEqual(len(changes["top_deteriorations"]), 1)
        self.assertEqual(changes["top_deteriorations"][0]["symbol"], "INFY")
        self.assertEqual(changes["top_deteriorations"][0]["score_delta"], -6.0)

    def test_same_day_snapshot_idempotency(self):
        """Multiple runs on the same date replace today's snapshot in-place without duplicating rows."""
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_pws = MagicMock()
        mock_sh.worksheet.side_effect = lambda name: mock_ws if name == "History" else mock_pws

        today_str = history_tracker.datetime.now().strftime("%Y-%m-%d")

        mock_ws.get_all_values.return_value = [
            history_tracker.HISTORY_HEADERS,
            ["2026-08-28", "RELIANCE", "2500", "24.0", "45.0", "65", "HOLD", "28", "20", "17"],
            [today_str, "RELIANCE", "2520", "24.2", "46.0", "66", "HOLD", "28", "20", "18"],
        ]
        mock_pws.get_all_values.return_value = [
            history_tracker.PORTFOLIO_HISTORY_HEADERS,
            ["2026-08-28", "100000", "75"],
            [today_str, "102000", "76"],
        ]

        C = github_data_builder.GITHUB_DATA_COLS
        row_rel = [""] * len(C)
        row_rel[C["symbol"]] = "RELIANCE"
        row_rel[C["pe"]] = 24.5
        row_rel[C["rsi"]] = 48.0
        row_rel[C["total"]] = 68
        row_rel[C["action"]] = "BUY"
        row_rel[C["quality"]] = 28
        row_rel[C["valuation"]] = 20
        row_rel[C["timing"]] = 20

        with patch("sheet_writer.update_sheet_safe") as mock_update,              patch("sheet_writer.batch_update_safe"):
            history_tracker.append_history_snapshot(mock_sh, [row_rel], 103000.0, {"RELIANCE": 2550.0}, health_score=78)

            self.assertTrue(mock_update.called)
            history_data_written = mock_update.call_args_list[0][0][2]
            self.assertEqual(len(history_data_written), 3, "Must not duplicate today's row in History")
            self.assertEqual(history_data_written[1][0], "2026-08-28")
            self.assertEqual(history_data_written[2][0], today_str)

    def test_telegram_digest_formatting(self):
        """Telegram digest properly formats improvement counts and highlights."""
        changes = {
            "prev_date": "2026-08-28",
            "digest": {
                "improved": 5, "weakened": 2, "entered_strong_buy": 1,
                "best_symbol": "RELIANCE", "worst_symbol": "INFY"
            }
        }
        digest_msg = history_tracker.format_telegram_digest(changes)
        self.assertIn("5 stocks improved", digest_msg)
        self.assertIn("2 stocks weakened", digest_msg)
        self.assertIn("Best opportunity: RELIANCE", digest_msg)


class TestETFStopLossAndSignalTreatment(unittest.TestCase):
    """Tests for P1-2: ETF Stop-Loss exemption and signal handling."""

    def test_normal_stock_uses_stop_loss_and_target(self):
        """Normal stocks retain -7% Stop Loss and +20% Target."""
        combined_dict = {
            "RELIANCE": {
                "symbol": "RELIANCE", "shares": 10, "invested": 25000.0,
                "value": 22000.0, "cmp": 2200.0, "isins": {"INE002A01018"}
            }
        }
        source_map = {"RELIANCE": "SELF"}
        fund_map = {"RELIANCE": {"sector": "Energy"}}

        sym = "RELIANCE"
        c = combined_dict[sym]
        c["avg_buy"] = round(c["invested"] / c["shares"], 2)
        c["investment_source"] = source_map[sym]
        c["sl_price"] = round(c["avg_buy"] * (1 - config.SL_PCT), 2)
        c["target"] = round(c["avg_buy"] * (1 + config.TARGET_PCT), 2)
        c["buy_more"] = round(c["avg_buy"] * 0.90, 2)
        if c["cmp"] <= c["sl_price"]:
            c["signal"] = "SELL - SL HIT"
        else:
            c["signal"] = "HOLD"

        self.assertEqual(c["sl_price"], 2325.0)
        self.assertEqual(c["signal"], "SELL - SL HIT")

    def test_etf_exempt_from_stock_stop_loss(self):
        """ETFs do NOT receive a stock-style -7% stop loss or SELL - SL HIT signal."""
        combined_dict = {
            "NIFTYBEES": {
                "symbol": "NIFTYBEES", "shares": 100, "invested": 25000.0,
                "value": 22000.0, "cmp": 220.0, "isins": {"INF732E01015"}
            }
        }
        fund_map = {"NIFTYBEES": {"sector": "ETFs"}}

        sym = "NIFTYBEES"
        c = combined_dict[sym]
        c["avg_buy"] = round(c["invested"] / c["shares"], 2)

        if fund_map.get(sym, {}).get("sector") == "ETFs" or "BEES" in sym:
            c["investment_source"] = "ETF"

        if c["investment_source"] == "ETF":
            c["sl_price"] = ""
            c["target"] = ""
            c["buy_more"] = round(c["avg_buy"] * 0.90, 2)
            if c["cmp"] <= c["buy_more"]:
                c["signal"] = "BUY MORE"
            else:
                c["signal"] = "HOLD"

        self.assertEqual(c["sl_price"], "")
        self.assertEqual(c["target"], "")
        self.assertEqual(c["signal"], "BUY MORE")


class TestFutureBuyTop10OpportunityBlock(unittest.TestCase):
    """Tests for P1-3: Future Buy Top 10 Opportunity Callout block."""

    def test_top10_selection_and_ordering(self):
        """Top 10 opportunities are correctly selected by Buying Zone priority + Total Score."""
        C = github_data_builder.GITHUB_DATA_COLS
        rows = []

        zones = [
            ("S1", "🟢🟢 ADD AGGRESSIVELY", 80),
            ("S2", "🟢🟢 ADD AGGRESSIVELY", 75),
            ("S3", "🟢 ACCUMULATE", 85),
            ("S4", "🟢 ACCUMULATE", 70),
            ("S5", "🟡 SMALL BUY", 78),
            ("S6", "🟡 SMALL BUY", 65),
            ("S7", "❌ WAIT", 90),
            ("S8", "❌ WAIT", 88),
            ("S9", "❌ WAIT", 80),
            ("S10", "❌ WAIT", 75),
            ("S11", "❌ WAIT", 70),
            ("S12", "❌ WAIT", 60),
        ]

        for sym, zone, score in zones:
            r = [""] * len(C)
            r[C["symbol"]] = sym
            r[C["buying_zone"]] = zone
            r[C["total"]] = score
            r[C["cmp"]] = 1000.0
            r[C["price_range"]] = "950-1050"
            r[C["action"]] = "BUY"
            rows.append(r)

        ZONE_RANK = {
            "🟢🟢 ADD AGGRESSIVELY": 1,
            "🟢 ACCUMULATE": 2,
            "🟡 SMALL BUY": 3,
            "❌ WAIT": 4,
            "🔎 INVESTIGATE": 5,
        }
        rows.sort(key=lambda r: (ZONE_RANK.get(r[C["buying_zone"]], 9), -float(r[C["total"]])))

        top10 = rows[:10]
        self.assertEqual(len(top10), 10)
        self.assertEqual(top10[0][C["symbol"]], "S1")
        self.assertEqual(top10[1][C["symbol"]], "S2")
        self.assertEqual(top10[2][C["symbol"]], "S3")
        self.assertEqual(top10[3][C["symbol"]], "S4")
        self.assertEqual(top10[4][C["symbol"]], "S5")

        self.assertEqual(len(rows), 12)

    def test_write_future_buy_tab_structure(self):
        """write_future_buy_tab constructs the Top 10 block, separator, and full table."""
        mock_sh = MagicMock()
        mock_ws = MagicMock()
        mock_sh.worksheet.return_value = mock_ws

        C = github_data_builder.GITHUB_DATA_COLS
        rows = []
        for i in range(15):
            r = [""] * len(C)
            r[C["symbol"]] = f"SYM{i+1}"
            r[C["buying_zone"]] = "🟢 ACCUMULATE"
            r[C["total"]] = 70 + i
            r[C["cmp"]] = 500.0
            r[C["price_range"]] = "480-520"
            r[C["action"]] = "BUY"
            rows.append(r)

        with patch("sheet_writer.clear_sheet_safe"),              patch("sheet_writer.update_sheet_safe") as mock_update,              patch("sheet_writer.batch_update_safe"):
            future_buy_builder.write_future_buy_tab(mock_sh, rows)

            self.assertTrue(mock_update.called)
            written_data = mock_update.call_args_list[0][0][2]
            self.assertEqual(written_data[0][0], "TOP 10 BUY OPPORTUNITIES")
            self.assertEqual(written_data[1][0], "Rank")
            self.assertEqual(written_data[1][1], "Symbol")
            self.assertEqual(written_data[2][0], 1)
            self.assertEqual(written_data[11][0], 10)
            self.assertEqual(written_data[12][0], "")
            self.assertEqual(written_data[14][0], "Symbol")
            self.assertEqual(len(written_data), 1 + 1 + 10 + 1 + 1 + 1 + 15)


class TestCondensedNewsColumns(unittest.TestCase):
    """Tests for P2-1: Condensed News Columns in GITHUB DATA."""

    def test_github_data_cols_contiguous_43_columns(self):
        """GITHUB_DATA_COLS must have exactly 43 contiguous columns from 0 to 42."""
        C = github_data_builder.GITHUB_DATA_COLS
        self.assertEqual(len(C), 43)
        indices = sorted(C.values())
        self.assertEqual(indices, list(range(43)))

        self.assertEqual(C["symbol"], 0)
        self.assertEqual(C["day_chg_pct"], 11)
        self.assertEqual(C["return_1w"], 12)
        self.assertEqual(C["return_1m"], 13)
        self.assertEqual(C["return_3m"], 14)
        self.assertEqual(C["return_6m"], 15)
        self.assertEqual(C["news_summary"], 31)
        self.assertEqual(C["news_sentiment"], 32)
        self.assertEqual(C["news_source"], 33)
        self.assertEqual(C["quality"], 36)
        self.assertEqual(C["total"], 39)
        self.assertEqual(C["action"], 42)

    def test_build_result_row_condenses_news_fields(self):
        """build_result_row properly condenses digest+reason and sentiment+score."""
        news_data = {
            "digest": "Strong quarterly revenue growth",
            "reason": "Margin expansion in core business",
            "sentiment": "Bullish",
            "bullish_score": 8,
            "bearish_score": 2,
            "source": "Economic Times"
        }
        f = {"sector": "Technology", "industry": "IT Services"}
        tech = {"trend": "Uptrend", "rsi": 55.0, "vol_spike": 1.1, "setup": "EMA Pullback"}
        row, archetype, total, action = github_data_builder.build_result_row(
            "TCS", 3500.0, f, tech, 15.0, xirr_val=18.5, news_data=news_data
        )

        C = github_data_builder.GITHUB_DATA_COLS
        self.assertIn("Strong quarterly revenue growth", row[C["news_summary"]])
        self.assertIn("Margin expansion", row[C["news_summary"]])
        self.assertEqual(row[C["news_sentiment"]], "Bullish (8/10)")
        self.assertEqual(row[C["news_source"]], "Economic Times")


if __name__ == "__main__":
    unittest.main()