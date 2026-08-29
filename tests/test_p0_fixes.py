"""
test_p0_fixes.py
Run from repo root: python tests/test_p0_fixes.py
Or: python -m pytest tests/test_p0_fixes.py -v
"""
import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

# ── env vars ────────────────────────────────────────────────────────────────
os.environ.setdefault("SHEET_ID", "test_id")
os.environ.setdefault("GOOGLE_CREDENTIALS_JSON", '{"type":"service_account"}')

# ── stub heavy modules that the src files import at the top ─────────────────
def _stub(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m

for _m in ["yfinance", "gspread", "pandas", "numpy", "requests",
           "google", "google.oauth2", "google.oauth2.service_account",
           "news_engine", "news_engine.sources", "news_engine.sources.google_news_rss",
           "news_engine.classifier",
           "fund_cache"]:
    _stub(_m)

# google_news_rss must be a module attribute too (for `from news_engine.sources import google_news_rss`)
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
_prof_m.profiler = _prof_obj

# gspread.exceptions must be a proper sub-module with APIError class
# sheet_writer.py does `import gspread.exceptions` lazily inside functions,
# so we must register it as both sys.modules["gspread.exceptions"] AND as
# an attribute on the gspread stub module.
_gse = _stub("gspread.exceptions")
class _APIError(Exception):
    pass
_gse.APIError = _APIError
sys.modules["gspread"].exceptions = _gse  # attribute access: gspread.exceptions.APIError

# ── now add src/ to path and import the real modules ────────────────────────
SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC_DIR)

import sheet_writer
import sheet_formatter
import portfolio_builder
import portfolio_analytics
import growth_screener_builder


# ── shared helpers ───────────────────────────────────────────────────────────
def make_ws(ws_id=99999):
    ws = MagicMock()
    ws.id = ws_id
    ws.clear = MagicMock()
    ws.update = MagicMock()
    ws.append_row = MagicMock()
    ws.append_rows = MagicMock()
    return ws

def make_sh(ws):
    sh = MagicMock()
    sh.worksheet = MagicMock(return_value=ws)
    sh.add_worksheet = MagicMock(return_value=ws)
    sh.batch_update = MagicMock()
    sh.fetch_sheet_metadata = MagicMock(return_value={"sheets": []})
    return sh

def minimal_combined():
    return [
        {"symbol": "RELIANCE", "shares": 10, "avg_buy": 2500.0, "cmp": 2700.0,
         "invested": 25000.0, "value": 27000.0, "pnl": 2000.0, "return_pct": 8.0,
         "wt_pct": 50.0, "sl_price": 2325.0, "target": 3000.0, "buy_more": 2250.0,
         "signal": "HOLD", "investment_source": "SELF", "isins": {"INE002A01018"}},
    ]


# ============================================================================
# P0-1  portfolio_builder.write_portfolio — clear + update resilience
# ============================================================================

