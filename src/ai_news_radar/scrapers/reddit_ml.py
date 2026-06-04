
"""Reddit r/MachineLearning scraper using Reddit's JSON API."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from .base import BaseScraper
from ..models import RawArticle, SourceType

logger = logging.getLogger(__name__)

REDDIT_USER_AGENT = 'AI-News-Radar/0.1 (by /u/newsradarbot)'


class RedditMLScraper(BaseScraper):
    """Scrape Reddit r/MachineLearning and related AI subreddits."""

    source_type = SourceType.REDDIT_ML
    source_name = 'Reddit r/MachineLearning'

    SUBREDDITS = [
        'MachineLearning',
        'LocalLLaMA',
        'artificial',
        'OpenAI',
        'ClaudeAI',
        'ChatGPT',
        'singularity',
    ]

    def __init__(self, max_articles: int = 50, request_delay: float = 0.5,
                 subreddits: list[str] | None = None):
        super().__init__(max_articles=max_articles, request_delay=request_delay)
        self.subreddits = subreddits or self.SUBREDDITS

    async def fetch(self) -> list[RawArticle]:
        logger.info('Fetching Reddit AI subreddits...')

        # Try OAuth2 if credentials are available
        import os
        client_id = os.getenv('REDDIT_CLIENT_ID', '')
        client_secret = os.getenv('REDDIT_CLIENT_SECRET', '')
        oauth_headers = {}

        if client_id and client_secret:
            try:
                oauth_headers = await self._get_oauth_token(client_id, client_secret)
                logger.info('Reddit: using OAuth2 authentication')
            except Exception as e:
                logger.warning('Reddit OAuth2 failed: %s, falling back to public API', e)

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={'User-Agent': REDDIT_USER_AGENT, **oauth_headers},
        ) as client:
            all_articles = []
            for subreddit in self.subreddits:
                try:
                    articles = await self._fetch_subreddit(client, subreddit)
                    all_articles.extend(articles)
                    logger.debug('r/%s: %d articles', subreddit, len(articles))
                    await asyncio.sleep(self.request_delay)
                except Exception as e:
                    logger.warning('Error fetching r/%s: %s', subreddit, e)

            # Remove duplicates by URL
            seen_urls = set()
            unique = []
            for article in all_articles:
                if article.url not in seen_urls:
                    seen_urls.add(article.url)
                    unique.append(article)

            logger.info(f'Reddit: {len(unique)} unique articles from {len(self.subreddits)} subs')
            return self._apply_ai_filter(unique)

    async def _get_oauth_token(self, client_id: str, client_secret: str) -> dict:
        """Get Reddit OAuth2 access token."""
        import httpx as _httpx
        auth = _httpx.BasicAuth(client_id, client_secret)
        async with _httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                'https://www.reddit.com/api/v1/access_token',
                data={'grant_type': 'client_credentials'},
                auth=auth,
                headers={'User-Agent': REDDIT_USER_AGENT},
            )
            resp.raise_for_status()
            token = resp.json()['access_token']
            return {'Authorization': f'Bearer {token}'}

    async def _fetch_subreddit(
        self, client: httpx.AsyncClient, subreddit: str
    ) -> list[RawArticle]:
        """Fetch top posts from a single subreddit using old.reddit.com."""
        articles = []

        # Use old.reddit.com which is more scraper-friendly (HTML, no OAuth needed)
        endpoints = [
            f'https://old.reddit.com/r/{subreddit}/hot/?limit=25',
            f'https://old.reddit.com/r/{subreddit}/top/?sort=top&t=day&limit=25',
        ]

        for url in endpoints:
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    logger.warning('Rate limited on r/%s, waiting...', subreddit)
                    await asyncio.sleep(5)
                    continue
                if resp.status_code != 200:
                    logger.debug(
                        'old.reddit.com r/%s returned %d', subreddit, resp.status_code
                    )
                    continue

                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, 'lxml')

                # Find post entries (old Reddit structure)
                for thing in soup.find_all('div', class_='thing'):
                    # Skip stickied and promoted posts
                    if 'stickied' in thing.get('class', []):
                        continue
                    if 'promoted' in thing.get('class', []):
                        continue

                    entry = thing.find('div', class_='entry')
                    if not entry:
                        continue

                    title_link = entry.find('a', class_='title')
                    if not title_link:
                        continue

                    title = title_link.get_text(strip=True)
                    href = title_link.get('href', '')
                    if href.startswith('/r/'):
                        href = 'https://old.reddit.com' + href

                    # Author
                    author_tag = entry.find('a', class_='author')
                    author = author_tag.get_text(strip=True) if author_tag else ''

                    # Score
                    score_tag = thing.find('div', class_='score unvoted')
                    score = 0
                    if score_tag:
                        try:
                            score = int(score_tag.get('title', '0').split()[0])
                        except (ValueError, IndexError):
                            score = 0

                    # Comments count
                    comments_tag = entry.find('a', class_='comments')
                    comment_count = 0
                    if comments_tag:
                        comments_text = comments_tag.get_text(strip=True)
                        try:
                            comment_count = int(comments_text.split()[0])
                        except (ValueError, IndexError):
                            comment_count = 0

                    # Time
                    time_tag = entry.find('time')
                    published_at = datetime.utcnow()
                    if time_tag and time_tag.get('datetime'):
                        try:
                            published_at = datetime.fromisoformat(
                                time_tag['datetime'].replace('Z', '+00:00')
                            )
                        except (ValueError, TypeError):
                            pass

                    # Reddit ID
                    reddit_id = thing.get('data-fullname', '')

                    articles.append(RawArticle(
                        source_type=SourceType.REDDIT_ML,
                        title=title,
                        url=href,
                        author=author,
                        published_at=published_at,
                        raw_content=title,  # Summary comes from title
                        score=score,
                        comment_count=comment_count,
                        metadata={
                            'subreddit': subreddit,
                            'reddit_id': reddit_id,
                        },
                    ))

            except Exception as e:
                logger.debug('Error scraping old.reddit.com/r/%s: %s', subreddit, e)

        return articles
