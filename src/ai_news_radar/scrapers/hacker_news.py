
"""Hacker News scraper using the official Firebase API."""

import asyncio
import logging
from datetime import datetime

import httpx

from .base import BaseScraper
from ..models import RawArticle, SourceType

logger = logging.getLogger(__name__)

HN_BASE = 'https://hacker-news.firebaseio.com/v0'
MAX_CONCURRENT = 20  # parallel item fetches
HN_ITEM_URL = 'https://news.ycombinator.com/item?id={id}'


class HackerNewsScraper(BaseScraper):
    """Scrape Hacker News top stories, filtering for AI-related content."""

    source_type = SourceType.HACKER_NEWS
    source_name = 'Hacker News'

    def __init__(self, max_articles: int = 100, request_delay: float = 0.1,
                 top_stories_limit: int = 500):
        super().__init__(max_articles=max_articles, request_delay=request_delay)
        self.top_stories_limit = top_stories_limit

    async def fetch(self) -> list[RawArticle]:
        logger.info('Fetching Hacker News top stories...')
        import asyncio as _asyncio

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={'User-Agent': 'AI-News-Radar/0.1'},
        ) as client:
            # 1. Get top story IDs with retry
            story_ids = []
            for attempt in range(3):
                try:
                    resp = await client.get(f'{HN_BASE}/topstories.json')
                    resp.raise_for_status()
                    story_ids = resp.json()[:self.top_stories_limit]
                    break
                except Exception as e:
                    logger.warning(f'HN top stories attempt {attempt+1}/3 failed: {e}')
                    if attempt < 2:
                        await _asyncio.sleep(2 * (attempt + 1))

            if not story_ids:
                logger.error('HN: Could not fetch story IDs after 3 attempts')
                return []

            logger.debug('Got %d story IDs', len(story_ids))

            # 2. Fetch items in parallel batches
            articles = []
            semaphore = _asyncio.Semaphore(MAX_CONCURRENT)

            async def fetch_item(story_id: int):
                async with semaphore:
                    for retry in range(2):
                        try:
                            item_resp = await client.get(
                                f'{HN_BASE}/item/{story_id}.json'
                            )
                            item_resp.raise_for_status()
                            item = item_resp.json()
                            if item and item.get('title'):
                                return self._parse_item(item)
                            return None
                        except Exception:
                            if retry < 1:
                                await _asyncio.sleep(0.5)
                    return None

            tasks = [fetch_item(sid) for sid in story_ids]
            results = await _asyncio.gather(*tasks)
            articles = [r for r in results if r is not None]

            logger.info('HN: fetched %d items, applying AI filter...', len(articles))
            return self._apply_ai_filter(articles)

    def _parse_item(self, item: dict) -> RawArticle:
        url = item.get('url') or HN_ITEM_URL.format(id=item.get('id'))
        return RawArticle(
            source_type=SourceType.HACKER_NEWS,
            title=item.get('title', ''),
            url=url,
            author=item.get('by', ''),
            published_at=(
                datetime.utcfromtimestamp(item['time'])
                if item.get('time') else None
            ),
            raw_content=item.get('text', ''),
            score=item.get('score', 0),
            comment_count=item.get('descendants', 0),
            metadata={
                'hn_id': item.get('id'),
                'type': item.get('type', 'story'),
            },
        )