class TestWritePortfolioResilience(unittest.TestCase):

    def _run(self, sh):
        portfolio_builder.write_portfolio(sh, {"combined": minimal_combined(), "groww": [], "zerodha": []})

    def test_happy_path_clear_and_update_called(self):
        ws = make_ws(); sh = make_sh(ws)
        self._run(sh)
        ws.clear.assert_called_once()
        ws.update.assert_called_once()

    def test_transient_429_on_clear_retries_and_succeeds(self):
        ws = make_ws(); sh = make_sh(ws)
        ws.clear.side_effect = [_APIError("429 Too Many Requests"), None]
        with patch("time.sleep"):
            self._run(sh)
        self.assertEqual(ws.clear.call_count, 2, "Should retry once after 429")
        ws.update.assert_called_once()

    def test_transient_503_on_clear_retries_and_succeeds(self):
        ws = make_ws(); sh = make_sh(ws)
        ws.clear.side_effect = [_APIError("503 Service Unavailable"), None]
        with patch("time.sleep"):
            self._run(sh)
        self.assertEqual(ws.clear.call_count, 2)

    def test_permanent_clear_failure_raises_and_no_update(self):
        """Tab must not be silently left as blank after all retries fail."""
        ws = make_ws(); sh = make_sh(ws)
        ws.clear.side_effect = _APIError("429 Too Many Requests")
        with patch("time.sleep"):
            with self.assertRaises(Exception):
                self._run(sh)
        ws.update.assert_not_called()

    def test_transient_429_on_update_retries_and_succeeds(self):
        ws = make_ws(); sh = make_sh(ws)
        ws.update.side_effect = [_APIError("429 Too Many Requests"), None]
        with patch("time.sleep"):
            self._run(sh)
        self.assertEqual(ws.update.call_count, 2)

    def test_permanent_update_failure_raises(self):
        ws = make_ws(); sh = make_sh(ws)
        ws.update.side_effect = _APIError("503 Service Unavailable")
        with patch("time.sleep"):
            with self.assertRaises(Exception):
                self._run(sh)

    def test_update_receives_non_empty_data(self):
        ws = make_ws(); sh = make_sh(ws)
        self._run(sh)
        args = ws.update.call_args[0]
        data = args[1] if len(args) > 1 else []
        self.assertGreater(len(data), 1, "Must write header + at least one data row")


# ============================================================================
# P0-2  portfolio_analytics.write_dashboard_tab — clear resilience
# ============================================================================

class TestWriteDashboardResilience(unittest.TestCase):

    def _minimal_dash(self):
        return {
            "portfolio_value": 100000.0,
            "invested_value": 90000.0,
            "total_pnl": 10000.0,
            "return_pct": 11.1,
            "portfolio_beta": 0.85,
            "portfolio_xirr": 14.5,
            "div_income": 1200.0,
            "sector_alloc": [["Technology", 50000.0, 50.0]],
            "positions": [["RELIANCE", 50000.0, 50.0, ""]],
            "source_summary": {
                "SELF": {"count": 1, "invested": 90000.0, "value": 100000.0},
                "SMALLCASE": {"count": 0, "invested": 0.0, "value": 0.0},
                "ETF": {"count": 0, "invested": 0.0, "value": 0.0},
                "LEGACY": {"count": 0, "invested": 0.0, "value": 0.0},
                "UNKNOWN": {"count": 0, "invested": 0.0, "value": 0.0},
            },
            "signal_counts": {"HOLD": 1},
            "action_required": [],
            "num_holdings": 1,
            "largest_holding": ["RELIANCE", 100000.0, 100.0, ""],
            "top5_weight": 100.0,
            "sector_detail": [
                {"sector": "Technology", "count": 1, "weight_pct": 100.0,
                 "invested": 90000.0, "value": 100000.0, "pnl": 10000.0,
                 "return_pct": 11.1, "beta": 0.9, "flag": ""},
            ],
            "num_sectors": 1,
            "top3_sector_weight": 100.0,
            "top_gainers": [],
            "top_losers": [],
            "top_positive_impact": [],
            "top_negative_impact": [],
            "top_positive_sectors": [],
            "top_negative_sectors": [],
        }

    def _run(self, sh, dash=None):
        portfolio_analytics.write_dashboard_tab(
            sh, dash or self._minimal_dash(),
            changes=None, health=None, health_trend=None
        )

    def test_happy_path_clear_called(self):
        ws = make_ws(); sh = make_sh(ws)
        self._run(sh)
        ws.clear.assert_called_once()

    def test_transient_429_on_clear_retries(self):
        ws = make_ws(); sh = make_sh(ws)
        ws.clear.side_effect = [_APIError("429 Too Many Requests"), None]
        with patch("time.sleep"):
            self._run(sh)
        self.assertEqual(ws.clear.call_count, 2)
        ws.update.assert_called()

    def test_permanent_clear_failure_raises_no_update(self):
        ws = make_ws(); sh = make_sh(ws)
        ws.clear.side_effect = _APIError("429 Too Many Requests")
        with patch("time.sleep"):
            with self.assertRaises(Exception):
                self._run(sh)
        ws.update.assert_not_called()


