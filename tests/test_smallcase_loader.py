import os
import sys
import unittest

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import smallcase_loader as sl
import portfolio_builder as pb


class TestSmallcaseLoader(unittest.TestCase):

    def test_normalize_symbol(self):
        self.assertEqual(sl.normalize_symbol("INFY"), "INFY")
        self.assertEqual(sl.normalize_symbol("infy"), "INFY")
        self.assertEqual(sl.normalize_symbol(" INFY.NS "), "INFY")
        self.assertEqual(sl.normalize_symbol("TCS.BO"), "TCS")
        self.assertEqual(sl.normalize_symbol("NSE:RELIANCE"), "RELIANCE")
        self.assertEqual(sl.normalize_symbol("BSE:TATASTEEL"), "TATASTEEL")
        self.assertEqual(sl.normalize_symbol(""), "")
        self.assertEqual(sl.normalize_symbol(None), "")

    def test_parse_smallcase_name(self):
        self.assertEqual(sl.parse_smallcase_name("Electric_Mobility_Theme_20_9_2026.csv"), "Electric Mobility Theme")
        self.assertEqual(sl.parse_smallcase_name("IT_Tracker_20_9_2026.csv"), "IT Tracker")
        self.assertEqual(sl.parse_smallcase_name("House_of_Tata_Tracker_20_9_2026.csv"), "House of Tata Tracker")
        self.assertEqual(sl.parse_smallcase_name("Growth_Value_Multicap_Model_20_9_2026.csv"), "Growth & Value Multicap Model")
        self.assertEqual(sl.parse_smallcase_name("data/imports/Zerodha_Smallcase/Sunai_Smallcase_2026-09-20.csv"), "Sunai Smallcase")

    def test_load_all_smallcases(self):
        sc_map, sc_symbols = sl.load_all_smallcases("data/imports")
        self.assertGreater(len(sc_symbols), 0)
        self.assertIn("INFY", sc_symbols)
        self.assertIn("EXIDEIND", sc_symbols)
        self.assertIn("TCS", sc_symbols)
        self.assertIn("RELIANCE", sc_symbols)
        self.assertIn("COFORGE", sc_symbols)

        # Verify multi-smallcase edge case
        reliance_scs = [e["smallcase"] for e in sc_map.get("RELIANCE", [])]
        self.assertGreaterEqual(len(reliance_scs), 2)
        self.assertIn("Energy Tracker", reliance_scs)
        self.assertIn("Dividend Aristocrats Model", reliance_scs)

    def test_smallcase_exclusion_audit_generation(self):
        sc_map, sc_symbols = sl.load_all_smallcases("data/imports")
        all_trades = pb.load_all_trades("data/imports")
        holdings = pb.compute_holdings(all_trades)
        broker_symbols = {h["symbol"] for h in holdings.values() if h.get("qty", 0) > 0}

        stats = sl.generate_smallcase_audit(broker_symbols, sc_map, output_dir="data")

        self.assertGreater(stats["total_broker_symbols"], 0)
        self.assertEqual(stats["total_smallcase_symbols"], len(sc_symbols))
        self.assertGreater(stats["excluded_count"], 0)
        self.assertGreater(stats["remaining_portfolio_count"], 0)
        self.assertIn("EXIDEIND", stats["excluded_symbols"])
        self.assertIn("INFY", stats["excluded_symbols"])
        self.assertIn("TCS", stats["excluded_symbols"])

        # Verify audit files exist on disk
        txt_path = os.path.join(os.path.abspath("data"), "smallcase_exclusion_audit.txt")
        csv_path = os.path.join(os.path.abspath("data"), "smallcase_excluded_stocks.csv")
        self.assertTrue(os.path.exists(txt_path))
        self.assertTrue(os.path.exists(csv_path))

    def test_build_portfolio_excludes_smallcases(self):
        all_trades = pb.load_all_trades("data/imports")
        # Mock prices
        prices = {
            "INFY": 1500.0,
            "EXIDEIND": 450.0,
            "5PAISA": 400.0,
            "ASIANPAINT": 2800.0,
        }
        
        # Portfolio with smallcase exclusion
        portfolio = pb.build_portfolio(prices, trades=all_trades, exclude_smallcases=True)
        combined_syms = {r["symbol"] for r in portfolio.get("combined", [])}
        
        self.assertNotIn("INFY", combined_syms)
        self.assertNotIn("EXIDEIND", combined_syms)
        if "5PAISA" in [t.get("symbol") for t in all_trades]:
            self.assertIn("5PAISA", combined_syms)

        # Portfolio without smallcase exclusion (e.g. raw or Wife)
        portfolio_raw = pb.build_portfolio(prices, trades=all_trades, exclude_smallcases=False)
        combined_raw_syms = {r["symbol"] for r in portfolio_raw.get("combined", [])}
        self.assertIn("EXIDEIND", combined_raw_syms)
