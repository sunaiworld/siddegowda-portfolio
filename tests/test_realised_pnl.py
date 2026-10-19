"""
Test suite for realised_pnl.py

Tests:
1.  Basic FIFO matching (single buy, single sell)
2.  Multiple buy lots at different prices (FIFO ordering)
3.  Partial sell (only some shares sold)
4.  Multiple sells from same buy pool
5.  Orphan sell (sell with no corresponding buy)
6.  Cross-month attribution (sell attributed to sell date, not buy date)
7.  Cross-FY attribution (Indian FY Apr-Mar)
8.  FY total reconciles with monthly sum
9.  Deduplication by trade_id
10. Loss-making sell (sell below cost)
11. Full exit then re-buy
"""

import sys
import os
import csv
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import realised_pnl as _rpnl


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_tradebook(tmpdir, filename, rows):
    """Write a minimal Tradebook CSV with the headers Zerodha uses."""
    path = os.path.join(tmpdir, filename)
    header = ["symbol", "isin", "trade_date", "exchange", "segment",
              "series", "trade_type", "auction", "quantity", "price",
              "trade_id", "order_id", "order_execution_time"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for i, r in enumerate(rows):
            symbol, date, ttype, qty, price = r
            w.writerow([symbol, f"INE{i:06d}01", date, "NSE", "EQ",
                        "EQ", ttype, "false", qty, price,
                        str(1000 + i), str(2000 + i), f"{date}T10:00:00"])
    return tmpdir


def run_test(tmpdir):
    return _rpnl.compute_monthly_realised_pnl(zerodha_dir=tmpdir)


PASS = []
FAIL = []


def check(name, condition, msg=""):
    if condition:
        PASS.append(name)
        print(f"  PASS: {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL: {name}  {msg}")


# ── Test 1: Basic single buy + single sell ───────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2024.csv", [
    ("TESTSTOCK", "2024-01-15", "buy",  100, 200.0),
    ("TESTSTOCK", "2024-03-20", "sell", 100, 250.0),
])
r = run_test(tmpdir)
check("Test1 monthly count", len(r["monthly"]) == 1, f"got {len(r['monthly'])}")
check("Test1 correct month",  r["monthly"][0]["label"] == "Mar 2024",
      f"got {r['monthly'][0]['label']}")
check("Test1 P&L value",      abs(r["monthly"][0]["gross_pnl"] - 5000.0) < 0.01,
      f"got {r['monthly'][0]['gross_pnl']}")
check("Test1 FY total",       abs(r["fy_totals"]["FY 23-24"] - 5000.0) < 0.01,
      f"got {r['fy_totals']}")
shutil.rmtree(tmpdir)

# ── Test 2: FIFO with two buy lots ────────────────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2024.csv", [
    ("ABC", "2024-01-01", "buy",  50, 100.0),   # lot 1: avg 100
    ("ABC", "2024-02-01", "buy",  50, 200.0),   # lot 2: avg 200
    ("ABC", "2024-06-01", "sell", 80, 250.0),   # FIFO: 50@100 + 30@200
])
r = run_test(tmpdir)
# Expected: (50*(250-100)) + (30*(250-200)) = 7500 + 1500 = 9000
expected = 9000.0
check("Test2 FIFO P&L", abs(r["monthly"][0]["gross_pnl"] - expected) < 0.01,
      f"got {r['monthly'][0]['gross_pnl']}")
shutil.rmtree(tmpdir)

# ── Test 3: Partial sell ──────────────────────────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2025.csv", [
    ("XYZ", "2025-04-01", "buy",  100, 300.0),
    ("XYZ", "2025-05-01", "sell",  40, 350.0),
])
r = run_test(tmpdir)
# Expected: 40 * (350 - 300) = 2000
check("Test3 partial sell P&L", abs(r["monthly"][0]["gross_pnl"] - 2000.0) < 0.01,
      f"got {r['monthly'][0]['gross_pnl']}")
shutil.rmtree(tmpdir)

# ── Test 4: Multiple sells from same buy pool ─────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2025.csv", [
    ("MULTI", "2025-04-01", "buy",  100, 500.0),
    ("MULTI", "2025-05-01", "sell",  30, 600.0),   # pnl = 3000
    ("MULTI", "2025-06-01", "sell",  30, 550.0),   # pnl = 1500
])
r = run_test(tmpdir)
months = {m["label"]: m["gross_pnl"] for m in r["monthly"]}
check("Test4 sell1 month", abs(months.get("May 2025", 0) - 3000.0) < 0.01,
      f"got {months}")
check("Test4 sell2 month", abs(months.get("Jun 2025", 0) - 1500.0) < 0.01,
      f"got {months}")
shutil.rmtree(tmpdir)

# ── Test 5: Orphan sell (no buy available) ────────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2024.csv", [
    ("ORPHAN", "2024-05-01", "sell", 10, 1000.0),
])
r = run_test(tmpdir)
check("Test5 orphan no monthly",  len(r["monthly"]) == 0,
      f"got monthly={r['monthly']}")