# ============================================================================
# P0-3  growth_screener_builder.write_growth_screener — clear resilience
# ============================================================================

class TestWriteGrowthScreenerResilience(unittest.TestCase):

    def _minimal_rows(self):
        from github_data_builder import GITHUB_DATA_COLS
        C = GITHUB_DATA_COLS
        n = max(C.values()) + 1
        r = [""] * n
        r[C["symbol"]]    = "RELIANCE"
        r[C["action"]]    = "BUY"
        r[C["total"]]     = 65
        r[C["quality"]]   = 30
        r[C["valuation"]] = 22
        r[C["timing"]]    = 13
        r[C["pe"]]        = 25.0
        r[C["roe"]]       = 18.0
        r[C["debt_eq"]]   = 0.4
        r[C["rev_growth"]]= 12.0
        r[C["div"]]       = 1.5
        r[C["pct_high"]]  = "-5%"
        r[C["rsi"]]       = 45.0
        r[C["trend"]]     = "Uptrend"
        r[C["strengths"]] = "Good ROE"
        r[C["weaknesses"]]= "High PE"
        return [r]

    def _run(self, sh):
        growth_screener_builder.write_growth_screener(sh, self._minimal_rows())

    def test_happy_path_clear_called(self):
        """Normal run: clear_sheet_safe is called on the existing worksheet."""
        ws = make_ws(); sh = make_sh(ws)
        self._run(sh)
        # worksheet() was found → clear must have been attempted
        ws.clear.assert_called_once()

    def test_transient_429_on_clear_retries_and_recovers(self):
        """A 429 on clear is retried by clear_sheet_safe; second attempt succeeds."""
        ws = make_ws(); sh = make_sh(ws)
        ws.clear.side_effect = [_APIError("429 Too Many Requests"), None]
        with patch("time.sleep"):
            self._run(sh)
        self.assertEqual(ws.clear.call_count, 2,
            "Should retry clear exactly once after a transient 429")
        ws.append_row.assert_called()   # header row written after successful retry

    def test_permanent_clear_failure_raises(self):
        """Permanent clear failure must raise — not silently continue."""
        ws = make_ws()
        sh = make_sh(ws)
        ws.clear.side_effect = _APIError("429 Too Many Requests")
        with patch("time.sleep"):
            with self.assertRaises(Exception):
                self._run(sh)


# ============================================================================
# P0-4  ETF classification correctness
# ============================================================================

