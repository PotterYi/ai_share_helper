"""
Core orchestration engine that coordinates the full pipeline.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from .config import get_sources_config, is_configured
from .database import Database
from .models import Article, FetchResult, PipelineResult, DailyReport
from .scrapers import AnthropicBlogScraper, GitHubTrendingScraper, GitHubFastGrowingScraper, HackerNewsScraper, RedditMLScraper
from .processors.deduplicator import Deduplicator
from .processors.ai_analyzer import AIAnalyzer
from .reporters.markdown_report import MarkdownReporter
from .reporters.notifier import Notifier

logger = logging.getLogger(__name__)


class Engine:
    """Main orchestration engine for AI News Radar."""

    def __init__(self, db=None, backend="auto", analyze=True, notify=False, source_filter=None):
        self.db = db or Database()
        self.analyze_enabled = analyze
        self.notify_enabled = notify
        self.source_filter = source_filter  # None = all, or list of SourceType

        if analyze and is_configured():
            self.analyzer = AIAnalyzer(backend=backend)
        else:
            self.analyzer = None
            if analyze:
                logger.warning(
                    "No API key configured. AI analysis disabled. "
                    "Set DEEPSEEK_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY in .env"
                )

        self.deduplicator = Deduplicator()
        self.reporter = MarkdownReporter()
        self.notifier = Notifier()

    async def run_full_pipeline(self):
        """Run the complete news pipeline."""
        started_at = datetime.now()
        result = PipelineResult(started_at=started_at)
        logger.info("=" * 60)
        logger.info("AI News Radar - Pipeline Start")
        logger.info("=" * 60)

        # Phase 1: Scrape all sources
        logger.info("[Phase 1/4] Scraping sources...")
        all_raw_articles = await self._scrape_all()
        total_fetched = len(all_raw_articles)
        result.total_fetched = total_fetched
        logger.info("Scraping complete: %d raw articles", total_fetched)

        # Phase 2: Convert to Articles, dedup, and store
        logger.info("[Phase 2/4] Deduplicating and storing...")
        new_articles = self._dedup_and_store(all_raw_articles)
        result.total_new = len(new_articles)
        logger.info("Stored %d new articles", len(new_articles))

        # Phase 3: AI Analysis
        if self.analyze_enabled and self.analyzer and new_articles:
            logger.info("[Phase 3/4] Analyzing articles with AI...")
            analyzed = await self.analyzer.analyze_batch(new_articles)
            for article in analyzed:
                if article.is_analyzed and article.id:
                    self.db.update_article_analysis(article)
            result.analyzed_count = sum(1 for a in analyzed if a.is_analyzed)
            logger.info("Analysis complete: %d articles analyzed", result.analyzed_count)
        else:
            logger.info("[Phase 3/4] Skipping AI analysis")

        # Phase 4: Generate report (only articles from the filtered sources)
        logger.info("[Phase 4/4] Generating report...")
        today_articles = self.db.get_today_articles(limit=100, source_filter=self.source_filter)
        if today_articles:
            report = self.reporter.generate_daily_report(today_articles, top_n=15)
            self.db.save_report(report)
            result.report = report
            logger.info("Report generated: %d articles", len(today_articles))

            if self.notify_enabled:
                logger.info("Sending notifications...")
                notif_results = await self.notifier.send_daily_report(report)
                logger.info("Notifications: %s", notif_results)

        result.finished_at = datetime.now()
        duration = (result.finished_at - result.started_at).total_seconds()
        logger.info("=" * 60)
        logger.info(
            "Pipeline Complete in %.1fs | Fetched: %d | New: %d | Analyzed: %d",
            duration, result.total_fetched, result.total_new, result.analyzed_count,
        )
        logger.info("=" * 60)
        return result

    async def scrape_only(self):
        """Only scrape and store."""
        all_raw = await self._scrape_all()
        self._dedup_and_store(all_raw)
        return len(all_raw)

    async def analyze_only(self, limit=50):
        """Only analyze unanalyzed articles."""
        if not self.analyzer:
            logger.warning("Analyzer not available")
            return 0
        articles = self.db.get_unanalyzed_articles(limit=limit)
        if not articles:
            logger.info("No unanalyzed articles found")
            return 0
        analyzed = await self.analyzer.analyze_batch(articles)
        for article in analyzed:
            if article.is_analyzed and article.id:
                self.db.update_article_analysis(article)
        return sum(1 for a in analyzed if a.is_analyzed)

    async def report_only(self, top_n=15):
        """Only generate a report from existing articles."""
        today_articles = self.db.get_today_articles(limit=100, source_filter=self.source_filter)
        if not today_articles:
            logger.info("No articles found for today")
            return None
        report = self.reporter.generate_daily_report(today_articles, top_n=top_n)
        self.db.save_report(report)
        if self.notify_enabled:
            await self.notifier.send_daily_report(report)
        return report

    async def _scrape_all(self):
        """Scrape all enabled sources, optionally filtered by source_filter."""
        sources_config = get_sources_config()
        scrapers = []

        def _should(source_type_val: str) -> bool:
            if self.source_filter is None:
                return True
            return source_type_val in self.source_filter

        def _max(source_key: str, default: int = 10) -> int:
            return sources_config.get(source_key, {}).get("max_articles_per_fetch", default)

        if _should("anthropic") and sources_config.get("anthropic", {}).get("enabled", True):
            scrapers.append(AnthropicBlogScraper(max_articles=_max("anthropic")))
        if _should("github_trending") and sources_config.get("github_trending", {}).get("enabled", True):
            scrapers.append(GitHubTrendingScraper(max_articles=_max("github_trending")))
        if _should("hacker_news") and sources_config.get("hacker_news", {}).get("enabled", True):
            scrapers.append(HackerNewsScraper(max_articles=_max("hacker_news")))
        if _should("reddit_ml") and sources_config.get("reddit_ml", {}).get("enabled", True):
            scrapers.append(RedditMLScraper(max_articles=_max("reddit_ml")))
        if _should("github_fast_growing") and sources_config.get("github_fast_growing", {}).get("enabled", True):
            scrapers.append(GitHubFastGrowingScraper(max_articles=_max("github_fast_growing")))

        all_articles = []
        for scraper in scrapers:
            try:
                articles = await scraper.fetch()
                all_articles.extend(articles)
                self.db.ensure_source(scraper.source_type.value, scraper.source_name)
                self.db.update_source_fetch(
                    scraper.source_type.value, len(articles)
                )
                logger.info(
                    "%s: %d articles fetched", scraper.source_name, len(articles)
                )
            except Exception as e:
                logger.error("Error scraping %s: %s", scraper.source_name, e)

        return all_articles

    def _dedup_and_store(self, raw_articles):
        """Convert raw articles, deduplicate, and store new ones."""
        articles = []
        for raw in raw_articles:
            articles.append(Article(
                source_type=raw.source_type,
                title=raw.title,
                url=raw.url,
                author=raw.author,
                published_at=raw.published_at,
                fetched_at=datetime.now(),
                raw_content=raw.raw_content,
                score=raw.score,
                comment_count=raw.comment_count,
                metadata=raw.metadata,
            ))

        existing_urls = self.deduplicator.get_existing_urls(self.db)
        existing_titles = self.deduplicator.get_existing_titles(self.db)
        new_articles = self.deduplicator.filter_new(
            articles, existing_urls, existing_titles
        )

        count = 0
        for article in new_articles:
            aid = self.db.insert_article(article)
            if aid:
                article.id = aid
                count += 1

        logger.info("Dedup: %d/%d new articles stored", count, len(articles))
        return [a for a in new_articles if a.id is not None]

    def close(self):
        self.db.close()
