import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
from score_engine import compute_unified_score, calculate_buying_zone
from data_fetcher import fetch_fundamentals, fetch_technicals, fetch_rev_growth, fetch_prices_batch
from score_engine import get_archetype

symbols = ["RELIANCE", "TCS", "HDFCBANK", "ITC", "HINDUNILVR", "LT", "ICICIBANK", "SBIN", "BAJFINANCE", "BHARTIARTL"]
print("Fetching prices...")
prices = fetch_prices_batch(symbols)

distribution = {
    "❌ WAIT": 0,
    "🟡 SMALL BUY": 0,
    "🟢 ACCUMULATE": 0,
    "🟢🟢 ADD AGGRESSIVELY": 0,
    "🔎 INVESTIGATE WHY": 0
}

results = []

for sym in symbols:
    cmp = prices.get(sym)
    if not cmp:
        continue
    
    print(f"Fetching {sym}...")
    f = fetch_fundamentals(sym)
    tech = fetch_technicals(sym)
    rev_gr = fetch_rev_growth(sym)
    
    sector = f.get("sector", "")
    industry = f.get("industry", "")
    archetype = get_archetype(sym, sector, industry)
    
    metrics = {
        "roe":                f.get("roe"),
        "roa":                f.get("roa"),
        "roce":               f.get("roce"),
        "rev_growth":         rev_gr,
        "debt_eq":            f.get("debt_eq"),
        "pe":                 f.get("pe"),
        "pb":                 f.get("pb"),
        "div":                f.get("div"),
        "rsi":                tech.get("rsi") if tech.get("rsi") != "" else None,
        "sma200":             tech.get("sma200") if tech.get("sma200") != "" else None,
        "cmp":                cmp,
        "vol_spike":          tech.get("vol_spike") if tech.get("vol_spike") != "" else None,
        "cross":              tech.get("cross"),
    }
    
    q_sc, v_sc, t_sc, tot_sc, final_action, strengths, weaknesses = compute_unified_score(
        sym, archetype, metrics
    )
    
    zone = calculate_buying_zone(q_sc, v_sc, tot_sc, metrics)
    if zone in distribution:
        distribution[zone] += 1
        
    results.append({
        "Symbol": sym,
        "CMP": cmp,
        "Action": final_action,
        "Zone": zone,
        "Val_Score": v_sc,
        "PE": metrics.get("pe"),
        "Q_Score": q_sc
    })

with open('report.txt', 'w', encoding='utf-8') as f:
    f.write("Buying Zone distribution:\n")
    for k, v in distribution.items():
        f.write(f"{k}: {v}\n")

    f.write("\n10 example stocks:\n")
    f.write(f"{'Symbol':<15} | {'CMP':<8} | {'Final Action':<12} | {'Buying Zone':<25} | {'Val Score':<10} | {'PE':<8} | {'Q Score':<8}\n")
    f.write("-" * 100 + "\n")
    for r in results:
        f.write(f"{r['Symbol']:<15} | {r['CMP']:<8.2f} | {r['Action']:<12} | {r['Zone']:<25} | {r['Val_Score']:<10.1f} | {str(r['PE']):<8} | {r['Q_Score']:<8.1f}\n")