class TestETFClassification(unittest.TestCase):
    """
    Test the fixed sector classification in compute_portfolio_dashboard().

    Rules:
      investment_source == "ETF"  → sector = "ETFs"   (correct, was ETF-tagged via source_map)
      yf_sector == "ETFs"         → sector = "ETFs"   (yfinance returned the ETF string)
      yf_sector = ""  + SELF      → sector = "Unknown" (stock with missing sector data)
      yf_sector = "Energy"        → sector = "Energy"  (known sector)
    """

    def _get_sector_from_combined_rows(self, sym, investment_source, yf_sector):
        """
        Run compute_portfolio_dashboard with one combined row and return the
        sector label assigned to it in sector_detail.
        """
        holdings = {sym: (10, 1000.0, 900.0)}
        fund_map = {sym: {"sector": yf_sector, "beta": None, "div": None}}
        combined_row = {
            "symbol": sym, "investment_source": investment_source,
            "shares": 10, "avg_buy": 900.0, "cmp": 1000.0,
            "invested": 9000.0, "value": 10000.0, "pnl": 1000.0,
            "return_pct": 11.1, "wt_pct": 100.0, "signal": "HOLD",
        }
        dash = portfolio_analytics.compute_portfolio_dashboard(
            holdings, fund_map, [], 10000.0, combined_rows=[combined_row]
        )
        sector_detail = dash.get("sector_detail", [])
        if not sector_detail:
            return None
        return sector_detail[0]["sector"]

    def test_etf_via_investment_source_gets_etfs_label(self):
        """investment_source=ETF with empty yfinance sector → 'ETFs'."""
        sector = self._get_sector_from_combined_rows("NIFTYBEES", "ETF", "")
        self.assertEqual(sector, "ETFs")

    def test_etf_via_yfinance_sector_string_gets_etfs_label(self):
        """yfinance returned sector='ETFs' → 'ETFs'."""
        sector = self._get_sector_from_combined_rows("NIFTYBEES", "SELF", "ETFs")
        self.assertEqual(sector, "ETFs")

    def test_stock_with_known_sector_gets_correct_label(self):
        """Normal stock with sector='Energy' → 'Energy'."""
        sector = self._get_sector_from_combined_rows("RELIANCE", "SELF", "Energy")
        self.assertEqual(sector, "Energy")

    def test_stock_with_missing_sector_gets_Unknown_NOT_ETFs(self):
        """CRITICAL: Stock with empty yfinance sector → 'Unknown', NOT 'ETFs'."""
        sector = self._get_sector_from_combined_rows("OBSCURE", "SELF", "")
        self.assertEqual(sector, "Unknown",
            "A stock with missing yfinance sector must be 'Unknown', not 'ETFs'")

    def test_stock_with_None_sector_gets_Unknown(self):
        """Sector = None → 'Unknown'."""
        holdings = {"NEWSTOCK": (10, 1000.0, 900.0)}
        fund_map = {"NEWSTOCK": {"sector": None, "beta": None, "div": None}}
        combined_row = {
            "symbol": "NEWSTOCK", "investment_source": "SELF",
            "shares": 10, "avg_buy": 900.0, "cmp": 1000.0,
            "invested": 9000.0, "value": 10000.0, "pnl": 1000.0,
            "return_pct": 11.1, "wt_pct": 100.0, "signal": "HOLD",
        }
        dash = portfolio_analytics.compute_portfolio_dashboard(
            holdings, fund_map, [], 10000.0, combined_rows=[combined_row]
        )
        sector_detail = dash.get("sector_detail", [])
        self.assertTrue(len(sector_detail) > 0)
        self.assertNotEqual(sector_detail[0]["sector"], "ETFs")
        self.assertEqual(sector_detail[0]["sector"], "Unknown")

    def test_etf_investment_source_overrides_yfinance_empty_sector(self):
        """ETF tagged via source_map gets 'ETFs' even with empty yfinance sector."""
        sector = self._get_sector_from_combined_rows("GOLDBEES", "ETF", "")
        self.assertEqual(sector, "ETFs")

    def test_smallcase_stock_with_missing_sector_is_Unknown(self):
        """SMALLCASE stock with missing sector → 'Unknown', not 'ETFs'."""
        sector = self._get_sector_from_combined_rows("SC_STOCK", "SMALLCASE", "")
        self.assertEqual(sector, "Unknown")

    def test_health_score_loop_uses_Unknown_fallback(self):
        """The health score diversification loop (sector_value) must use 'Unknown'
        not 'ETFs' as the fallback for missing sectors."""
        holdings = {"NODATA": (10, 1000.0, 900.0)}
        fund_map = {"NODATA": {"sector": "", "beta": None, "div": None}}
        dash = portfolio_analytics.compute_portfolio_dashboard(
            holdings, fund_map, [], 10000.0, combined_rows=[]
        )
        sector_alloc = dash.get("sector_alloc", [])
        self.assertTrue(len(sector_alloc) > 0)
        sectors = [s[0] for s in sector_alloc]
        self.assertNotIn("ETFs", sectors,
            "sector_alloc health-score loop must not produce 'ETFs' for missing-sector stocks")
        self.assertIn("Unknown", sectors)


# ============================================================================
# P0-5  Portfolio Beta — benchmark and missing-beta handling
# ============================================================================

