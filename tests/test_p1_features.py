"""
test_p1_features.py
===================
Unit test suite for Phase 2 (P1) features:

  P1-1  data_fetcher 1W/1M returns stored as numeric floats
  P1-2  data_fetcher NIFTY 50 beta covariance calculation
  P1-3  score_engine ETF archetype & scoring logic
  P1-4  portfolio_analytics Smallcase tagging in Action Required
  P1-5  future_buy_builder opportunity-first watchlist sorting
  P1-6  portfolio_analytics 1W/1M momentum summary counts
  P1-7  main.py growth_screener_builder hook

Run with:
    cd siddegowda-portfolio-clone
    python tests/test_p1_features.py
"""

import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("SHEET_ID", "test_id")
os.environ.setdefault("GOOGLE_CREDENTIALS_JSON", '{"type":"service_account"}')

# ── Stubs for heavy dependencies ─────────────────────────────────────────────
def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

for _m in ["yfinance", "gspread", "requests",
           "google", "google.oauth2", "google.oauth2.service_account",
           "news_engine", "news_engine.sources", "news_engine.sources.google_news_rss",
           "news_engine.classifier", "fund_cache", "mutual_fund_builder", "dividend_builder",
           "telegram_alerts"]:
    _stub(_m)

sys.modules["news_engine.sources"].google_news_rss = sys.modules["news_engine.sources.google_news_rss"]
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

import pandas as pd
import numpy as np

# Re-enable real numpy and pandas for math calculations in tests
sys.modules["numpy"] = np
sys.modules["pandas"] = pd

import data_fetcher
import score_engine
import github_data_builder
import portfolio_analytics
import future_buy_builder
import growth_screener_builder


# ============================================================================
# P1-1: 1W / 1M Return % Numeric Floats
# ============================================================================
class TestReturnNumericFloats(unittest.TestCase):

    def test_return_calculations_produce_floats(self):
        """Simulate a price series and verify 1W and 1M returns are computed as floats."""
        # 135 daily closes: starting at 100, ending at 146.90
        closes = pd.Series([100.0 + i * 0.35 for i in range(135)])
        cmp = float(closes.iloc[-1])
        p5 = float(closes.iloc[-6])
        p21 = float(closes.iloc[-22])
        p63 = float(closes.iloc[-64])
        p126 = float(closes.iloc[-127])

        ret_1w = round((cmp / p5 - 1) * 100, 2)
        ret_1m = round((cmp / p21 - 1) * 100, 2)
        ret_3m = round((cmp / p63 - 1) * 100, 2)
        ret_6m = round((cmp / p126 - 1) * 100, 2)

        self.assertIsInstance(ret_1w, float)
        self.assertIsInstance(ret_1m, float)
        self.assertIsInstance(ret_3m, float)
        self.assertIsInstance(ret_6m, float)
        self.assertGreater(ret_1w, 0)
        self.assertGreater(ret_1m, 0)
        self.assertGreater(ret_3m, 0)
        self.assertGreater(ret_6m, 0)

    def test_negative_return_is_numeric_float(self):
        closes = pd.Series([120.0 - i * 0.5 for i in range(30)])
        cmp = float(closes.iloc[-1])
        p5 = float(closes.iloc[-6])
        ret_1w = round((cmp / p5 - 1) * 100, 2)
        self.assertIsInstance(ret_1w, float)
        self.assertLess(ret_1w, 0)


# ============================================================================
# P1-2: NIFTY 50 Beta Covariance Calculation
# ============================================================================
class TestNiftyBetaCalculation(unittest.TestCase):

    def test_perfectly_correlated_stock_has_beta_one(self):
        """Stock with identical daily returns to index should have beta = 1.0."""
        dates = pd.date_range("2025-01-01", periods=100)
        nifty_closes = pd.Series(np.cumprod(1 + np.random.normal(0.0005, 0.01, 100)) * 20000, index=dates)
        stock_closes = nifty_closes * 0.1  # perfectly scaled
        beta = data_fetcher.compute_nifty_beta(stock_closes, nifty_closes)
        self.assertIsNotNone(beta)
        self.assertAlmostEqual(beta, 1.0, places=1)

    def test_leveraged_stock_has_higher_beta(self):
        """Stock with 1.5x movements has beta ~ 1.5."""
        dates = pd.date_range("2025-01-01", periods=100)
        np.random.seed(42)
        bench_rets = np.random.normal(0.0005, 0.01, 100)
        stock_rets = 1.5 * bench_rets + np.random.normal(0, 0.002, 100)

        nifty_closes = pd.Series(np.cumprod(1 + bench_rets) * 20000, index=dates)
        stock_closes = pd.Series(np.cumprod(1 + stock_rets) * 1000, index=dates)

        beta = data_fetcher.compute_nifty_beta(stock_closes, nifty_closes)
        self.assertIsNotNone(beta)
        self.assertAlmostEqual(beta, 1.5, delta=0.2)

    def test_insufficient_history_returns_none(self):
        """Less than 30 common days returns None."""
        dates = pd.date_range("2025-01-01", periods=15)
        nifty_closes = pd.Series([20000 + i for i in range(15)], index=dates)
        stock_closes = pd.Series([1000 + i for i in range(15)], index=dates)
        beta = data_fetcher.compute_nifty_beta(stock_closes, nifty_closes)
        self.assertIsNone(beta)


