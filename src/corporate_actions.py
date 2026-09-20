"""
Corporate actions module tracking stock splits and bonus issues.
Maintains historical split/bonus events to ensure share counts and average buy
prices accurately reflect current market pricing and prevent artificial portfolio loss distortions.
"""

from typing import Dict, List, Tuple

# Map of Symbol -> List of (effective_date_iso, split_ratio)
# Verified from exchange records (NSE/BSE) and yfinance corporate actions.
# split_ratio is the post-action multiplier:
# e.g., a 1:5 split turns 1 share into 5 shares (ratio = 5.0)
# e.g., a 1:1 bonus issue turns 1 share into 2 shares (ratio = 2.0)
SPLIT_REGISTRY: Dict[str, List[Tuple[str, float]]] = {
    "ANGELONE": [("2026-02-26", 10.0)],
    "ASTRAL": [("2023-03-14", 1.3333333333333333)],
    "BAJFINANCE": [("2025-06-16", 2.0)],
    "BECTORFOOD": [("2025-12-12", 5.0)],
    "BERGEPAINT": [("2023-09-22", 1.2)],
    "BPCL": [("2024-06-21", 2.0)],
    "CAMS": [("2025-12-05", 5.0)],
    "CANBK": [("2024-05-15", 5.0)],
    "CDSL": [("2024-08-23", 2.0)],
    "COFORGE": [("2025-06-04", 5.0)],
    "CONCOR": [("2025-07-04", 1.25)],
    "CUB": [("2026-06-12", 1.3333333333333333)],
    "GMMPFAUDLR": [("2022-07-11", 3.0)],
    "HDFCAMC": [("2025-11-26", 2.0)],
    "HDFCBANK": [("2025-08-26", 2.0)],
    "JINDALSAW": [("2024-10-09", 2.0)],
    "KIRLPNU": [("2026-08-18", 2.0)],
    "KOTAKBANK": [("2026-01-14", 5.0)],
    "MOTHERSON": [("2025-07-18", 1.5)],
    "MOTILALOFS": [("2024-06-10", 4.0)],
    "NESTLEIND": [("2024-01-05", 10.0), ("2025-08-08", 2.0)],
    "NMDC": [("2024-12-27", 3.0)],
    "NUVAMA": [("2025-12-26", 5.0)],
    "OIL": [("2024-07-02", 1.5)],
    "PERSISTENT": [("2024-03-28", 2.0)],
    "RELIANCE": [("2024-10-28", 2.0)],
    "RUSHIL": [("2024-08-09", 10.0)],
    "SHRIRAMFIN": [("2025-01-10", 5.0)],
    "SONATSOFTW": [("2022-09-08", 1.3333333333333333), ("2023-12-12", 2.0)],
    "TATASTEEL": [("2022-07-28", 10.0)],
    "TRENT": [("2026-06-04", 1.5)],
}


def get_splits_for_symbol(symbol: str) -> List[Tuple[str, float]]:
    """Return list of (date, ratio) splits for a symbol sorted chronologically."""
    sym = symbol.strip().upper()
    return sorted(SPLIT_REGISTRY.get(sym, []), key=lambda x: x[0])