class TestPortfolioBeta(unittest.TestCase):

    def _beta(self, holdings, fund_map):
        live_val = sum(qty * cmp for qty, cmp, _ in holdings.values())
        dash = portfolio_analytics.compute_portfolio_dashboard(
            holdings, fund_map, [], live_val, combined_rows=[]
        )
        return dash.get("portfolio_beta")

    def test_single_holding_beta(self):
        holdings = {"RELIANCE": (10, 2700.0, 2500.0)}
        fm = {"RELIANCE": {"sector": "Energy", "beta": 1.2, "div": None}}
        self.assertAlmostEqual(self._beta(holdings, fm), 1.2, places=2)

    def test_value_weighted_equal_holdings(self):
        holdings = {"A": (10, 1000.0, 900.0), "B": (10, 1000.0, 900.0)}
        fm = {"A": {"sector": "T", "beta": 1.4, "div": None}, "B": {"sector": "E", "beta": 0.8, "div": None}}
        self.assertAlmostEqual(self._beta(holdings, fm), 1.1, places=2)

    def test_missing_beta_excluded_not_zero(self):
        """beta=None must be excluded — not treated as beta=0."""
        holdings = {"KNOWN": (10, 1000.0, 900.0), "NDATA": (10, 1000.0, 900.0)}
        fm = {"KNOWN": {"sector": "T", "beta": 1.0, "div": None}, "NDATA": {"sector": "E", "beta": None, "div": None}}
        # Only KNOWN contributes → portfolio beta = 1.0
        self.assertAlmostEqual(self._beta(holdings, fm), 1.0, places=2)

    def test_all_missing_beta_returns_None(self):
        holdings = {"A": (10, 1000.0, 900.0)}
        fm = {"A": {"sector": "T", "beta": None, "div": None}}
        self.assertIsNone(self._beta(holdings, fm))

    def test_beta_label_in_dashboard_contains_benchmark_note(self):
        """After the P0-5 fix, the Dashboard must include a benchmark note."""
        ws = make_ws(); sh = make_sh(ws)
        dash = {
            "portfolio_value": 100000.0, "portfolio_beta": 0.85,
            "portfolio_xirr": None, "div_income": 0.0,
            "sector_alloc": [], "positions": [],
        }
        portfolio_analytics.write_dashboard_tab(sh, dash)
        # The update call must include text mentioning benchmark/yfinance
        ws.update.assert_called()
        all_data = ws.update.call_args[0][1]
        flat_text = " ".join(str(cell) for row in all_data for cell in row)
        self.assertTrue("NIFTY 50" in flat_text or "vs NIFTY" in flat_text or "yfinance" in flat_text,
            "Dashboard must note the beta benchmark source")


# ============================================================================
# sheet_writer safe helpers — unit tests
# ============================================================================

class TestSheetWriterSafeHelpers(unittest.TestCase):

    def test_clear_sheet_safe_retries_429(self):
        ws = make_ws()
        ws.clear.side_effect = [_APIError("429 Too Many Requests"), None]
        with patch("time.sleep"):
            sheet_writer.clear_sheet_safe(ws)
        self.assertEqual(ws.clear.call_count, 2)

    def test_clear_sheet_safe_raises_after_all_retries(self):
        ws = make_ws()
        ws.clear.side_effect = _APIError("429 Too Many Requests")
        with patch("time.sleep"):
            with self.assertRaises(_APIError):
                sheet_writer.clear_sheet_safe(ws)

    def test_update_sheet_safe_retries_503(self):
        ws = make_ws()
        ws.update.side_effect = [_APIError("503 Service Unavailable"), None]
        with patch("time.sleep"):
            sheet_writer.update_sheet_safe(ws, "A1", [["data"]])
        self.assertEqual(ws.update.call_count, 2)

    def test_update_sheet_safe_raises_after_all_retries(self):
        ws = make_ws()
        ws.update.side_effect = _APIError("503 Service Unavailable")
        with patch("time.sleep"):
            with self.assertRaises(_APIError):
                sheet_writer.update_sheet_safe(ws, "A1", [["data"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
