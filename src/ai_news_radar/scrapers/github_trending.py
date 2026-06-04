"""
GitHub Trending scraper.
"""

import logging
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..models import RawArticle, SourceType

logger = logging.getLogger(__name__)

GITHUB_TRENDING_URL = "https://github.com/trending"


class GitHubTrendingScraper(BaseScraper):
    """Scrape GitHub Trending page for AI-related repositories."""

    source_type = SourceType.GITHUB_TRENDING
    source_name = "GitHub Trending"

    TRENDING_URLS = [
        f"{GITHUB_TRENDING_URL}?since=daily",
        f"{GITHUB_TRENDING_URL}/python?since=daily",
        f"{GITHUB_TRENDING_URL}?since=weekly",
    ]

    def __init__(self, max_articles: int = 25, request_delay: float = 1.5):
        super().__init__(max_articles=max_articles, request_delay=request_delay)

    async def fetch(self) -> list[RawArticle]:
        """Fetch trending AI repos from GitHub."""
        logger.info("Fetching GitHub Trending...")
        all_articles = []

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
            follow_redirects=True,
        ) as client:
            for url in self.TRENDING_URLS:
                try:
                    articles = await self._scrape_trending(client, url)
                    all_articles.extend(articles)
                    logger.debug("GitHub %s: %d repos found", url, len(articles))
                except Exception as e:
                    logger.warning("Error scraping GitHub %s: %s", url, e)

            # If primary method fails, try fallback
            if not all_articles:
                try:
                    articles = await self._fallback_github_search(client)
                    all_articles.extend(articles)
                    logger.info("GitHub fallback: %d repos", len(articles))
                except Exception as e:
                    logger.error("GitHub fallback also failed: %s", e)

        # Deduplicate by repo URL
        seen = set()
        unique = []
        for a in all_articles:
            if a.url not in seen:
                seen.add(a.url)
                unique.append(a)

        logger.info("GitHub Trending: %d unique AI repos", len(unique))
        return unique[:self.max_articles]

    async def _scrape_trending(
        self, client: httpx.AsyncClient, url: str
    ) -> list[RawArticle]:
        """Scrape a single GitHub Trending page."""
        resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning("GitHub returned %d for %s", resp.status_code, url)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        articles = []

        # Find repo boxes
        repo_boxes = soup.find_all("article", class_="Box-row")
        if not repo_boxes:
            repo_boxes = soup.select('.Box-row, [data-hovercard-type="repository"]')

        for box in repo_boxes:
            name_link = box.find("h2") or box.find("h3")
            if not name_link:
                continue
            link = name_link.find("a")
            if not link:
                continue

            href = link.get("href", "").strip()
            repo_name = href.strip("/")
            full_url = urljoin("https://github.com", href)

            # Description
            desc_tag = box.find("p")
            description = desc_tag.get_text(strip=True) if desc_tag else ""

            # Language
            lang_tag = box.find("span", itemprop="programmingLanguage")
            language = lang_tag.get_text(strip=True) if lang_tag else ""

            # Stars today
            stars_today = 0
            stars_spans = box.find_all("span", class_="d-inline-block")
            for span in stars_spans:
                text = span.get_text(strip=True)
                if "star" in text.lower():
                    nums = re.findall(r'[\d,]+', text)
                    if nums:
                        stars_today = int(nums[0].replace(",", ""))
                        break

            # Total stars and forks
            total_stars = 0
            forks = 0
            stats_links = box.find_all("a", class_="Link--muted")
            for sl in stats_links:
                text = sl.get_text(strip=True)
                nums = re.findall(r'[\d,]+', text)
                if nums:
                    val = int(nums[0].replace(",", ""))
                    if "star" in sl.get("href", "").lower():
                        total_stars = val
                    elif "fork" in sl.get("href", "").lower():
                        forks = val

            articles.append(RawArticle(
                source_type=SourceType.GITHUB_TRENDING,
                title=repo_name,
                url=full_url,
                author=repo_name.split("/")[0] if "/" in repo_name else "",
                published_at=datetime.utcnow(),
                raw_content=description,
                score=stars_today + (total_stars // 100),
                comment_count=forks,
                metadata={
                    "language": language,
                    "total_stars": total_stars,
                    "stars_today": stars_today,
                    "forks": forks,
                },
            ))

        return self._apply_ai_filter(articles)

    async def _fallback_github_search(
        self, client: httpx.AsyncClient
    ) -> list[RawArticle]:
        """Fallback: use GitHub Search API for recent AI repos."""
        yesterday = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        query = f"ai+machine+learning+created:>={yesterday}"
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={query}&sort=stars&order=desc&per_page=30"
        )

        resp = await client.get(
            url,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        articles = []
        for repo in data.get("items", []):
            articles.append(RawArticle(
                source_type=SourceType.GITHUB_TRENDING,
                title=repo.get("full_name", ""),
                url=repo.get("html_url", ""),
                author=repo.get("owner", {}).get("login", ""),
                published_at=datetime.utcnow(),
                raw_content=repo.get("description", ""),
                score=repo.get("stargazers_count", 0),
                comment_count=repo.get("forks_count", 0),
                metadata={
                    "language": repo.get("language", ""),
                    "total_stars": repo.get("stargazers_count", 0),
                    "stars_today": 0,
                    "forks": repo.get("forks_count", 0),
                    "topics": repo.get("topics", []),
                },
            ))

        return self._apply_ai_filter(articles)