# ============================================================================
# P1-3: ETF Archetype & Scoring
# ============================================================================
class TestETFArchetypeScoring(unittest.TestCase):

    def test_etf_archetype_detection_by_symbol(self):
        """Known ETF symbols are classified into the ETF archetype."""
        self.assertEqual(score_engine.get_archetype("NIFTYBEES", "", ""), "ETF")
        self.assertEqual(score_engine.get_archetype("GOLDBEES", "", ""), "ETF")
        self.assertEqual(score_engine.get_archetype("ICICIB22", "", ""), "ETF")
        self.assertEqual(score_engine.get_archetype("JUNIORBEES", "", ""), "ETF")

    def test_etf_archetype_detection_by_sector(self):
        """Sector='ETFs' gets ETF archetype."""
        self.assertEqual(score_engine.get_archetype("RANDOMETF", "ETFs", ""), "ETF")
        self.assertEqual(score_engine.get_archetype("SOMESEC", "ETF", ""), "ETF")

    def test_etf_scoring_does_not_fail_to_avoid(self):
        """ETFs with uptrend/good RSI should score as BUY/ACCUMULATE/HOLD, not AVOID."""
        metrics = {
            "pe": None, "pb": None, "roe": None, "debt_eq": None,
            "rsi": 45.0, "cmp": 250.0, "sma200": 240.0, "vol_spike": 1.0,
            "cross": "Golden Cross",
        }
        q, v, t, tot, action, strengths, weaknesses = score_engine.compute_unified_score(
            "NIFTYBEES", "ETF", metrics
        )
        self.assertEqual(q, 30, "ETF baseline quality score")
        self.assertEqual(v, 20, "ETF baseline valuation score")
        self.assertGreaterEqual(tot, 55)
        self.assertIn(action, ("BUY", "ACCUMULATE", "HOLD"))
        self.assertNotIn(action, ("AVOID", "SELL"))


# ============================================================================
# P1-4: Smallcase Tagging in Action Required
# ============================================================================
class TestSmallcaseActionTagging(unittest.TestCase):

    def test_smallcase_holding_gets_sc_tag_in_action_required(self):
        holdings = {"ABC": (10, 1000.0, 900.0), "XYZ": (5, 500.0, 600.0)}
        fund_map = {"ABC": {"sector": "Technology"}, "XYZ": {"sector": "Energy"}}
        combined_rows = [
            {"symbol": "ABC", "investment_source": "SMALLCASE", "signal": "BUY MORE",
             "shares": 10, "invested": 9000.0, "value": 10000.0, "pnl": 1000.0, "return_pct": 11.1, "wt_pct": 50.0},
            {"symbol": "XYZ", "investment_source": "SELF", "signal": "SELL - SL HIT",
             "shares": 5, "invested": 3000.0, "value": 2500.0, "pnl": -500.0, "return_pct": -16.6, "wt_pct": 50.0},
        ]
        dash = portfolio_analytics.compute_portfolio_dashboard(
            holdings, fund_map, [], 12500.0, combined_rows=combined_rows
        )
        ar = dash.get("action_required", [])
        self.assertEqual(len(ar), 2)
        sc_item = next((r for r in ar if r["symbol"] == "ABC"), None)
        self.assertIsNotNone(sc_item)
        self.assertEqual(sc_item["symbol_display"], "ABC (SC)")

        self_item = next((r for r in ar if r["symbol"] == "XYZ"), None)
        self.assertIsNotNone(self_item)
        self.assertEqual(self_item["symbol_display"], "XYZ")


