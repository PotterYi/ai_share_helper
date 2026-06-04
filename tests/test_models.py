"""Tests for data models."""

import pytest
from datetime import datetime
from ai_news_radar.models import (
    Article, RawArticle, SourceType, Sentiment, Category, DailyReport, FetchResult
)


class TestArticle:
    def test_create_article(self):
        article = Article(
            source_type=SourceType.HACKER_NEWS,
            title="GPT-5 announced",
            url="https://example.com/gpt5",
            author="testuser",
            published_at=datetime(2025, 1, 1),
            raw_content="OpenAI announces GPT-5 with groundbreaking features.",
            score=100,
            comment_count=50,
        )
        assert article.source_type == SourceType.HACKER_NEWS
        assert article.title == "GPT-5 announced"
        assert article.importance == 0.0
        assert article.is_analyzed is False

    def test_is_breaking(self):
        article = Article(
            source_type=SourceType.ANTHROPIC,
            title="Breaking news",
            url="https://example.com",
            importance=0.9,
        )
        assert article.is_breaking() is True

        article.importance = 0.5
        assert article.is_breaking() is False


class TestRawArticle:
    def test_create_raw_article(self):
        raw = RawArticle(
            source_type=SourceType.REDDIT_ML,
            title="Test post",
            url="https://reddit.com/r/ml/test",
            score=42,
            comment_count=10,
        )
        assert raw.source_type == SourceType.REDDIT_ML
        assert raw.score == 42


class TestFetchResult:
    def test_fetch_result(self):
        result = FetchResult(
            source_type=SourceType.HACKER_NEWS,
            fetched_count=10,
            new_count=5,
            duration_seconds=1.5,
        )
        assert result.fetched_count == 10
        assert result.new_count == 5


class TestDailyReport:
    def test_create_report(self):
        report = DailyReport(
            report_type="daily",
            title="Daily Report",
            content="# Report",
            article_count=10,
        )
        assert report.report_type == "daily"
        assert report.article_count == 10
