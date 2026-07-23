"""
News Engine — Phase 1: Google News RSS ingestion, rule-based
classification, and caching.

Source-agnostic by design: each source module exposes
fetch(symbol, company_name) -> list[dict] with a common article shape
{title, source, published, link}. Adding a new source later means
adding a new file to sources/, not touching classifier.py, news_cache.py,
or main.py.
"""