check("Test5 orphan warning",     len(r["warnings"]) >= 1,
      f"got {len(r['warnings'])} warnings")
shutil.rmtree(tmpdir)

# ── Test 6: Loss-making sell ──────────────────────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2025.csv", [
    ("LOSER", "2025-04-01", "buy",  50, 400.0),
    ("LOSER", "2025-07-01", "sell", 50, 300.0),
])
r = run_test(tmpdir)
# Expected: 50 * (300 - 400) = -5000
check("Test6 loss P&L", abs(r["monthly"][0]["gross_pnl"] - (-5000.0)) < 0.01,
      f"got {r['monthly'][0]['gross_pnl']}")
shutil.rmtree(tmpdir)

# ── Test 7: Cross-FY attribution ─────────────────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2024.csv", [
    ("CRSFY", "2024-02-01", "buy",  100, 100.0),   # FY 23-24
    ("CRSFY", "2024-05-01", "sell", 100, 150.0),   # FY 24-25
])
r = run_test(tmpdir)
# Sell in May 2024 → FY 24-25, not FY 23-24
check("Test7 sell in FY24-25", "FY 24-25" in r["fy_totals"],
      f"got {list(r['fy_totals'].keys())}")
check("Test7 FY24-25 total",   abs(r["fy_totals"]["FY 24-25"] - 5000.0) < 0.01,
      f"got {r['fy_totals']}")
check("Test7 no FY23-24 P&L", r["fy_totals"].get("FY 23-24", 0.0) == 0.0,
      f"got {r['fy_totals']}")
shutil.rmtree(tmpdir)

# ── Test 8: FY total == sum of monthly totals ─────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2025.csv", [
    ("RECONC", "2025-04-01", "buy",  100, 200.0),
    ("RECONC", "2025-05-01", "sell",  40, 250.0),
    ("RECONC", "2025-09-01", "sell",  30, 180.0),  # loss
    ("RECONC", "2026-01-15", "sell",  30, 270.0),
])
r = run_test(tmpdir)
for fy_lbl, fy_total in r["fy_totals"].items():
    monthly_sum = sum(m["gross_pnl"] for m in r["monthly"] if m["fy"] == fy_lbl)
    diff = abs(fy_total - round(monthly_sum, 2))
    check(f"Test8 reconcile {fy_lbl}", diff < 0.02,
          f"FY={fy_total} monthly_sum={monthly_sum}")
shutil.rmtree(tmpdir)

# ── Test 9: Deduplication by trade_id ────────────────────────────────────────
tmpdir = tempfile.mkdtemp()
# Write same trade twice — different files, same trade_id=1000
path1 = os.path.join(tmpdir, "Tradebook_2024.csv")
path2 = os.path.join(tmpdir, "Tradebook_2025.csv")
header = ["symbol","isin","trade_date","exchange","segment","series","trade_type",
          "auction","quantity","price","trade_id","order_id","order_execution_time"]
for path in [path1, path2]:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerow(["DEDUP","INE00001","2024-06-01","NSE","EQ","EQ","buy","false",
                    100, 300.0, "9999", "8888", "2024-06-01T10:00:00"])
        w.writerow(["DEDUP","INE00001","2024-08-01","NSE","EQ","EQ","sell","false",
                    100, 400.0, "7777", "6666", "2024-08-01T10:00:00"])
r = run_test(tmpdir)
# Without dedup, the buy would appear twice → orphan sell when second buy is consumed
check("Test9 dedup single P&L", len(r["monthly"]) == 1,
      f"got {len(r['monthly'])} months")
check("Test9 dedup value", abs(r["monthly"][0]["gross_pnl"] - 10000.0) < 0.01,
      f"got {r['monthly'][0]['gross_pnl']}")
shutil.rmtree(tmpdir)

# ── Test 10: Full exit then re-buy ─────────────────────────────────────────
tmpdir = tempfile.mkdtemp()
make_tradebook(tmpdir, "Tradebook_2025.csv", [
    ("REBUY", "2025-04-01", "buy",  100, 200.0),
    ("REBUY", "2025-05-01", "sell", 100, 250.0),   # full exit, P&L = 5000
    ("REBUY", "2025-06-01", "buy",  100, 220.0),   # re-buy, starts new FIFO lot
    ("REBUY", "2025-08-01", "sell", 100, 210.0),   # loss: P&L = -1000
])
r = run_test(tmpdir)
months = {m["label"]: m["gross_pnl"] for m in r["monthly"]}
check("Test10 first exit P&L", abs(months.get("May 2025", 0) - 5000.0) < 0.01,
      f"got {months}")
check("Test10 second exit loss", abs(months.get("Aug 2025", 0) - (-1000.0)) < 0.01,
      f"got {months}")
shutil.rmtree(tmpdir)

# ── Summary ──────────────────────────────────────────────────────────────────
print()
print(f"Results: {len(PASS)} PASSED, {len(FAIL)} FAILED")
if FAIL:
    print("FAILED tests:", FAIL)
    sys.exit(1)
else:
    print("All tests passed!")
