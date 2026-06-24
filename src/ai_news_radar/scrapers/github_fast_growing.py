"""
GitHub Fast-Growing Repositories scraper.

Uses GitHub Search API to find repositories with the most stars
gained recently — surfaces fast-growing / viral projects.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from .base import BaseScraper
from ..models import RawArticle, SourceType

logger = logging.getLogger(__name__)


def _estimate_daily_growth(repo: dict) -> float:
    """Estimate daily star growth from created_at and total stars."""
    created_raw = repo.get("created_at", "")
    stars = repo.get("stargazers_count", 0)
    if not created_raw or not stars:
        return 0
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        if created.tzinfo is not None:
            created = created.replace(tzinfo=None)
        days_since = max((datetime.utcnow() - created).days, 1)
        return round(stars / days_since, 1)
    except Exception:
        return 0


def _days_since_created(repo: dict) -> int:
    """Days since repo creation."""
    created_raw = repo.get("created_at", "")
    if not created_raw:
        return 999
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        # Make created offset-naive so it can be subtracted from utcnow()
        if created.tzinfo is not None:
            created = created.replace(tzinfo=None)
        return max((datetime.utcnow() - created).days, 0)
    except Exception:
        return 999


class GitHubFastGrowingScraper(BaseScraper):
    """Scrape GitHub for fast-growing / viral repositories."""

    source_type = SourceType.GITHUB_FAST_GROWING
    source_name = "GitHub Fast-Growing"

    def __init__(self, max_articles: int = 10, request_delay: float = 0.5):
        super().__init__(max_articles=max_articles, request_delay=request_delay)

    async def fetch(self) -> list[RawArticle]:
        """Fetch fast-growing repos via multiple GitHub API queries."""
        logger.info("Fetching GitHub Fast-Growing repos...")
        all_articles = []

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "AI-News-Radar/1.0",
            },
            follow_redirects=True,
        ) as client:
            # Query 1: brand-new repos (last 7 days) with most stars
            all_articles.extend(
                await self._search_github(
                    client,
                    query=f"created:>={(datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')}",
                    sort="stars",
                    order="desc",
                    label="🔥 New & Rising",
                )
            )

            # Query 2: repos created in last month, sorted by stars
            all_articles.extend(
                await self._search_github(
                    client,
                    query=f"created:>={(datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d')} stars:>200",
                    sort="stars",
                    order="desc",
                    label="📈 Monthly Rising",
                )
            )

        # Deduplicate
        seen = set()
        unique = []
        for a in all_articles:
            if a.url not in seen:
                seen.add(a.url)
                unique.append(a)

        # Sort: "Hottest New" (7d) first, then "Trending This Month" (30d);
        # within each group, highest stars first (= fastest growing for new repos)
        def _sort_key(a):
            label = a.metadata.get("growth_label", "")
            stars = a.metadata.get("total_stars", 0) or 0
            # 7d repos rank higher than 30d repos
            group = 0 if "Hottest" in label else 1
            return (group, -stars)

        unique.sort(key=_sort_key)

        # Use total stars as score
        for a in unique:
            a.score = a.metadata.get("total_stars", 0)

        logger.info("GitHub Fast-Growing: %d unique repos", len(unique))
        return unique[:self.max_articles]

    async def _search_github(
        self,
        client: httpx.AsyncClient,
        query: str,
        sort: str = "stars",
        order: str = "desc",
        label: str = "",
        per_page: int = 30,
    ) -> list[RawArticle]:
        """Run a GitHub search query and return results as RawArticles."""
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={query}&sort={sort}&order={order}&per_page={per_page}"
        )

        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "GitHub API returned %d for query '%s'",
                    resp.status_code,
                    query[:60],
                )
                return []

            data = resp.json()
            articles = []
            for repo in data.get("items", []):
                full_name = repo.get("full_name", "")
                stars = repo.get("stargazers_count", 0)
                forks = repo.get("forks_count", 0)
                daily_growth = _estimate_daily_growth(repo)
                age_days = _days_since_created(repo)

                articles.append(RawArticle(
                    source_type=SourceType.GITHUB_FAST_GROWING,
                    title=full_name,
                    url=repo.get("html_url", ""),
                    author=repo.get("owner", {}).get("login", ""),
                    published_at=datetime.utcnow(),
                    raw_content=repo.get("description", "") or "",
                    score=stars,
                    comment_count=forks,
                    metadata={
                        "language": repo.get("language", "") or "",
                        "total_stars": stars,
                        "forks": forks,
                        "open_issues": repo.get("open_issues_count", 0),
                        "topics": repo.get("topics", []),
                        "daily_star_growth": daily_growth,
                        "age_days": age_days,
                        "growth_label": label,
                        "created_at": repo.get("created_at", ""),
                    },
                ))

            return articles

        except Exception as e:
            logger.error("GitHub search error for '%s': %s", query[:60], e)
            return []
