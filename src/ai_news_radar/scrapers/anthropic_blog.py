"""
Anthropic official blog scraper.
"""

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..models import RawArticle, SourceType

logger = logging.getLogger(__name__)

ANTHROPIC_NEWS_URL = "https://www.anthropic.com/news"
ANTHROPIC_RESEARCH_URL = "https://www.anthropic.com/research"
ANTHROPIC_ENG_URL = "https://www.anthropic.com/engineering"


class AnthropicBlogScraper(BaseScraper):
    """Scrape Anthropic's official news, research, and engineering blogs."""

    source_type = SourceType.ANTHROPIC
    source_name = "Anthropic Blog"

    BLOG_URLS = [
        ANTHROPIC_NEWS_URL,
        ANTHROPIC_RESEARCH_URL,
        ANTHROPIC_ENG_URL,
    ]

    def __init__(self, max_articles: int = 30, request_delay: float = 1.0):
        super().__init__(max_articles=max_articles, request_delay=request_delay)

    async def fetch(self) -> list[RawArticle]:
        logger.info("Fetching Anthropic blogs...")
        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AI-News-Radar/0.1)",
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=True,
        ) as client:
            all_articles = []
            for url in self.BLOG_URLS:
                try:
                    articles = await self._scrape_page(client, url)
                    all_articles.extend(articles)
                    logger.debug("Anthropic %s: %d articles", url, len(articles))
                except Exception as e:
                    logger.warning("Error scraping Anthropic %s: %s", url, e)

            logger.info("Anthropic: %d total articles", len(all_articles))
            return all_articles[:self.max_articles]

    async def _scrape_page(
        self, client: httpx.AsyncClient, url: str
    ) -> list[RawArticle]:
        resp = await client.get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        articles = []
        candidates = []

        # Pattern 1: <article> tags
        for article_tag in soup.find_all("article"):
            candidates.append(article_tag)

        # Pattern 2: Blog post cards
        for card in soup.find_all(["div", "li"], class_=lambda c: c and any(
            kw in (c or "").lower()
            for kw in ["post", "article", "blog", "card", "item"]
        )):
            if card.find("a"):
                candidates.append(card)

        # Pattern 3: Fallback - heading + link pairs
        if not candidates:
            for heading in soup.find_all(["h2", "h3"]):
                link = heading.find("a")
                if link and link.get("href"):
                    candidates.append(heading.parent)

        seen_urls = set()
        for candidate in candidates:
            link = candidate.find("a")
            if not link:
                continue

            href = link.get("href", "")
            if not href or "#" == href:
                continue

            full_url = urljoin(url, href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # Filter: only keep blog/article URLs
            blog_patterns = ["/news/", "/research/", "/engineering/", "/blog/", "/posts/"]
            if not any(kw in full_url for kw in blog_patterns):
                continue

            # Extract clean title: find the heading element that contains the link
            heading = candidate.find(["h1", "h2", "h3", "h4"])
            if heading and heading.find("a"):
                title = heading.get_text(strip=True)
            else:
                title = link.get_text(strip=True)

            # Clean up concatenated Anthropic blog format:
            # Pattern: "Real Title + CategoryTag + Date + ExtraSummaryText"
            import re as _re

            # 1. Remove date patterns like "May 7, 2026" or "Jan 21, 2026"
            title = _re.sub(
                r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+\d{1,2},?\s+\d{4}\s*',
                '', title
            ).strip()

            # 2. Cut at known category tags that appear in titles:
            #    Interpretability, Alignment, Research, Product, Engineering, Safety,
            #    Company, Policy, Announcements, Perspectives
            cut_at = _re.search(
                r'\b(Interpretability|Alignment|Research|Product|Engineering|Safety|'
                r'Company|Policy|Announcements|Perspectives|Featured)\b',
                title
            )
            if cut_at and cut_at.start() > 10:
                title = title[:cut_at.start()].strip()

            # 3. Remove leading "Featured" if still present
            title = _re.sub(r'^Featured\s*', '', title).strip()

            # 4. If title still too long (>150 chars), it likely has summary mixed in
            if len(title) > 150:
                # Try to find the end of the actual title (first period or colon)
                for sep in ['. ', '? ', '! ']:
                    idx = title.find(sep, 20)
                    if 20 < idx < 120:
                        title = title[:idx + 1].strip()
                        break

            if not title or len(title) < 5:
                continue

            # Try to find date
            date_tag = candidate.find("time")
            date_str = date_tag.get("datetime") if date_tag else None
            published_at = None
            if date_str:
                try:
                    published_at = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            # Try to find summary (avoid the heading itself)
            summary = ""
            for p in candidate.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 30 and text != title:
                    summary = text
                    break
            if not summary:
                summary = title

            articles.append(RawArticle(
                source_type=SourceType.ANTHROPIC,
                title=title,
                url=full_url,
                author="Anthropic",
                published_at=published_at,
                raw_content=summary,
                metadata={"blog_section": url},
            ))

            if len(articles) >= self.max_articles:
                break

        return articles
