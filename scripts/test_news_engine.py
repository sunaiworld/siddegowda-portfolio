"""
Standalone test - HDFCBANK, BEL, DRREDDY only. Prints raw articles and
classification output for manual review. Does NOT write to any sheet.
Run: python scripts/test_news_engine.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from news_engine.sources import google_news_rss
from news_engine import classifier

TEST_SYMBOLS = {
    "HDFCBANK": "HDFC Bank",
    "BEL": "Bharat Electronics",
    "DRREDDY": "Dr Reddy's Laboratories",
}

for sym, company in TEST_SYMBOLS.items():
    print(sym, company)
    articles = google_news_rss.fetch(sym, company)
    print(len(articles))

    result, enriched = classifier.classify(sym, articles)

    for a in enriched[:5]:
        print(json.dumps(a, indent=2))

    print(json.dumps(result.__dict__, indent=2))
