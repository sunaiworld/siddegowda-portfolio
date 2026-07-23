"""
NewsResult — the single object the news engine returns per symbol.
Phase 1 populates the first 7 fields. Phase 2 (AI Reasoning) extends
the same object rather than changing build_result_row()'s signature
again.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class NewsResult:
    # Phase 1
    summary: str = ""
    bullish_score: float = 0.0
    bearish_score: float = 0.0
    sentiment: str = "Neutral"     # Bullish | Neutral | Bearish
    reason: str = ""
    timestamp: str = ""            # ISO string, latest article's published time
    source: str = ""               # which source(s) contributed, e.g. "google_news_rss"

    # Phase 2 — unset for now, reserved so the pipeline doesn't change again
    investment_thesis: str = ""
    bull_case: str = ""
    bear_case: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    one_line_verdict: str = ""

    def to_sheet_row(self):
        """Only the 7 Phase-1 fields ever reach GITHUB DATA."""
        return [self.summary, self.bullish_score, self.bearish_score,
                self.sentiment, self.reason, self.timestamp, self.source]
