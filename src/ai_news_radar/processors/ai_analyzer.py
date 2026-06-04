
"""AI-powered article analyzer using DeepSeek, OpenAI, or Anthropic API."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from ..config import get_openai_api_key, get_anthropic_api_key, get_deepseek_api_key, get_deepseek_base_url
from ..models import Article, RawArticle, Sentiment, Category

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """You are an AI news analyst. Analyze the following article and return a JSON object with these fields:
- summary: A concise 1-2 sentence summary in Chinese (中文)
- category: One of [model_release, research, tool, safety, discussion, tutorial, industry, unknown]
- tags: 2-5 relevant keyword tags (lowercase)
- importance: A score from 0.0 to 1.0 indicating how important/noteworthy this is for AI practitioners
  - 0.9-1.0: Major breakthrough, new flagship model, critical safety finding
  - 0.7-0.9: Significant release, important paper, major funding
  - 0.5-0.7: Interesting tool, good discussion, noteworthy update
  - 0.3-0.5: Minor news, tutorial, general discussion
  - 0.0-0.3: Routine, low relevance
- sentiment: One of [positive, neutral, negative]
- reason: Brief explanation for the importance score (1 sentence)

Title: {title}
Content: {content}
Source: {source}

Return ONLY valid JSON, no markdown formatting, no code blocks."""


class AIAnalyzer:
    """AI-powered article analyzer supporting DeepSeek, OpenAI, and Anthropic backends."""

    def __init__(self, backend: str = "auto", model: Optional[str] = None):
        """
        Args:
            backend: "deepseek", "openai", "anthropic", or "auto"
            model: Specific model name, or None for default
        """
        self.backend = self._resolve_backend(backend)
        self.model = model or self._default_model()

    def _resolve_backend(self, backend: str) -> str:
        if backend == "auto":
            # Priority: DeepSeek > Anthropic > OpenAI
            if get_deepseek_api_key():
                return "deepseek"
            elif get_anthropic_api_key():
                return "anthropic"
            elif get_openai_api_key():
                return "openai"
            else:
                raise ValueError(
                    "No API key found. "
                    "Set DEEPSEEK_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY in .env"
                )
        return backend

    def _default_model(self) -> str:
        if self.backend == "deepseek":
            return "deepseek-chat"
        elif self.backend == "anthropic":
            return "claude-3-haiku-20240307"
        else:
            return "gpt-4o-mini"

    async def analyze(self, article: Article) -> Article:
        """Analyze a single article and populate AI fields."""
        try:
            result = await self._call_llm(
                title=article.title,
                content=(article.raw_content or article.title)[:2000],
                source=article.source_type.value,
            )
            article.summary = result.get("summary", "")
            article.category = Category(
                result.get("category", "unknown")
            )
            article.tags = result.get("tags", [])
            article.importance = float(result.get("importance", 0.0))
            article.sentiment = Sentiment(
                result.get("sentiment", "neutral")
            )
            article.is_analyzed = True
            logger.debug(
                "Analyzed: %s [importance=%.2f, cat=%s]",
                article.title[:40],
                article.importance,
                article.category.value,
            )
        except Exception as e:
            logger.error("Analysis failed for '%s': %s", article.title[:40], e)
            article.is_analyzed = True  # mark as done to avoid retry loop
            article.summary = article.title
            article.importance = 0.3
        return article

    async def analyze_batch(
        self, articles: list[Article], concurrency: int = 5
    ) -> list[Article]:
        """Analyze multiple articles concurrently."""
        if not articles:
            return []

        logger.info("Analyzing %d articles with %s...", len(articles), self.backend)
        semaphore = asyncio.Semaphore(concurrency)

        async def analyze_one(article: Article) -> Article:
            async with semaphore:
                return await self.analyze(article)

        tasks = [analyze_one(a) for a in articles]
        results = await asyncio.gather(*tasks)

        analyzed = sum(1 for a in results if a.is_analyzed)
        logger.info("Analysis complete: %d/%d analyzed", analyzed, len(results))
        return results

    async def _call_llm(
        self, title: str, content: str, source: str
    ) -> dict:
        """Call the LLM API and parse JSON response."""
        prompt = ANALYSIS_PROMPT.format(title=title, content=content, source=source)

        if self.backend == "deepseek":
            return await self._call_deepseek(prompt)
        elif self.backend == "anthropic":
            return await self._call_anthropic(prompt)
        else:
            return await self._call_openai(prompt)

    async def _call_deepseek(self, prompt: str) -> dict:
        """Call DeepSeek API (OpenAI-compatible)."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=get_deepseek_api_key(),
            base_url=get_deepseek_base_url(),
        )
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise AI news analyst. Always respond with valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        text = response.choices[0].message.content or "{}"
        return self._parse_json(text)

    async def _call_openai(self, prompt: str) -> dict:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=get_openai_api_key())
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise AI news analyst. Always respond with valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        text = response.choices[0].message.content or "{}"
        return self._parse_json(text)

    async def _call_anthropic(self, prompt: str) -> dict:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=get_anthropic_api_key())
        response = await client.messages.create(
            model=self.model,
            max_tokens=500,
            temperature=0.1,
            system="You are a precise AI news analyst. Always respond with valid JSON only.",
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        return self._parse_json(text)

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from LLM response, handling common formatting issues."""
        text = text.strip()
        # Remove markdown code blocks if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines if they are ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from the text
            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            logger.warning("Failed to parse LLM response as JSON: %s", text[:200])
            return {
                "summary": "",
                "category": "unknown",
                "tags": [],
                "importance": 0.3,
                "sentiment": "neutral",
                "reason": "parse error",
            }
