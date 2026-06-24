"""Pydantic data models for AI News Radar."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class SourceType(str, Enum):
    ANTHROPIC = "anthropic"
    GITHUB_TRENDING = "github_trending"
    GITHUB_FAST_GROWING = "github_fast_growing"
    HACKER_NEWS = "hacker_news"
    REDDIT_ML = "reddit_ml"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class Category(str, Enum):
    MODEL_RELEASE = "model_release"
    RESEARCH = "research"
    TOOL = "tool"
    SAFETY = "safety"
    DISCUSSION = "discussion"
    TUTORIAL = "tutorial"
    INDUSTRY = "industry"
    UNKNOWN = "unknown"


class Source(BaseModel):
    """An information source configuration."""
    id: Optional[int] = None
    name: str
    source_type: SourceType
    base_url: str
    enabled: bool = True
    last_fetch: Optional[datetime] = None
    fetch_count: int = 0


class RawArticle(BaseModel):
    """A raw article scraped from a source, before AI analysis."""
    source_type: SourceType
    title: str
    url: str
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    raw_content: Optional[str] = None
    score: Optional[int] = None           # upvotes/stars
    comment_count: Optional[int] = None
    metadata: dict = Field(default_factory=dict)


class Article(BaseModel):
    """A fully processed article with AI analysis."""
    id: Optional[int] = None
    source_type: SourceType
    title: str
    url: str
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    fetched_at: datetime = Field(default_factory=datetime.now)
    summary: Optional[str] = None          # AI-generated summary
    category: Category = Category.UNKNOWN
    tags: list[str] = Field(default_factory=list)
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    sentiment: Sentiment = Sentiment.NEUTRAL
    is_analyzed: bool = False
    raw_content: Optional[str] = None
    score: Optional[int] = None
    comment_count: Optional[int] = None
    metadata: dict = Field(default_factory=dict)

    def is_breaking(self) -> bool:
        """Check if this article qualifies as breaking news."""
        return self.importance >= 0.8


class DailyReport(BaseModel):
    """A generated daily news digest."""
    id: Optional[int] = None
    report_type: str = "daily"
    generated_at: datetime = Field(default_factory=datetime.now)
    title: str = ""
    content: str = ""                      # Markdown content
    article_count: int = 0
    top_articles: list[Article] = Field(default_factory=list)
    source_summary: dict[str, int] = Field(default_factory=dict)


class FetchResult(BaseModel):
    """Result of a single fetch operation."""
    source_type: SourceType
    fetched_count: int
    new_count: int
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class PipelineResult(BaseModel):
    """Result of a full pipeline run."""
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    fetch_results: list[FetchResult] = Field(default_factory=list)
    total_fetched: int = 0
    total_new: int = 0
    analyzed_count: int = 0
    report: Optional[DailyReport] = None
    errors: list[str] = Field(default_factory=list)
