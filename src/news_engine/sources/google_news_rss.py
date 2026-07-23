"""
Google News RSS source. No API key, no rate limit published by Google,
but no SLA either — timeout + try/except at the call site is mandatory,
never assume this call succeeds.
"""
import logging
import urllib.parse
import xml.etree.ElementTree as ET
import requests

log = logging.getLogger(__name__)

RSS_BASE = "https://news.google.com/rss/search"
DEFAULT_TIMEOUT = 10


def fetch(symbol, company_name, timeout=DEFAULT_TIMEOUT):
    """
    Returns a list of {title, source, published, link} dicts, newest
    first, or [] on any failure — never raises past this function.
    """
    query = f'"{company_name}" NSE stock'
    params = {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    url = f"{RSS_BASE}?{urllib.parse.urlencode(params)}"

    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        log.warning(f"[news] {symbol}: RSS fetch failed — {e}")
        return []

    articles = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source_name = source_el.text.strip() if source_el is not None and source_el.text else ""
        if title:
            articles.append({
                "title": title,
                "source": source_name,
                "published": pub_date,
                "link": link,
            })
    return articles
