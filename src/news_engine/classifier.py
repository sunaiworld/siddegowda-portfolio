"""
Rule-based classification — keyword matching, not ML, not an LLM.
Every score this produces is explainable by pointing at which keyword
matched which article. No hidden model, nothing here should be
described to the user as "AI" without that caveat.
"""
import logging
from datetime import datetime, timezone
from news_engine.news_result import NewsResult

log = logging.getLogger(__name__)

CATEGORY_KEYWORDS = {
    "earnings": ["net profit", "net loss", "quarterly results", "q1 results", "q2 results",
                 "q3 results", "q4 results", "ebitda", "profit rises", "profit falls",
                 "results announced", "beats estimates", "misses estimates"],
    "management": ["resign", "resigns", "appoints", "appointed", "chairman", " ceo ",
                    "managing director", "board meeting", "independent director"],
    "corporate_actions": ["bonus issue", "stock split", "rights issue", "buyback",
                           "merger", "demerger", "amalgamation"],
    "promoter_activity": ["promoter", "pledge", "pledged shares", "stake sale", "stake purchase",
                           "insider trading"],
    "order_wins": ["order win", "contract win", "bags order", "wins contract",
                    "awarded contract", "l1 bidder", "lowest bidder"],
    "approvals": ["approval", "clearance", "license granted", "licence granted",
                   "regulatory approval", "nod from"],
    "regulatory": ["sebi", "rbi action", "penalty", "fine imposed", "show cause notice",
                    "investigation", "notice from"],
    "acquisitions": ["to acquire", "acquisition of", "acquires", "takeover", "stake in"],
    "dividends": ["dividend", "interim dividend", "final dividend", "record date"],
}

BULLISH_KEYWORDS = ["profit rises", "beats estimates", "record high", "upgrade", "order win",
                     "wins contract", "bags order", "strong growth", "outperform", "buy rating",
                     "raises guidance", "approval granted", "expansion plan"]
BEARISH_KEYWORDS = ["profit falls", "misses estimates", "downgrade", "record low", "penalty",
                     "fine imposed", "investigation", "resigns", "stake sale", "sell rating",
                     "cuts guidance", "loss widens", "show cause notice"]


def _match_keywords(text_lower, keyword_list):
    return [kw for kw in keyword_list if kw in text_lower]


def classify(symbol, articles):
    """
    Takes raw articles (from a source module), returns:
      (NewsResult, enriched_articles)
    enriched_articles carries matched_category/bullish_keywords/bearish_keywords
    per article — for the raw cache, never written to GITHUB DATA.
    """
    if not articles:
        return NewsResult(summary="No recent news found", sentiment="Neutral",
                           source="google_news_rss"), []

    enriched = []
    bullish_hits, bearish_hits = [], []
    category_counts = {}

    for art in articles:
        text_lower = art["title"].lower()
        matched_category = None
        for cat, kws in CATEGORY_KEYWORDS.items():
            if any(kw in text_lower for kw in kws):
                matched_category = cat
                category_counts[cat] = category_counts.get(cat, 0) + 1
                break  # first matching category wins, avoids double-counting

        b_kws = _match_keywords(text_lower, BULLISH_KEYWORDS)
        r_kws = _match_keywords(text_lower, BEARISH_KEYWORDS)
        bullish_hits.extend(b_kws)
        bearish_hits.extend(r_kws)

        enriched.append({
            **art,
            "matched_category": matched_category or "uncategorized",
            "bullish_keywords": b_kws,
            "bearish_keywords": r_kws,
        })

    bullish_score = min(10.0, len(bullish_hits))
    bearish_score = min(10.0, len(bearish_hits))

    if bullish_score > bearish_score + 1:
        sentiment = "Bullish"
    elif bearish_score > bullish_score + 1:
        sentiment = "Bearish"
    else:
        sentiment = "Neutral"

    top_category = max(category_counts, key=category_counts.get) if category_counts else "general"
    reason_bits = []
    if bullish_hits:
        reason_bits.append(f"bullish signals: {', '.join(sorted(set(bullish_hits))[:3])}")
    if bearish_hits:
        reason_bits.append(f"bearish signals: {', '.join(sorted(set(bearish_hits))[:3])}")
    reason = "; ".join(reason_bits) if reason_bits else f"mostly {top_category}-related coverage, no strong signal"

    summary = f"{len(articles)} articles, dominant topic: {top_category}. {enriched[0]['title']}"

    return NewsResult(
        summary=summary[:300],
        bullish_score=bullish_score,
        bearish_score=bearish_score,
        sentiment=sentiment,
        reason=reason[:300],
        timestamp=datetime.now(timezone.utc).isoformat(),
        source="google_news_rss",
    ), enriched
