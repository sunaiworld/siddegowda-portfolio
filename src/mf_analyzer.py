"""
mf_analyzer.py
Analyzes Mutual Fund performance based on historical NAV data.
"""

from datetime import datetime
from dateutil.relativedelta import relativedelta
import statistics
import logging

from mf_history_fetcher import fetch_historical_nav, get_nav_on_date
import mf_data_fetcher

log = logging.getLogger(__name__)

def get_return(current_nav, historical_nav, years=1):
    if not current_nav or not historical_nav:
        return None
    try:
        ret = (current_nav - historical_nav) / historical_nav
        if years > 1:
            # Annualized return (CAGR)
            ret = ((1 + ret) ** (1/years)) - 1
        return ret
    except ZeroDivisionError:
        return None

def analyze_fund(isin):
    """
    Analyzes a mutual fund given its ISIN.
    Fetches the scheme code using amfi_data, then fetches historical NAVs.
    Computes 1Y, 3Y, 5Y returns, persistence, volatility (downside approximation), trend, and score.
    """
    amfi_data = mf_data_fetcher.fetch_amfi_data()
    fund_info = amfi_data.get(isin)
    
    if not fund_info:
        log.warning(f"No AMFI data found for ISIN {isin}")
        return None
        
    scheme_code = fund_info.get("scheme_code")
    hist_data = fetch_historical_nav(scheme_code)
    
    if not hist_data:
        log.warning(f"No historical data for scheme {scheme_code} (ISIN {isin})")
        return None
        
    today = datetime.now()
    
    # Current NAV from amfi
    curr_nav = fund_info.get("nav")
    if not curr_nav:
        curr_nav_str = hist_data[0].get("nav") if hist_data else None
        curr_nav = float(curr_nav_str) if curr_nav_str else None
        
    if not curr_nav:
        return None

    date_1y = (today - relativedelta(years=1)).strftime("%d-%m-%Y")
    date_3y = (today - relativedelta(years=3)).strftime("%d-%m-%Y")
    date_5y = (today - relativedelta(years=5)).strftime("%d-%m-%Y")
    
    nav_1y = get_nav_on_date(hist_data, date_1y)
    nav_3y = get_nav_on_date(hist_data, date_3y)
    nav_5y = get_nav_on_date(hist_data, date_5y)
    
    ret_1y = get_return(curr_nav, nav_1y, years=1)
    ret_3y = get_return(curr_nav, nav_3y, years=3)
    ret_5y = get_return(curr_nav, nav_5y, years=5)
    
    # Calculate simple volatility (standard deviation of monthly returns over last 3 years)
    # We will sample NAV roughly every 30 days
    monthly_returns = []
    prev_nav = curr_nav
    current_date = today
    for _ in range(36): # 36 months = 3 years
        current_date = current_date - relativedelta(months=1)
        m_nav = get_nav_on_date(hist_data, current_date.strftime("%d-%m-%Y"))
        if m_nav and prev_nav:
            monthly_returns.append((prev_nav - m_nav) / m_nav)
            prev_nav = m_nav
        else:
            break
            
    volatility = None
    if len(monthly_returns) >= 12:
        # Annualized volatility
        volatility = statistics.stdev(monthly_returns) * (12 ** 0.5)

    # Persistence Score: 
    # Measures if performance is consistent across periods.
    # We penalize huge variance between 1Y, 3Y, 5Y returns.
    valid_returns = [r for r in [ret_1y, ret_3y, ret_5y] if r is not None]
    persistence = None
    if len(valid_returns) >= 2:
        # Lower std dev of returns means higher persistence
        ret_stdev = statistics.stdev(valid_returns)
        persistence = max(0, 100 - (ret_stdev * 100 * 2)) # Arbitrary scaling for a 0-100 score
    elif len(valid_returns) == 1:
        persistence = 50 # Neutral if we only have 1 period

    # We don't have benchmark data, so we compare against a static hurdle rate (e.g. 10% annualized)
    HURDLE_RATE = 0.10
    
    consistency_score = 0
    weight_sum = 0
    
    if ret_1y is not None:
        consistency_score += max(0, min(100, (ret_1y / HURDLE_RATE) * 50)) * 0.2
        weight_sum += 0.2
    if ret_3y is not None:
        consistency_score += max(0, min(100, (ret_3y / HURDLE_RATE) * 50)) * 0.4
        weight_sum += 0.4
    if ret_5y is not None:
        consistency_score += max(0, min(100, (ret_5y / HURDLE_RATE) * 50)) * 0.4
        weight_sum += 0.4
        
    if weight_sum > 0:
        consistency_score = consistency_score / weight_sum
    else:
        consistency_score = 50

    # Downside Risk Score (higher is better, meaning lower volatility)
    if volatility is not None:
        # Volatility usually ranges 10% - 30% for equity. 
        # 10% vol = 90 score, 30% vol = 30 score
        risk_score = max(0, min(100, 100 - (volatility * 100 * 2.5)))
    else:
        risk_score = 50
        
    # Overall Score (0-100)
    overall_score = (consistency_score * 0.5) + ((persistence or 50) * 0.25) + (risk_score * 0.25)
    
    # Trend
    if len(valid_returns) >= 2 and ret_1y is not None and ret_3y is not None:
        if ret_1y > ret_3y + 0.02:
            trend = "Improving"
        elif ret_1y < ret_3y - 0.02:
            trend = "Deteriorating"
        else:
            trend = "Stable"
    else:
        trend = "Unknown"
        
    # AI Decision
    if overall_score >= 75:
        decision = "BUY / ADD"
        reason = "Strong long-term performance and good risk metrics."
    elif overall_score >= 60:
        decision = "HOLD"
        reason = "Stable performance, meets standard expectations."
    elif overall_score >= 45:
        decision = "WATCH"
        reason = "Average performance or lack of sufficient history."
    else:
        decision = "REVIEW / REDUCE"
        reason = "Underperforming over long periods or high downside risk."

    return {
        "ret_1y": ret_1y,
        "ret_3y": ret_3y,
        "ret_5y": ret_5y,
        "volatility": volatility,
        "persistence": persistence,
        "consistency_score": consistency_score,
        "risk_score": risk_score,
        "overall_score": overall_score,
        "trend": trend,
        "decision": decision,
        "reason": reason
    }
