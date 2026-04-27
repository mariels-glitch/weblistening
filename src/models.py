"""
Shared data models for the listening engine.

Every scraped artifact — a Reddit comment, an App Store review, a Google Trends
point — is normalized into a single Item shape so downstream enrichment and
aggregation don't care where it came from.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Source(str, Enum):
    REDDIT = "reddit"
    APPSTORE = "appstore"
    TRENDS = "trends"
    TRUSTPILOT_MANUAL = "trustpilot_manual"


class ProductLine(str, Enum):
    CARD = "card"
    INSURANCE = "insurance"
    BRAND = "brand"
    COMPETITOR = "competitor"


Sentiment = Literal["positive", "negative", "neutral", "mixed"]


class Item(BaseModel):
    item_id: str
    source: Source
    product_line: ProductLine = ProductLine.BRAND
    author_hash: str
    created_at: datetime
    text: str = Field(max_length=10000)
    url: str
    engagement: dict = Field(default_factory=dict)  # upvotes, stars, volume index
    lang: str = "en"

    # Filled in by enrichers
    sentiment: Optional[Sentiment] = None
    sentiment_conf: Optional[float] = None
    themes: list[str] = Field(default_factory=list)
    pii_scrubbed: bool = False
    competitor_match: Optional[str] = None  # competitor name if this is competitor mention

    # Roster flags (Decision 10)
    is_first_party: bool = False
    mentions_leadership: bool = False

    @staticmethod
    def hash_author(author: str) -> str:
        return sha256(author.encode("utf-8")).hexdigest()[:16]


class TrendPoint(BaseModel):
    query: str
    wow_percent: float  # +220 means +220% week over week
    current_index: float
    as_of: datetime


class ThemeCard(BaseModel):
    theme: str
    one_line: str
    supporting_item_ids: list[str]
    count: int
    sentiment: Sentiment


class QuoteCard(BaseModel):
    text: str
    source_label: str     # "App Store · US", "Reddit · r/CreditCards"
    item_id: str
    url: str
    engagement_label: str  # "★★★★★ · verified verbatim", "47 upvotes · verified verbatim"


class Alert(BaseModel):
    theme: str
    delta_sigma: float
    human_readable: str
    supporting_item_ids: list[str]


class LeadershipMention(BaseModel):
    text: str
    source_label: str
    item_id: str
    url: str
    subject: str  # whose name got mentioned


class Digest(BaseModel):
    """
    The structured output the summarizer must produce.
    The renderer refuses to ship a digest where any claim has zero citations.
    """
    digest_id: str
    date: datetime
    window_hours: int
    items_analyzed: int

    sentiment_score: int  # 0-100
    sentiment_delta_vs_7d: int
    headline: str

    loved: list[ThemeCard]      # up to 3
    disliked: list[ThemeCard]   # up to 3
    trending: list[TrendPoint]  # up to 3
    quotes: list[QuoteCard]     # up to 3
    alerts: list[Alert] = Field(default_factory=list)
    leadership_mentions: list[LeadershipMention] = Field(default_factory=list)

    methodology_sources: list[str]
    methodology_window: str
