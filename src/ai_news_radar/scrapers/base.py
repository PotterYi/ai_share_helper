"""Abstract base class for all scrapers."""

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from ..models import RawArticle, FetchResult, SourceType

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Abstract base class for news source scrapers."""

    source_type: SourceType
    source_name: str = ""

    def __init__(self, max_articles: int = 50, request_delay: float = 1.0):
        self.max_articles = max_articles
        self.request_delay = request_delay

    @abstractmethod
    async def fetch(self) -> list[RawArticle]:
        """Fetch articles from the source. Must be implemented by subclasses."""
        ...

    async def fetch_with_result(self) -> FetchResult:
        """Fetch articles and return a FetchResult with metadata."""
        start = time.time()
        errors = []
        fetched = []
        try:
            fetched = await self.fetch()
        except Exception as e:
            errors.append(f"{self.source_type.value}: {e}")
            logger.error(f"Error fetching {self.source_type.value}: {e}", exc_info=True)

        return FetchResult(
            source_type=self.source_type,
            fetched_count=len(fetched),
            new_count=0,  # set by engine after dedup
            errors=errors,
            duration_seconds=round(time.time() - start, 2),
        )

    def _apply_ai_filter(
        self, articles: list[RawArticle], keywords: Optional[list[str]] = None
    ) -> list[RawArticle]:
        """Filter articles to only AI-related ones."""
        from ..utils.helpers import is_ai_related

        if keywords is None:
            keywords = [
                'ai', 'artificial intelligence', 'machine learning', 'llm',
                'gpt', 'claude', 'openai', 'anthropic', 'chatbot',
                'transformer', 'neural', 'deep learning', 'stable diffusion',
                'llama', 'mistral', 'gemini', 'langchain', 'embedding',
                'rag', 'agent', 'prompt', 'finetune', 'pytorch',
                'hugging face', 'nvidia', 'gpu', 'cuda', 'benchmark',
            ]

        filtered = []
        for article in articles:
            text = (article.raw_content or '')
            if is_ai_related(article.title, text, keywords):
                filtered.append(article)

        logger.debug(
            'AI filter: %d/%d articles passed for %s',
            len(filtered), len(articles), self.source_type.value
        )
        return filtered[:self.max_articles]
