
import hashlib
import io
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse

from ..config import LOGS_DIR, get_log_level


def setup_logging(name='ai_news_radar', level=None, log_file=None):
    # Force UTF-8 on Windows console to avoid encoding errors
    if sys.platform == 'win32':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    logger = logging.getLogger(name)
    level = level or get_log_level()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if logger.handlers:
        return logger
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    fmt = '%(asctime)s | %(levelname)-7s | %(name)s | %(message)s'
    console.setFormatter(logging.Formatter(fmt, datefmt='%H:%M:%S'))
    logger.addHandler(console)
    if log_file is None:
        from ..config import LOGS_DIR
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file = str(LOGS_DIR / 'app.log')
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    file_fmt = '%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s'
    fh.setFormatter(logging.Formatter(file_fmt))
    logger.addHandler(fh)
    return logger


def normalize_url(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip('/'), '', '', ''))


def url_hash(url):
    return hashlib.md5(normalize_url(url).encode()).hexdigest()[:16]


def truncate_text(text, max_length=500, suffix='...'):
    if not text or len(text) <= max_length:
        return text or ''
    truncated = text[:max_length].rsplit(' ', 1)[0]
    return truncated + suffix


def extract_date(text, patterns=None):
    if patterns is None:
        patterns = [
            r'(\d{4}-\d{2}-\d{2})',
            r'(\d{4}/\d{2}/\d{2})',
            r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})',
            r'(\d{1,2}\s+[A-Z][a-z]{2,8}\s+\d{4})',
        ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            date_str = match.group(1)
            for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%B %d, %Y', '%B %d %Y', '%d %B %Y', '%b %d, %Y']:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
    return None


def parse_datetime(value, default=None):
    if value is None:
        return default
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    for fmt in ['%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']:
        try:
            return datetime.strptime(str(value), fmt)
        except (ValueError, TypeError):
            continue
    return default


def similarity_score(text1, text2):
    if not text1 or not text2:
        return 0.0
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    if not words1 or not words2:
        return 0.0
    return len(words1 & words2) / len(words1 | words2)


def is_ai_related(title, text='', keywords=None):
    if keywords is None:
        keywords = [
            'ai', 'artificial intelligence', 'machine learning', 'llm',
            'gpt', 'claude', 'openai', 'anthropic', 'chatbot',
            'transformer', 'neural', 'deep learning', 'stable diffusion',
            'llama', 'mistral', 'gemini', 'langchain', 'embedding',
            'rag', 'agent', 'prompt', 'finetune', 'pytorch',
        ]
    combined = (title + ' ' + text).lower()
    return any(kw.lower() in combined for kw in keywords)


def time_ago(timestamp):
    if timestamp is None:
        return 'unknown'
    now = datetime.now()
    diff = now - timestamp.replace(tzinfo=None)
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return str(seconds) + 's ago'
    minutes = seconds // 60
    if minutes < 60:
        return str(minutes) + 'm ago'
    hours = minutes // 60
    if hours < 24:
        return str(hours) + 'h ago'
    days = hours // 24
    if days < 7:
        return str(days) + 'd ago'
    weeks = days // 7
    return str(weeks) + 'w ago'