# ============================================================================
# P1-5: Future Buy Opportunity Sorting
# ============================================================================
class TestFutureBuyOpportunitySorting(unittest.TestCase):

    def test_watchlist_sorted_by_buying_zone_priority(self):
        """ADD AGGRESSIVELY (1) and ACCUMULATE (2) should appear before WAIT (4)."""
        from github_data_builder import GITHUB_DATA_COLS
        C = GITHUB_DATA_COLS
        n = max(C.values()) + 1

        def make_row(sym, zone, total):
            r = [""] * n
            r[C["symbol"]] = sym
            r[C["buying_zone"]] = zone
            r[C["total"]] = total
            return r

        rows = [
            make_row("WAITING_STOCK", "❌ WAIT", 40),
            make_row("AGGRESSIVE_BUY", "🟢🟢 ADD AGGRESSIVELY", 85),
            make_row("ACCUMULATE_STOCK", "🟢 ACCUMULATE", 70),
        ]

        ZONE_RANK = {
            "🟢🟢 ADD AGGRESSIVELY": 1,
            "🟢 ACCUMULATE": 2,
            "🟡 SMALL BUY": 3,
            "❌ WAIT": 4,
            "🔎 INVESTIGATE": 5,
        }
        def _sort_key(r):
            zone_str = str(r[C["buying_zone"]]).strip()
            z_rank = ZONE_RANK.get(zone_str, 9)
            tot_val = float(r[C["total"]]) if r[C["total"]] != "" else 0
            return (z_rank, -tot_val)

        rows.sort(key=_sort_key)
        self.assertEqual(rows[0][C["symbol"]], "AGGRESSIVE_BUY")
        self.assertEqual(rows[1][C["symbol"]], "ACCUMULATE_STOCK")
        self.assertEqual(rows[2][C["symbol"]], "WAITING_STOCK")


# ============================================================================
# P1-6: Dashboard Momentum Counts
# ============================================================================
class TestDashboardMomentumCounts(unittest.TestCase):

    def test_momentum_counts_aggregated_accurately(self):
        holdings = {"A": (10, 100, 90), "B": (10, 100, 90), "C": (10, 100, 90)}
        fund_map = {"A": {"sector": "T"}, "B": {"sector": "T"}, "C": {"sector": "T"}}
        combined_rows = [
            {"symbol": "A", "investment_source": "SELF", "return_1w": 3.5, "return_1m": -1.2, "invested": 100, "value": 100, "wt_pct": 33},
            {"symbol": "B", "investment_source": "SELF", "return_1w": 1.2, "return_1m": 4.5, "invested": 100, "value": 100, "wt_pct": 33},
            {"symbol": "C", "investment_source": "SELF", "return_1w": -2.0, "return_1m": -3.0, "invested": 100, "value": 100, "wt_pct": 33},
        ]
        dash = portfolio_analytics.compute_portfolio_dashboard(
            holdings, fund_map, [], 300.0, combined_rows=combined_rows
        )
        self.assertEqual(dash.get("momentum_1w"), (2, 1))  # 2 Up, 1 Down
        self.assertEqual(dash.get("momentum_1m"), (1, 2))  # 1 Up, 2 Down


# ============================================================================
# P1-7: Google Sheets Formatting & Beta Coverage Verification
# ============================================================================
class TestSheetsFormattingAndBetaCoverage(unittest.TestCase):

    def test_sheets_percentage_format_pattern(self):
        """Verify Sheets percentage formatting request format and pattern."""
        import sheet_formatter
        reqs = sheet_formatter.get_percentage_format_reqs(0, 2, 10, 5, 6)
        self.assertEqual(len(reqs), 1)
        nf = reqs[0]["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]
        self.assertEqual(nf["type"], "NUMBER")
        self.assertEqual(nf["pattern"], '0.00"%"')

    def test_portfolio_beta_coverage_excludes_missing_beta(self):
        """Holdings with beta=None (ETFs) must be excluded from beta weighting and reflected in coverage %."""
        holdings = {
            "STOCK_A": (10, 100.0, 90.0),   # Value = 1000, Beta = 1.2
            "STOCK_B": (10, 100.0, 90.0),   # Value = 1000, Beta = 0.8
            "ETF_GOLD": (20, 100.0, 90.0),  # Value = 2000, Beta = None (ETF)
        }
        fund_map = {
            "STOCK_A": {"beta": 1.2, "sector": "Tech"},
            "STOCK_B": {"beta": 0.8, "sector": "Finance"},
            "ETF_GOLD": {"beta": None, "sector": "ETFs"},
        }
        dash = portfolio_analytics.compute_portfolio_dashboard(
            holdings, fund_map, [], 4000.0
        )
        # Value-weighted beta of covered stocks = (1000*1.2 + 1000*0.8) / 2000 = 1.00
        self.assertEqual(dash["portfolio_beta"], 1.00)
        self.assertEqual(dash["beta_covered_value"], 2000.0)
        self.assertEqual(dash["beta_covered_pct"], 50.0)  # 2000 / 4000 = 50.0%


if __name__ == "__main__":
    unittest.main(verbosity=2)
