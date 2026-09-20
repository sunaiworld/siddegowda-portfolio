import os
import pytest
import sys

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import smallcase_loader as sl
import portfolio_builder as pb


def test_normalize_symbol():
    assert sl.normalize_symbol("INFY") == "INFY"
    assert sl.normalize_symbol("infy") == "INFY"
    assert sl.normalize_symbol(" INFY.NS ") == "INFY"
    assert sl.normalize_symbol("TCS.BO") == "TCS"
    assert sl.normalize_symbol("NSE:RELIANCE") == "RELIANCE"
    assert sl.normalize_symbol("BSE:TATASTEEL") == "TATASTEEL"
    assert sl.normalize_symbol("") == ""
    assert sl.normalize_symbol(None) == ""


def test_parse_smallcase_name():
    assert sl.parse_smallcase_name("Electric_Mobility_Theme_20_9_2026.csv") == "Electric Mobility Theme"
    assert sl.parse_smallcase_name("IT_Tracker_20_9_2026.csv") == "IT Tracker"
    assert sl.parse_smallcase_name("House_of_Tata_Tracker_20_9_2026.csv") == "House of Tata Tracker"
    assert sl.parse_smallcase_name("Growth_Value_Multicap_Model_20_9_2026.csv") == "Growth & Value Multicap Model"
    assert sl.parse_smallcase_name("data/imports/Zerodha_Smallcase/Sunai_Smallcase_2026-09-20.csv") == "Sunai Smallcase"


def test_load_all_smallcases():
    sc_map, sc_symbols = sl.load_all_smallcases("data/imports")
    assert len(sc_symbols) > 0
    assert "INFY" in sc_symbols
    assert "EXIDEIND" in sc_symbols
    assert "TCS" in sc_symbols
    assert "RELIANCE" in sc_symbols
    assert "COFORGE" in sc_symbols

    # Verify multi-smallcase edge case
    reliance_scs = [e["smallcase"] for e in sc_map.get("RELIANCE", [])]
    assert len(reliance_scs) >= 2
    assert "Energy Tracker" in reliance_scs
    assert "Dividend Aristocrats Model" in reliance_scs


def test_smallcase_exclusion_audit_generation():
    sc_map, sc_symbols = sl.load_all_smallcases("data/imports")
    all_trades = pb.load_all_trades("data/imports")
    holdings = pb.compute_holdings(all_trades)
    broker_symbols = {h["symbol"] for h in holdings.values() if h.get("qty", 0) > 0}

    stats = sl.generate_smallcase_audit(broker_symbols, sc_map, output_dir="data")

    assert stats["total_broker_symbols"] > 0
    assert stats["total_smallcase_symbols"] == len(sc_symbols)
    assert stats["excluded_count"] > 0
    assert stats["remaining_portfolio_count"] > 0
    assert "EXIDEIND" in stats["excluded_symbols"]
    assert "INFY" in stats["excluded_symbols"]
    assert "TCS" in stats["excluded_symbols"]

    # Verify audit files exist on disk
    txt_path = os.path.join(os.path.abspath("data"), "smallcase_exclusion_audit.txt")
    csv_path = os.path.join(os.path.abspath("data"), "smallcase_excluded_stocks.csv")
    assert os.path.exists(txt_path)
    assert os.path.exists(csv_path)


def test_build_portfolio_excludes_smallcases():
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
    
    assert "INFY" not in combined_syms
    assert "EXIDEIND" not in combined_syms
    if "5PAISA" in [t.get("symbol") for t in all_trades]:
        assert "5PAISA" in combined_syms

    # Portfolio without smallcase exclusion (e.g. raw or Wife)
    portfolio_raw = pb.build_portfolio(prices, trades=all_trades, exclude_smallcases=False)
    combined_raw_syms = {r["symbol"] for r in portfolio_raw.get("combined", [])}
    assert "EXIDEIND" in combined_raw_syms
